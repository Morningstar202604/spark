"""Frontend source invariants that a headless screenshot pass already caught once.

These are cheap "won't regress silently" checks for bugs that TS cannot see and
that only appear at runtime in a real browser (React #310 white screen, hidden
buttons still occupying the mobile header).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src"


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def test_settings_hooks_come_before_the_cfg_early_return() -> None:
    """Hooks after `if (!cfg) return` change hook count → React #310 white screen."""
    src = _read("components/SettingsModal.tsx")
    early = src.find("if (!cfg) {")
    assert early != -1, "expected the loading early-return in SettingsModal"
    for hook in ("useState(() => notificationsEnabled())", "useState(() => notificationPermission())"):
        pos = src.find(hook)
        assert pos != -1, f"missing {hook}"
        assert pos < early, f"{hook} must be declared before the `if (!cfg)` early return"


def test_header_responsive_hiding_wraps_icon_buttons() -> None:
    """`hidden` on IconButton loses to its own `inline-flex` (Tailwind order)."""
    src = _read("components/Header.tsx")
    assert 'className="hidden sm:inline-flex"' in src or 'className="hidden md:inline-flex"' in src
    for line in src.splitlines():
        if "<IconButton" in line and "hidden " in line:
            raise AssertionError(f"responsive `hidden` must wrap IconButton, not be passed into it: {line.strip()}")


def test_shell_card_escapes_output() -> None:
    """`!cmd` output is rendered as text, never as HTML."""
    src = _read("components/MessageItem.tsx")
    start = src.find("const ShellMessage")
    assert start != -1
    body = src[start : src.find("export default function MessageItem", start)]
    assert "{shell.stdout}" in body
    assert "dangerouslySetInnerHTML" not in body


def test_composer_routes_bang_commands_to_shell() -> None:
    src = _read("components/Composer.tsx")
    assert 'input.startsWith("!")' in src
    assert "runShell(input.slice(1).trim())" in src


def test_share_command_is_in_the_palette() -> None:
    src = _read("components/CommandPalette.tsx")
    assert "downloadShareHtml" in src
    assert 'exportSession("html")' in src
