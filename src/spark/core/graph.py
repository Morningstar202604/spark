"""LangGraph-powered ReAct loop orchestration.

Thin wrapper around :class:`langgraph.graph.StateGraph` that maps Spark's
ReAct cycle to a LangGraph state machine.  ``AgentLoop`` (loop.py) remains the
outer orchestrator — it owns history, session store, memory, cancellation, etc.
The graph only formalises the call-model → execute-tools → repeat cycle using
LangGraph's interrupt mechanism for human approval.

> **现状（如实标注）**：生产路径（CLI / TUI / Web 的 ``AgentLoop``）当前使用
> 手写 while 循环而非本模块；本文件是 LangGraph 参考实现与接入的备选，
> 供 P0-6（plan/verify 节点）决策时直接采用。接入生产时使用
> :func:`build_react_graph_with_sqlite_saver` 将 graph 状态落盘到 SQLite
> （跨进程中断可恢复），或由 ``AgentLoop`` 显式传入 checkpointer。

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

Usage (when wiring the graph into production)::

    graph = build_react_graph_with_sqlite_saver(state_db_path)(...)
    async for event in turn_events_from_graph(graph, user_text, images):
        yield event
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from spark.core.compact import Compactor
from spark.core.context import build_messages
from spark.models import (
    ApprovalRequest,
    ChatMessage,
    ToolCall,
    ToolResult,
)
from spark.providers.base import Provider
from spark.tools.registry import ToolRegistry

try:
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:  # older langgraph
    MemorySaver = None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class ReActState(dict):
    """Typed dict holding ReAct loop state for one user turn.

    LangGraph passes this between nodes; each node mutates-and-returns a patch
    dictionary that LangGraph merges into the live state.

    Only msgpack-serializable fields live here.  Non-serializable collaborators
    (provider / registry / store / cfg / ...) are bound to the node callables
    by closure so LangGraph never tries to serialise them.
    """

    # Mutable turn state (serialisable)
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
    allow_always: set[str]


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------


def _make_node_llm(
    *,
    provider: Provider,
    registry: ToolRegistry,
    workdir: Any,
    cfg: Any,
    compactor: Compactor | None,
    memory_obj: Any | None,
):
    async def _node_llm(state: ReActState) -> dict:
        """Call the LLM provider and accumulate streamed deltas.

        Collaborators (provider/registry/cfg/...) come from the closure, not the
        state, so LangGraph never tries to serialise them.
        """
        history: list[ChatMessage] = state["history"]

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
                # memory 检索失败不影响主流程，仅降级为无记忆注入
                logger.debug("memory 上下文检索失败，跳过本次记忆注入", exc_info=True)
                memory_block = None

        # Check whether compaction turned over during this turn (AgentLoop does it)
        if state.get("compacted_this_turn"):
            yield_patch = {"compacted_this_turn": False}
        else:
            yield_patch = {}

        # Build LLM messages using the existing context/compactor helpers
        messages = build_messages(
            workdir=workdir,
            cfg=cfg,
            history=history,
            memory_block=memory_block,
            _compactor=compactor,
        )

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        try:
            async for delta in provider.stream(messages, registry.schemas()):
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
                "tool_results": [],  # reset to prevent stale-result loop
                "_error": str(exc),
            }

        return {
            **yield_patch,
            "text_parts": text_parts,
            "reasoning_parts": reasoning_parts,
            "tool_calls": tool_calls,
            "tool_results": [],  # reset — ensures route decisions use fresh data
        }

    return _node_llm


def _make_node_tools(
    *,
    cfg: Any,
    registry: ToolRegistry,
    store: Any = None,
    session_id: str = "",
):
    async def _node_tools(state: ReActState) -> dict:
        """Execute tool calls and collect results.

        Each tool call is invoked via ``registry.execute`` — the same execution
        engine used by the classic ``loop.py`` path — so behaviour is identical.
        Approval checks (via ``policy.decide``) are performed before execution.
        After execution, tool calls + results are appended to ``history`` so the
        next LLM round sees them.
        """
        calls: list[ToolCall] = state.get("tool_calls", [])
        if not calls:
            return {}

        results: list[ToolResult] = []
        history: list[ChatMessage] = list(state.get("history", []))

        def _append_result(call: ToolCall, result: ToolResult) -> None:
            """Mirror how the classic loop builds history: assistant(tool_calls) + tool."""
            results.append(result)
            history.append(ChatMessage(role="assistant", tool_calls=[call]))
            history.append(
                ChatMessage(
                    role="tool",
                    content=json.dumps(result.payload, default=str, ensure_ascii=False),
                    tool_call_id=call.id,
                )
            )

        for call in calls:
            from spark.policy import decide

            verdict = decide(
                cfg.agent.approval,
                call,
                allow_always=state.get("allow_always", set()),
                readonly_mcp=(
                    registry.readonly_mcp
                    if hasattr(registry, "readonly_mcp")
                    else set()
                ),
                rules=cfg.permission_rules or None,
            )
            if verdict == "deny":
                _append_result(
                    call, ToolResult(ok=False, payload={"error": "denied by policy"})
                )
                continue
            if verdict == "prompt":
                # Approval required — interrupt via LangGraph's HIL protocol and
                # honour the resumed decision (deny must NOT execute the tool).
                req = ApprovalRequest(
                    tool_call=call,
                    summary=f"{call.name}({call.arguments})",
                )
                decision = interrupt(req.model_dump())
                action = (
                    decision.get("action")
                    if isinstance(decision, dict)
                    else str(decision or "")
                )
                if action not in {"allow", "allow_always"}:
                    _append_result(
                        call,
                        ToolResult(ok=False, payload={"error": "approval denied"}),
                    )
                    continue

            try:
                result = await registry.execute(call)
            except Exception as exc:  # noqa: BLE001
                result = ToolResult(ok=False, payload={"error": str(exc)})
            _append_result(call, result)

        return {
            "tool_results": results,
            "history": history,
            "rounds": int(state.get("rounds", 0)) + 1,
            "tool_calls": [],  # consumed — next LLM call should not re-see them
        }

    return _node_tools


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

    Non-serializable collaborators (provider/registry/cfg/...) are bound to the
    node callables by closure, so the LangGraph state only carries msgpack-safe
    data and ``astream`` never fails on serialisation.
    """
    graph = StateGraph(ReActState)

    graph.add_node(
        "llm",
        _make_node_llm(
            provider=provider,
            registry=registry,
            workdir=workdir,
            cfg=cfg,
            compactor=compactor,
            memory_obj=memory_obj,
        ),
    )
    graph.add_node(
        "tools",
        _make_node_tools(cfg=cfg, registry=registry, store=store, session_id=session_id),
    )

    graph.set_entry_point("llm")
    graph.add_edge("llm", "tools")
    # After tools: cycle back to LLM if the turn should continue, else END.
    # ``_route_tools`` inspects the current state to decide.
    graph.add_conditional_edges("tools", _route_tools, ["llm", END])

    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


def _route_tools(state: ReActState) -> str:
    """Decide whether to continue the ReAct cycle after the tools node.

    Returns ``"llm"`` to drive another call→execute round, or ``"END"`` to
    terminate the turn.  Checks the same stop conditions as the classic loop.
    """
    max_rounds = int(state.get("__max_rounds", 30))
    rounds = int(state.get("rounds", 0))
    if rounds >= max_rounds:
        return END
    # If the LLM terminated with an error, do not cycle.
    if state.get("_error"):
        return END
    # Fresh tool results → the LLM produced actionable calls, continue.
    if state.get("tool_results"):
        return "llm"
    return END


def build_react_graph_with_memory(checkpointer: Optional[BaseCheckpointSaver] = None):
    """Convenience: build with an in-memory checkpoint saver (for dev/test)."""
    from langgraph.checkpoint.memory import MemorySaver
    cp = checkpointer or MemorySaver()
    return lambda **kw: build_react_graph(**kw, checkpointer=cp)


def build_react_graph_with_sqlite_saver(
    db_path: Any,
    *,
    thread_id: str = "spark",
) -> Any:
    """Convenience: build the graph with a persisted SQLite checkpointer.

    ``db_path`` 可以是 ``Path``/``str``（文件库）或 ``":memory:"``。graph
    的每个 checkpoint（含中断审批点）落盘，进程重启后可从中断点恢复——
    与 ``AgentLoop`` 的会话级检查点互补（前者恢复执行状态，后者恢复工作区
    快照 + 消息索引）。
    """
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    if str(db_path) == ":memory:":
        conn = sqlite3.connect(":memory:", check_same_thread=False)
    else:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cp = SqliteSaver(conn)

    def _build(**kw: Any) -> Any:
        return build_react_graph(**kw, checkpointer=cp)

    _build.thread_id = thread_id  # type: ignore[attr-defined]
    return _build
