"""对话流式 API 路由：chat / approval / cancel。

从 server.py 拆分（原 create_app 内"交互核心"端点：SSE 流式对话、审批响应、取消运行，行为不变）。
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from spark.compaction import compact_messages
from spark.config import APPROVAL_MODES, ProviderConfig
from spark.loop import AgentLoop
from spark.sanitize import sanitize_event
from spark.slash import expand_slash
from spark.tools import build_registry

from .api_common import AppState, check_token, clamp_int, get_app_state, sse

router = APIRouter()


def _build_provider_cfg(state: AppState, model: str) -> dict:
    """组装 provider 配置（从全局配置 + 请求级 override）。

    内部用 ProviderConfig dataclass 集中挑选字段 + 类型校验；
    下游 AgentLoop 仍吃 dict，故 .to_dict() 转回。
    mock_script / mock_subagent_script 走 deepcopy 隔绝非预期共享引用——
    两者是 list，不拷贝会跨会话共享同一 list 对象，pop(0) 一个会话影响另一个。
    """
    cfg_dict = ProviderConfig.from_cfg(state.cfg, model_override=model).to_dict()
    import copy
    if cfg_dict.get("mock_script") is not None:
        cfg_dict["mock_script"] = copy.deepcopy(cfg_dict["mock_script"])
    if cfg_dict.get("mock_subagent_script") is not None:
        cfg_dict["mock_subagent_script"] = copy.deepcopy(cfg_dict["mock_subagent_script"])
    return cfg_dict


@router.post("/api/chat/stream")
async def chat_stream(request: Request, state: AppState = Depends(get_app_state)) -> StreamingResponse:
    check_token(request, state)
    import spark.trace as trace  # 延迟导入，避免启动开销

    body = await request.json()
    sid = str(body.get("session_id") or "")
    prompt = str(body.get("prompt") or "").strip()
    if not sid or not prompt:
        raise HTTPException(status_code=400, detail="缺少 session_id 或 prompt")
    if sid in state.running:
        raise HTTPException(status_code=409, detail="该会话正在运行，请先取消或等待完成")
    meta = state.store.meta(sid)
    if not meta:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 进入 chat_stream 即生成 trace_id，贯穿本次对话全链路
    trace.set_trace(trace.new_trace_id())

    workdir = str(
        body.get("workdir") or meta.get("workdir") or state.cfg.get("workdir") or ""
    ).strip()
    if not workdir:
        raise HTTPException(status_code=400, detail="未设置工作目录")
    model = str(body.get("model") or state.cfg.get("model") or "").strip()
    mode = str(body.get("approval_mode") or state.cfg.get("approval_mode") or "suggest")

    gate = state.gate(sid)
    gate.mode = mode if mode in APPROVAL_MODES else "suggest"
    provider_cfg = _build_provider_cfg(state, model)
    custom_prompt = str(state.cfg.get("system_prompt") or "").strip()
    loop = AgentLoop(
        workdir=Path(workdir),
        provider_cfg=provider_cfg,
        gate=gate,
        max_context_tokens=int(state.cfg.get("max_context_tokens", 32000)),
        max_turns=clamp_int(state.cfg.get("max_turns"), 1, 200, 25),
        tool_timeout=float(clamp_int(state.cfg.get("tool_timeout"), 10, 3600, 180)),
        extra_protected=[str(p) for p in (state.cfg.get("protected_paths") or [])],
        auto_verify=bool(state.cfg.get("auto_verify", True)),
        system_prompt_text=custom_prompt or None,
        memory=state.memory,
        mcp=state.mcp,
        log_path=state.log_dir / f"{date.today().isoformat()}.jsonl",
        registry=build_registry(plugin_tools=state.plugin_tools),
    )
    state.loops[sid] = loop
    state.running.add(sid)

    # 先落盘用户消息再取历史：发给模型的消息必须包含当前 prompt
    # 多模态：body["images"] 为 [{data: base64, mime}]，≤3 张、单张 base64 ≤ 2.8MB（≈2MB 原图）
    images = body.get("images") or []
    expanded_prompt = expand_slash(prompt, workdir)
    user_content: str | list = expanded_prompt
    if images:
        if not isinstance(images, list) or len(images) > 3:
            raise HTTPException(status_code=400, detail="图片最多 3 张")
        parts: list = [{"type": "text", "text": expanded_prompt}]
        for img in images[:3]:
            data = str((img or {}).get("data") or "")
            mime = str((img or {}).get("mime") or "image/png")
            if not data or len(data) > 2_800_000:
                raise HTTPException(status_code=400, detail="单张图片超过 2MB 上限")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        user_content = parts
    user_msg_id = uuid.uuid4().hex[:12]
    state.store.append(sid, {"role": "user", "content": user_content, "id": user_msg_id})
    messages = state.store.messages(sid)

    async def event_stream() -> AsyncIterator[str]:
        assistant_text = ""
        # 输出门禁：SSE 出口统一脱敏（密钥/主目录/堆栈），模型内部仍保留原始信息
        secrets = tuple(
            s
            for s in (
                state.cfg.get("api_key"),
                state.cfg.get("token"),
                state.cfg.get("embed_api_key"),
            )
            if s and isinstance(s, str)
        )
        try:
            trace.info("chat_stream_start", session_id=sid, model=model or state.cfg.get("model", ""), approval_mode=gate.mode)
            yield sse({"type": "hello", "session_id": sid, "user_msg_id": user_msg_id, "trace_id": trace.trace_id()})
            async for ev in loop.stream(messages):
                if ev["type"] == "text":
                    assistant_text += ev.get("delta", "")
                elif ev["type"] == "usage":
                    try:
                        state.usage.record(
                            sid,
                            str(ev.get("model") or model or "unknown"),
                            int(ev.get("prompt_tokens") or 0),
                            int(ev.get("completion_tokens") or 0),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                yield sse(sanitize_event(ev, secrets))
                if ev["type"] == "done":
                    aid = None
                    for m in reversed(messages):
                        if m.get("role") == "assistant":
                            m["id"] = m.get("id") or uuid.uuid4().hex[:12]
                            aid = m["id"]
                            break
                    try:
                        await compact_messages(
                            messages,
                            provider_cfg,
                            int(state.cfg.get("max_context_tokens", 32000)),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    state.store.replace(sid, messages)
                    trace.info("chat_stream_done", session_id=sid, assistant_msg_id=aid)
                    yield sse({**ev, **({"assistant_msg_id": aid} if aid else {})})
                    yield sse({"type": "close"})
                    break
        except asyncio.CancelledError:
            trace.warning("chat_stream_cancelled", session_id=sid)
            await loop.cancel()
            raise
        except Exception as e:
            trace.error("chat_stream_error", session_id=sid, error=str(e))
            raise
        finally:
            state.running.discard(sid)
            state.loops.pop(sid, None)
            trace.info("chat_stream_end", session_id=sid)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/api/approval")
async def approval(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    body = await request.json()
    request_id = str(body.get("request_id") or "")
    action = str(body.get("action") or "")
    tool_name = body.get("tool") or None
    files = body.get("files")  # 逐文件审批：只放行这些文件（apply_patch 按文件过滤）
    if files is not None and (
        not isinstance(files, list) or not all(isinstance(f, str) for f in files)
    ):
        raise HTTPException(status_code=400, detail="files 需为字符串列表")
    if action not in ("allow", "deny", "always"):
        raise HTTPException(status_code=400, detail="action 需为 allow/deny/always")
    ok = False
    for gate in state.gates.values():
        if gate.respond(request_id, action, tool_name, files):
            ok = True
            break
    if not ok:
        raise HTTPException(status_code=404, detail="审批请求不存在或已过期")
    return {"ok": True}


@router.post("/api/cancel")
async def cancel(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    body = await request.json()
    sid = str(body.get("session_id") or "")
    loop = state.loops.get(sid)
    if loop:
        await loop.cancel()
        return {"ok": True}
    raise HTTPException(status_code=404, detail="没有正在运行的会话")
