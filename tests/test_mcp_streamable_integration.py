"""真实进程的 streamable_http MCP 端到端测试。

registry 首页绝大多数条目只有远程 URL（没有本地命令），一键添加后走的正是
``streamable_http``。之前的实现把 ``streamable_http_client`` 的返回值按二元组
解包，导致所有远程 server 启动时抛 "too many values to unpack"——这里用真实
HTTP server 覆盖整条链路，避免再次回归。
"""
from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path

import pytest

from spark.config import McpServerConfig, McpTransportConfig, SparkConfig
from spark.sandbox import WorkdirSandbox
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry

HTTP_SERVER = '''"""Minimal MCP streamable-http server for integration tests."""
import uvicorn
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("http-demo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def marker() -> str:
    """Fixed string proving the call reached this process."""
    return "http-server-reached"


app = mcp.streamable_http_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(__import__("sys").argv[1]), log_level="error")
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.15)
    raise AssertionError(f"server on {port} did not start")


@pytest.fixture()
def http_server(tmp_path: Path):
    import subprocess
    import sys

    script = tmp_path / "http_server.py"
    script.write_text(HTTP_SERVER, encoding="utf-8")
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(script), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_port(port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


@pytest.mark.asyncio
async def test_streamable_http_mcp_server_real_process(http_server: str, tmp_path: Path) -> None:
    """远程 server 要能被发现工具并真正调用。"""
    work = tmp_path / "work"
    work.mkdir()
    cfg = SparkConfig()
    cfg.mcp_servers = [
        McpServerConfig(
            name="remote",
            transport="streamable_http",
            http=McpTransportConfig(url=http_server, timeout_sec=30),
        )
    ]
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
        assert names == {"mcp__remote__add", "mcp__remote__marker"}, names

        marker = await bridge.call("mcp__remote__marker", {})
        assert marker.ok is True
        assert "http-server-reached" in str(marker.payload)

        added = await bridge.call("mcp__remote__add", {"a": 19, "b": 23})
        assert added.ok is True
        assert "42" in str(added.payload)
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_unreachable_remote_server_does_not_abort_start(tmp_path: Path) -> None:
    """连不上的远程 server 只能被记录为错误，不能拖垮宿主启动。"""
    work = tmp_path / "work"
    work.mkdir()
    cfg = SparkConfig()
    cfg.mcp_servers = [
        McpServerConfig(
            name="dead",
            transport="streamable_http",
            http=McpTransportConfig(url=f"http://127.0.0.1:{_free_port()}/mcp", timeout_sec=1),
        )
    ]
    ctx = ToolContext(sandbox=WorkdirSandbox(work, cfg), config=cfg)
    bridge = McpBridge()
    try:
        await asyncio.wait_for(bridge.start(cfg.mcp_servers, ToolRegistry(ctx)), timeout=60)
        assert len(bridge.errors) == 1
        assert "dead" in bridge.errors[0]
    finally:
        await bridge.close()
