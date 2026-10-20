"""Every declared hook event must have a real emission point.

A previous state of the codebase declared 11 events but only fired 4 of them;
operators configuring a hook for the rest got silence with no warning. These
tests trigger each lifecycle and assert the corresponding event arrives.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.hooks import HookDecision, HookRegistry
from spark.models import ApprovalDecision, ChatDelta, ChatMessage, ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


class Recorder:
    def __init__(self) -> None:
        self.fired: list[str] = []

    def hook(self, event: str):
        class _H:
            def run(self, payload):
                outer.fired.append(event)
                return HookDecision(allow=True)

        outer = self
        return _H()

    def attach(self, loop: AgentLoop) -> None:
        loop.hooks = HookRegistry()
        for event in HookRegistry.EVENTS:
            loop.hooks.register(event, self.hook(event))


def _loop(tmp_path: Path, rounds, approval: str = "full-auto", approver=None) -> AgentLoop:
    cfg = SparkConfig()
    cfg.agent.approval = approval
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=MockProvider(rounds=rounds),
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
        approver=approver,
    )


async def _run(loop: AgentLoop, prompt: str = "go") -> None:
    async for _ in loop.iter_turn(prompt):
        pass


def test_every_declared_event_is_documented() -> None:
    """Guards against adding an event name with no emission site."""
    expected = {
        "turn_start", "pre_tool", "post_tool", "turn_end",
        "app_start", "app_stop", "session_create", "session_load", "session_switch",
        "compaction", "memory_extract", "context_threshold",
        "approval_needed", "tool_error", "plan_update", "turn_error", "provider_error",
    }
    assert set(HookRegistry.EVENTS) == expected


@pytest.mark.asyncio
async def test_turn_lifecycle_events_fire(tmp_path: Path) -> None:
    rec = Recorder()
    loop = _loop(tmp_path, [[ChatDelta(type="text", text="hi"), ChatDelta(type="end")]])
    rec.attach(loop)
    await _run(loop)
    assert {"turn_start", "turn_end"}.issubset(set(rec.fired)), rec.fired


@pytest.mark.asyncio
async def test_tool_and_plan_events_fire(tmp_path: Path) -> None:
    rec = Recorder()
    rounds = [
        [
            ChatDelta(type="tool_call", tool_call=ToolCall(
                id="p1", name="update_plan",
                arguments={"steps": [{"title": "s", "status": "in_progress"}]})),
            ChatDelta(type="tool_call", tool_call=ToolCall(
                id="w1", name="write_file", arguments={"path": "a.txt", "content": "x"})),
            ChatDelta(type="end"),
        ],
        [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
    ]
    loop = _loop(tmp_path, rounds)
    rec.attach(loop)
    await _run(loop)
    assert {"pre_tool", "post_tool", "plan_update"}.issubset(set(rec.fired)), rec.fired


@pytest.mark.asyncio
async def test_tool_error_fires_when_tool_fails(tmp_path: Path) -> None:
    rec = Recorder()
    rounds = [
        [ChatDelta(type="tool_call", tool_call=ToolCall(
            id="f1", name="run_shell", arguments={"command": "sudo rm -rf /"})),
         ChatDelta(type="end")],
        [ChatDelta(type="text", text="ok"), ChatDelta(type="end")],
    ]
    loop = _loop(tmp_path, rounds)
    rec.attach(loop)
    await _run(loop)
    assert "tool_error" in rec.fired, rec.fired


@pytest.mark.asyncio
async def test_approval_event_fires_in_suggest_mode(tmp_path: Path) -> None:
    rec = Recorder()
    rounds = [
        [ChatDelta(type="tool_call", tool_call=ToolCall(
            id="s1", name="run_shell", arguments={"command": "echo x"})),
         ChatDelta(type="end")],
        [ChatDelta(type="text", text="ok"), ChatDelta(type="end")],
    ]

    async def deny(request):
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="deny")

    loop = _loop(tmp_path, rounds, approval="suggest", approver=deny)
    rec.attach(loop)
    await _run(loop)
    assert "approval_needed" in rec.fired, rec.fired


@pytest.mark.asyncio
async def test_context_threshold_and_compaction_fire(tmp_path: Path) -> None:
    rec = Recorder()
    loop = _loop(tmp_path, [[ChatDelta(type="text", text="hi"), ChatDelta(type="end")]])
    loop.cfg.context.compact_threshold = 0.10
    loop.cfg.context.max_context_tokens = 2000
    loop.cfg.context.keep_recent_messages = 2
    for _ in range(40):
        loop._append_history(ChatMessage(role="user", content="x" * 400))
        loop._append_history(ChatMessage(role="assistant", content="y" * 400))
    rec.attach(loop)
    await _run(loop)
    assert "context_threshold" in rec.fired, rec.fired
    assert "compaction" in rec.fired, rec.fired


@pytest.mark.asyncio
async def test_provider_error_fires_on_model_failure(tmp_path: Path) -> None:
    rec = Recorder()

    class Boom(MockProvider):
        async def stream(self, messages, tools, effort=None):
            raise RuntimeError("provider exploded")
            yield  # pragma: no cover

    loop = _loop(tmp_path, [])
    loop.provider = Boom()
    rec.attach(loop)
    await _run(loop)
    assert {"provider_error", "turn_error"}.issubset(set(rec.fired)), rec.fired
