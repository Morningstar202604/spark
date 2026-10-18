"""LangGraph 生产控制流（core/graph.py）的真实覆盖。

历史教训：本文件曾用 ``loop._use_graph = True`` 伪装成"graph 路径"测试——
该属性在 ``AgentLoop`` 上从未存在，4 个用例实际全部执行经典 while 循环，
制造了"LangGraph 路径已有测试"的假象。

0.9.8 起 while 循环已删除：``AgentLoop.iter_turn`` 直接驱动
``build_turn_graph(loop)`` 编译的状态机（llm → tools → llm → … → finalize），
事件经 LangGraph custom stream 实时透传。本文件因此改为：

- **route 转移表**单元测试（route_after_llm 的三种去向）；
- **graph 真实驱动**：直接 astream 编译图（不经 iter_turn），断言
  ReAct 循环（工具轮 → 收尾）与审批门语义；
- **生产接线回归**：iter_turn 内部确实走的 graph（防回退到手写循环）。
"""

from __future__ import annotations

from pathlib import Path

from langgraph.graph import END

from spark.config import SparkConfig
from spark.core.graph import build_turn_graph, route_after_llm
from spark.models import ChatDelta, ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def _cfg(approval: str = "full-auto") -> SparkConfig:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = approval  # type: ignore[assignment]
    return cfg


def _loop(tmp_path: Path, provider, cfg: SparkConfig | None = None):
    from spark.core.loop import AgentLoop

    cfg = cfg or _cfg()
    store = SessionStore(Path(":memory:"))
    sid = store.create_session(tmp_path, cfg.provider.model, title="t")
    registry = ToolRegistry(
        ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    )
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=registry,
        store=store,
        session_id=sid,
    )
    return loop, store


def _tool_call_provider() -> MockProvider:
    return MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )


# ---------------------------------------------------------------------------
# route 转移表
# ---------------------------------------------------------------------------


def test_route_after_llm_transitions() -> None:
    assert route_after_llm({"error": "boom"}) == END
    assert route_after_llm({"error": None, "tool_calls": [object()]}) == "approve"
    assert route_after_llm({"error": None, "tool_calls": []}) == "finalize"
    # 缺省（首轮前置 state 之外的极端输入）也必须安全
    assert route_after_llm({}) == "finalize"


# ---------------------------------------------------------------------------
# graph 真实驱动（不经 iter_turn，直接 astream 编译图）
# ---------------------------------------------------------------------------


async def test_graph_drives_react_cycle(tmp_path: Path) -> None:
    """编译图直接驱动：一轮工具 + 一轮文本收尾，事件与 iter_turn 等价。"""
    from spark.models import ChatMessage

    loop, store = _loop(tmp_path, _tool_call_provider())
    loop.history.append(ChatMessage(role="user", content="list files"))
    graph = build_turn_graph(loop)

    events = [
        event
        async for event in graph.astream(
            {
                "rounds": 0,
                "tool_calls": [],
                "verdicts": [],
                "memory_block": None,
                "error": None,
            },
            {"configurable": {"thread_id": "t-direct"}},
            stream_mode="custom",
        )
    ]

    types = [e.type for e in events]
    # 一轮工具：tool_start/tool_end → 计划无关 → 下一轮 LLM 文本 → finalize 收尾
    assert "tool_start" in types
    assert "tool_end" in types
    assert types[-1] == "turn_end"
    assert events[-1].text == "done"
    store.close()


async def test_graph_finalizes_without_tools(tmp_path: Path) -> None:
    """无工具调用：llm → finalize，产出 context + turn_end。"""
    from spark.models import ChatMessage

    provider = MockProvider(rounds=[[ChatDelta(type="text", text="hi"), ChatDelta(type="end")]])
    loop, store = _loop(tmp_path, provider)
    loop.history.append(ChatMessage(role="user", content="hello"))
    graph = build_turn_graph(loop)

    events = [
        event
        async for event in graph.astream(
            {
                "rounds": 0,
                "tool_calls": [],
                "verdicts": [],
                "memory_block": None,
                "error": None,
            },
            {"configurable": {"thread_id": "t-direct-final"}},
            stream_mode="custom",
        )
    ]

    types = [e.type for e in events]
    assert "context" in types
    assert types[-1] == "turn_end"
    assert events[-1].text == "hi"
    store.close()


async def test_graph_provider_error_stops(tmp_path: Path) -> None:
    """provider 异常：llm 发 turn_error，route END，无 finalize。"""

    class ExplodingProvider:
        async def stream(self, messages, tools, **_):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover - 使其成为 async generator

    from spark.models import ChatMessage

    loop, store = _loop(tmp_path, ExplodingProvider())
    loop.history.append(ChatMessage(role="user", content="go"))
    graph = build_turn_graph(loop)

    events = [
        event
        async for event in graph.astream(
            {
                "rounds": 0,
                "tool_calls": [],
                "verdicts": [],
                "memory_block": None,
                "error": None,
            },
            {"configurable": {"thread_id": "t-direct-err"}},
            stream_mode="custom",
        )
    ]

    assert len(events) == 1
    assert events[0].type == "turn_error"
    assert "kaboom" in events[0].text
    store.close()


# ---------------------------------------------------------------------------
# 生产接线回归：iter_turn 必须驱动 graph（防回退到手写 while 循环）
# ---------------------------------------------------------------------------


async def test_iter_turn_wires_through_graph(tmp_path: Path, monkeypatch) -> None:
    from spark.core import graph as graph_mod

    provider = _tool_call_provider()
    loop, store = _loop(tmp_path, provider)
    calls = {"build": 0}
    real_build = graph_mod.build_turn_graph

    def spy(loop_arg, checkpointer=None):
        calls["build"] += 1
        return real_build(loop_arg, checkpointer=checkpointer)

    monkeypatch.setattr(graph_mod, "build_turn_graph", spy)

    events = [event async for event in loop.iter_turn("list files")]
    types = [e.type for e in events]

    assert calls["build"] == 1, "iter_turn 必须经 build_turn_graph 构建状态机"
    assert "tool_start" in types and "tool_end" in types
    assert types[-1] == "turn_end"
    # graph 缓存于实例：第二个 turn 不再重复编译
    provider2 = MockProvider(rounds=[[ChatDelta(type="text", text="ok"), ChatDelta(type="end")]])
    loop.provider = provider2
    events2 = [event async for event in loop.iter_turn("again")]
    assert calls["build"] == 1, "graph 应跨 turn 复用（实例级缓存）"
    assert events2[-1].text == "ok"
    store.close()


async def test_iter_turn_behavior_equivalent_tool_cycle(tmp_path: Path) -> None:
    """行为等价快照：事件序列与旧 while 循环语义一致（tool 循环 + 收尾）。"""
    loop, store = _loop(tmp_path, _tool_call_provider())

    events = [event async for event in loop.iter_turn("list files")]
    types = [e.type for e in events]

    # 事件序列骨架：context（turn 开头）→ tool_start → tool_end → context（收尾）→ turn_end
    assert types[0] == "context"
    assert "tool_start" in types and "tool_end" in types
    assert types[-1] == "turn_end"
    assert events[-1].text == "done"
    # 工具结果已入历史：user → assistant(tool_calls) → tool → assistant(text)
    roles = [m.role for m in loop.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    # 轮次推进：mock 第二轮文本为最终回复
    assert loop.history[-1].content == "done"
    store.close()
