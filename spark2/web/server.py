"""FastAPI 服务：SSE 流式、令牌鉴权、审批/取消、配置、会话、静态单页。

与旧版的关键差异：
- 单进程单事件循环（uvicorn asyncio），不再 http.server + 每轮新事件循环。
- 默认只绑 127.0.0.1，访问令牌校验，审批/取消走 asyncio 原生机制。
- 静态页面（单文件 index.html）随包发布，无第二套前端。
"""
from __future__ import annotations

import asyncio
import json
import shutil
from datetime import date
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from spark2 import __version__
from spark2.approval import ApprovalGate
from spark2.config import APPROVAL_MODES, PRESETS, apply_preset, load_config, mask_key, save_config
from spark2.loop import AgentLoop
from spark2.memory import MemoryStore, make_embedder
from spark2.plugins import collect_plugin_tools, load_plugins, plugins_dir
from spark2.provider import test_connection
from spark2.pty import PtyManager
from spark2.store import SessionStore
from spark2.tools import build_registry
from spark2.tools.mcp import McpManager, servers_from_cfg
from spark2.usage import UsageStore

WEB_DIR = Path(__file__).parent


class AppState:
    def __init__(self, cfg: dict | None = None, store: SessionStore | None = None, memory: MemoryStore | None = None) -> None:
        self.cfg = cfg or load_config()
        self.store = store or SessionStore()
        self.memory = memory or MemoryStore(embedder=make_embedder(self.cfg))
        self.mcp = McpManager(servers_from_cfg(self.cfg))
        self.pty = PtyManager()
        self.log_dir = Path(self.cfg.get("log_dir") or (Path.home() / ".spark2" / "logs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.loops: dict[str, AgentLoop] = {}
        self.gates: dict[str, ApprovalGate] = {}
        self.running: set[str] = set()
        # P3：插件 + 用量
        self.plugin_dir = Path(str(self.cfg.get("plugins_dir") or plugins_dir())).expanduser()
        self.plugins = load_plugins(self.plugin_dir)
        self.plugin_tools = collect_plugin_tools(self.plugin_dir)
        pricing = self.cfg.get("usage_pricing") or None
        self.usage = UsageStore(
            root=Path(str(self.cfg.get("usage_dir") or (Path.home() / ".spark2" / "usage"))),
            pricing=pricing,
        )

    def gate(self, sid: str) -> ApprovalGate:
        if sid not in self.gates:
            self.gates[sid] = ApprovalGate(mode=self.cfg.get("approval_mode", "suggest"))
        return self.gates[sid]

    def reload_mcp(self) -> None:
        """配置变更后重建 MCP 管理器（下次对话时生效）。"""
        self.mcp.close()
        self.mcp = McpManager(servers_from_cfg(self.cfg))


def _check_token(request: Request, state: AppState) -> None:
    token = request.headers.get("x-spark-token") or request.query_params.get("token")
    if token != state.cfg.get("token"):
        raise HTTPException(status_code=401, detail="未授权：访问令牌不正确")


def create_app(state: AppState | None = None) -> FastAPI:
    state = state or AppState()
    app = FastAPI(title="Spark Agent", version=__version__)
    app.state.spark = state

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    # ---------- 配置 ----------
    @app.get("/api/config")
    async def get_config(request: Request) -> dict:
        _check_token(request, state)
        return {
            "presets": {k: {"label": v["label"], "base_url": v["base_url"], "model": v["model"]} for k, v in PRESETS.items()},
            "current": {
                "provider": state.cfg.get("provider", ""),
                "base_url": state.cfg.get("base_url", ""),
                "model": state.cfg.get("model", ""),
                "model_fast": state.cfg.get("model_fast", ""),
                "api_key": mask_key(state.cfg.get("api_key", "")),
                "workdir": state.cfg.get("workdir", ""),
                "approval_mode": state.cfg.get("approval_mode", "suggest"),
                "max_context_tokens": state.cfg.get("max_context_tokens", 32000),
                "memory_embedding": state.cfg.get("memory_embedding", "off"),
                "memory_embed_model": state.cfg.get("memory_embed_model", ""),
            },
            "approval_modes": [{"value": m, "label": _mode_label(m)} for m in APPROVAL_MODES],
            "mcp": {
                "available": state.mcp.available,
                "enabled": state.mcp.enabled,
                "servers": [
                    {
                        "name": s.name,
                        "transport": s.transport,
                        "command": s.command,
                        "args": list(s.args),
                        "url": s.url,
                        "headers": dict(s.headers),
                        "env": dict(s.env),
                    }
                    for s in state.mcp.servers
                ],
            },
            "log_dir": str(state.log_dir),
            "version": __version__,
        }

    @app.get("/api/usage")
    async def get_usage(request: Request) -> dict:
        """用量统计：?session_id=xxx 查单会话；否则查全局总览。"""
        _check_token(request, state)
        sid = (request.query_params.get("session_id") or "").strip()
        if sid:
            return state.usage.session_summary(sid)
        return state.usage.global_summary()

    @app.get("/api/recent-dirs")
    async def get_recent_dirs(request: Request) -> dict:
        """最近使用的工作目录（最多 8 条，供前端快速选择）。"""
        _check_token(request, state)
        from spark2.recent_dirs import load_recent

        return {"dirs": load_recent()}

    @app.get("/api/plugins")
    async def get_plugins(request: Request) -> dict:
        """插件列表：已加载工具与失败原因（页面只读展示，插件在本地目录增删后重启生效）。"""
        _check_token(request, state)
        out = []
        for name, info in state.plugins.items():
            out.append({
                "name": name,
                "tools": [t.name for t in info.get("tools", [])],
                "error": info.get("error"),
            })
        return {"dir": str(state.plugin_dir), "plugins": out, "tool_count": len(state.plugin_tools)}

    @app.post("/api/config")
    async def set_config(request: Request) -> dict:
        _check_token(request, state)
        body = await request.json()
        for k in ("base_url", "model", "workdir", "approval_mode", "model_fast", "memory_embed_model"):
            v = body.get(k)
            if isinstance(v, str) and v.strip():
                state.cfg[k] = v.strip()
        v = body.get("max_context_tokens")
        if isinstance(v, int) and v > 0:
            state.cfg["max_context_tokens"] = v
        if body.get("memory_embedding") in ("off", "api", "local"):
            state.cfg["memory_embedding"] = body["memory_embedding"]
        key = body.get("api_key")
        if isinstance(key, str) and key and not set(key) <= {"*"}:
            state.cfg["api_key"] = key
        if body.get("provider") in PRESETS:
            apply_preset(state.cfg, body["provider"])
        # MCP 服务器列表（本地 stdio / 远程 Streamable HTTP）
        ms = body.get("mcp_servers")
        if isinstance(ms, list):
            cleaned = []
            for item in ms:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                transport = str(item.get("transport", "stdio")).strip().lower()
                if transport not in ("stdio", "http"):
                    transport = "stdio"
                command = str(item.get("command", "")).strip()
                url = str(item.get("url", "")).strip()
                if transport == "http":
                    if not url:
                        continue
                elif not command:
                    continue
                cleaned.append(
                    {
                        "name": name,
                        "transport": transport,
                        "command": command,
                        "args": [str(a) for a in item.get("args") or []],
                        "url": url,
                        "headers": (
                            {str(k): str(v) for k, v in item["headers"].items()}
                            if isinstance(item.get("headers"), dict)
                            else {}
                        ),
                        "env": (
                            {str(k): str(v) for k, v in item["env"].items()}
                            if isinstance(item.get("env"), dict)
                            else {}
                        ),
                    }
                )
            state.cfg["mcp_servers"] = cleaned
            state.reload_mcp()
        save_config(state.cfg)
        # 工作目录记入最近列表（切换项目不用每次手打路径）
        try:
            if state.cfg.get("workdir"):
                from spark2.recent_dirs import remember

                remember(str(state.cfg["workdir"]))
        except Exception:  # noqa: BLE001
            pass
        # 语义记忆开关变化后重建记忆库嵌入器（下次对话生效）
        try:
            state.memory = MemoryStore(embedder=make_embedder(state.cfg))
        except Exception:  # noqa: BLE001
            pass
        return await get_config(request)

    @app.post("/api/test-connection")
    async def test(request: Request) -> dict:
        _check_token(request, state)
        body = await request.json()
        probe = dict(state.cfg)
        for k in ("base_url", "model"):
            if body.get(k):
                probe[k] = body[k]
        key = body.get("api_key")
        if isinstance(key, str) and key and not set(key) <= {"*"}:
            probe["api_key"] = key
        ok, msg = await test_connection(probe)
        return {"ok": ok, "message": msg}

    # ---------- 会话 ----------
    @app.get("/api/sessions")
    async def list_sessions(request: Request) -> list[dict]:
        _check_token(request, state)
        rows = state.store.list()
        for r in rows:
            r["running"] = r.get("id") in state.running
        return rows

    @app.post("/api/sessions/{sid}/fork")
    async def fork_session(sid: str, request: Request) -> dict:
        _check_token(request, state)
        meta = state.store.fork(sid)
        if not meta:
            raise HTTPException(status_code=404, detail="会话不存在")
        return meta

    @app.get("/api/sessions/{sid}")
    async def get_session(sid: str, request: Request) -> dict:
        _check_token(request, state)
        meta = state.store.meta(sid)
        if not meta:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"meta": meta, "messages": state.store.messages(sid)}

    @app.post("/api/sessions")
    async def create_session(request: Request) -> dict:
        _check_token(request, state)
        body = await request.json()
        workdir = str(body.get("workdir") or state.cfg.get("workdir") or "").strip()
        if not workdir:
            raise HTTPException(status_code=400, detail="请先在工作目录设置里指定项目路径")
        meta = state.store.create(workdir, str(body.get("model") or state.cfg.get("model") or ""))
        return meta

    @app.delete("/api/sessions/{sid}")
    async def delete_session(sid: str, request: Request) -> dict:
        _check_token(request, state)
        if sid in state.running:
            raise HTTPException(status_code=409, detail="该会话正在运行，先停止再删除")
        if not state.store.delete(sid):
            raise HTTPException(status_code=404, detail="会话不存在")
        state.gates.pop(sid, None)
        return {"ok": True}

    @app.patch("/api/sessions/{sid}")
    async def rename_session(sid: str, request: Request) -> dict:
        _check_token(request, state)
        body = await request.json()
        title = str(body.get("title") or "").strip()[:60]
        if not title:
            raise HTTPException(status_code=400, detail="标题不能为空")
        if not state.store.rename(sid, title):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"ok": True, "title": title}

    # ---------- 对话 ----------
    @app.post("/api/chat/stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        _check_token(request, state)
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

        workdir = str(body.get("workdir") or meta.get("workdir") or state.cfg.get("workdir") or "").strip()
        if not workdir:
            raise HTTPException(status_code=400, detail="未设置工作目录")
        model = str(body.get("model") or state.cfg.get("model") or "").strip()
        mode = str(body.get("approval_mode") or state.cfg.get("approval_mode") or "suggest")

        gate = state.gate(sid)
        gate.mode = mode if mode in APPROVAL_MODES else "suggest"
        provider_cfg = {
            "provider": state.cfg.get("provider"),
            "base_url": state.cfg.get("base_url", ""),
            "model": model or state.cfg.get("model", ""),
            "api_key": state.cfg.get("api_key", ""),
            "model_fast": state.cfg.get("model_fast", ""),
        }
        # 演示/测试脚本随 provider 传递（不进配置文件）
        if state.cfg.get("mock_script"):
            provider_cfg["mock_script"] = state.cfg["mock_script"]
        if state.cfg.get("mock_subagent_script"):
            provider_cfg["mock_subagent_script"] = state.cfg["mock_subagent_script"]
        loop = AgentLoop(
            workdir=Path(workdir),
            provider_cfg=provider_cfg,
            gate=gate,
            max_context_tokens=int(state.cfg.get("max_context_tokens", 32000)),
            memory=state.memory,
            mcp=state.mcp,
            log_path=state.log_dir / f"{date.today().isoformat()}.jsonl",
            registry=build_registry(plugin_tools=state.plugin_tools),
        )
        state.loops[sid] = loop
        state.running.add(sid)

        messages = state.store.messages(sid)
        state.store.append(sid, {"role": "user", "content": prompt})

        async def event_stream() -> AsyncIterator[str]:
            assistant_text = ""
            try:
                yield _sse({"type": "hello", "session_id": sid})
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
                    yield _sse(ev)
                    if ev["type"] == "done":
                        if assistant_text:
                            state.store.append(sid, {"role": "assistant", "content": assistant_text})
                        break
                yield _sse({"type": "close"})
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
        _check_token(request, state)
        body = await request.json()
        request_id = str(body.get("request_id") or "")
        action = str(body.get("action") or "")
        tool_name = body.get("tool") or None
        if action not in ("allow", "deny", "always"):
            raise HTTPException(status_code=400, detail="action 需为 allow/deny/always")
        ok = False
        for gate in state.gates.values():
            if gate.respond(request_id, action, tool_name):
                ok = True
                break
        if not ok:
            raise HTTPException(status_code=404, detail="审批请求不存在或已过期")
        return {"ok": True}

    @app.post("/api/cancel")
    async def cancel(request: Request) -> dict:
        _check_token(request, state)
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
        if websocket.query_params.get("token") != state.cfg.get("token"):
            await websocket.send_text(json.dumps({"type": "err", "message": "未授权：访问令牌不正确"}))
            await websocket.close(code=4401)
            return
        tab_id = websocket.query_params.get("tab") or ""
        sid = websocket.query_params.get("sid") or ""
        # 工作目录：优先取该会话的 workdir，其次全局配置
        cwd = state.cfg.get("workdir") or ""
        if sid:
            meta = state.store.meta(sid)
            if meta and meta.get("workdir"):
                cwd = meta["workdir"]
        if not cwd:
            await websocket.send_text(json.dumps({"type": "err", "message": "未设置工作目录（先到设置里指定）"}))
            await websocket.close(code=4400)
            return
        sess = state.pty.get_or_create(tab_id, Path(cwd))
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
                    state.pty.write(sess.tab_id, str(msg.get("data", "")))
                elif t == "resize":
                    state.pty.resize(sess.tab_id, int(msg.get("cols", 110)), int(msg.get("rows", 28)))

        try:
            await asyncio.gather(send_loop(), recv_loop())
        except WebSocketDisconnect:
            pass
        except (RuntimeError, asyncio.CancelledError):
            pass
        finally:
            # 只断开推送，不杀进程：UI 折叠/刷新后重连，终端内容仍在（持久终端）
            pass

    # ---------- 记忆 ----------
    @app.get("/api/memory")
    async def list_memory(request: Request) -> dict:
        _check_token(request, state)
        workdir = str(request.query_params.get("workdir") or state.cfg.get("workdir") or "")
        if not workdir:
            raise HTTPException(status_code=400, detail="未设置工作目录")
        return {
            "workdir": workdir,
            "count": state.memory.count(workdir),
            "items": state.memory.list(workdir, limit=200),
        }

    @app.delete("/api/memory/{memory_id}")
    async def delete_memory(memory_id: int, request: Request) -> dict:
        _check_token(request, state)
        if not state.memory.delete_by_id(memory_id):
            raise HTTPException(status_code=404, detail="记忆不存在")
        return {"ok": True}

    # 静态资源（放在路由之后，作为兜底）
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="static")
    return app


def _sse(data: dict) -> str:
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


# 与 spark2.pty._EXIT_MARKER 对应（避免跨模块耦合字符串，这里直接引用常量）
from spark2.pty import _EXIT_MARKER as _PTY_EXIT_MARKER  # noqa: E402


def _mode_label(mode: str) -> str:
    return {
        "suggest": "询问（写入与命令都要确认）",
        "auto-edit": "自动编辑（工作区内写入不询问，命令询问）",
        "full-auto": "全自动（都不询问，谨慎使用）",
    }.get(mode, mode)


# 供 uvicorn 直接使用：python -m spark2.web
app = create_app()
