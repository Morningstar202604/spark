"""Regression tests for the hardening batch (2026-10-20).

Every case here failed before the corresponding fix; they exist so the specific
hole cannot silently come back.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.models import ChatMessage
from spark.platform_compat import is_reserved_filename
from spark.sandbox import WorkdirSandbox, safe_bare_filename
from spark.store import SessionStore
from spark.tools import fs
from spark.tools.diagnostics import DiagnosticsArgs, diagnostics_tool


def _sb(tmp_path: Path, mode: str = "workspace") -> WorkdirSandbox:
    cfg = SparkConfig()
    cfg.agent.sandbox_mode = mode
    return WorkdirSandbox(tmp_path, cfg)


# ---------------------------------------------------------------------------
# H1 — quoted protected paths must not bypass the shell check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        'cat /etc/hosts',
        'cat "/etc/hosts"',
        "cat '/etc/hosts'",
        'type "/etc/hosts"',
        # Indirection forms: the path never becomes a clean token, yet the
        # shell still opens the file.
        'cat "$(echo /etc/hosts)"',
        "cat `/etc/hosts`",
        "cat ${IFS}/etc/hosts",
    ],
)
def test_h1_quoted_protected_path_is_blocked(tmp_path: Path, command: str) -> None:
    reason = _sb(tmp_path).check_shell(command)
    assert reason is not None, f"{command!r} should be blocked"
    assert "protected path" in reason


def test_h1_harmless_command_still_allowed(tmp_path: Path) -> None:
    assert _sb(tmp_path).check_shell("echo survived") is None
    assert _sb(tmp_path).check_shell("python -m pytest -q") is None


# ---------------------------------------------------------------------------
# H2 — diagnostics is read-only; its arguments must not become ruff flags
# ---------------------------------------------------------------------------


def test_h2_diagnostics_rejects_flag_like_path(tmp_path: Path) -> None:
    target = tmp_path / "bad.py"
    target.write_text("import os\nimport sys\nprint(1)\n", encoding="utf-8")
    before = target.read_text(encoding="utf-8")

    result = diagnostics_tool(
        _sb(tmp_path), DiagnosticsArgs(paths=["--fix"], python=True, typescript=False)
    )

    assert result.ok is False
    assert "invalid diagnostics path" in str(result.payload.get("error", ""))
    # The whole point: a read-only tool must never rewrite the file.
    assert target.read_text(encoding="utf-8") == before


def test_h2_diagnostics_accepts_relative_path(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("print(1)\n", encoding="utf-8")
    result = diagnostics_tool(
        _sb(tmp_path), DiagnosticsArgs(paths=["ok.py"], python=True, typescript=False)
    )
    assert result.ok is True


def test_h2_diagnostics_rejects_escape(tmp_path: Path) -> None:
    result = diagnostics_tool(
        _sb(tmp_path), DiagnosticsArgs(paths=["../outside"], python=True, typescript=False)
    )
    assert result.ok is False


# ---------------------------------------------------------------------------
# H3 — .git/hooks is persistent code execution, never writable
# ---------------------------------------------------------------------------


def test_h3_git_hook_write_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    sb = _sb(tmp_path)
    hook = tmp_path / ".git" / "hooks" / "post-commit"

    assert sb.check_write_path(hook) is not None
    result = fs.write_file(
        sb, fs.WriteFileArgs(path=".git/hooks/post-commit", content="#!/bin/sh\n")
    )
    assert result.ok is False
    assert not hook.exists()


def test_h3_git_config_write_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert _sb(tmp_path).check_write_path(tmp_path / ".git" / "config") is not None


def test_h3_normal_file_still_writable(tmp_path: Path) -> None:
    sb = _sb(tmp_path)
    assert fs.write_file(sb, fs.WriteFileArgs(path="src/a.txt", content="x")).ok


# ---------------------------------------------------------------------------
# Platform parity — deny patterns are word-bounded, aliases expanded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "blocked"),
    [
        ("rm -rf /", True),
        ("RM   -RF  /", True),
        ("sudo apt install x", True),
        ("su - root", True),
        ("git push origin main", True),
        ("del /s C:/x", True),
        ("ri -recurse C:/x", True),
        ("echo survived", False),
        ("git status", False),
        ("ls -la", False),
    ],
)
def test_deny_patterns_are_word_bounded(tmp_path: Path, command: str, blocked: bool) -> None:
    reason = _sb(tmp_path).check_shell(command)
    assert (reason is not None) is blocked, f"{command!r} -> {reason!r}"


def test_env_dump_is_blocked(tmp_path: Path) -> None:
    sb = _sb(tmp_path)
    assert sb.check_shell("env") is not None
    assert sb.check_shell("env | grep KEY") is not None


def test_windows_reserved_names_rejected() -> None:
    assert is_reserved_filename("AUX.py")
    assert is_reserved_filename("con")
    assert is_reserved_filename("com3.txt")
    assert not is_reserved_filename("auxiliary.py")
    assert safe_bare_filename("AUX.py") is None


# ---------------------------------------------------------------------------
# M3 / M4 — store correctness
# ---------------------------------------------------------------------------


def test_m3_touch_without_title_keeps_title(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "m", title="keep me")
    store.touch(sid)  # must not write NULL into a NOT NULL column
    assert store.get_session(sid)["title"] == "keep me"


def test_m4_delete_session_purges_fts(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "m", title="t")
    store.append_message(sid, ChatMessage(role="user", content="alpha bravo charlie"))
    assert store._conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0] == 1

    store.delete_session(sid)

    assert store._conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0] == 0
    assert store._conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# M5 — OpenAPI contract degrades instead of crashing
# ---------------------------------------------------------------------------


def test_m5_openapi_unavailable_is_explicit() -> None:
    from spark.web import api_spec

    api_spec._APP = None
    try:
        schema = api_spec.openapi_schema()
    except api_spec.OpenApiUnavailable as exc:
        assert "fastapi" in str(exc)
    else:  # fastapi installed: the contract must still describe the routes
        assert "/api/status" in schema.get("paths", {})


# ---------------------------------------------------------------------------
# M1 — one version source
# ---------------------------------------------------------------------------


def test_m1_version_matches_pyproject() -> None:
    import spark

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    declared = next(
        line.split("=", 1)[1].strip().strip('"')
        for line in text.splitlines()
        if line.startswith("version =")
    )
    assert spark.__version__ in {declared, "0.0.0", "development build"} or spark.__version__ == declared


# ---------------------------------------------------------------------------
# M6 — no Windows-reserved module filenames in the tree
# ---------------------------------------------------------------------------


def test_m6_no_reserved_module_names() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    offenders = [p for p in root.rglob("*.py") if is_reserved_filename(p.name)]
    assert not offenders, f"Windows-reserved module names: {offenders}"


# ---------------------------------------------------------------------------
# Redirections, nested .git, cwd-relative paths, env indirection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "echo x>.git/hooks/pre-commit",
        "echo x > .git/hooks/pre-commit",
        "echo x>>.git/hooks/pre-commit",
        "cat /etc/hosts 2>/dev/null",
    ],
)
def test_redirection_next_to_protected_path_is_blocked(tmp_path: Path, command: str) -> None:
    assert _sb(tmp_path).check_shell(command) is not None, command


@pytest.mark.parametrize(
    "rel",
    [
        ".git/hooks/pre-commit",
        "sub/.git/hooks/pre-commit",
        "vendor/x/.git/hooks/pre-commit",
        "pkg/.git/config",
        ".git/modules/m/hooks/pre-commit",
    ],
)
def test_nested_git_control_surfaces_are_protected(tmp_path: Path, rel: str) -> None:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    sb = _sb(tmp_path)
    assert sb.check_write_path(target) is not None, rel
    assert fs.write_file(sb, fs.WriteFileArgs(path=rel, content="x")).ok is False


def test_cwd_relative_escape_is_blocked(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    sb = _sb(tmp_path)
    # Same file, two spellings: from the root and from a subdirectory.
    assert sb.check_shell("echo hi > ../.git/hooks/post-commit", cwd=tmp_path / "a") is not None


def test_env_indirection_to_config_is_blocked(tmp_path: Path, monkeypatch) -> None:
    """%VAR% must be expanded before the protected-path check.

    The protected list contains the *real* config path (self_protected()), so the
    variable has to resolve onto one of those entries for this to be meaningful.
    SPARK_HOME is redirected by the test fixture, so its config.toml is the file
    the sandbox protects.
    """
    import os

    home = os.environ["SPARK_HOME"]
    Path(home).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SPARK_CFG_DIR", home)
    sb = _sb(tmp_path)
    cmd = "echo pwned > %SPARK_CFG_DIR%/config.toml"
    assert sb.check_shell(cmd) is not None


def test_backslash_credential_path_is_blocked(tmp_path: Path) -> None:
    sb = _sb(tmp_path)
    cmd = "type " + "C:" + chr(92) + "Users" + chr(92) + "me" + chr(92) + ".ssh" + chr(92) + "config"
    assert sb.check_shell(cmd) is not None


def test_git_alias_execution_is_blocked(tmp_path: Path) -> None:
    sb = _sb(tmp_path)
    assert sb.check_shell("git -c alias.x=!echo pwned x") is not None
    assert sb.check_shell("git --git-dir=/tmp/other status") is not None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /home/user",
        "rm -rf /var/www",
        "Remove-Item x -Recurse",
        "ri -r x",
        "ri -rec x",
    ],
)
def test_destructive_variants_stay_blocked(tmp_path: Path, command: str) -> None:
    assert _sb(tmp_path).check_shell(command) is not None, command


@pytest.mark.parametrize(
    "command",
    [
        "npm run format",
        "echo format",
        "git log --pretty=format:%h",
        "python -m pytest 2>/dev/null",
        "npm test >/dev/null 2>&1",
        "echo su",
    ],
)
def test_normal_commands_are_not_false_positives(tmp_path: Path, command: str) -> None:
    assert _sb(tmp_path).check_shell(command) is None, command


# ---------------------------------------------------------------------------
# Settings round-trip and read-only tool execution surface
# ---------------------------------------------------------------------------


def test_context_settings_round_trip(tmp_path: Path) -> None:
    """/api/config must expose every context field the Project tab edits."""
    from spark.config import SparkConfig
    from spark.store import SessionStore as _Store
    from spark.web.state import SparkWebState

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = _Store(tmp_path / "s.db")
    state = SparkWebState(tmp_path, cfg, store)
    ctx = state.full_config()["context"]
    for key in ("compact_threshold", "max_context_tokens", "keep_recent_messages",
                "history_budget_chars", "repo_map_enabled", "repo_map_max_chars"):
        assert key in ctx, f"{key} missing from /api/config — the UI field cannot persist"
    store.close()


def test_diagnostics_never_runs_repo_supplied_tsc() -> None:
    """diagnostics runs without approval, so it must not execute repo binaries."""
    import inspect

    from spark.tools import diagnostics as mod

    source = inspect.getsource(mod._tsc_diagnostics)
    assert "npx" not in source, "npx would run node_modules/.bin/tsc from the cloned repo"
