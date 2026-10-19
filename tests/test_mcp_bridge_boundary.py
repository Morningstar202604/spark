"""MCP bridge boundary and error-handling tests.

Covers edge cases for the official SDK-based MCP bridge:

* Bad namespaced tool names
* Unknown server in tool call
* Empty / None content handling
* Connection state tracking (not connected)
* Server enumeration with missing names
* Tool schema validation
* Error propagation
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from spark.config import McpServerConfig
from spark.tools.mcp_bridge import (
    McpBridge,
    ToolResult,
    _content_to_payload,
)


def _run(coro):
    """Run coroutine safely in test context."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result(timeout=30)


# Helper: create a simple object with given attributes
class _Obj:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# _content_to_payload — pure function, easy boundary tests
# ---------------------------------------------------------------------------
class TestContentToPayload:
    def test_none_content(self):
        result = _content_to_payload(None)
        assert result == {"result": None}

    def test_dict_content_passthrough(self):
        input_data = {"key": "value", "nested": {"a": 1}}
        result = _content_to_payload(input_data)
        assert result is input_data

    def test_list_of_text_blocks(self):
        block1 = _Obj(text="Hello")
        block2 = _Obj(text=" World")
        result = _content_to_payload([block1, block2])
        # _content_to_payload joins texts with "\n"
        assert result["text"] == "Hello\n World"
        assert len(result["content"]) == 2

    def test_list_with_data_blocks(self):
        block = _Obj(data=b"binary data")
        result = _content_to_payload([block])
        assert "binary data" in result["content"][0]

    def test_list_with_unknown_blocks(self):
        # Plain object without .text or .data → str(block) fallback
        result = _content_to_payload([object()])
        assert "content" in result
        assert len(result["content"]) == 1

    def test_scalar_string(self):
        result = _content_to_payload("plain text")
        assert result == {"result": "plain text"}

    def test_scalar_number(self):
        result = _content_to_payload(42)
        assert result == {"result": "42"}

    def test_empty_list(self):
        result = _content_to_payload([])
        assert result["text"] == ""
        assert result["content"] == []


# ---------------------------------------------------------------------------
# McpBridge — call routing
# ---------------------------------------------------------------------------
class TestMcpBridgeCall:
    def test_bad_namespaced_name_too_few_parts(self):
        bridge = McpBridge()
        result = _run(bridge.call("mcp__only_two_parts", {}))
        assert result.ok is False
        assert "Bad MCP tool name" in result.payload["error"]

    def test_unknown_server(self):
        bridge = McpBridge()
        result = _run(bridge.call("mcp__nonexistent__tool", {}))
        assert result.ok is False
        assert "not available" in result.payload["error"]

    def test_unknown_tool_on_known_server(self):
        """Server exists but tool call returns error."""
        bridge = McpBridge()
        mock_conn = McpServer.mock_conn("test")
        bridge.servers["test"] = mock_conn

        result = _run(bridge.call("mcp__test__some_tool", {}))
        assert result.ok is False


class McpServer:
    """Helper to create mock MCP connections."""

    @staticmethod
    def mock_conn(name: str) -> MagicMock:
        conn = MagicMock()
        conn.call = AsyncMock(
            return_value=ToolResult(ok=False, payload={"error": "tool not found"})
        )
        conn.cfg = McpServerConfig(name=name, command="echo")
        conn.tools = []
        return conn


# ---------------------------------------------------------------------------
# McpBridge — tool registration
# ---------------------------------------------------------------------------
class TestMcpBridgeRegistration:
    def test_register_empty_servers(self):
        bridge = McpBridge()
        registry = MagicMock()
        bridge.register_into(registry)
        registry.register_scope.assert_not_called()

    def test_register_server_with_no_tools(self):
        bridge = McpBridge()
        mock_conn = MagicMock()
        mock_conn.tools = []
        mock_conn.cfg = McpServerConfig(name="empty_srv", command="echo")
        bridge.servers["empty_srv"] = mock_conn
        registry = MagicMock()
        bridge.register_into(registry)
        registry.register_scope.assert_not_called()

    def test_register_skips_tools_without_name(self):
        bridge = McpBridge()
        mock_conn = MagicMock()
        mock_conn.tools = [
            {"description": "no name", "inputSchema": {}},
        ]
        mock_conn.cfg = McpServerConfig(name="srv", command="echo")
        bridge.servers["srv"] = mock_conn
        registry = MagicMock()
        bridge.register_into(registry)
        # P2 重构：注册机制收敛为 register_scope（动态重注册复用同一入口）；
        # 无名工具回退 "tool" 的语义保持不变。
        assert registry.register_scope.called

    def test_register_with_readonly_tools(self):
        bridge = McpBridge()
        mock_conn = MagicMock()
        mock_conn.tools = [
            {"name": "read_tool", "description": "R", "inputSchema": {}},
        ]
        mock_conn.cfg = McpServerConfig(
            name="srv", command="echo", readonly_tools=["read_tool"]
        )
        bridge.servers["srv"] = mock_conn
        registry = MagicMock()
        registry.readonly_mcp = set()
        bridge.register_into(registry)
        assert "mcp__srv__read_tool" in registry.readonly_mcp


# ---------------------------------------------------------------------------
# McpBridge — lifecycle
# ---------------------------------------------------------------------------
class TestMcpBridgeLifecycle:
    def test_close_empty_bridge(self):
        bridge = McpBridge()
        _run(bridge.close())
        assert len(bridge.servers) == 0

    def test_close_clears_servers(self):
        bridge = McpBridge()
        mock_conn = MagicMock()
        mock_conn.close = AsyncMock()
        mock_conn.cfg = McpServerConfig(name="test", command="echo")
        mock_conn.tools = []
        bridge.servers["test"] = mock_conn

        _run(bridge.close())
        assert mock_conn.close.called
        assert len(bridge.servers) == 0

    def test_errors_populated_on_start_failure(self):
        """When a server fails to start, error should be in errors list."""
        bridge = McpBridge()
        configs = [McpServerConfig(name="bad", command="nonexistent-binary-xyz")]
        registry = MagicMock()

        with patch(
            "spark.tools.mcp_bridge._McpConnection.start",
            side_effect=RuntimeError("mock fail"),
        ):
            _run(bridge.start(configs, registry))

        assert len(bridge.errors) == 1
        assert "mock fail" in bridge.errors[0]


# ---------------------------------------------------------------------------
# ToolResult creation
# ---------------------------------------------------------------------------
class TestToolResult:
    def test_ok_result(self):
        tr = ToolResult(ok=True, payload={"result": "42"})
        assert tr.ok is True

    def test_error_result(self):
        tr = ToolResult(ok=False, payload={"error": "not found"})
        assert tr.ok is False

    def test_ok_with_empty_payload(self):
        tr = ToolResult(ok=True, payload={})
        assert tr.ok is True
        assert tr.payload == {}
