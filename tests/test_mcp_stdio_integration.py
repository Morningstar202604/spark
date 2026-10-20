"""Integration tests that spawn REAL processes instead of faking the transport.

The rest of the suite mocks the model, which is correct for unit tests but says
nothing about whether the tool layer can actually drive an external system.
These cases start a genuine MCP server over stdio and call it, so a regression in
process spawn / protocol handshake / result decoding fails here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from spark.config import McpServerConfig, SparkConfig
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry

pytestmark = pytest.mark.skipif(
    os.name == "nt" and False, reason="stdio MCP works on Windows too"
)


def _server_script(tmp_path: Path) -> Path:
    script = tmp_path / "stdio_server.py"
    script.write_text(
        '''"""Minimal MCP stdio server for integration tests."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stdio-demo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def marker() -> str:
    """Fixed string proving the call reached this process."""
    return "stdio-server-reached"


if __name__ == "__main__":
    mcp.run(transport="stdio")
''',
        encoding="utf-8",
    )
    return script


@pytest.mark.asyncio
async def test_stdio_mcp_server_real_process(tmp_path: Path) -> None:
    """Spawn a real MCP server, discover its tools, and call them."""
    script = _server_script(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    cfg = SparkConfig()
    cfg.mcp_servers = [
        McpServerConfig(
            name="demo",
            command=sys.executable,
            args=[str(script)],
            transport="stdio",
        )
    ]
    store = SessionStore(":memory:")
    store.create_session(work, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(work, cfg), config=cfg)
    registry = ToolRegistry(ctx)
    bridge = McpBridge()

    try:
        await bridge.start(cfg.mcp_servers, registry)
        assert bridge.errors == [], bridge.errors

        names = {
            schema["function"]["name"]
            for schema in registry.schemas()
            if schema["function"]["name"].startswith("mcp__")
        }
        assert names == {"mcp__demo__add", "mcp__demo__marker"}, names

        marker = await bridge.call("mcp__demo__marker", {})
        assert marker.ok is True
        assert "stdio-server-reached" in str(marker.payload)

        added = await bridge.call("mcp__demo__add", {"a": 19, "b": 23})
        assert added.ok is True
        assert "42" in str(added.payload)
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_mcp_results_are_fenced_as_untrusted(tmp_path: Path) -> None:
    """External tool output must be wrapped so it cannot act as instructions."""
    from spark.models import ToolCall

    script = _server_script(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    cfg = SparkConfig()
    cfg.mcp_servers = [
        McpServerConfig(name="demo", command=sys.executable, args=[str(script)], transport="stdio")
    ]
    store = SessionStore(":memory:")
    store.create_session(work, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(work, cfg), config=cfg)
    registry = ToolRegistry(ctx)
    bridge = McpBridge()
    try:
        await bridge.start(cfg.mcp_servers, registry)
        ctx.mcp_call = bridge.call
        result = await registry.execute(ToolCall(id="t1", name="mcp__demo__marker", arguments={}))
        assert result.ok is True
        text = str(result.payload)
        assert "<untrusted>" in text and "</untrusted>" in text, text
    finally:
        await bridge.close()
