"""远程 MCP（Streamable HTTP）集成测试：配置解析 + 真实端到端连接。

服务器：tests/fixtures/http_mcp_server.py（FastMCP streamable-http，端口 8931）。
端口冲突或 SDK 不支持时跳过（不阻塞全量测试）。
"""
from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from spark2.tools.mcp import MCP_AVAILABLE, McpManager, McpServer, servers_from_cfg

FIXTURE = Path(__file__).parent / "fixtures" / "http_mcp_server.py"
PORT = 8931
URL = f"http://127.0.0.1:{PORT}/mcp"


def _port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def http_server():
    if not MCP_AVAILABLE or not FIXTURE.exists():
        pytest.skip("MCP SDK 不可用或无 fixture")
    # 若已有人占用 8931 且能正常响应，直接复用（避免端口冲突）
    if _port_open():
        yield "reused"
        return
    proc = subprocess.Popen(
        [sys.executable, str(FIXTURE)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if _port_open():
            break
        time.sleep(0.2)
    else:
        proc.terminate()
        pytest.skip("HTTP MCP 服务器未在 20s 内就绪")
    yield "spawned"
    proc.terminate()
    proc.wait(timeout=5)


def test_servers_from_cfg_http(tmp_path: Path) -> None:
    cfg = {
        "mcp_servers": [
            {
                "name": "远程服务",
                "transport": "http",
                "url": "https://mcp.example.com/mcp",
                "headers": {"Authorization": "Bearer abc"},
            },
            {"name": "本地脚本", "command": "npx", "args": ["-y", "@model/ctx"]},
            {"name": "残缺-无url", "transport": "http", "url": "  "},
        ]
    }
    servers = servers_from_cfg(cfg)
    assert len(servers) == 2  # 残缺项被过滤
    http_srv = servers[0]
    assert http_srv.transport == "http"
    assert http_srv.url == "https://mcp.example.com/mcp"
    assert http_srv.headers["Authorization"] == "Bearer abc"
    stdio_srv = servers[1]
    assert stdio_srv.transport == "stdio" and stdio_srv.command == "npx"
    # 老配置（无 transport 字段）兼容为 stdio
    legacy = servers_from_cfg({"mcp_servers": [{"name": "a", "command": "cmd"}]})
    assert legacy[0].transport == "stdio"


async def test_http_transport_end_to_end(http_server) -> None:
    if not _port_open():
        pytest.skip("服务器未就绪")
    mgr = McpManager([McpServer(name="demo-http", transport="http", url=URL)])
    await mgr.start()
    try:
        assert mgr.errors == [], mgr.errors
        names = {t.name for t in mgr.tools()}
        assert "mcp_demo_http_add" in names
        assert "mcp_demo_http_echo" in names
        out = await mgr.call("demo-http", "add", {"a": 2, "b": 3})
        assert out == "5"
    finally:
        mgr.close()


async def test_http_transport_parse_and_disabled() -> None:
    # 空 servers → 不启动
    mgr = McpManager([])
    assert not mgr.enabled
    # 无命令/无 url 的服务器不进列表
    mgr2 = McpManager([McpServer(name="x", transport="http", url="")])
    assert len(mgr2.servers) == 0
