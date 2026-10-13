"""P1-9 安全闭环补全 — 第三方 MCP server 最小权限白名单。

覆盖：

* ``McpServerConfig.allowed_tools`` 配置模型（默认空 = 向后兼容）
* ``policy.mcp_tool_allowed`` 判定逻辑（glob、"*"、默认拒绝未声明项）
* bridge 加载路径强制：非空白名单下，越权工具不注册、记入 errors
* bridge 调用路径复核：越权调用被拒并给出明确错误
* 向后兼容：未声明 ``allowed_tools`` 的 server 行为不变
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from spark.config import McpServerConfig
from spark.policy import mcp_tool_allowed
from spark.tools.mcp_bridge import McpBridge, ToolResult


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result(timeout=30)


def _tool(name: str) -> dict:
    return {"name": name, "description": "", "inputSchema": {}}


class FakeConn:
    """测试替身：替代真实 SDK 连接，公告给定工具列表。"""

    def __init__(self, cfg: McpServerConfig, tools: list[dict]) -> None:
        self.cfg = cfg
        self.tools = tools

    async def start(self) -> None:  # 真实逻辑在 bridge 里过滤 self.tools
        return None

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# 配置模型
# ---------------------------------------------------------------------------
class TestWhitelistConfigModel:
    def test_allowed_tools_default_empty(self):
        cfg = McpServerConfig(name="gh", command="npx")
        assert cfg.allowed_tools == []

    def test_allowed_tools_passthrough(self):
        cfg = McpServerConfig(name="gh", command="npx", allowed_tools=["list_*", "get_issue"])
        assert cfg.allowed_tools == ["list_*", "get_issue"]


# ---------------------------------------------------------------------------
# policy.mcp_tool_allowed 判定
# ---------------------------------------------------------------------------
class TestMcpToolAllowed:
    def test_empty_list_allows_everything_back_compat(self):
        # 未声明白名单 = 历史行为：全部放行
        assert mcp_tool_allowed([], "anything") is True
        assert mcp_tool_allowed([], "delete_everything") is True

    def test_star_allows_all(self):
        assert mcp_tool_allowed(["*"], "whatever") is True

    def test_exact_match(self):
        assert mcp_tool_allowed(["list_issues"], "list_issues") is True
        assert mcp_tool_allowed(["list_issues"], "list_other") is False

    def test_glob_segment(self):
        assert mcp_tool_allowed(["list_*"], "list_issues") is True
        # 下划线是字面字符："list_*" 不匹配 "list"
        assert mcp_tool_allowed(["list_*"], "list") is False
        assert mcp_tool_allowed(["list_*"], "create_issue") is False

    def test_default_deny_undeclared(self):
        # 最小权限模式：未声明的工具一律拒绝
        assert mcp_tool_allowed(["list_*", "get_issue"], "delete_repo") is False
        assert mcp_tool_allowed(["list_*"], "admin.reset") is False

    def test_any_pattern_matches(self):
        assert mcp_tool_allowed(["get_issue", "list_*"], "list_prs") is True


# ---------------------------------------------------------------------------
# bridge 加载路径：白名单过滤
# ---------------------------------------------------------------------------
class TestBridgeLoadEnforcement:
    def test_whitelist_filters_tools_at_load(self):
        bridge = McpBridge()
        cfg = McpServerConfig(name="gh", command="npx", allowed_tools=["list_*", "get_issue"])
        tools = [_tool("list_issues"), _tool("get_issue"), _tool("delete_repo")]

        with patch(
            "spark.tools.mcp_bridge._McpConnection",
            new=lambda c: FakeConn(c, tools),
        ):
            registry = MagicMock()
            registry.readonly_mcp = set()
            _run(bridge.start([cfg], registry))

        conn = bridge.servers["gh"]
        names = [t["name"] for t in conn.tools]
        assert names == ["list_issues", "get_issue"]
        # 越权工具被拦截并显式记录
        assert any("delete_repo" in e for e in bridge.errors)
        _run(bridge.close())

    def test_no_whitelist_back_compat_register_all(self):
        bridge = McpBridge()
        cfg = McpServerConfig(name="gh", command="npx")  # allowed_tools 空
        tools = [_tool("list_issues"), _tool("delete_repo")]

        with patch(
            "spark.tools.mcp_bridge._McpConnection",
            new=lambda c: FakeConn(c, tools),
        ):
            registry = MagicMock()
            registry.readonly_mcp = set()
            _run(bridge.start([cfg], registry))

        conn = bridge.servers["gh"]
        names = [t["name"] for t in conn.tools]
        assert "delete_repo" in names  # 历史行为：全部注册
        assert bridge.errors == []
        _run(bridge.close())


# ---------------------------------------------------------------------------
# bridge 调用路径：白名单复核
# ---------------------------------------------------------------------------
class TestBridgeCallEnforcement:
    def test_denied_tool_call_rejected(self):
        bridge = McpBridge()
        mock_conn = MagicMock()
        mock_conn.cfg = McpServerConfig(name="gh", command="npx", allowed_tools=["list_*"])
        mock_conn.call = AsyncMock(return_value=ToolResult(ok=True, payload={}))
        bridge.servers["gh"] = mock_conn

        result = _run(bridge.call("mcp__gh__delete_repo", {}))
        assert result.ok is False
        assert "denied by whitelist" in result.payload["error"]
        assert "allowed_tools" in result.payload["error"]
        mock_conn.call.assert_not_called()  # 越权请求根本到不了 server
        _run(bridge.close())

    def test_allowed_tool_call_passes(self):
        bridge = McpBridge()
        mock_conn = MagicMock()
        mock_conn.cfg = McpServerConfig(name="gh", command="npx", allowed_tools=["list_*"])
        mock_conn.call = AsyncMock(return_value=ToolResult(ok=True, payload={"issues": []}))
        bridge.servers["gh"] = mock_conn

        result = _run(bridge.call("mcp__gh__list_issues", {}))
        assert result.ok is True
        mock_conn.call.assert_awaited_once_with("list_issues", {})
        _run(bridge.close())

    def test_back_compat_empty_whitelist_calls_anything(self):
        bridge = McpBridge()
        mock_conn = MagicMock()
        mock_conn.cfg = McpServerConfig(name="gh", command="npx")  # 无白名单
        mock_conn.call = AsyncMock(return_value=ToolResult(ok=True, payload={}))
        bridge.servers["gh"] = mock_conn

        result = _run(bridge.call("mcp__gh__anything", {}))
        assert result.ok is True
        mock_conn.call.assert_awaited_once()
        _run(bridge.close())
