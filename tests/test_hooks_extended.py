"""Tests for the extended hook system — 12 event types and ShellHook on_deny modes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from spark.hooks import (
    NOTIFY_ONLY_EVENTS,
    HookDecision,
    HookRegistry,
    ShellHook,
)


class RecordingHook:
    def __init__(self, allow=True):
        self.allow = allow
        self.calls = []

    def run(self, payload):
        self.calls.append(payload)
        return HookDecision(allow=self.allow, reason="recorded")


def test_all_12_events_exist():
    expected = {
        "turn_start",
        "pre_tool",
        "post_tool",
        "turn_end",
        "app_start",
        "session_create",
        "session_load",
        "compaction",
        "memory_extract",
        "approval_needed",
        "tool_error",
    }
    assert expected.issubset(set(HookRegistry.EVENTS))


def test_notify_only_events():
    assert "approval_needed" in NOTIFY_ONLY_EVENTS
    assert "tool_error" in NOTIFY_ONLY_EVENTS


def test_approval_needed_cannot_block():
    """approval_needed is notify-only — hooks cannot deny it."""
    registry = HookRegistry()
    registry.register("approval_needed", RecordingHook(allow=False))
    decision = registry.notify("approval_needed", {"tool": "run_shell"})
    assert decision.allow is True  # always allow


def test_tool_error_cannot_block():
    registry = HookRegistry()
    registry.register("tool_error", RecordingHook(allow=False))
    decision = registry.notify("tool_error", {"error": "timeout"})
    assert decision.allow is True


def test_pre_tool_still_blocks():
    """pre_tool is NOTIFY_ONLY=False — hooks can block."""
    registry = HookRegistry()
    registry.register("pre_tool", RecordingHook(allow=False))
    decision = registry.notify("pre_tool", {"name": "run_shell"})
    assert decision.allow is False


def test_new_hook_event_registration():
    """New lifecycle events can accept hooks."""
    registry = HookRegistry()
    h = RecordingHook()
    registry.register("app_start", h)
    registry.register("session_create", h)
    registry.register("compaction", h)
    registry.notify("app_start", {})
    registry.notify("session_create", {"id": "s1"})
    registry.notify("compaction", {})
    assert len(h.calls) == 3


def test_unknown_event_still_rejected():
    with pytest.raises(ValueError):
        HookRegistry().register("nope", RecordingHook())


def test_from_config_accepts_on_deny_field(tmp_path):
    """from_config accepts on_deny field, defaults to 'block'."""
    registry = HookRegistry.from_config(
        [
            {"event": "pre_tool", "command": "cat", "on_deny": "block"},
            {"event": "pre_tool", "command": "cat", "on_deny": "warn"},
        ],
        tmp_path,
    )
    assert registry.enabled
    hooks = registry._hooks.get("pre_tool", [])
    assert len(hooks) == 2
    assert hooks[0].on_deny == "block"
    assert hooks[1].on_deny == "warn"


def test_from_config_invalid_on_deny_fallback(tmp_path):
    registry = HookRegistry.from_config(
        [
            {"event": "pre_tool", "command": "cat", "on_deny": "bogus"},
        ],
        tmp_path,
    )
    hook = registry._hooks["pre_tool"][0]
    assert hook.on_deny == "block"  # fallback


def test_shell_hook_warn_does_not_block(tmp_path):

    if os.name == "nt":
        hook = ShellHook(
            "ok", ["cmd.exe", "/c", "exit", "3"], tmp_path, on_deny="warn"
        )
    else:
        hook = ShellHook(
            "ok", ["sh", "-c", "exit", "3"], tmp_path, on_deny="warn"
        )
    decision = hook.run({})
    assert decision.allow is True  # warn mode: don't block


def test_shell_hook_block_returns_deny(tmp_path):

    if os.name == "nt":
        hook = ShellHook(
            "bad",
            ["cmd.exe", "/c", "echo", "denied", "1>&2", "&&", "exit", "2"],
            tmp_path,
            on_deny="block",
        )
    else:
        hook = ShellHook(
            "bad", ["sh", "-c", "echo denied >&2; exit 2"], tmp_path, on_deny="block"
        )
    decision = hook.run({})
    assert decision.allow is False
    assert "denied" in decision.reason
