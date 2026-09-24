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


def _usage_loop(tmp_path: Path):
    from spark.core.loop import AgentLoop
    from spark.providers.mock import MockProvider
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "usage.db")
    session_id = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=MockProvider(),
        registry=ToolRegistry(ctx),
        store=store,
        session_id=session_id,
    )


def test_usage_repeated_calls_stay_bounded_for_large_history(tmp_path: Path) -> None:
    from spark.core.context import history_token_usage
    from spark.models import ChatMessage

    loop = _usage_loop(tmp_path)
    loop.history = [ChatMessage(role="user", content="x" * 1_000_000)]
    started = time.perf_counter()
    usages = [loop._usage() for _ in range(10)]
    elapsed = time.perf_counter() - started
    expected = history_token_usage(
        cfg=loop.cfg,
        history=loop.history,
        workdir=loop.workdir,
        tool_overhead_tokens=loop._tool_overhead(),
    )
    assert usages[-1] == expected
    assert set(usages[-1]) == {"used", "limit", "percent"}
    assert elapsed < 0.5, f"ten usage calculations took {elapsed:.3f}s"
    loop.store.close()


def test_usage_invalidates_prompt_and_schema_caches(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("short", encoding="utf-8")
    loop = _usage_loop(tmp_path)
    before_prompt = loop._usage()["used"]
    agents.write_text("新" * 2000, encoding="utf-8")
    after_prompt = loop._usage()["used"]
    assert after_prompt > before_prompt
    loop.registry.add_mcp_schema(
        {
            "type": "function",
            "function": {
                "name": "mcp__test__large",
                "description": "x" * 4000,
                "parameters": {"type": "object", "properties": {}},
            },
        }
    )
    after_schema = loop._usage()["used"]
    assert after_schema > after_prompt
    loop.store.close()


def test_incremental_snapshot_archives_only_changed_files_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tarfile

    from spark.core import checkpoints

    work = tmp_path / "work"
    work.mkdir()
    for index in range(50):
        (work / f"f{index}.txt").write_text(f"v1-{index}", encoding="utf-8")
    skipped = work / "node_modules"
    skipped.mkdir()
    (skipped / "ignored.txt").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(checkpoints, "SNAPSHOT_DIR", tmp_path / "snaps")
    baseline = checkpoints.snapshot_workdir(work)
    (work / "f17.txt").write_text("changed-and-longer", encoding="utf-8")
    (work / "f3.txt").unlink()
    (work / "new.txt").write_text("new snapshot value", encoding="utf-8")
    delta = checkpoints.snapshot_workdir(work)
    with tarfile.open(
        checkpoints._snapshot_dir() / f"{delta}.tar.gz", "r:gz"
    ) as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
    assert names == {"f17.txt", "new.txt"}
    (work / "f17.txt").write_text("mutated after snapshot", encoding="utf-8")
    (work / "f0.txt").write_text("mutated too", encoding="utf-8")
    (work / "new.txt").write_text("mutated new", encoding="utf-8")
    (work / "after.txt").write_text("remove me", encoding="utf-8")
    report = checkpoints.restore_workdir(work, delta)
    assert (work / "f17.txt").read_text(encoding="utf-8") == "changed-and-longer"
    assert (work / "f0.txt").read_text(encoding="utf-8") == "v1-0"
    assert (work / "new.txt").read_text(encoding="utf-8") == "new snapshot value"
    assert not (work / "f3.txt").exists()
    assert not (work / "after.txt").exists()
    assert (skipped / "ignored.txt").read_text(encoding="utf-8") == "ignored"
    assert report["restored_files"] >= 2
    assert report["removed_new_files"] == 2
    assert baseline != delta


def test_large_unchanged_directory_snapshot_has_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spark.core import checkpoints

    work = tmp_path / "work"
    work.mkdir()
    for index in range(5000):
        (work / f"f{index}.txt").write_text(str(index), encoding="utf-8")
    monkeypatch.setattr(checkpoints, "SNAPSHOT_DIR", tmp_path / "snaps")
    checkpoints.snapshot_workdir(work)
    started = time.perf_counter()
    snapshot_id = checkpoints.snapshot_workdir(work)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"unchanged snapshot took {elapsed:.3f}s"
    assert snapshot_id
