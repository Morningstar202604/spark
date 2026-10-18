"""LangGraph 驱动的 ReAct 控制流——**唯一真相源**。

生产路径（CLI / TUI / Web / ACP 的 ``AgentLoop.iter_turn``）直接驱动本模块
构建的状态机；手写 while 循环已删除（A11 消除）。职责切分：

- **graph（本模块）**：轮次控制流——``llm → tools → llm → … → finalize``。
- **loop（loop.py）**：外围职责——历史/存储、记忆、压缩、取消、token 计账、
  hooks、审批回调、子代理、检查点快照。

事件泵：节点内通过 LangGraph custom stream（:func:`get_stream_writer`）实时
推送 ``TurnEvent``，``iter_turn`` 以 ``astream(stream_mode="custom")`` 消费——
text/reasoning delta 的逐 token 流式语义与旧 while 循环逐字节等价。

下一批接入：``interrupt``（审批跨进程挂起）+ ``SqliteSaver``（turn 级崩溃恢复，
A10）；本版审批仍走 ``loop.approver`` 回调，行为与旧循环一致。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph

from spark.models import ToolCall

if TYPE_CHECKING:  # pragma: no cover - 仅类型引用，避免运行时循环依赖
    from spark.core.loop import AgentLoop

logger = logging.getLogger(__name__)

__all__ = ["AgentTurnState", "build_turn_graph", "route_after_llm"]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class AgentTurnState(TypedDict, total=False):
    """单次 turn 的可序列化轮次状态。

    只放控制流需要的轻量字段；不可序列化的协作者（provider/registry/store）
    全部经闭包绑定到节点，LangGraph 永远不尝试序列化它们。
    """

    rounds: int                 # 已完成的工具执行轮数
    tool_calls: list[ToolCall]  # 本轮 LLM 产出、待 tools 节点执行的调用
    memory_block: str | None    # turn 开始时检索一次的记忆上下文
    error: str | None           # 非空 = 致命停止（turn_error 已发出）


# ---------------------------------------------------------------------------
# Nodes（闭包绑定 loop 实例）
# ---------------------------------------------------------------------------


def _make_node_llm(loop: "AgentLoop"):
    async def node_llm(state: AgentTurnState) -> dict[str, Any]:
        """一轮 LLM 调用：停止条件检查 → 流式调模型 → 计账 → 熔断检查。"""
        writer = get_stream_writer()

        async for event in loop._llm_round(state.get("memory_block"), int(state.get("rounds", 0))):
            writer(event)

        result = loop._last_round
        return {
            "rounds": int(state.get("rounds", 0)),
            "tool_calls": result.get("tool_calls", []),
            "error": result.get("error"),
        }

    return node_llm


def _make_node_tools(loop: "AgentLoop"):
    async def node_tools(state: AgentTurnState) -> dict[str, Any]:
        """一轮工具执行：assistant 入历史 → 逐个跑工具（事件实时透传）→ rounds+1。"""
        writer = get_stream_writer()
        tool_calls = state.get("tool_calls") or []

        async for event in loop._tools_round(tool_calls, loop._last_round.get("text", "")):
            writer(event)

        return {
            "rounds": int(state.get("rounds", 0)) + 1,
            "tool_calls": [],
            "error": None,
        }

    return node_tools


def _make_node_finalize(loop: "AgentLoop"):
    async def node_finalize(state: AgentTurnState) -> dict[str, Any]:
        """收尾：assistant 入历史 → 检查点 → context/预算 → turn_end。"""
        writer = get_stream_writer()

        async for event in loop._finalize_turn(loop._last_round.get("text", "")):
            writer(event)

        return {"error": None}

    return node_finalize


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_after_llm(state: AgentTurnState) -> str:
    """llm 节点后的转移：致命停止 → END；有工具调用 → tools；否则收尾。"""
    if state.get("error"):
        return END
    if state.get("tool_calls"):
        return "tools"
    return "finalize"


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------


def build_turn_graph(loop: "AgentLoop") -> Any:
    """为 *loop* 构建单 turn 的 ReAct 状态机（CompiledStateGraph）。

    graph 跨 turn 复用（无 turn 间状态，state 每次新建）；节点闭包绑定
    loop 实例，因此一个 ``AgentLoop`` 编译一次、缓存于实例。
    """
    graph = StateGraph(AgentTurnState)
    graph.add_node("llm", _make_node_llm(loop))
    graph.add_node("tools", _make_node_tools(loop))
    graph.add_node("finalize", _make_node_finalize(loop))

    graph.set_entry_point("llm")
    graph.add_conditional_edges("llm", route_after_llm, ["tools", "finalize", END])
    graph.add_edge("tools", "llm")

    return graph.compile()
