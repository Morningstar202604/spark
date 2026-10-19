"""P2 MCP 全能力：sampling 反向补全 / elicitation 策略 / 动态重注册 / templates。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from spark.config import McpServerConfig
from spark.tools.mcp_bridge import McpBridge


class _FakeProvider:
    """Provider-like：stream 返回固定文本，记录调用。"""

    def __init__(self) -> None:
        self.calls: list[list] = []

    async def stream(self, messages, tools, effort=None):
        self.calls.append(list(messages))
        from spark.models import ChatDelta

        yield ChatDelta(type="text", text="sampled-reply")
        yield ChatDelta(type="end")


def test_sampling_callback_routes_to_provider() -> None:
    bridge = McpBridge()
    provider = _FakeProvider()
    bridge.set_sampling_handler(provider)

    params = SimpleNamespace(
        messages=[
            SimpleNamespace(role="user", content=SimpleNamespace(text="summarize")),
            SimpleNamespace(role="assistant", content=SimpleNamespace(text="part")),
        ],
        systemPrompt="be brief",
        maxTokens=64,
    )
    ctx = SimpleNamespace(params=params)
    result = asyncio.run(bridge._sampling_callback(ctx))
    assert result.stopReason == "endTurn"
    assert result.content.text == "sampled-reply"
    assert provider.calls, "sampling 应到达 provider"
    # system 槽 + 两条消息按序透传
    roles = [m.role for m in provider.calls[0]]
    assert roles == ["system", "user", "assistant"]
    assert provider.calls[0][0].content == "be brief"


def test_sampling_without_handler_returns_error_stop() -> None:
    bridge = McpBridge()
    params = SimpleNamespace(
        messages=[SimpleNamespace(role="user", content=SimpleNamespace(text="hi"))],
        systemPrompt=None,
        maxTokens=32,
    )
    result = asyncio.run(bridge._sampling_callback(SimpleNamespace(params=params)))
    assert result.stopReason == "error"
    assert result.content.text == ""


def test_sampling_provider_failure_degrades_to_error_stop() -> None:
    class Boom:
        async def stream(self, messages, tools, effort=None):
            raise RuntimeError("endpoint down")
            yield  # pragma: no cover

    bridge = McpBridge()
    bridge.set_sampling_handler(Boom())
    params = SimpleNamespace(
        messages=[SimpleNamespace(role="user", content=SimpleNamespace(text="hi"))],
        systemPrompt=None,
        maxTokens=8,
    )
    result = asyncio.run(bridge._sampling_callback(SimpleNamespace(params=params)))
    assert result.stopReason == "error"


def test_elicitation_always_declines() -> None:
    bridge = McpBridge()
    result = asyncio.run(
        bridge._elicitation_callback(SimpleNamespace(params=SimpleNamespace(message="give key")))
    )
    assert result.action == "decline"


def test_list_changed_re_registers_with_whitelist() -> None:
    """tools/list_changed → 重列 + 白名单强制下重注册。"""
    from pathlib import Path

    from spark.config import SparkConfig
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    registry = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(Path(".")), config=cfg))
    bridge = McpBridge()
    bridge._registry = registry
    server_cfg = McpServerConfig(
        name="srv", command="noop", allowed_tools=["public_*"]
    )
    server = SimpleNamespace(
        cfg=server_cfg,
        tools=[
            {"name": "public_one", "description": "", "inputSchema": {"type": "object"}},
            {"name": "secret_tool", "description": "", "inputSchema": {"type": "object"}},
        ],
        _session=SimpleNamespace(),
        resource_templates=[],
    )
    bridge.servers["srv"] = server

    # 模拟 server 侧工具列表变化：新增 public_two
    async def fake_list_tools():
        return SimpleNamespace(
            tools=[
                SimpleNamespace(name="public_one", description="", inputSchema={"type": "object"}),
                SimpleNamespace(name="public_two", description="", inputSchema={"type": "object"}),
                SimpleNamespace(name="secret_tool", description="", inputSchema={"type": "object"}),
            ]
        )

    server._session = SimpleNamespace(list_tools=fake_list_tools)
    asyncio.run(bridge._refresh_server_tools("srv"))

    names = {s["function"]["name"] for s in registry.schemas()}
    assert "mcp__srv__public_one" in names
    assert "mcp__srv__public_two" in names
    assert "mcp__srv__secret_tool" not in names  # 白名单仍然强制


def test_templates_aggregated_into_resources_list() -> None:
    from pathlib import Path

    from spark.config import SparkConfig
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    registry = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(Path(".")), config=cfg))
    bridge = McpBridge()
    bridge._registry = registry
    server = SimpleNamespace(
        cfg=McpServerConfig(name="srv", command="noop"),
        tools=[],
    )

    async def fake_list_resources():
        return {"resources": []}

    async def fake_list_templates():
        return [{"uriTemplate": "file:///{path}", "name": "files"}]

    server.list_resources = fake_list_resources
    server.list_resource_templates = fake_list_templates
    bridge.servers["srv"] = server
    out = asyncio.run(bridge.list_resources())
    assert out[0]["templates"][0]["uriTemplate"] == "file:///{path}"
