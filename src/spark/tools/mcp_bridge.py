from __future__ import annotations

import asyncio
import json
from typing import Any

from spark.config import McpServerConfig
from spark.models import ToolResult
from spark.tools.registry import ToolRegistry


class McpServerProcess:
    def __init__(self, cfg: McpServerConfig) -> None:
        self.cfg = cfg
        self.proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self.tools: list[dict[str, Any]] = []

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            self.cfg.command,
            *self.cfg.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "spark", "version": "0.1.0"}})
        await self._notify("notifications/initialized", {})
        listed = await self._rpc("tools/list", {})
        self.tools = listed.get("tools") or []

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2)
            except TimeoutError:
                self.proc.kill()

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        except Exception as exc:
            return ToolResult(ok=False, payload={"error": str(exc)})
        is_error = bool(result.get("isError"))
        return ToolResult(ok=not is_error, payload=result)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._id += 1
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._write(msg)

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError(f"MCP server {self.cfg.name} is not running")
        self._id += 1
        req_id = self._id
        await self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        while True:
            raw = await asyncio.wait_for(self.proc.stdout.readline(), timeout=30)
            if not raw:
                raise RuntimeError(f"MCP server {self.cfg.name} closed")
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            payload = json.loads(line)
            if payload.get("id") != req_id:
                continue
            if "error" in payload:
                raise RuntimeError(str(payload["error"]))
            return payload.get("result") or {}

    async def _write(self, msg: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError(f"MCP server {self.cfg.name} is not running")
        data = json.dumps(msg).encode("utf-8") + b"\n"
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()


class McpBridge:
    def __init__(self) -> None:
        self.servers: dict[str, McpServerProcess] = {}
        self.errors: list[str] = []

    def register_into(self, registry: ToolRegistry) -> None:
        for name, proc in self.servers.items():
            for tool in proc.tools:
                raw_name = tool.get("name") or "tool"
                ns = f"mcp__{name}__{raw_name}"
                registry.add_mcp_schema(
                    {
                        "type": "function",
                        "function": {
                            "name": ns,
                            "description": tool.get("description") or ns,
                            "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                        },
                    }
                )
                server_cfg = proc.cfg
                if raw_name in server_cfg.readonly_tools:
                    registry.readonly_mcp.add(ns)

    async def start(self, configs: list[McpServerConfig], registry: ToolRegistry) -> None:
        for cfg in configs:
            proc = McpServerProcess(cfg)
            try:
                await proc.start()
            except Exception as exc:
                detail = str(exc)
                if proc.proc and proc.proc.stderr:
                    try:
                        err = await asyncio.wait_for(proc.proc.stderr.read(), timeout=0.5)
                        if err:
                            detail = f"{detail}: {err.decode('utf-8', errors='replace')[:200]}"
                    except Exception:
                        pass
                self.errors.append(f"MCP {cfg.name} failed: {detail}")
                await proc.close()
                continue
            self.servers[cfg.name] = proc
            for tool in proc.tools:
                raw_name = tool.get("name") or "tool"
                ns = f"mcp__{cfg.name}__{raw_name}"
                registry.add_mcp_schema(
                    {
                        "type": "function",
                        "function": {
                            "name": ns,
                            "description": tool.get("description") or ns,
                            "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                        },
                    }
                )
                if raw_name in cfg.readonly_tools:
                    registry.readonly_mcp.add(ns)

    async def call(self, namespaced: str, arguments: dict[str, Any]) -> ToolResult:
        parts = namespaced.split("__", 2)
        if len(parts) != 3:
            return ToolResult(ok=False, payload={"error": f"Bad MCP tool name: {namespaced}"})
        server = self.servers.get(parts[1])
        if server is None:
            return ToolResult(ok=False, payload={"error": f"MCP server not available: {parts[1]}"})
        return await server.call(parts[2], arguments)

    async def close(self) -> None:
        for server in self.servers.values():
            await server.close()
