"""MCP 接入测试：stdio 服务器启动、工具注册、调用、审批、失败隔离。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from spark2.approval import ApprovalGate
from spark2.loop import AgentLoop
from spark2.tools.mcp import MCP_AVAILABLE, McpManager, McpServer

pytestmark = pytest.mark.skipif(not MCP_AVAILABLE, reason="需要 mcp SDK")

SERVER_SRC = '''\
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
mcp = FastMCP("demo")
@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加（写类，无 annotations → 默认进审批门）"""
    return a + b
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def greet(name: str) -> str:
    """打招呼（只读）"""
    return f"你好，{name}"
mcp.run()
'''


def _write_server(tmp_path: Path) -> Path:
    p = tmp_path / "mcp_demo_server.py"
    p.write_text(SERVER_SRC, encoding="utf-8")
    return p


async def test_manager_starts_and_lists_tools(tmp_path: Path) -> None:
    srv = _write_server(tmp_path)
    mgr = McpManager([McpServer(name="demo", command=sys.executable, args=[str(srv)])])
    assert mgr.enabled
    await mgr.start()
    try:
        assert mgr.errors == []
        names = [t.name for t in mgr.tools()]
        assert "mcp_demo_add" in names and "mcp_demo_greet" in names
        add_tool = next(t for t in mgr.tools() if t.name == "mcp_demo_add")
        assert "MCP:demo" in add_tool.description
        # 2026 MCP 规范（2025-03-26 修订）：readOnlyHint=true 的工具只读放行，其余进审批门
        assert add_tool.category == "write"
        greet_tool = next(t for t in mgr.tools() if t.name == "mcp_demo_greet")
        assert greet_tool.category == "read"
        assert "只读" in greet_tool.description
    finally:
        mgr.close()


async def test_call_tool(tmp_path: Path) -> None:
    srv = _write_server(tmp_path)
    mgr = McpManager([McpServer(name="demo", command=sys.executable, args=[str(srv)])])
    await mgr.start()
    try:
        out = await mgr.call("demo", "add", {"a": 2, "b": 3})
        assert out == "5"
        out2 = await mgr.call("demo", "greet", {"name": "小明"})
        assert "小明" in out2
        # 未就绪的服务器 → 明确报错
        out3 = await mgr.call("nope", "x", {})
        assert "未就绪" in out3
    finally:
        mgr.close()


async def test_bad_server_is_isolated(tmp_path: Path) -> None:
    mgr = McpManager([McpServer(name="bad", command="definitely_not_a_program_xyz")])
    await mgr.start()
    try:
        assert mgr.errors and mgr.tools() == []  # 只记错误，不抛阻断
    finally:
        mgr.close()


async def test_loop_executes_mcp_tool_with_approval(tmp_path: Path) -> None:
    srv = _write_server(tmp_path)
    mgr = McpManager([McpServer(name="demo", command=sys.executable, args=[str(srv)])])
    loop = AgentLoop(
        tmp_path,
        {"model": "mock", "mock_script": [
            [{"type": "tool_calls", "calls": [{"id": "m1", "name": "mcp_demo_add", "arguments": {"a": 10, "b": 5}}]}],
            [{"type": "text", "text": "算好了。"}],
        ]},
        ApprovalGate(mode="suggest"),
        mcp=mgr,
    )
    got: list[dict] = []

    async def run():
        async for ev in loop.stream([{"role": "user", "content": "帮我算 10+5"}]):
            got.append(ev)
            if ev["type"] == "approval":
                loop.gate.respond(ev["request_id"], "allow")

    await run()
    try:
        tr = next(e for e in got if e["type"] == "tool_result")
        assert tr["approved"] is True
        assert tr["output"] == "15"
        assert "mcp_demo_add" in tr["name"]
    finally:
        mgr.close()
