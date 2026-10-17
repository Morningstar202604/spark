"""P2：MCP resources / prompts 客户端能力（假 stdio server 端到端）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from spark.config import McpServerConfig, SparkConfig
from spark.sandbox import WorkdirSandbox
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry

FAKE = '''import json, sys
def read():
    line = sys.stdin.readline()
    if not line: return None
    return json.loads(line)
def write(o): sys.stdout.write(json.dumps(o)+"\\n"); sys.stdout.flush()
while True:
    msg = read()
    if msg is None: break
    method, req_id = msg.get("method"), msg.get("id")
    if method == "initialize":
        write({"jsonrpc":"2.0","id":req_id,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{},"resources":{},"prompts":{}},"serverInfo":{"name":"fake","version":"0.0.0"}}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        write({"jsonrpc":"2.0","id":req_id,"result":{"tools":[]}})
    elif method == "resources/list":
        write({"jsonrpc":"2.0","id":req_id,"result":{"resources":[{"uri":"file:///x.txt","name":"x","mimeType":"text/plain"}]}})
    elif method == "resources/read":
        uri = (msg.get("params") or {}).get("uri","")
        write({"jsonrpc":"2.0","id":req_id,"result":{"contents":[{"uri":uri,"mimeType":"text/plain","text":"RES-DATA"}]}})
    elif method == "prompts/list":
        write({"jsonrpc":"2.0","id":req_id,"result":{"prompts":[{"name":"hi","description":"say hi","arguments":[]}]}})
    elif method == "prompts/get":
        write({"jsonrpc":"2.0","id":req_id,"result":{"messages":[{"role":"user","content":{"type":"text","text":"PROMPT-TEXT"}}]}})
    elif req_id is not None:
        write({"jsonrpc":"2.0","id":req_id,"error":{"code":-32601,"message":"unknown"}})
'''


@pytest.mark.asyncio
async def test_mcp_resources_and_prompts(tmp_path: Path) -> None:
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE)
    cfg = SparkConfig()
    cfg.mcp_servers = [
        McpServerConfig(name="fake", command=sys.executable, args=["-u", str(script)])
    ]
    bridge = McpBridge()
    registry = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg))
    await bridge.start(cfg.mcp_servers, registry)
    registry.ctx.mcp_bridge = bridge
    try:
        names = [s["function"]["name"] for s in registry.schemas()]
        assert "mcp_resources" in names and "mcp_prompts" in names

        listed = await bridge.list_resources()
        assert listed[0]["server"] == "fake"
        assert listed[0]["resources"][0]["uri"] == "file:///x.txt"

        read = await bridge.read_resource("fake", "file:///x.txt")
        assert read.ok and read.payload["contents"][0]["text"] == "RES-DATA"

        prompts = await bridge.list_prompts()
        assert prompts[0]["prompts"][0]["name"] == "hi"

        got = await bridge.get_prompt("fake", "hi", {})
        assert got.ok and got.payload["messages"][0]["text"] == "PROMPT-TEXT"

        # 不存在的 server 优雅报错
        bad = await bridge.read_resource("nope", "file:///x")
        assert not bad.ok
    finally:
        await bridge.close()
