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
from spark2.config import (
    APPROVAL_MODES,
    PRESETS,
    apply_preset,
    config_dir,
    config_file,
    load_config,
    mask_key,
    save_config,
)
from spark2.loop import AgentLoop
from spark2.memory import MemoryStore, make_embedder
from spark2.plugins import collect_plugin_tools, load_plugins, plugins_dir
from spark2.provider import test_connection
from spark2.pty import PtyManager, safe_tab_id
from spark2.store import SessionStore
from spark2.tools import build_registry
from spark2.tools.mcp import McpManager, servers_from_cfg
from spark2.usage import UsageStore

WEB_DIR = Path(__file__).parent


class AppState:
    def __init__(
        self,
        cfg: dict | None = None,
        store: SessionStore | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self.store = store or SessionStore()
        self.memory = memory or MemoryStore(embedder=make_embedder(self.cfg))
        self.mcp = McpManager(servers_from_cfg(self.cfg))
        self.pty = PtyManager()
        # 默认目录动态走 config_dir()：遵守 SPARK2_HOME 隔离契约（见 config.py）
        self.log_dir = Path(self.cfg.get("log_dir") or (config_dir() / "logs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.loops: dict[str, AgentLoop] = {}
        self.gates: dict[str, ApprovalGate] = {}
        self.running: set[str] = set()
        # P3：插件 + 用量
        self.plugin_dir = Path(
            str(self.cfg.get("plugins_dir") or plugins_dir())
        ).expanduser()
        self.plugins = load_plugins(self.plugin_dir)
        self.plugin_tools = collect_plugin_tools(self.plugin_dir)
        pricing = self.cfg.get("usage_pricing") or None
        self.usage = UsageStore(
            root=Path(str(self.cfg.get("usage_dir") or (config_dir() / "usage"))),
            pricing=pricing,
        )

    def gate(self, sid: str) -> ApprovalGate:
        if sid not in self.gates:
            self.gates[sid] = ApprovalGate(
                mode=self.cfg.get("approval_mode", "suggest")
            )
        return self.gates[sid]

    def reload_mcp(self) -> None:
        """配置变更后重建 MCP 管理器（下次对话时生效）。"""
        self.mcp.close()
        self.mcp = McpManager(servers_from_cfg(self.cfg))


def _check_token(request: Request, state: AppState) -> None:
    expected = (state.cfg.get("token") or "").strip()
    if not expected:
        return  # 未设置令牌：本机免登录（服务只绑 127.0.0.1），可在设置里开启
    token = request.headers.get("x-spark-token") or request.query_params.get("token")
    if token != expected:
        raise HTTPException(status_code=401, detail="未授权：访问令牌不正确")


def _clamp_int(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _session_workdir(state: AppState, sid: str) -> str:
    """会话工作目录：优先会话元数据，兜底全局配置。"""
    if sid:
        meta = state.store.meta(sid)
        if meta and meta.get("workdir"):
            return str(meta["workdir"])
    return str(state.cfg.get("workdir") or "")


async def _json_body(request: Request) -> dict:
    """解析 JSON 请求体；非法 JSON/非对象 → 400（不落 500）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="请求体需是合法 JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体需是 JSON 对象")
    return body


def _config_payload(state: AppState) -> dict:
    """配置响应体（鉴权由调用方入口保证）。

    get_config 与 set_config 共用；set_config 设置令牌后直接返回本 payload，
    避免"从免登录状态设置令牌"时被新令牌二次校验打成 401。
    """
    return {
        "presets": {
            k: {"label": v["label"], "base_url": v["base_url"], "model": v["model"]}
            for k, v in PRESETS.items()
        },
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
            "token_set": bool((state.cfg.get("token") or "").strip()),
            # 高级可调项（全部可在设置面板控制）
            "system_prompt": state.cfg.get("system_prompt", ""),
            "protected_paths": list(state.cfg.get("protected_paths") or []),
            "max_turns": int(state.cfg.get("max_turns", 25)),
            "tool_timeout": int(state.cfg.get("tool_timeout", 180)),
            "temperature": state.cfg.get("temperature", ""),
            "max_tokens": state.cfg.get("max_tokens", ""),
            "route_enabled": bool(state.cfg.get("route_enabled", True)),
            "route_keywords": state.cfg.get("route_keywords", ""),
            "usage_pricing": dict(state.cfg.get("usage_pricing") or {}),
        },
        "approval_modes": [
            {"value": m, "label": _mode_label(m)} for m in APPROVAL_MODES
        ],
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
        "dirs": {
            "config": str(config_file()),
            "sessions": str(state.store.root),
            "logs": str(state.log_dir),
            "usage": str(state.usage.root),
            "plugins": str(state.plugin_dir),
            "memory_db": str(config_dir() / "memory.db"),
        },
        "version": __version__,
    }


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
        return _config_payload(state)

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
            out.append(
                {
                    "name": name,
                    "tools": [t.name for t in info.get("tools", [])],
                    "error": info.get("error"),
                }
            )
        return {
            "dir": str(state.plugin_dir),
            "plugins": out,
            "tool_count": len(state.plugin_tools),
        }

    @app.post("/api/config")
    async def set_config(request: Request) -> dict:
        _check_token(request, state)
        body = await request.json()
        # provider 变化先应用预设（切换服务重置 base_url/model），
        # 随后显式字段覆盖预设：同 provider 再保存时，用户手填的第三方端点不被回滚
        new_provider = body.get("provider")
        if new_provider in PRESETS and new_provider != state.cfg.get("provider"):
            apply_preset(state.cfg, new_provider)
        for k in (
            "base_url",
            "model",
            "workdir",
            "approval_mode",
            "model_fast",
            "memory_embed_model",
        ):
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
        if new_provider == "mock":
            # 演示模式不联网：清掉本轮可能写入的密钥，不留残留
            state.cfg["api_key"] = ""
        # 访问令牌：显式提供 token 键才改（空字符串 = 清除，恢复本机免登录）
        if "token" in body and isinstance(body.get("token"), str):
            state.cfg["token"] = body["token"].strip()
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
        # ---- 高级可调项（类型/范围校验后才落盘） ----
        if "system_prompt" in body and isinstance(body.get("system_prompt"), str):
            state.cfg["system_prompt"] = body["system_prompt"][:20000]
        if "protected_paths" in body and isinstance(body.get("protected_paths"), list):
            cleaned_paths = []
            for item in body["protected_paths"][:50]:
                if isinstance(item, str) and item.strip():
                    cleaned_paths.append(item.strip())
            state.cfg["protected_paths"] = cleaned_paths
        if "max_turns" in body:
            try:
                state.cfg["max_turns"] = max(1, min(200, int(body["max_turns"])))
            except (TypeError, ValueError):
                pass  # 无效值保留原配置
        if "tool_timeout" in body:
            try:
                state.cfg["tool_timeout"] = max(
                    10, min(3600, int(body["tool_timeout"]))
                )
            except (TypeError, ValueError):
                pass
        if "temperature" in body:
            raw = body.get("temperature")
            try:
                if raw is None or str(raw).strip() == "":
                    state.cfg["temperature"] = ""
                else:
                    state.cfg["temperature"] = max(0.0, min(2.0, float(raw)))
            except (TypeError, ValueError):
                pass  # 无效值保留原配置
        if "max_tokens" in body:
            raw = body.get("max_tokens")
            try:
                if raw is None or str(raw).strip() == "":
                    state.cfg["max_tokens"] = ""
                else:
                    state.cfg["max_tokens"] = max(1, min(1_000_000, int(raw)))
            except (TypeError, ValueError):
                pass
        if isinstance(body.get("route_enabled"), bool):
            state.cfg["route_enabled"] = body["route_enabled"]
        if "route_keywords" in body and isinstance(body.get("route_keywords"), str):
            state.cfg["route_keywords"] = body["route_keywords"][:2000]
        if "usage_pricing" in body and isinstance(body.get("usage_pricing"), dict):
            pricing: dict[str, dict] = {}
            for mk, mv in list(body["usage_pricing"].items())[:100]:
                if not isinstance(mk, str) or not isinstance(mv, dict):
                    continue
                try:
                    pricing[mk.strip()] = {
                        "input": float(mv.get("input", 0)),
                        "output": float(mv.get("output", 0)),
                    }
                except (TypeError, ValueError):
                    continue
            state.cfg["usage_pricing"] = pricing
            # 单价立即生效（重建 usage 的价格表，不丢历史）
            from spark2.usage import DEFAULT_PRICING

            state.usage.pricing = {**DEFAULT_PRICING, **pricing}
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
        return _config_payload(state)  # 入口已鉴权；不再经 get_config 二次校验

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
            raise HTTPException(
                status_code=400, detail="请先在工作目录设置里指定项目路径"
            )
        meta = state.store.create(
            workdir, str(body.get("model") or state.cfg.get("model") or "")
        )
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
            max_turns=_clamp_int(state.cfg.get("max_turns"), 1, 200, 25),
            tool_timeout=float(
                _clamp_int(state.cfg.get("tool_timeout"), 10, 3600, 180)
            ),
            extra_protected=[str(p) for p in (state.cfg.get("protected_paths") or [])],
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
        state.store.append(sid, {"role": "user", "content": prompt})
        messages = state.store.messages(sid)

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
                            state.store.append(
                                sid, {"role": "assistant", "content": assistant_text}
                            )
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
        expected = (state.cfg.get("token") or "").strip()
        if expected and websocket.query_params.get("token") != expected:
            await websocket.send_text(
                json.dumps({"type": "err", "message": "未授权：访问令牌不正确"})
            )
            await websocket.close(code=4401)
            return
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
        _check_token(request, state)
        body = await request.json()
        sid = str(body.get("sid") or "")
        tab_id = safe_tab_id(str(body.get("tab") or ""))
        state.pty.close(sid, tab_id)
        return {"ok": True}

    # ---------- 记忆 ----------
    @app.get("/api/memory")
    async def list_memory(request: Request) -> dict:
        _check_token(request, state)
        workdir = str(
            request.query_params.get("workdir") or state.cfg.get("workdir") or ""
        )
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

    @app.post("/api/memory")
    async def add_memory(request: Request) -> dict:
        """手动添加/更新一条长期记忆（Web 设置侧入口，与 remember 工具同语义）。"""
        _check_token(request, state)
        body = await _json_body(request)
        workdir = str(body.get("workdir") or state.cfg.get("workdir") or "").strip()
        key = str(body.get("key") or "").strip()
        value = str(body.get("value") or "").strip()
        if not workdir:
            raise HTTPException(status_code=400, detail="未设置工作目录")
        if not key or not value:
            raise HTTPException(status_code=400, detail="key 与 value 均为必填")
        if len(key) > 200 or len(value) > 5000:
            raise HTTPException(
                status_code=400, detail="key/value 超长（200/5000 字符）"
            )
        # 写入走线程池：remember 内部可能触发同步语义嵌入（HTTP/本地模型），
        # 直接执行会阻塞单事件循环。
        await asyncio.to_thread(state.memory.remember, workdir, key, value)
        return {"ok": True, "count": state.memory.count(workdir)}

    # ---------- 文件浏览（@ 引用 / 工作目录导航，只读列表） ----------
    @app.get("/api/fs")
    async def fs_list(request: Request) -> dict:
        _check_token(request, state)
        sid = (request.query_params.get("sid") or "").strip()
        rel = (request.query_params.get("path") or "").strip()
        workdir = _session_workdir(state, sid)
        if not workdir:
            raise HTTPException(status_code=400, detail="未设置工作目录")
        base = Path(workdir).resolve()
        try:
            target = (base / rel).resolve() if rel else base
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"路径无效：{exc}")
        if target != base and base not in target.parents:
            raise HTTPException(status_code=400, detail="工作目录之外的路径")
        if not target.exists() or not target.is_dir():
            raise HTTPException(status_code=404, detail="目录不存在")
        skip = {"node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
        entries: list[dict] = []
        try:
            for e in sorted(
                target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            ):
                if e.name in skip:
                    continue
                if e.name.startswith(".") and rel == "":
                    continue  # 顶层隐藏项（.git 等）不列，子目录允许
                entries.append({"name": e.name, "dir": e.is_dir()})
                if len(entries) >= 300:
                    break
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"目录读取失败：{exc}")
        rel_out = str(target.relative_to(base)) if target != base else ""
        return {"workdir": str(base), "path": rel_out, "entries": entries}

    # ---------- git 检查点（UI 入口：状态 / 手动存档 / 回滚） ----------
    @app.get("/api/git")
    async def git_info(request: Request) -> dict:
        _check_token(request, state)
        sid = (request.query_params.get("sid") or "").strip()
        workdir = _session_workdir(state, sid)
        if not workdir:
            raise HTTPException(status_code=400, detail="未设置工作目录")
        from spark2.tools.git import _run_git, git_available, is_git_repo

        wd = Path(workdir)
        if not git_available():
            return {"repo": False, "reason": "git 未安装"}
        if not is_git_repo(wd):
            return {"repo": False, "reason": "工作目录不是 git 仓库"}
        _, status = await _run_git(wd, "status", "--porcelain")
        _, branch = await _run_git(wd, "rev-parse", "--abbrev-ref", "HEAD")
        _, log = await _run_git(
            wd,
            "log",
            "--pretty=format:%h|%ad|%s",
            "--date=format:%Y-%m-%d %H:%M",
            "-15",
        )
        checkpoints = []
        for line in log.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                checkpoints.append(
                    {"hash": parts[0], "time": parts[1], "message": parts[2]}
                )
        changes = [ln for ln in status.splitlines() if ln.strip()]
        return {
            "repo": True,
            "branch": branch.strip(),
            "changes": len(changes),
            "checkpoints": checkpoints,
        }

    @app.post("/api/git/checkpoint")
    async def git_checkpoint_ep(request: Request) -> dict:
        _check_token(request, state)
        body = await _json_body(request)
        sid = str(body.get("sid") or "")
        message = str(body.get("message") or "").strip()[:120]
        message = message or "spark2 手动存档（Web）"
        workdir = _session_workdir(state, sid)
        if not workdir:
            raise HTTPException(status_code=400, detail="未设置工作目录")
        from spark2.tools.git import git_commit

        ok, text = await git_commit(Path(workdir), message)
        if not ok:
            raise HTTPException(status_code=400, detail=text)
        return {"ok": True, "message": text}

    @app.post("/api/git/reset")
    async def git_reset_ep(request: Request) -> dict:
        """回滚到最近检查点（破坏性操作，必须 confirm=yes）。"""
        _check_token(request, state)
        body = await _json_body(request)
        if str(body.get("confirm") or "") != "yes":
            raise HTTPException(
                status_code=400, detail="破坏性操作：需 confirm=yes 确认"
            )
        sid = str(body.get("sid") or "")
        workdir = _session_workdir(state, sid)
        if not workdir:
            raise HTTPException(status_code=400, detail="未设置工作目录")
        from spark2.tools.git import git_reset

        ok, text = await git_reset(Path(workdir))
        if not ok:
            raise HTTPException(status_code=400, detail=text)
        return {"ok": True, "message": text}

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
