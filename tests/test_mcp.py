from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from spark.config import McpServerConfig, SparkConfig
from spark.models import ToolCall
from spark.sandbox import WorkdirSandbox
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry


FAKE_SERVER = r"""
import json, sys

def read():
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)

def write(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

while True:
    msg = read()
    if msg is None:
        break
    method = msg.get("method")
    req_id = msg.get("id")
    if method == "initialize":
        write({"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        write({"jsonrpc": "2.0", "id": req_id, "result": {"tools": [{"name": "echo", "description": "echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}})
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        write({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": args.get("text", "")}]}})
    else:
        if req_id is not None:
            write({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "unknown"}})
"""


@pytest.mark.asyncio
async def test_mcp_namespaced_and_forwarded(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(FAKE_SERVER)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    cfg.mcp_servers = [McpServerConfig(name="fake", command=sys.executable, args=["-u", str(script)])]
    registry = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg))
    bridge = McpBridge()
    await bridge.start(cfg.mcp_servers, registry)
    registry.ctx.mcp_call = bridge.call
    try:
        names = [s["function"]["name"] for s in registry.schemas() if s["function"]["name"].startswith("mcp__")]
        assert "mcp__fake__echo" in names
        result = await registry.execute(ToolCall(id="1", name="mcp__fake__echo", arguments={"text": "hi"}))
        assert result.ok
        assert "hi" in json.dumps(result.payload)
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_mcp_dead_server_dropped(tmp_path: Path) -> None:
    cfg = SparkConfig()
    registry = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg))
    bridge = McpBridge()
    await bridge.start(
        [McpServerConfig(name="dead", command=sys.executable, args=["-c", "raise SystemExit(1)"])],
        registry,
    )
    assert bridge.errors
    assert "mcp__dead" not in json.dumps(registry.schemas())
    await bridge.close()
