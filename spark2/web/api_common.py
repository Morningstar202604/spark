"""Web API 共享基础：AppState 与鉴权/辅助工具（server.py 拆分）。

各 api_* 路由模块与 server.py 共用；AppState 在此定义以避免
server.py ↔ 路由模块的循环导入。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from spark2 import __version__
from spark2.approval import ApprovalGate
from spark2.config import (
    APPROVAL_MODES,
    PRESETS,
    config_dir,
    config_file,
    load_config,
    mask_key,
)
from spark2.loop import AgentLoop
from spark2.memory import MemoryStore, make_embedder
from spark2.plugins import collect_plugin_tools, load_plugins, plugins_dir
from spark2.pty import PtyManager
from spark2.store import SessionStore
from spark2.tools.mcp import McpManager, servers_from_cfg
from spark2.usage import UsageStore


class AppState:
    """进程级共享状态：配置 / 会话 / 记忆 / MCP / 终端 / 运行中的 AgentLoop。"""

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


def get_app_state(request: Request) -> AppState:
    """FastAPI 依赖：取 create_app 挂载的 AppState（路由模块用 Depends 注入）。"""
    return request.app.state.spark


def check_token(request: Request, state: AppState) -> None:
    """令牌鉴权；未设置令牌 = 本机免登录（服务只绑 127.0.0.1）。"""
    expected = (state.cfg.get("token") or "").strip()
    if not expected:
        return
    token = request.headers.get("x-spark-token") or request.query_params.get("token")
    if token != expected:
        raise HTTPException(status_code=401, detail="未授权：访问令牌不正确")


def clamp_int(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def session_workdir(state: AppState, sid: str) -> str:
    """会话工作目录：优先会话元数据，兜底全局配置。"""
    if sid:
        meta = state.store.meta(sid)
        if meta and meta.get("workdir"):
            return str(meta["workdir"])
    return str(state.cfg.get("workdir") or "")


async def json_body(request: Request) -> dict:
    """解析 JSON 请求体；非法 JSON/非对象 → 400（不落 500）。"""
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="请求体需是合法 JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体需是 JSON 对象")
    return body


def config_payload(state: AppState) -> dict:
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
            {"value": m, "label": mode_label(m)} for m in APPROVAL_MODES
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


def mode_label(mode: str) -> str:
    return {
        "suggest": "询问（写入与命令都要确认）",
        "auto-edit": "自动编辑（工作区内写入不询问，命令询问）",
        "full-auto": "全自动（都不询问，谨慎使用）",
    }.get(mode, mode)


def sse(data: dict) -> str:
    """SSE 帧序列化：`data: <json>\n\n`。"""
    import json

    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
