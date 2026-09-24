from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SHELL_METACHARACTERS = set("&|<>^%!\n\r\x00")


@dataclass
class HookDecision:
    allow: bool
    reason: str = ""


class Hook(Protocol):
    def run(self, payload: dict[str, Any]) -> HookDecision: ...


class ShellHook:
    """Runs a configured command directly. Never routed through a shell interpreter."""

    def __init__(
        self, name: str, command: list[str], cwd: Path, timeout: int = 15
    ) -> None:
        self.name = name
        self.command = command
        self.cwd = cwd
        self.timeout = timeout

    def run(self, payload: dict[str, Any]) -> HookDecision:
        try:
            completed = subprocess.run(
                list(self.command),
                cwd=str(self.cwd),
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return HookDecision(allow=False, reason=f"hook {self.name} timed out")
        except OSError as exc:
            return HookDecision(allow=False, reason=f"hook {self.name} failed: {exc}")
        if completed.returncode == 0:
            return HookDecision(allow=True)
        reason = (completed.stderr or completed.stdout or "").strip()
        return HookDecision(allow=False, reason=f"hook {self.name} blocked: {reason}")


class HookRegistry:
    """Registry of lifecycle hooks keyed by event name. Empty registry is a no-op."""

    EVENTS = (
        "turn_start",
        "pre_tool",
        "post_tool",
        "turn_end",
    )

    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = {}

    @property
    def enabled(self) -> bool:
        return any(self._hooks.values())

    def register(self, event: str, hook: Hook) -> None:
        if event not in self.EVENTS:
            raise ValueError(f"unknown hook event: {event}")
        self._hooks.setdefault(event, []).append(hook)

    def notify(self, event: str, payload: dict[str, Any]) -> HookDecision:
        if event not in self.EVENTS:
            raise ValueError(f"unknown hook event: {event}")
        for hook in self._hooks.get(event, []):
            decision = hook.run(payload)
            if not decision.allow:
                return decision
        return HookDecision(allow=True)

    @classmethod
    def from_config(cls, raw: list[dict[str, Any]] | None, cwd: Path) -> HookRegistry:
        registry = cls()
        for item in raw or []:
            event = str(item.get("event") or "")
            command = item.get("command")
            if event not in cls.EVENTS or not isinstance(command, str):
                continue
            args = [str(part) for part in (item.get("args") or [])]
            if not command.strip() or _has_shell_metacharacters(command):
                continue
            if any(_has_shell_metacharacters(part) for part in args):
                continue
            registry.register(
                event,
                ShellHook(
                    str(item.get("name") or f"{event}:{command}"),
                    [command, *args],
                    cwd,
                    int(item.get("timeout_sec") or 15),
                ),
            )
        return registry


def _has_shell_metacharacters(value: str) -> bool:
    return any(char in SHELL_METACHARACTERS for char in value)
