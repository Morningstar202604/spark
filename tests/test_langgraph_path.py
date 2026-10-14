"""LangGraph 参考实现（core/graph.py）的真实覆盖。

历史教训：本文件曾用 ``loop._use_graph = True`` 伪装成"graph 路径"测试 ——
该属性在 ``AgentLoop`` 上并不存在，4 个用例实际全部执行经典 while 循环，
制造了"LangGraph 路径已有测试"的假象。现在改为**直接构建并驱动 graph**：

- 一轮 turn 执行工具并回填 history（不 NameError、不产生 _error）；
- approval=prompt 时经 LangGraph interrupt 挂起；resume=deny 必须拒绝执行，
  resume=allow 才执行（覆盖 graph 的 resume 语义回归）；
- ``_route_tools`` 的四个路由条件。
"""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END
from langgraph.types import Command

from spark.config import SparkConfig
from spark.core.graph import _route_tools, build_react_graph
from spark.models import ChatDelta, ChatMessage, ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.tools.registry import ToolContext, ToolRegistry


def _cfg(approval: str = "full-auto") -> SparkConfig:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = approval
    return cfg


def _registry(tmp_path: Path, cfg: SparkConfig) -> ToolRegistry:
    return ToolRegistry(
        ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    )


def _initial_state(text: str = "list files", max_rounds: int = 5) -> dict:
    return {
        "history": [ChatMessage(role="user", content=text)],
        "__max_rounds": max_rounds,
    }


def _list_files_provider() -> MockProvider:
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


def _write_file_provider() -> MockProvider:
    """写工具（非只读）才会在 suggest/manual 档触发 approval=prompt。"""
    return MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "a.txt", "content": "hello"},
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )


async def _drain(graph, payload, config: dict) -> list[dict]:
    return [chunk async for chunk in graph.astream(payload, config)]


def _tools_chunk(chunks: list[dict]) -> dict | None:
    for chunk in chunks:
        if "tools" in chunk:
            return chunk["tools"]
    return None


async def test_graph_turn_executes_tool_and_fills_history(tmp_path: Path) -> None:
    cfg = _cfg()
    graph = build_react_graph(
        provider=_list_files_provider(),
        registry=_registry(tmp_path, cfg),
        workdir=tmp_path,
        cfg=cfg,
    )
    chunks = await _drain(graph, _initial_state(), {})
    tools_out = _tools_chunk(chunks)
    assert tools_out is not None, "tools 节点必须执行"
    results = tools_out["tool_results"]
    assert len(results) == 1
    assert results[0].ok is True
    history = tools_out["history"]
    assert any(m.role == "tool" for m in history), "tools 节点必须把结果写回 history"


async def test_graph_interrupt_resume_deny_blocks_execution(tmp_path: Path) -> None:
    cfg = _cfg(approval="suggest")
    graph = build_react_graph(
        provider=_write_file_provider(),
        registry=_registry(tmp_path, cfg),
        workdir=tmp_path,
        cfg=cfg,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "deny-1"}}
    await _drain(graph, _initial_state(), config)

    snapshot = await graph.aget_state(config)
    assert snapshot.next, "approval=prompt 时 graph 应悬挂在 tools 节点"

    chunks = await _drain(graph, Command(resume={"action": "deny"}), config)
    tools_out = _tools_chunk(chunks)
    assert tools_out is not None
    results = tools_out["tool_results"]
    assert results and results[0].ok is False
    assert "approval denied" in str(results[0].payload.get("error", ""))
    tool_msgs = [m for m in tools_out["history"] if m.role == "tool"]
    assert any("approval denied" in (m.content or "") for m in tool_msgs)
    assert not (tmp_path / "a.txt").exists(), "被拒绝的写工具不得产生文件"


async def test_graph_interrupt_resume_allow_executes_tool(tmp_path: Path) -> None:
    cfg = _cfg(approval="suggest")
    graph = build_react_graph(
        provider=_write_file_provider(),
        registry=_registry(tmp_path, cfg),
        workdir=tmp_path,
        cfg=cfg,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "allow-1"}}
    await _drain(graph, _initial_state(), config)
    chunks = await _drain(graph, Command(resume={"action": "allow"}), config)
    tools_out = _tools_chunk(chunks)
    assert tools_out is not None
    results = tools_out["tool_results"]
    assert results and results[0].ok is True
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello"


def test_route_tools_stops_at_max_rounds() -> None:
    assert _route_tools({"rounds": 5, "__max_rounds": 5, "tool_results": [1]}) == END


def test_route_tools_continues_with_fresh_results() -> None:
    assert _route_tools({"rounds": 1, "__max_rounds": 5, "tool_results": [1]}) == "llm"


def test_route_tools_ends_on_error() -> None:
    state = {"rounds": 1, "__max_rounds": 5, "tool_results": [1], "_error": "boom"}
    assert _route_tools(state) == END


def test_route_tools_ends_without_results() -> None:
    assert _route_tools({"rounds": 1, "__max_rounds": 5, "tool_results": []}) == END
