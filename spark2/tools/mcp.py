"""MCP 接入：官方 mcp SDK，stdio / Streamable HTTP 双传输，懒启动，动态注册为普通工具。

原则：
- 不造轮子：直接用官方 mcp 包的 ClientSession / stdio_client / streamable_http_client / FastMCP 兼容服务。
- 2026-07-28 MCP 规范：远程服务器走无状态 Streamable HTTP（删除 GET stream 端点，
  请求带 Mcp-Method / Mcp-Name 头；OAuth 2.1 + PKCE 由 mcp SDK 处理）。本实现通过
  mcp SDK 的 streamable_http_client 接入，支持 url + 可选 headers（Bearer 等）。
- 安全：MCP 工具按 category="write" 走审批门——suggest 模式下每个 MCP 工具调用
  都会经用户确认，auto-edit / full-auto 按档位放行。远程 HTTP 与本地 stdio 同等对待。
- 稳定：服务器进程/连接由常驻后台任务持有；单个服务器初始化失败只记错误、
  不影响主流程（模型看不到它的工具而已）。
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from spark2.tools.base import Tool

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    MCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    MCP_AVAILABLE = False


@dataclass
class McpServer:
    name: str
    command: str = ""
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    transport: str = "stdio"  # "stdio" | "http"
    url: str = ""
    headers: dict = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        if self.transport == "http":
            return bool(self.url.strip())
        return bool(self.command.strip())


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s or "")


def servers_from_cfg(cfg: dict | None) -> list[McpServer]:
    """把配置里的 mcp_servers（list[dict]）转成 McpServer 列表。"""
    out = []
    for item in (cfg or {}).get("mcp_servers") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        transport = str(item.get("transport", "stdio")).strip().lower() or "stdio"
        if transport not in ("stdio", "http"):
            transport = "stdio"
        command = str(item.get("command", "")).strip()
        url = str(item.get("url", "")).strip()
        if transport == "http":
            if not url:
                continue
        elif not command:
            continue
        out.append(
            McpServer(
                name=name,
                command=command,
                args=[str(a) for a in item.get("args") or []],
                env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
                transport=transport,
                url=url,
                headers={str(k): str(v) for k, v in (item.get("headers") or {}).items()},
            )
        )
    return out


class McpManager:
    """管理配置中的 MCP 服务器：懒启动 + 动态工具注册。"""

    def __init__(self, servers: list[McpServer] | None = None) -> None:
        self.servers = [s for s in (servers or []) if s.enabled]
        self._tools: list[Tool] = []
        self._sessions: dict[str, ClientSession] = {}
        self._tasks: list[asyncio.Task] = []
        self._stops: dict[str, asyncio.Event] = {}
        self._started = False
        self._start_lock = asyncio.Lock()
        self.errors: list[str] = []

    @property
    def available(self) -> bool:
        return MCP_AVAILABLE

    @property
    def enabled(self) -> bool:
        return MCP_AVAILABLE and bool(self.servers)

    async def start(self) -> None:
        """懒启动：只启动一次；单个服务器失败只记错误不阻断。"""
        if self._started or not self.enabled:
            return
        async with self._start_lock:
            if self._started:
                return
            for s in self.servers:
                try:
                    await self._start_one(s)
                except Exception as e:  # noqa: BLE001
                    self.errors.append(f"{s.name}: {e}")
            self._started = True

    async def _start_one(self, server: McpServer) -> None:
        holder: dict[str, Any] = {"session": None, "tools": [], "error": None}
        stop = asyncio.Event()

        async def run() -> None:
            try:
                if server.transport == "http":
                    # 2026-07-28 无状态 Streamable HTTP：SDK 自带 OAuth 2.1+PKCE 流程；
                    # 额外 headers（Bearer/自定义鉴权）经 httpx.AsyncClient 传入
                    from httpx import AsyncClient

                    http_client: Any = None
                    if server.headers:
                        http_client = AsyncClient(
                            headers={str(k): str(v) for k, v in server.headers.items()},
                            timeout=30.0,
                        )
                    try:
                        async with streamable_http_client(server.url, http_client=http_client) as (r, w, _sess):
                            async with ClientSession(r, w) as session:
                                await session.initialize()
                                result = await session.list_tools()
                                holder["session"] = session
                                holder["tools"] = list(result.tools)
                                await stop.wait()
                    finally:
                        if http_client is not None:
                            await http_client.aclose()
                else:
                    params = StdioServerParameters(
                        command=server.command, args=list(server.args), env=server.env or None
                    )
                    async with stdio_client(params) as (r, w):
                        async with ClientSession(r, w) as session:
                            await session.initialize()
                            result = await session.list_tools()
                            holder["session"] = session
                            holder["tools"] = list(result.tools)
                            await stop.wait()
            except Exception as e:  # noqa: BLE001
                holder["error"] = f"{type(e).__name__}: {e}"
            finally:
                stop.set()

        task = asyncio.create_task(run())
        self._tasks.append(task)
        self._stops[server.name] = stop

        # 等待初始化完成（最多 10s）
        for _ in range(500):
            await asyncio.sleep(0.02)
            if holder["tools"] or holder["error"]:
                break
        if holder["error"]:
            raise RuntimeError(f"服务器 {server.name} 启动失败：{holder['error']}")
        if not holder["tools"]:
            raise RuntimeError(f"服务器 {server.name} 初始化超时")
        self._sessions[server.name] = holder["session"]
        for t in holder["tools"]:
            self._tools.append(self._make_tool(server.name, t))

    def _make_tool(self, server_name: str, t: Any) -> Tool:
        tool_name = f"mcp_{_safe_name(server_name)}_{_safe_name(t.name)}"
        schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
        # 2025-03-26 MCP 规范的 Tool Annotations：readOnlyHint=true 的工具只读、
        # 自动放行；其余默认视为"写"，进审批门（与主流 agent 的 writes 模式一致）。
        ann = getattr(t, "annotations", None) or {}
        readonly = bool(getattr(ann, "readOnlyHint", None))
        desc = f"[MCP:{server_name}] {getattr(t, 'description', '') or t.name}"
        if readonly:
            desc += "（只读）"
        return Tool(
            name=tool_name,
            description=desc,
            parameters=schema,
            category="read" if readonly else "write",
            handler=lambda args, ctx, _s=server_name, _t=t.name: self.call(_s, _t, args),
        )

    async def call(self, server_name: str, tool_name: str, args: dict) -> str:
        session = self._sessions.get(server_name)
        if session is None:
            return f"错误：MCP 服务器 {server_name} 未就绪"
        try:
            result = await session.call_tool(tool_name, args or {})
        except Exception as e:  # noqa: BLE001
            return f"错误：调用 MCP 工具 {tool_name} 失败：{e}"
        parts = []
        for c in (result.content or []):
            txt = getattr(c, "text", None)
            if txt:
                parts.append(txt)
        text = "\n".join(parts)
        if result.isError:
            return f"错误：{text or '无详情'}"
        return text or "（无返回内容）"

    def tools(self) -> list[Tool]:
        return list(self._tools)

    def close(self) -> None:
        """结束所有服务器连接（供 CLI 等一次性场景使用）。"""
        for stop in self._stops.values():
            stop.set()
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        self._sessions.clear()
        self._tools.clear()
