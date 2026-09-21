from __future__ import annotations

from pathlib import Path

from spark.config import MemoryConfig, SparkConfig
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
    store.update_content(mid, "新内容", importance=9)
    row = store.get(mid)
    assert row.content == "新内容"
    assert row.importance == 9
    store.archive(mid)
    assert store.get(mid).status == "archived"
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
    messages = build_messages(workdir=tmp_path, cfg=cfg, history=[], memory_block=memory_block)
    contents = [m.content for m in messages]
    assert any("long_term_memory" in c for c in contents)
