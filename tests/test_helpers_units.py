"""Unit coverage for the small pure/near-pure helpers that had 0-40% coverage.

Audit F-003 singled these out as the cheapest large wins: they need no network,
no subprocess and no server, so they can be asserted precisely — which is also
why a real assertion is possible instead of "does not raise".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from spark.memory.extractor import redact_secrets
from spark.platform_compat import (
    default_home_dir,
    is_reserved_filename,
    normalize_newlines,
    popen_kwargs,
    pty_supported,
    shell_executable,
    terminal_size,
    which_pip,
    which_python,
)
from spark.providers.auxiliary import _extract_content

# ---------------------------------------------------------------------------
# auxiliary._extract_content — the shape differences between providers
# ---------------------------------------------------------------------------


def _resp(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_extract_content_plain_string() -> None:
    assert _extract_content(_resp("hello")) == "hello"


def test_extract_content_block_list() -> None:
    blocks = [
        {"type": "text", "text": "part1 "},
        {"type": "text", "content": "part2"},
        "raw",
    ]
    assert _extract_content(_resp(blocks)) == "part1 part2raw"


def test_extract_content_handles_missing_everything() -> None:
    assert _extract_content(SimpleNamespace(choices=[])) == ""
    assert _extract_content(SimpleNamespace()) == ""
    assert _extract_content(_resp(None)) == ""


def test_extract_content_ignores_unknown_block_types() -> None:
    assert _extract_content(_resp([{"type": "image"}, {"type": "text", "text": "ok"}])) == "ok"


# ---------------------------------------------------------------------------
# platform_compat
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("CON", True),
        ("con.txt", True),
        ("PRN", True),
        ("AUX", True),
        ("NUL", True),
        ("COM1", True),
        ("LPT9", True),
        ("console.py", False),
        ("auxiliary.py", False),
        ("COM10", False),
    ],
)
def test_is_reserved_filename(name: str, expected: bool) -> None:
    assert is_reserved_filename(name) is expected


def test_normalize_newlines_collapses_crlf() -> None:
    assert normalize_newlines("a\r\nb\rc\n") == "a\nb\nc\n"


def test_default_home_dir_respects_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "custom"))
    assert default_home_dir() == tmp_path / "custom"


def test_popen_kwargs_platform_shape() -> None:
    kwargs = popen_kwargs(new_group=True)
    assert "creationflags" in kwargs or "start_new_session" in kwargs
    assert isinstance(kwargs, dict)


def test_terminal_size_falls_back() -> None:
    rows, cols = terminal_size()
    assert rows >= 1 and cols >= 1


def test_pty_supported_is_boolean() -> None:
    assert isinstance(pty_supported(), bool)


def test_which_python_and_pip_return_runnable_names() -> None:
    """These return an executable *name*, not a path — assert they run."""
    import shutil
    import subprocess

    py = which_python()
    assert py in {"python", "python3", "py"} or py is None
    if py:
        out = subprocess.run([py, "--version"], capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, out.stderr[:200]
    pip = which_pip()
    assert pip in {"pip", "pip3"} or pip is None
    if pip:
        assert shutil.which(pip)


def test_shell_executable_is_a_string_or_none() -> None:
    shell = shell_executable()
    assert shell is None or isinstance(shell, str)


# ---------------------------------------------------------------------------
# extractor.redact_secrets — must not leak credentials into stored memories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,secret",
    [
        ("key sk-abcdef1234567890abcdef", "sk-abcdef1234567890abcdef"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9"),
        ("api_key=supersecretvalue123456", "supersecretvalue123456"),
        ("password: hunter2hunter2", "hunter2hunter2"),
    ],
)
def test_redact_secrets_removes_credentials(text: str, secret: str) -> None:
    out = redact_secrets(text)
    assert secret not in out
    assert "REDACTED" in out


def test_redact_secrets_leaves_plain_text_alone() -> None:
    text = "项目使用 pnpm，构建命令是 npm run build"
    assert redact_secrets(text) == text


def test_redact_secrets_empty() -> None:
    assert redact_secrets("") == ""


# ---------------------------------------------------------------------------
# memory store / service: the paths reachable without an embedding provider
# ---------------------------------------------------------------------------


def test_memory_store_ephemeral_lifecycle() -> None:
    from spark.config import MemoryConfig
    from spark.memory.store import MemoryStore

    store = MemoryStore(":memory:", MemoryConfig())
    try:
        assert store.stats()["active"] == 0
        mem_id = store.add(content="偏好 pnpm", type="preference", importance=7)
        assert mem_id > 0
        assert store.get(mem_id) is not None
        store.touch_access(mem_id)
        assert store.get(mem_id).access_count == 1
        store.delete(mem_id)
        assert store.get(mem_id) is None
    finally:
        store.close()


def test_memory_service_rejects_empty_content() -> None:
    from spark.config import MemoryConfig, SparkConfig
    from spark.memory.service import MemoryService
    from spark.memory.store import MemoryStore

    store = MemoryStore(":memory:", MemoryConfig())
    try:
        svc = MemoryService(store, SparkConfig())
        with pytest.raises(ValueError):
            svc.add_manual("", "general", 5)
        # A non-empty add goes through and lands in the store.
        mem_id = svc.add_manual("记住用 pnpm", "preference", 7)
        assert store.get(mem_id).content == "记住用 pnpm"
    finally:
        store.close()


def test_memory_search_returns_ranked_rows() -> None:
    from spark.config import MemoryConfig
    from spark.memory.store import MemoryStore

    store = MemoryStore(":memory:", MemoryConfig())
    try:
        store.add(content="用户喜欢用 pnpm 安装依赖", type="preference", importance=8)
        hits = store.search("pnpm", top_k=5)
        assert hits
        assert "pnpm" in hits[0][0].content
    finally:
        store.close()


# ---------------------------------------------------------------------------
# PtyManager — the bookkeeping layer is platform independent, so it can be
# exercised even where a real PTY does not exist (Windows).
# ---------------------------------------------------------------------------


def test_pty_manager_rejects_unknown_tabs() -> None:
    from spark.pty import PtyManager

    mgr = PtyManager()
    assert mgr.get("s", "t") is None
    assert mgr.write("s", "t", "x") is False
    assert mgr.resize("s", "t", 10, 10) is False
    assert mgr.close_tab("s", "t") is False
    assert mgr.list_tabs("s") == []


def test_pty_manager_close_all_is_idempotent() -> None:
    from spark.pty import PtyManager

    mgr = PtyManager()
    mgr.close_all()
    mgr.close_all()
    assert mgr.list_tabs("anything") == []


def test_pty_manager_creates_or_reports_import_error(tmp_path: Path) -> None:
    """On Windows this raises ImportError (no ptyprocess); on POSIX it starts."""
    from spark.pty import PtyManager

    mgr = PtyManager()
    try:
        sess = mgr.get_or_create("s", "t1", tmp_path, rows=30, cols=90)
    except ImportError as exc:
        assert "ptyprocess" in str(exc) or "伪终端" in str(exc)
        return
    try:
        assert sess.tab_id == "t1"
        assert sess.size == (30, 90)
        tabs = mgr.list_tabs("s")
        assert any(t["tab_id"] == "t1" for t in tabs)
        # Same key returns the live session rather than spawning a second shell.
        assert mgr.get_or_create("s", "t1", tmp_path) is sess
    finally:
        mgr.close_all()
        assert mgr.list_tabs("s") == []


# ---------------------------------------------------------------------------
# extractor parsing / resolution helpers
# ---------------------------------------------------------------------------


def test_valid_type_coerces_unknown_to_general() -> None:
    from spark.memory.extractor import _valid_type

    assert _valid_type("preference") == "preference"
    assert _valid_type("project") == "project"
    assert _valid_type("lesson") == "lesson"
    # Types are case-sensitive by design (the prompt constrains them), so an
    # unexpected value must fall back rather than reach the store.
    assert _valid_type("PROJECT") == "general"
    assert _valid_type("nonsense") == "general"
    assert _valid_type("") == "general"


def test_parse_json_object_strips_fences_and_prose() -> None:
    from spark.memory.llm import parse_json_object

    assert parse_json_object('prefix {"a": 1} suffix') == {"a": 1}
    assert parse_json_object("```json" + chr(10) + '{"a": 2}' + chr(10) + "```") == {"a": 2}
    assert parse_json_object("{broken") is None


def test_candidate_and_op_models_validate() -> None:
    from spark.memory.extractor import Candidate, Op

    cand = Candidate(content="用 pnpm", type="preference", importance=7)
    assert cand.type == "preference"
    op = Op(action="ADD", content="用 pnpm", type="preference", importance=7)
    assert op.target_id is None
    upd = Op(action="UPDATE", content="改为 npm", type="preference", importance=5, target_id=12)
    assert upd.target_id == 12
