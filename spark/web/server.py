"""FastAPI 服务主装配：SSE 流式对话、审批/取消、内置终端、静态单页。

由 create_app() 组装各 api_* 路由模块，本文件保留"交互核心"：
- POST /api/chat/stream（SSE 事件流，AgentLoop 驱动）
- POST /api/approval / /api/cancel
- WS /ws/pty + POST /api/pty/close
- GET /（单页）+ 静态资源兜底挂载

已拆出的兄弟模块（见 api_common / api_config / api_sessions / api_data）：
- api_common.py  AppState 与鉴权/辅助（check_token/json_body/config_payload/sse 等）
- api_config.py  配置 / 测试连接 / 最近目录 / 用量 / 插件
- api_sessions.py 会话 CRUD / 分叉
- api_data.py    记忆 / 文件浏览 / git 检查点
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from spark import __version__
from spark.config import APPROVAL_MODES
from spark.loop import AgentLoop
from spark.sanitize import sanitize_event
from spark.slash import expand_slash
from spark.tools import build_registry

from .api_common import (
    AppState,
    check_token,
    clamp_int,
    sse,
)
from .api_config import router as router_config
from .api_data import router as router_data
from .api_sessions import router as router_sessions

WEB_DIR = Path(__file__).parent
DIST_DIR = WEB_DIR / "dist"
STATIC_DIR = DIST_DIR if DIST_DIR.exists() else WEB_DIR


# 与 spark.pty._EXIT_MARKER 对应（避免跨模块耦合字符串，这里直接引用常量）
from spark.pty import _EXIT_MARKER as _PTY_EXIT_MARKER  # noqa: E402

# 兼容性再导出：拆分前这些符号从 spark.web.server 导入（tests / cli 无感）
# （AppState / create_app / app）


def create_app(state: AppState | None = None) -> FastAPI:
    state = state or AppState()
    app = FastAPI(title="Spark Agent", version=__version__)
    app.state.spark = state

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # 按域组装路由（各模块自行鉴权）
    app.include_router(router_config)
    app.include_router(router_sessions)
    app.include_router(router_data)

    # ---------- 对话（SSE 流式） ----------
    @app.post("/api/chat/stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        check_token(request, state)
        body = await request.json()
        sid = str(body.get("session_id") or "")
        prompt = str(body.get("prompt") or "").strip()
        if not sid or not prompt:
            raise HTTPException(status_code=400, detail="缺少 session_id 或 prompt")
        if sid in state.running:
            raise HTTPException(
                status_code=409, detail="该会话正在运行，请先取消或等待完成"
            )
        meta = state.store.meta(sid)
        if not meta:
            raise HTTPException(status_code=404, detail="会话不存在")

        workdir = str(
            body.get("workdir") or meta.get("workdir") or state.cfg.get("workdir") or ""
        ).strip()
        if not workdir:
            raise HTTPException(status_code=400, detail="未设置工作目录")
        model = str(body.get("model") or state.cfg.get("model") or "").strip()
        mode = str(
            body.get("approval_mode") or state.cfg.get("approval_mode") or "suggest"
        )

        gate = state.gate(sid)
        gate.mode = mode if mode in APPROVAL_MODES else "suggest"
        provider_cfg = {
            "provider": state.cfg.get("provider"),
            "base_url": state.cfg.get("base_url", ""),
            "model": model or state.cfg.get("model", ""),
            "api_key": state.cfg.get("api_key", ""),
            "model_fast": state.cfg.get("model_fast", ""),
            "fallback_model": state.cfg.get("fallback_model", ""),
        }
        # 演示/测试脚本随 provider 传递（不进配置文件）
        if state.cfg.get("mock_script"):
            provider_cfg["mock_script"] = state.cfg["mock_script"]
        if state.cfg.get("mock_subagent_script"):
            provider_cfg["mock_subagent_script"] = state.cfg["mock_subagent_script"]
        # 高级可调项随 provider 传递（采样参数 + 模型路由定制）
        provider_cfg["temperature"] = state.cfg.get("temperature", "")
        provider_cfg["max_tokens"] = state.cfg.get("max_tokens", "")
        provider_cfg["route_enabled"] = state.cfg.get("route_enabled", True)
        provider_cfg["route_keywords"] = state.cfg.get("route_keywords", "")
        custom_prompt = str(state.cfg.get("system_prompt") or "").strip()
        loop = AgentLoop(
            workdir=Path(workdir),
            provider_cfg=provider_cfg,
            gate=gate,
            max_context_tokens=int(state.cfg.get("max_context_tokens", 32000)),
            max_turns=clamp_int(state.cfg.get("max_turns"), 1, 200, 25),
            tool_timeout=float(
                clamp_int(state.cfg.get("tool_timeout"), 10, 3600, 180)
            ),
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
        # （此前先取后写，真实模型首轮收到空对话 → 400 No user query）
        # 多模态：body["images"] 为 [{data: base64, mime}]，≤3 张、单张 base64 ≤ 2.8MB（≈2MB 原图）
        images = body.get("images") or []
        user_content: str | list = expand_slash(prompt, workdir)
        if images:
            if not isinstance(images, list) or len(images) > 3:
                raise HTTPException(status_code=400, detail="图片最多 3 张")
            parts: list = [{"type": "text", "text": prompt}]
            for img in images[:3]:
                data = str((img or {}).get("data") or "")
                mime = str((img or {}).get("mime") or "image/png")
                if not data or len(data) > 2_800_000:
                    raise HTTPException(status_code=400, detail="单张图片超过 2MB 上限")
                parts.append(
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
                )
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
                yield sse({"type": "hello", "session_id": sid, "user_msg_id": user_msg_id})
                async for ev in loop.stream(messages):
                    if ev["type"] == "text":
                        assistant_text += ev.get("delta", "")
                    elif ev["type"] == "usage":
                        # 用量记录：估算费用按现役官方价（可在设置覆盖单价）
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
                        # 中间轮工具消息也一并落库（原来只存最终文本），
                        # 断点/重开会话时恢复完整上下文不失真。
                        aid = None
                        for m in reversed(messages):
                            if m.get("role") == "assistant":
                                m["id"] = m.get("id") or uuid.uuid4().hex[:12]
                                aid = m["id"]
                                break
                        state.store.replace(sid, messages)
                        yield sse(
                            {**ev, **({"assistant_msg_id": aid} if aid else {})}
                        )
                        break
                yield sse({"type": "close"})
            except asyncio.CancelledError:
                await loop.cancel()
                raise
            finally:
                state.running.discard(sid)
                state.loops.pop(sid, None)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ---------- 审批 / 取消 ----------
    @app.post("/api/approval")
    async def approval(request: Request) -> dict:
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

    @app.post("/api/cancel")
    async def cancel(request: Request) -> dict:
        check_token(request, state)
        body = await request.json()
        sid = str(body.get("session_id") or "")
        loop = state.loops.get(sid)
        if loop:
            await loop.cancel()
            return {"ok": True}
        raise HTTPException(status_code=404, detail="没有正在运行的会话")

    # ---------- 内置终端（WebSocket，用户自己的持久 PTY） ----------
    @app.websocket("/ws/pty")
    async def ws_pty(websocket: WebSocket):
        await websocket.accept()
        # WS 无法自定义 header，令牌走 query 参数（与页面内嵌 token 一致）
        expected = (state.cfg.get("token") or "").strip()
        if expected and websocket.query_params.get("token") != expected:
            await websocket.send_text(
                json.dumps({"type": "err", "message": "未授权：访问令牌不正确"})
            )
            await websocket.close(code=4401)
            return
        from spark.pty import safe_tab_id

        tab_id = safe_tab_id(websocket.query_params.get("tab") or "")
        sid = websocket.query_params.get("sid") or ""
        # 工作目录：优先取该会话的 workdir，其次全局配置
        cwd = state.cfg.get("workdir") or ""
        if sid:
            meta = state.store.meta(sid)
            if meta and meta.get("workdir"):
                cwd = meta["workdir"]
        if not cwd:
            await websocket.send_text(
                json.dumps(
                    {"type": "err", "message": "未设置工作目录（先到设置里指定）"}
                )
            )
            await websocket.close(code=4400)
            return
        try:
            sess = state.pty.get_or_create(sid, tab_id, Path(cwd))
        except (
            RuntimeError,
            OSError,
        ) as exc:  # 后端不可用/shell 缺失：报明确错误而非崩溃
            await websocket.send_text(json.dumps({"type": "err", "message": str(exc)}))
            await websocket.close(code=4400)
            return
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2048)
        sess.start_reader(asyncio.get_running_loop(), queue)

        async def send_loop() -> None:
            while True:
                data = await queue.get()
                if data == _PTY_EXIT_MARKER:
                    await websocket.send_text(json.dumps({"type": "exit"}))
                    return
                await websocket.send_text(json.dumps({"type": "out", "data": data}))

        async def recv_loop() -> None:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "in":
                    state.pty.write(sid, sess.tab_id, str(msg.get("data", "")))
                elif t == "resize":
                    state.pty.resize(
                        sid,
                        sess.tab_id,
                        int(msg.get("cols", 110)),
                        int(msg.get("rows", 28)),
                    )

        try:
            await asyncio.gather(send_loop(), recv_loop())
        except WebSocketDisconnect:
            pass
        except (RuntimeError, asyncio.CancelledError):
            pass
        finally:
            # 只断开推送，不杀进程：UI 折叠/刷新后重连，终端内容仍在（持久终端）
            pass

    @app.post("/api/pty/close")
    async def pty_close(request: Request) -> dict:
        """关闭一个终端 tab（真正终止其 shell 子进程）。"""
        check_token(request, state)
        body = await request.json()
        sid = str(body.get("sid") or "")
        from spark.pty import safe_tab_id

        tab_id = safe_tab_id(str(body.get("tab") or ""))
        state.pty.close(sid, tab_id)
        return {"ok": True}

    # 静态资源（放在路由之后，作为兜底）
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


# 供 uvicorn 直接使用：python -m spark.web
app = create_app()
