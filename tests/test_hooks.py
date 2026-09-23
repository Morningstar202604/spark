from __future__ import annotations

from pathlib import Path
from typing import Any

from spark.hooks import HookDecision, HookRegistry


class RecordingHook:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.calls: list[dict[str, Any]] = []

    def run(self, payload: dict[str, Any]) -> HookDecision:
        self.calls.append(payload)
        return HookDecision(allow=self.allow, reason="recorded")


def test_empty_registry_allows_everything() -> None:
    registry = HookRegistry()
    assert not registry.enabled
    assert registry.notify("pre_tool", {"name": "run_shell"}).allow


def test_pre_tool_hook_can_block(tmp_path: Path) -> None:
    registry = HookRegistry()
    hook = RecordingHook(allow=False)
    registry.register("pre_tool", hook)
    decision = registry.notify("pre_tool", {"name": "run_shell"})
    assert not decision.allow
    assert hook.calls == [{"name": "run_shell"}]


def test_first_blocking_hook_wins(tmp_path: Path) -> None:
    registry = HookRegistry()
    registry.register("pre_tool", RecordingHook(allow=True))
    second = RecordingHook(allow=False)
    registry.register("pre_tool", second)
    assert not registry.notify("pre_tool", {}).allow
    assert second.calls


def test_unknown_event_rejected() -> None:
    registry = HookRegistry()
    try:
        registry.register("nope", RecordingHook())
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_shell_hook_allows_on_zero_exit(tmp_path: Path) -> None:
    from spark.hooks import ShellHook

    if __import__("os").name == "nt":
        hook = ShellHook("ok", ["cmd.exe", "/c", "exit", "0"], tmp_path)
    else:
        hook = ShellHook("ok", ["true"], tmp_path)
    assert hook.run({}).allow


def test_shell_hook_blocks_on_nonzero(tmp_path: Path) -> None:
    from spark.hooks import ShellHook

    if __import__("os").name == "nt":
        hook = ShellHook(
            "bad",
            ["cmd.exe", "/c", "echo", "denied", "1>&2", "&&", "exit", "3"],
            tmp_path,
        )
    else:
        hook = ShellHook("bad", ["sh", "-c", "echo denied >&2; exit 3"], tmp_path)
    decision = hook.run({})
    assert not decision.allow
    assert "denied" in decision.reason


def test_from_config_skips_invalid_entries(tmp_path: Path) -> None:
    registry = HookRegistry.from_config(
        [
            {"event": "bogus", "command": "echo x"},
            {"event": "pre_tool", "command": 123},
            {"event": "turn_start", "command": "echo start"},
        ],
        tmp_path,
    )
    assert registry.enabled
    assert registry.notify("pre_tool", {}).allow


def test_configured_hook_receives_payload(tmp_path: Path) -> None:
    registry = HookRegistry.from_config(
        [{"event": "pre_tool", "command": "cat", "name": "cat"}], tmp_path
    )
    hook = next(iter(registry._hooks["pre_tool"]))
    hook.run({"tool": "run_shell", "arguments": {"command": "ls"}})
    assert hook.name == "cat"


def test_loop_pre_tool_hook_blocks_execution(tmp_path: Path) -> None:
    import asyncio
    import os

    from spark.config import HookConfig, SparkConfig
    from spark.core.loop import AgentLoop
    from spark.models import ChatDelta, ToolCall
    from spark.sandbox import WorkdirSandbox
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    cfg.hooks = [
        HookConfig(
            event="pre_tool",
            command="exit 4" if os.name != "nt" else "cmd.exe /c exit 4",
            name="blocker",
        )
    ]
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    store = SessionStore(tmp_path / "h.db")
    sid = store.create_session(tmp_path, "mock")

    class OneCall:
        def __init__(self):
            self.n = 0

        async def stream(self, messages, tools):
            self.n += 1
            if self.n == 1:
                yield ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="x", name="run_shell", arguments={"command": "echo hi"}
                    ),
                )
            else:
                yield ChatDelta(type="text", text="stopped")

    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=OneCall(),
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )

    async def run():
        return [event async for event in loop.iter_turn("go")]

    events = asyncio.run(run())
    ends = [e for e in events if e.type == "tool_end"]
    assert ends
    assert not ends[0].result.ok
    assert "hook" in str(ends[0].result.payload.get("error", "")).lower()
    store.close()
