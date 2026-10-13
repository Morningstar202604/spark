"""MCP bridge — thin wrapper over the official ``mcp`` Python SDK.

Replaces the hand-rolled JSON-RPC/SSE implementation with the official SDK.  The
SDK provides production-grade transports, protocol negotiation, session
management and reconnection — none of which we need to reinvent.

Supported transports (all official SDK):

* ``stdio`` → :func:`mcp.client.stdio.stdio_client`
* ``sse`` → :func:`mcp.client.sse.sse_client` (legacy SSE servers)
* ``streamable_http`` → :func:`mcp.client.streamable_http.streamable_http_client`
  (modern replacement for SSE; preferred when available)
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any, Coroutine

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from spark.config import McpServerConfig
from spark.models import ToolResult
from spark.policy import mcp_tool_allowed
from spark.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-server connection — one MCP server session
# ---------------------------------------------------------------------------


class _McpConnection:
    """Wraps a single MCP server connection using the official SDK."""

    def __init__(self, cfg: McpServerConfig) -> None:
        self.cfg = cfg
        self.tools: list[dict[str, Any]] = []
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def start(self) -> None:
        """Connect, negotiate protocol version, and enumerate tools."""
        transport: AbstractAsyncContextManager[Any]
        if self.cfg.transport == "stdio":
            params = StdioServerParameters(
                command=self.cfg.command,
                args=self.cfg.args or [],
            )
            transport = stdio_client(params)
        elif self.cfg.http is not None:
            url = self.cfg.http.url
            headers = self.cfg.http.headers or {}
            timeout = self.cfg.http.timeout_sec or 30
            if self.cfg.transport == "sse":
                transport = sse_client(url=url, headers=headers, sse_read_timeout=timeout * 5)
            else:  # streamable_http
                http_client = httpx.AsyncClient(
                    base_url=url,
                    headers=headers,
                    timeout=timeout,
                )
                transport = streamable_http_client(url=url, http_client=http_client)
        else:
            raise RuntimeError(
                f"MCP server {self.cfg.name} requires http transport but none configured"
            )

        read, write = await self._exit_stack.enter_async_context(transport)
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session

        listed = await session.list_tools()
        self.tools = [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema
                if isinstance(t.inputSchema, dict)
                else {"type": "object"},
            }
            for t in listed.tools
        ]
        logger.info("MCP server '%s' connected: %d tool(s)", self.cfg.name, len(self.tools))

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self._session is None:
            return ToolResult(ok=False, payload={"error": f"MCP {self.cfg.name} not connected"})
        try:
            result = await self._session.call_tool(name, arguments)
        except Exception as exc:
            logger.exception("MCP call %s.%s failed", self.cfg.name, name)
            # ExceptionGroup/CancelledError can stringify to nothing, which left
            # the model with an error it could not act on.
            detail = str(exc).strip() or exc.__class__.__name__
            return ToolResult(ok=False, payload={"error": detail})
        is_error = getattr(result, "isError", False)
        payload = _content_to_payload(result.content)
        return ToolResult(ok=not is_error, payload=payload)

    async def close(self) -> None:
        await self._exit_stack.aclose()
        self._session = None
        self.tools = []


def _content_to_payload(content: Any) -> dict[str, Any]:
    """Convert MCP SDK content blocks to a plain dict."""
    if content is None:
        return {"result": None}
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if hasattr(block, "text"):
                texts.append(block.text)
            elif hasattr(block, "data"):
                texts.append(str(block.data))
            else:
                texts.append(str(block))
        return {"content": texts, "text": "\n".join(texts) if texts else ""}
    return {"result": str(content)}


# ---------------------------------------------------------------------------
# Bridge — orchestrates one or more MCP servers
# ---------------------------------------------------------------------------

McpServer = _McpConnection


class McpBridge:
    """Manages multiple MCP server connections via the official SDK.

    Exposes the same public API as the original hand-rolled bridge so callers
    (cli.py, server.py, tui/app.py) do not need extensive changes.
    """

    def __init__(self) -> None:
        self.servers: dict[str, _McpConnection] = {}
        self.errors: list[str] = []
        # The SDK binds stdio streams and session tasks to the loop that opened
        # them, so a bridge created inside a short-lived ``asyncio.run`` becomes
        # uncallable as soon as that loop closes — which is every CLI command and
        # every web turn. Owning a loop keeps sessions alive for the process.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, name="mcp-loop", daemon=True)
        self._loop_thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _on_owner(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Await *coro* on the bridge's loop, no matter which loop calls us."""
        if asyncio.get_running_loop() is self._loop:
            return await coro
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def register_into(self, registry: ToolRegistry) -> None:
        """Register all MCP tools into the registry (read-only annotations)."""
        for name, server in self.servers.items():
            for tool in server.tools:
                raw_name = tool.get("name") or "tool"
                ns = f"mcp__{name}__{raw_name}"
                registry.add_mcp_schema(
                    {
                        "type": "function",
                        "function": {
                            "name": ns,
                            "description": tool.get("description") or ns,
                            "parameters": tool.get("inputSchema")
                            or {"type": "object", "properties": {}},
                        },
                    }
                )
                server_cfg = server.cfg
                if raw_name in server_cfg.readonly_tools:
                    registry.readonly_mcp.add(ns)

    async def start(self, configs: list[McpServerConfig], registry: ToolRegistry) -> None:
        """Connect to all MCP servers in *configs* and register their tools."""
        await self._on_owner(self._start_on_owner(configs, registry))

    async def _start_on_owner(self, configs: list[McpServerConfig], registry: ToolRegistry) -> None:
        for cfg in configs:
            conn = _McpConnection(cfg)
            try:
                await asyncio.wait_for(conn.start(), timeout=15)
            except Exception as exc:
                logger.warning("MCP server '%s' failed to start: %s", cfg.name, exc)
                self.errors.append(f"MCP {cfg.name} failed: {exc}")
                await conn.close()
                continue
            # P1-9: 最小权限白名单（加载路径强制）。
            # allowed_tools 非空 = 该 server 进入最小权限模式：公告的工具里
            # 未声明的一律不注册（默认拒绝），并记录被拦截的工具名，
            # 便于用户及时发现 server 新增了未授权能力。
            if cfg.allowed_tools:
                kept: list[dict[str, Any]] = []
                dropped: list[str] = []
                for tool in conn.tools:
                    tname = tool.get("name") or ""
                    if mcp_tool_allowed(cfg.allowed_tools, tname):
                        kept.append(tool)
                    else:
                        dropped.append(tname)
                if dropped:
                    logger.warning(
                        "MCP server '%s' blocked %d tool(s) outside whitelist %s: %s",
                        cfg.name,
                        len(dropped),
                        cfg.allowed_tools,
                        ", ".join(dropped),
                    )
                    self.errors.append(
                        f"MCP {cfg.name} blocked tools outside whitelist: {', '.join(dropped)}"
                    )
                conn.tools = kept
            self.servers[cfg.name] = conn
        self.register_into(registry)

    async def call(self, namespaced: str, arguments: dict[str, Any]) -> ToolResult:
        """Call a namespaced MCP tool: ``mcp__<server>__<tool>``."""
        return await self._on_owner(self._call_on_owner(namespaced, arguments))

    async def _call_on_owner(self, namespaced: str, arguments: dict[str, Any]) -> ToolResult:
        parts = namespaced.split("__", 2)
        if len(parts) != 3:
            return ToolResult(ok=False, payload={"error": f"Bad MCP tool name: {namespaced}"})
        _, server_name, tool_name = parts
        server = self.servers.get(server_name)
        if server is None:
            return ToolResult(
                ok=False,
                payload={"error": f"MCP server not available: {server_name}"},
            )
        # P1-9: 调用路径复核白名单（防御纵深）。注册时已按 allowed_tools
        # 过滤，此处再兜底一次：即使 registry 里出现了越权工具，也到不了
        # server。错误信息显式给出白名单内容，便于用户排障。
        if not mcp_tool_allowed(server.cfg.allowed_tools, tool_name):
            return ToolResult(
                ok=False,
                payload={
                    "error": (
                        f"MCP tool denied by whitelist: mcp__{server_name}__{tool_name}"
                        f" (allowed_tools={server.cfg.allowed_tools})"
                    )
                },
            )
        return await server.call(tool_name, arguments)

    async def close(self) -> None:
        """Shut down all MCP connections and the bridge's own loop."""
        await self._on_owner(self._close_on_owner())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        if not self._loop.is_closed():
            self._loop.close()

    async def _close_on_owner(self) -> None:
        for server in self.servers.values():
            try:
                await server.close()
            except Exception as exc:
                logger.warning("Error closing MCP server '%s': %s", server.cfg.name, exc)
        self.servers.clear()
