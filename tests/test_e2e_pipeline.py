"""End-to-end pipeline test: config → registry → loop → tool execution.

Verifies the full chain works together after the "replace all with official"
refactor: tomlkit config, mcp SDK bridge, logging, LangGraph loop.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from spark.config import SparkConfig, load_config, save_config
from spark.core.loop import AgentLoop
from spark.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChatDelta,
    ToolCall,
)
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loop(
    tmp_path: Path,
    provider: MockProvider,
    approval: str = "full-auto",
    max_rounds: int = 30,
    approver=None,
    use_graph: bool = False,
) -> AgentLoop:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = approval
    cfg.agent.max_tool_rounds = max_rounds
    if use_graph:
        os.environ["SPARK_USE_LANGGRAPH"] = "1"
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    registry = ToolRegistry(ctx)
    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=registry,
        store=store,
        session_id=sid,
        approver=approver,
    )


# ---------------------------------------------------------------------------
# 1. Config roundtrip (tomlkit)
# ---------------------------------------------------------------------------


def test_config_roundtrip_tomlkit(tmp_path: Path) -> None:
    """save_config uses tomlkit; load_config must read it back identically."""
    cfg = SparkConfig()
    cfg.agent.sandbox_mode = "workspace"
    cfg.agent.approval = "auto-edit"
    cfg.agent.max_tool_rounds = 12
    cfg.agent.shell_timeout_sec = 60
    cfg.context.max_context_tokens = 48000
    cfg.memory.enabled = True
    cfg.memory.top_k = 7
    path = save_config(cfg, tmp_path / "spark.toml")
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.agent.sandbox_mode == "workspace"
    assert loaded.agent.approval == "auto-edit"
    assert loaded.agent.max_tool_rounds == 12
    assert loaded.agent.shell_timeout_sec == 60
    assert loaded.context.max_context_tokens == 48000
    assert loaded.memory.top_k == 7


def test_config_roundtrip_with_special_chars(tmp_path: Path) -> None:
    """TOML with special characters (quotes, backslashes) must roundtrip."""
    cfg = SparkConfig()
    cfg.provider.base_url = 'https://api.example.com/v1/"quoted"'
    cfg.agent.protected_paths = [r"C:\Users\test", "/etc/passwd"]
    path = save_config(cfg, tmp_path / "spark.toml")
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.agent.protected_paths == [r"C:\Users\test", "/etc/passwd"]


# ---------------------------------------------------------------------------
# 2. Standard loop: tool execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_standard_loop_tool_executes(tmp_path: Path) -> None:
    """Standard loop: MockProvider requests list_dir, tool executes, LLM gets result."""
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="Done listing"), ChatDelta(type="end")],
        ]
    )
    loop = _make_loop(tmp_path, provider)
    events = await loop.run("list files in current directory")

    types = [e.type for e in events]
    assert "tool_start" in types
    assert "tool_end" in types
    assert types[-1] == "turn_end"

    # Tool was actually executed — check the result
    tool_end_events = [e for e in events if e.type == "tool_end"]
    assert len(tool_end_events) == 1
    assert tool_end_events[0].result.ok

    # History contains tool response
    messages = loop.store.load_messages(loop.session_id)
    tool_msgs = [m for m in messages if m.role == "tool"]
    assert len(tool_msgs) == 1


@pytest.mark.asyncio
async def test_e2e_standard_loop_write_file(tmp_path: Path) -> None:
    """Standard loop: write_file tool creates a file on disk."""
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="w1",
                        name="write_file",
                        arguments={"path": "hello.txt", "content": "Hello, Spark!"},
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="File written"), ChatDelta(type="end")],
        ]
    )
    loop = _make_loop(tmp_path, provider)
    events = await loop.run("create hello.txt")

    # File must exist on disk
    written = tmp_path / "hello.txt"
    assert written.exists()
    assert written.read_text() == "Hello, Spark!"
    # ...and the loop must have reported the tool round it ran.
    types = [e.type for e in events]
    assert "tool_start" in types
    assert "tool_end" in types
    assert events[-1].type == "turn_end"


# ---------------------------------------------------------------------------
# 3. LangGraph loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_langgraph_loop_tool_executes(tmp_path: Path) -> None:
    """LangGraph path: tools node executes tools and cycles back to LLM."""
    os.environ["SPARK_USE_LANGGRAPH"] = "1"
    try:
        provider = MockProvider(
            rounds=[
                [
                    ChatDelta(
                        type="tool_call",
                        tool_call=ToolCall(
                            id="lg1", name="list_dir", arguments={"path": "."}
                        ),
                    ),
                    ChatDelta(type="end"),
                ],
                [ChatDelta(type="text", text="LG done"), ChatDelta(type="end")],
            ]
        )
        loop = _make_loop(tmp_path, provider, use_graph=True)
        events = await loop.run("list via graph")

        types = [e.type for e in events]
        assert "tool_start" in types
        assert "tool_end" in types
        assert types[-1] == "turn_end"
    finally:
        os.environ.pop("SPARK_USE_LANGGRAPH", None)


# ---------------------------------------------------------------------------
# 4. Approval flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_approval_allow(tmp_path: Path) -> None:
    """Approver allows the tool → tool executes."""
    async def allow_all(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(tool_call_id=req.tool_call.id, action="allow")

    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="a1", name="list_dir", arguments={"path": "."}
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="approved"), ChatDelta(type="end")],
        ]
    )
    loop = _make_loop(tmp_path, provider, approval="suggest", approver=allow_all)
    events = await loop.run("list with approval")
    assert any(e.type == "tool_end" and e.result.ok for e in events)


@pytest.mark.asyncio
async def test_e2e_approval_deny(tmp_path: Path) -> None:
    """Approver denies → tool result has error."""
    async def deny_all(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(tool_call_id=req.tool_call.id, action="deny")

    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="d1",
                        name="write_file",
                        arguments={"path": "denied.txt", "content": "no"},
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="was denied"), ChatDelta(type="end")],
        ]
    )
    loop = _make_loop(tmp_path, provider, approval="suggest", approver=deny_all)
    events = await loop.run("try write denied file")
    tool_end = [e for e in events if e.type == "tool_end"]
    assert len(tool_end) == 1
    assert not tool_end[0].result.ok


# ---------------------------------------------------------------------------
# 5. Multi-round conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_multi_round_accumulates_history(tmp_path: Path) -> None:
    """Two consecutive turns: history from turn 1 persists into turn 2."""
    provider = MockProvider()
    loop = _make_loop(tmp_path, provider)

    # Turn 1: simple text response
    events1 = await loop.run("Hello Spark")
    assert any(e.type == "turn_end" for e in events1)

    # Turn 2: another message — history from turn 1 should be present
    events2 = await loop.run("What did I say before?")
    assert any(e.type == "turn_end" for e in events2)

    messages = loop.store.load_messages(loop.session_id)
    user_msgs = [m for m in messages if m.role == "user"]
    assert len(user_msgs) >= 2


# ---------------------------------------------------------------------------
# 6. Permission rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_permission_blocks_write_outside_workdir(tmp_path: Path) -> None:
    """Shell command attempting to write outside workdir is blocked by sandbox."""
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="s1",
                        name="run_shell",
                        arguments={"command": "echo hacked > /etc/passwd"},
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="blocked"), ChatDelta(type="end")],
        ]
    )
    loop = _make_loop(tmp_path, provider)
    events = await loop.run("try to escape sandbox")
    tool_end = [e for e in events if e.type == "tool_end"]
    # The point is that the command never reached the OS. The previous form only
    # asserted "the loop survived", which passed even when the write went
    # through — the security claim was untested.
    assert tool_end, "run_shell should have produced a tool_end event"
    result = tool_end[0].result
    assert result is not None
    assert result.ok is False, f"shell command unexpectedly ran: {result.payload}"
    message = str(result.payload.get("error", "")).lower()
    assert "blocked" in message or "denied" in message, result.payload


# ---------------------------------------------------------------------------
# 7. Logging is active (no print statements)
# ---------------------------------------------------------------------------


def test_no_print_in_source() -> None:
    """After refactor, no print() calls remain in src/spark/ (except egg-info)."""
    src = Path(__file__).parent.parent / "src" / "spark"
    offenders: list[tuple[Path, int, str]] = []
    for py in src.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("print(") and not stripped.startswith("#"):
                offenders.append((py, i, line.rstrip()))
    assert not offenders, f"Found {len(offenders)} print() calls: {offenders[:5]}"
