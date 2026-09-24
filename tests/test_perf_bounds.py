from __future__ import annotations

import time
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.search import GrepArgs, GlobArgs, grep_tool, glob_tool


def make_repo(tmp_path: Path, files: int = 60) -> Path:
    for i in range(files):
        (tmp_path / f"f{i}.py").write_text(f"value = {i}\n", encoding="utf-8")
    return tmp_path


def sb_for(path: Path) -> WorkdirSandbox:
    return WorkdirSandbox(path, SparkConfig())


def test_grep_stops_at_file_budget(tmp_path: Path) -> None:
    make_repo(tmp_path, 200)
    sb = sb_for(tmp_path)
    result = grep_tool(
        sb, GrepArgs(pattern="nomatch_xyz", max_results=10, max_files=25)
    )
    assert result.ok
    payload = result.payload
    assert payload.get("truncated") is True
    assert "budget" in str(payload.get("note", "")).lower()


def test_grep_respects_file_budget_limit(tmp_path: Path) -> None:
    make_repo(tmp_path, 120)
    sb = sb_for(tmp_path)
    result = grep_tool(sb, GrepArgs(pattern="nomatch_xyz", max_files=10))
    assert result.ok
    assert result.payload.get("scanned_files", 0) <= 11


def test_glob_stops_at_file_budget(tmp_path: Path) -> None:
    make_repo(tmp_path, 200)
    sb = sb_for(tmp_path)
    result = glob_tool(sb, GlobArgs(pattern="*.py", max_results=500, max_files=20))
    assert result.ok
    assert result.payload.get("truncated") is True


def test_grep_deadline_is_enforced(tmp_path: Path) -> None:
    make_repo(tmp_path, 400)
    sb = sb_for(tmp_path)
    started = time.perf_counter()
    result = grep_tool(sb, GrepArgs(pattern="nomatch_xyz", deadline_sec=0.05))
    elapsed = time.perf_counter() - started
    assert result.ok
    assert elapsed < 5.0


def test_search_budgets_have_hard_caps() -> None:
    from spark.tools import search

    assert 0 < search.MAX_GREP_FILES <= 20000
    assert 0 < search.MAX_GLOB_FILES <= 50000
    assert 0 < search.MAX_SEARCH_BYTES <= 512 * 1024 * 1024
    assert 0 < search.MAX_SEARCH_SECONDS <= 120


def test_grep_reads_only_bounded_bytes(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("needle\n" + ("x" * 5_000_000), encoding="utf-8")
    (tmp_path / "small.txt").write_text("needle\n", encoding="utf-8")
    sb = sb_for(tmp_path)
    result = grep_tool(sb, GrepArgs(pattern="needle"))
    assert result.ok
    assert result.payload["count"] >= 1


def test_grep_reports_scanned_files(tmp_path: Path) -> None:
    make_repo(tmp_path, 5)
    sb = sb_for(tmp_path)
    result = grep_tool(sb, GrepArgs(pattern="value"))
    assert result.ok
    assert result.payload["scanned_files"] == 5


# ---------- SQLite durability / index ----------


def test_store_enables_wal_and_indexes(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    try:
        journal = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(journal).lower() == "wal"
        indexes = {
            row[0]
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert any("messages" in name for name in indexes)
    finally:
        store.close()


def test_messages_load_uses_index_range_scan(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    try:
        a = store.create_session(tmp_path, "m", title="a")
        b = store.create_session(tmp_path, "m", title="b")
        for _ in range(20):
            store.append_message(
                a,
                __import__("spark.models", fromlist=["ChatMessage"]).ChatMessage(
                    role="user", content="x"
                ),
            )
            store.append_message(
                b,
                __import__("spark.models", fromlist=["ChatMessage"]).ChatMessage(
                    role="user", content="y"
                ),
            )
        plan = " ".join(
            " ".join(str(cell) for cell in row)
            for row in store._conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM messages WHERE session_id=? ORDER BY id",
                (a,),
            )
        )
        assert "USING INDEX" in plan.upper()
    finally:
        store.close()


def test_append_message_is_atomic_with_touch(tmp_path: Path) -> None:
    from spark.models import ChatMessage

    store = SessionStore(tmp_path / "s.db")
    try:
        sid = store.create_session(tmp_path, "m", title="before")
        before = store.get_session(sid)["updated_at"]
        time.sleep(1.05)
        store.append_message(sid, ChatMessage(role="user", content="hello"))
        after = store.get_session(sid)["updated_at"]
        assert after > before, "session metadata must advance with the message"
        assert len(store.load_messages(sid)) == 1
    finally:
        store.close()


# ---------- memory clustering complexity ----------


def test_similarity_clusters_handles_large_sets_quickly(tmp_path: Path) -> None:
    from spark.config import MemoryConfig
    from spark.memory.service import MemoryService
    from spark.memory.store import MemoryStore

    cfg = MemoryConfig()
    store = MemoryStore(tmp_path / "m.db", cfg)
    try:
        for i in range(200):
            store.add(
                content=f"memory entry number {i} about module alpha beta",
                type="fact",
                importance=5.0,
            )
        svc = MemoryService(store, cfg)
        started = time.perf_counter()
        clusters = svc._similarity_clusters()
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0, f"clustering took {elapsed:.2f}s"
        assert isinstance(clusters, list)
    finally:
        store.close()
