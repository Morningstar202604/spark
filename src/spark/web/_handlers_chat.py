"""对话端点：非流式 /api/chat 与 SSE 流式 /api/chat/stream（含 /test slash）。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import asyncio as _asyncio
import base64 as _b64mod
import json as _json
import logging
import socket
import threading

from spark.models import ImageRef
from spark.slash import handle_test, parse_slash
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.web.sse import SSE_DISCONNECT_ERRORS, _SSESession
from spark.web.state import _generate_session_meta_async
from spark.web.utils import _event_payload

logger = logging.getLogger(__name__)


def _handle_chat(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        handler._json(400, {"error": "prompt required"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return
    try:
        with state.turn_lock:
            events = _asyncio.run(state.loop.run(prompt))
    except Exception as exc:
        handler._json(500, {"error": str(exc)})
        return
    handler._json(200, {"events": [_event_payload(e) for e in events]})


def _handle_chat_stream(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt and not body.get("images"):
        handler._json(400, {"error": "prompt required"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return

    images: list[dict] = []
    for item in body.get("images") or []:
        if not isinstance(item, str) or not item.startswith("data:image/"):
            continue
        try:
            head, _, b64part = item.partition(",")
            media_type = (
                head.removeprefix("data:").split(";")[0] or "image/png"
            )
            _b64mod.b64decode(b64part, validate=True)
            images.append({"media_type": media_type, "data": b64part})
        except Exception:
            logger.debug("图片 base64 校验失败，跳过该项", exc_info=True)
            continue

    image_refs = [ImageRef.model_validate(i) for i in images] or None
    if not prompt and image_refs:
        prompt = "请分析这些图片。"
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
    except SSE_DISCONNECT_ERRORS:
        handler.close_connection = True
        return

    turn_loop = state.loop
    approver = state.approver

    def close_stream() -> None:
        handler.close_connection = True
        connection = getattr(handler, "connection", None)
        if connection is None:
            return
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except SSE_DISCONNECT_ERRORS:
            try:
                connection.close()
            except SSE_DISCONNECT_ERRORS:
                pass

    def _sse_secrets() -> tuple[str, ...]:
        """SSE 出口脱敏用的已知密钥集（控制令牌 + 模型 API Key）。"""
        api_key = ""
        try:
            api_key = state.cfg.provider.api_key or ""
        except AttributeError:
            pass
        token = getattr(state, "control_token", "") or ""
        return tuple(s for s in (token, api_key) if s)

    def cancel_turn() -> None:
        if turn_loop is not None:
            turn_loop.cancel()
        if approver is not None:
            approver.cancel()

    stream = _SSESession(
        handler.wfile,
        connection=handler.connection,
        on_disconnect=cancel_turn,
        close_connection=close_stream,
        secrets=_sse_secrets(),
    )
    stream.start()

    memory_collector: dict = {"text": "", "tools": []}

    def collect_event(event) -> None:
        if event.type == "text_delta" and event.text:
            memory_collector["text"] += event.text
        elif event.type == "turn_end" and event.text:
            memory_collector["text"] = event.text
        elif event.type == "tool_end" and event.tool_call:
            memory_collector["tools"].append(
                f"{event.tool_call.name}: {_json.dumps(event.result.payload if event.result else {}, ensure_ascii=False)[:200]}"
            )

    # P1-3：后端识别原始 "/test ..." 文本（前端最小实现=直接发送原始 slash），
    # 跑仓库测试；失败时把含 traceback 的输出回灌进同一 agent loop 修正。
    slash = parse_slash(prompt)
    slash_result = None
    if slash is not None and slash.name == "test":
        if not stream.submit_payload(
            {"type": "text_delta", "text": "⏳ 正在运行测试…\n"}
        ):
            return
        try:
            slash_result = handle_test(state.sandbox, slash.args)
        except Exception as exc:  # noqa: BLE001 — slash 失败不拖垮整条 SSE
            logger.warning("/test 执行失败: %s", exc, exc_info=True)
            stream.submit_payload(
                {"type": "turn_error", "text": f"/test 执行失败: {exc}"}
            )
            stream.finish()
            return

    # H5：自定义命令展开（.spark/commands/*.md）；内建 /test 优先，
    # 其余 /name 模板展开后作为用户消息进入 loop，审批/沙箱路径不变。
    from spark.slash import expand_command

    turn_prompt = prompt
    try:
        expanded = expand_command(str(state.sandbox.root), prompt)
    except Exception:
        logger.debug("自定义命令展开失败", exc_info=True)
        expanded = None
    if expanded:
        turn_prompt = expanded

    try:
        if stream.disconnected:
            return
        with state.turn_lock:
            if stream.disconnected:
                return
            state.busy = True
            try:
                if slash_result is not None:
                    stream.submit_payload(
                        {"type": "text_delta", "text": slash_result.summary + "\n"}
                    )
                    if slash_result.ok or not slash_result.feedback:
                        stream.submit_payload(
                            {"type": "turn_end", "text": slash_result.summary}
                        )
                    else:
                        # 回灌失败输出给 agent loop，让其读 traceback 自行修正
                        for event in turn_loop.iter_turn_sync(
                            slash_result.feedback, images=None
                        ):
                            collect_event(event)
                            if not stream.submit(event):
                                break
                        stream.submit_payload(
                            {"type": "turn_end", "text": slash_result.summary}
                        )
                else:
                    for event in turn_loop.iter_turn_sync(
                        turn_prompt, images=image_refs
                    ):
                        collect_event(event)
                        if not stream.submit(event):
                            break
            finally:
                state.busy = False
        if stream.disconnected:
            return
        if not stream.finish():
            return
        turn_text = memory_collector["text"]
        if turn_text:
            _generate_session_meta_async(state, prompt, turn_text)
        if (
            state.cfg.memory.enabled
            and state.cfg.memory.auto_extract
            and state.memory is not None
            and state.cfg.provider.name != "mock"
        ):

            def run_memory() -> None:
                try:
                    state.memory.record_turn(
                        user_text=prompt,
                        assistant_text=turn_text,
                        tool_summary=" | ".join(memory_collector["tools"]),
                        session_id=state.session_id,
                    )
                except Exception:
                    logger.warning("SSE 后台 memory 写入失败", exc_info=True)

            threading.Thread(target=run_memory, daemon=True).start()
    except SSE_DISCONNECT_ERRORS as exc:
        if stream.disconnected:
            cancel_turn()
            stream.abort()
            return
        if stream.submit_payload({"type": "turn_error", "text": str(exc)}):
            stream.finish()
        else:
            stream.abort()
    except Exception as exc:
        if stream.disconnected:
            stream.abort()
            return
        if stream.submit_payload({"type": "turn_error", "text": str(exc)}):
            stream.finish()
        else:
            stream.abort()
    finally:
        if stream.disconnected:
            stream.abort()
