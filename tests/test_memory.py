from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from spark.config import MemoryConfig, SparkConfig
from spark.memory.extractor import Candidate, Op
from spark.memory.service import MemoryService
from spark.memory.store import MemoryStore, cosine, pack_vector, tokenize


def make_store(tmp_path: Path, **kw) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db", MemoryConfig(**kw))


def test_tokenize_mixed_language():
    tokens = tokenize("用 pnpm 安装依赖 install")
    assert "pnpm" in tokens
    assert "安装" in tokens


def test_add_and_keyword_search(tmp_path):
    store = make_store(tmp_path)
    store.add("本项目使用 pnpm 作为包管理器", type="project", importance=8)
    store.add("用户偏好中文回复", type="preference", importance=9)
    store.add("部署目录是 /srv/app", type="project", importance=5)
    hits = store.search("包管理器用什么？pnpm", top_k=2)
    assert hits
    assert hits[0][0].content.startswith("本项目使用 pnpm")


def test_access_boost_and_touch(tmp_path):
    store = make_store(tmp_path)
    mid = store.add("记住这个事实")
    row = store.get(mid)
    assert row.access_count == 0
    store.touch_access(mid)
    assert store.get(mid).access_count == 1


def test_update_and_archive(tmp_path):
    store = make_store(tmp_path)
    mid = store.add("旧内容", type="general")
    assert store.get(mid).version == 1
    store.update_content(mid, "新内容", importance=9)
    row = store.get(mid)
    assert row.content == "新内容"
    assert row.importance == 9
    assert row.version == 2
    store.archive(mid)
    assert store.get(mid).status == "archived"
    assert store.get(mid).version == 3
    assert store.all_active() == []


def test_capacity_eviction(tmp_path):
    store = make_store(tmp_path, capacity=3)
    for i in range(5):
        store.add(f"memory number {i} filler", importance=float(i + 1))
    store.enforce_capacity()
    active = store.all_active()
    assert len(active) <= 3
    kept_contents = {r.content for r in active}
    assert "memory number 4 filler" in kept_contents


def test_embedding_cosine_path(tmp_path):
    store = make_store(tmp_path)
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [0.9, 0.1, 0.0]
    vec_c = [0.0, 1.0, 0.0]
    store.add("alpha fact", embedding=vec_a)
    store.add("beta fact", embedding=vec_c)
    hits = store.search("query", top_k=2, query_embedding=vec_b)
    assert hits[0][0].content == "alpha fact"
    assert cosine(vec_a, vec_b) > cosine(vec_a, vec_c)


def test_round_trip_blob(tmp_path):
    store = make_store(tmp_path)
    mid = store.add("with embedding", embedding=[0.5, 0.25, -1.0])
    row = store.get(mid)
    assert row.embedding == [0.5, 0.25, -1.0]


def test_stats(tmp_path):
    store = make_store(tmp_path)
    store.add("one", type="project")
    store.add("two", type="preference")
    store.archive(store.add("three"))
    stats = store.stats()
    assert stats["active"] == 2
    assert stats["archived"] == 1
    assert stats["by_type"]["project"] == 1


def test_build_messages_memory_block(tmp_path):
    from spark.core.context import build_messages

    cfg = SparkConfig()
    memory_block = "<long_term_memory>\n- [project] use pnpm\n</long_term_memory>"
    messages = build_messages(
        workdir=tmp_path, cfg=cfg, history=[], memory_block=memory_block
    )
    contents = [m.content for m in messages]
    assert any("long_term_memory" in c for c in contents)


def _memory_service(tmp_path: Path, capacity: int = 500) -> MemoryService:
    cfg = SparkConfig(memory=MemoryConfig(capacity=capacity))
    cfg.provider.name = "openai_compat"
    cfg.provider.base_url = "http://memory.test/v1"
    cfg.provider.api_key = "test"
    store = MemoryStore(tmp_path / "memory.db", cfg.memory)
    return MemoryService(store, cfg)


def test_record_turn_version_conflict_falls_back_to_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _memory_service(tmp_path)
    memory_id = service.store.add("old fact", type="project", importance=8)
    candidate = Candidate(content="new fact", type="project", importance=8)

    async def extract_facts(**kwargs) -> list[Candidate]:
        return [candidate]

    async def resolve_operations(**kwargs) -> list[Op]:
        other_store = MemoryStore(service.store.db_path, service.cfg.memory)
        other_store.archive(memory_id)
        other_store.close()
        return [
            Op(
                action="UPDATE",
                target_id=memory_id,
                content="new fact",
                type="project",
                importance=8,
            )
        ]

    monkeypatch.setattr("spark.memory.service.extract_facts", extract_facts)
    monkeypatch.setattr("spark.memory.service.resolve_operations", resolve_operations)
    monkeypatch.setattr(service, "_embed", lambda texts: None)

    result = service.record_turn(
        user_text="update the fact",
        assistant_text="",
        tool_summary="",
        session_id="session-1",
    )

    assert service.store.get(memory_id).status == "archived"
    assert [op["action"] for op in result["ops"]] == ["ADD"]
    assert result["conflicts"][0]["id"] == memory_id
    assert result["conflicts"][0]["fallback_id"] == result["ops"][0]["id"]
    assert service.store.get(result["ops"][0]["id"]).content == "new fact"


def test_record_turn_batch_failure_rolls_back_every_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _memory_service(tmp_path, capacity=1)
    memory_id = service.store.add("old fact", type="project", importance=9)
    candidates = [
        Candidate(content="new unrelated fact", type="project", importance=1),
        Candidate(content="old fact", type="project", importance=9),
    ]

    async def extract_facts(**kwargs) -> list[Candidate]:
        return candidates

    async def resolve_operations(**kwargs) -> list[Op]:
        return [
            Op(
                action="ADD",
                content="new unrelated fact",
                type="project",
                importance=1,
            ),
            Op(
                action="UPDATE",
                target_id=memory_id,
                content="replacement fact",
                type="project",
                importance=9,
            ),
        ]

    original_capacity = service.store.enforce_capacity

    def fail_after_capacity(**kwargs) -> int:
        original_capacity(**kwargs)
        raise RuntimeError("forced batch failure")

    monkeypatch.setattr("spark.memory.service.extract_facts", extract_facts)
    monkeypatch.setattr("spark.memory.service.resolve_operations", resolve_operations)
    monkeypatch.setattr(service, "_embed", lambda texts: None)
    monkeypatch.setattr(service.store, "enforce_capacity", fail_after_capacity)

    with pytest.raises(RuntimeError, match="forced batch failure"):
        service.record_turn(
            user_text="change the facts",
            assistant_text="",
            tool_summary="",
            session_id="session-1",
        )

    rows = service.store.list_all()
    assert len(rows) == 1
    assert rows[0].id == memory_id
    assert rows[0].content == "old fact"
    assert rows[0].status == "active"


def test_old_database_without_version_is_migrated(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL DEFAULT 'general',
                content TEXT NOT NULL,
                keywords TEXT NOT NULL DEFAULT '',
                embedding BLOB,
                embedding_model TEXT,
                importance REAL NOT NULL DEFAULT 5.0,
                status TEXT NOT NULL DEFAULT 'active',
                source_session TEXT,
                access_count INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_accessed INTEGER NOT NULL
            );
            INSERT INTO memories
                (type, content, keywords, importance, status, access_count, created_at, updated_at, last_accessed)
            VALUES
                ('project', 'legacy fact', 'legacy fact', 7, 'active', 0, 1, 1, 1);
            """
        )

    store = MemoryStore(db_path, MemoryConfig())
    row = store.get(1)

    assert row.content == "legacy fact"
    assert row.version == 1
