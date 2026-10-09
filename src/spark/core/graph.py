"""LangGraph-powered ReAct loop orchestration.

Thin wrapper around :class:`langgraph.graph.StateGraph` that maps Spark's
ReAct cycle to a LangGraph state machine.  ``AgentLoop`` (loop.py) remains the
outer orchestrator — it owns history, session store, memory, cancellation, etc.
The graph only formalises the call-model → execute-tools → repeat cycle using
LangGraph's interrupt mechanism for human approval.

Key integrations
----------------

* **StateGraph** formalises the ReAct loop as typed nodes + edges.
* **interrupt_before** on the tools node gives human-in-the-loop approval for
  free via LangGraph's native ``interrupt``/``Command(resume=...)`` protocol.
* **Checkpointer** backing can be enabled to persist graph state per session.
* All Spark-specific logic (token accounting, circuit breaker, compaction,
  checkpoint snapshots, hooks, sub-agents) is preserved in ``AgentLoop`` and
  called from graph node functions — the graph is just the control-flow
  skeleton.

Usage (from ``AgentLoop.iter_turn``)::

    graph = build_react_graph(self)
    async for event in turn_events_from_graph(graph, user_text, images):
        yield event
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, Interrupt, interrupt

from spark.core.compact import Compactor
from spark.core.context import build_messages
from spark.models import (
    ApprovalRequest,
    ChatMessage,
    ToolCall,
    ToolResult,
    TurnEvent,
)
from spark.providers.base import Provider
from spark.tools.registry import ToolRegistry

try:
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:  # older langgraph
    MemorySaver = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class ReActState(dict):
    """Typed dict holding ReAct loop state for one user turn.

    LangGraph passes this between nodes; each node mutates-and-returns a patch
    dictionary that LangGraph merges into the live state.
    """

    # Inherited from AgentLoop (read-only in graph nodes)
    provider: Provider
    registry: ToolRegistry
    workdir: Any  # pathlib.Path
    cfg: Any  # SparkConfig
    approver: Any  # Callable | None
    compactor: Compactor | None
    store: Any  # SessionStore
    session_id: str
    memory_obj: Any | None  # MemoryService

    # Mutable turn state
    messages: Annotated[list[ChatMessage], add_messages]  # LLM input
    history: list[ChatMessage]                          # full conversation history
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    text_parts: list[str]
    reasoning_parts: list[str]
    rounds: int
    turn_tokens: int
    compacted_this_turn: bool
    pending_approval: ApprovalRequest | None
    pending_tool_call: ToolCall | None


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------


async def _node_llm(state: ReActState) -> dict:
    """Call the LLM provider and accumulate streamed deltas.

    In the original ``iter_turn`` this is the inner loop of
    ``async for delta in provider.stream(...)``.  Here we run that same code as
    a LangGraph node so that LangGraph's streaming/checkpoint machinery can
    observe the state transitions.
    """
    provider: Provider = state["provider"]
    history: list[ChatMessage] = state["history"]
    compactor: Compactor | None = state.get("compactor")
    memory_obj = state.get("memory_obj")

    # Ask memory for context block (mirrors AgentLoop.iter_turn)
    memory_block = None
    if memory_obj is not None:
        try:
            last_user_content = next(
                (m.content for m in reversed(history) if m.role == "user"),
                None,
            )
            if last_user_content:
                memory_block = memory_obj.retrieve_context(last_user_content)
        except Exception:
            memory_block = None

    # Check whether compaction turned over during this turn (AgentLoop does it)
    if state.get("compacted_this_turn"):
        yield_patch = {"compacted_this_turn": False}
    else:
        yield_patch = {}

    # Build LLM messages using the existing context/compactor helpers
    messages = build_messages(
        workdir=state["workdir"],
        cfg=state["cfg"],
        history=history,
        memory_block=memory_block,
        _compactor=compactor,
    )

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    try:
        async for delta in provider.stream(messages, state["registry"].schemas()):
            if delta.type == "reasoning" and delta.text:
                reasoning_parts.append(delta.text)
            elif delta.type == "text" and delta.text:
                text_parts.append(delta.text)
            elif delta.type == "tool_call" and delta.tool_call:
                tool_calls.append(delta.tool_call)
    except Exception as exc:
        return {
            **yield_patch,
            "text_parts": text_parts,
            "reasoning_parts": reasoning_parts,
            "tool_calls": tool_calls,
            "_error": str(exc),
        }

    return {
        **yield_patch,
        "text_parts": text_parts,
        "reasoning_parts": reasoning_parts,
        "tool_calls": tool_calls,
    }


async def _node_tools(state: ReActState) -> dict:
    """Check approval for each tool call using LangGraph's interrupt mechanism.

    When a tool call requires approval (as decided by ``spark.policy.decide``),
    we call :func:`langgraph.types.interrupt` which suspends the graph until the
    caller provides a ``Command(resume=...)``.  This mirrors Spark's existing
    ``approval_needed`` event flow but rides LangGraph's first-class HIL
    protocol.
    """
    calls: list[ToolCall] = state.get("tool_calls", [])
    if not calls:
        return {}

    # For each call that needs approval, trigger LangGraph interrupt
    for call in calls:
        from spark.policy import decide

        verdict = decide(
            state["cfg"].agent.approval,
            call,
            state.get("allow_always", set()),
            state["registry"].readonly_mcp if hasattr(state["registry"], "readonly_mcp") else set(),
        )
        if verdict == "prompt":
            # Build ApprovalRequest and interrupt
            # The graph runner will catch this interrupt and emit
            # TurnEvent("approval_needed")
            req = ApprovalRequest(
                tool_call=call,
                summary=f"{call.name}({call.arguments})",
            )
            # interrupt() raises GraphInterrupt; we expect the runner to catch
            interrupt(req.model_dump())

    return {"_ready_to_execute": True}


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------


def build_react_graph(
    *,
    provider: Provider,
    registry: ToolRegistry,
    workdir: Any,
    cfg: Any,
    approver: Any = None,
    compactor: Compactor | None = None,
    store: Any = None,
    session_id: str = "",
    memory_obj: Any = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> Any:  # returns CompiledStateGraph
    """Construct the LangGraph StateGraph for one ReAct turn.

    Parameters mirror the fields that ``AgentLoop`` exposes so the outer
    orchestrator can build the graph per-turn (the graph itself is
    stateless across turns — ``history`` / store live in ``AgentLoop``).
    """
    graph = StateGraph(ReActState)

    graph.add_node("llm", _node_llm)
    graph.add_node("tools", _node_tools)

    graph.set_entry_point("llm")
    graph.add_edge("llm", "tools")
    # After tools we END; the outer loop re-enters if more rounds are needed.
    graph.add_edge("tools", END)

    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


def build_react_graph_with_memory(checkpointer: Optional[BaseCheckpointSaver] = None):
    """Convenience: build with an in-memory checkpoint saver (for dev/test)."""
    from langgraph.checkpoint.memory import MemorySaver
    cp = checkpointer or MemorySaver()
    return lambda **kw: build_react_graph(**kw, checkpointer=cp)
