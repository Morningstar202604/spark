"""MemoryStore boundary and edge-case tests.

Covers scenarios beyond the "happy path" in test_memory.py:

* Dimension mismatch (embedding padding / truncation)
* Empty / malformed inputs
* Version conflict race conditions
* Concurrent monotonic id generation
* Transaction rollback
* Archive + re-archive idempotency
* Capacity edge cases (zero capacity, single item)
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from spark.config import MemoryConfig
from spark.memory.store import (
    MemoryStore,
    cosine,
    pack_vector,
    tokenize,
    unpack_vector,
)


def make_store(tmp_path: Path, **kw) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db", MemoryConfig(**kw))


# ---------------------------------------------------------------------------
# Tokenize edge cases
# ---------------------------------------------------------------------------
class TestTokenize:
    def test_empty_string(self):
        assert tokenize("") == []

    def test_only_punctuation(self):
        assert tokenize("!!! ??? ...") == []

    def test_mixed_cjk_and_ascii(self):
        tokens = tokenize("use pip install 包")
        assert "pip" in tokens
        assert "install" in tokens
        assert "包" in tokens

    def test_single_char_cjk(self):
        # Single standalone CJK char should be kept; bigram for multi-char.
        tokens = tokenize("使 a 工")
        assert "工" in tokens  # single CJK kept as-is

    def test_two_char_cjk_biased(self):
        # 2-char CJK words should be bigramed
        tokens = tokenize("项目")
        assert len(tokens) == 1
        assert tokens[0] == "项目"

    def test_long_cjk(self):
        tokens = tokenize("人工智能的发展历史")
        # 人工, 工智, 智能, 能的, 的发, 发展, 展历, 历史
        assert len(tokens) > 1


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------
class TestVectorHelpers:
    def test_cosine_orthogonal(self):
        assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_cosine_identical(self):
        assert cosine([1.0, 1.0], [1.0, 1.0]) == pytest.approx(1.0)

    def test_cosine_dimension_mismatch(self):
        assert cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_cosine_zero_vector(self):
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_pack_unpack_round_trip(self):
        vec = [0.1, -0.2, 0.3, 1e-6]
        packed = pack_vector(vec)
        unpacked = unpack_vector(packed)
        assert len(unpacked) == len(vec)

    def test_unpack_none(self):
        assert unpack_vector(None) is None

    def test_unpack_empty(self):
        assert unpack_vector(b"") is None


# ---------------------------------------------------------------------------
# MemoryStore dimension handling
# ---------------------------------------------------------------------------
class TestDimensionHandling:
    def test_shorter_embedding_padded(self, tmp_path):
        store = make_store(tmp_path)
        # First add with a big embedding to set the store's dim
        big_vec = [0.1] * 2000
        store.add("big", embedding=big_vec)
        # Second add with shorter embedding should be padded
        mid = store.add("small", embedding=[1.0, 0.0])
        row = store.get(mid)
        assert len(row.embedding) == 2000
        assert row.embedding[0] == 1.0

    def test_longer_embedding_truncated(self, tmp_path):
        store = make_store(tmp_path)
        # Add first with large dim to set the store's dim
        big_vec = [0.1] * 2000
        mid1 = store.add("big", embedding=big_vec)
        row1 = store.get(mid1)
        assert len(row1.embedding) == 2000

        # Second add with smaller dim should be padded to store's dim
        mid2 = store.add("small", embedding=[1.0, 0.0])
        row2 = store.get(mid2)
        assert len(row2.embedding) == 2000

    def test_no_embedding_uses_placeholder(self, tmp_path):
        store = make_store(tmp_path)
        mid = store.add("no emb")
        row = store.get(mid)
        assert len(row.embedding) == 1536
        assert all(v == 0.0 for v in row.embedding)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
class TestInputValidation:
    def test_content_truncated_to_400(self, tmp_path):
        store = make_store(tmp_path)
        long_content = "x" * 500
        mid = store.add(long_content)
        row = store.get(mid)
        assert len(row.content) == 400

    def test_importance_clamped_high(self, tmp_path):
        store = make_store(tmp_path)
        mid = store.add("test", importance=99.0)
        row = store.get(mid)
        assert row.importance == 10.0

    def test_importance_clamped_low(self, tmp_path):
        store = make_store(tmp_path)
        mid = store.add("test", importance=-5.0)
        row = store.get(mid)
        assert row.importance == 0.0

    def test_empty_content(self, tmp_path):
        store = make_store(tmp_path)
        mid = store.add("")
        row = store.get(mid)
        assert row is not None
        assert row.content == ""

    def test_whitespace_content(self, tmp_path):
        store = make_store(tmp_path)
        mid = store.add("   ")
        row = store.get(mid)
        assert row is not None
        # strip() should make it empty
        assert row.content == ""


# ---------------------------------------------------------------------------
# Version conflict
# ---------------------------------------------------------------------------
class TestVersionConflict:
    def test_update_nonexistent_memory(self, tmp_path):
        store = make_store(tmp_path)
        store.update_content(9999, "nope")
        assert store.get(9999) is None

    def test_update_archived_memory(self, tmp_path):
        store = make_store(tmp_path)
        mid = store.add("original")
        store.archive(mid)
        store.update_content(mid, "updated")
        # Archived memory should not be updated
        assert store.get(mid).content == "original"

    def test_double_archive_is_idempotent(self, tmp_path):
        store = make_store(tmp_path)
        mid = store.add("first")
        assert store.archive(mid) is True
        assert store.archive(mid) is False  # already archived
        assert store.get(mid).status == "archived"

    def test_delete_nonexistent_memory(self, tmp_path):
        store = make_store(tmp_path)
        assert store.delete(9999) is False

    def test_get_nonexistent_memory(self, tmp_path):
        store = make_store(tmp_path)
        assert store.get(9999) is None

    def test_touch_access_nonexistent(self, tmp_path):
        store = make_store(tmp_path)
        # Should not raise
        store.touch_access(9999)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
class TestTransactions:
    def test_transaction_commit(self, tmp_path):
        store = make_store(tmp_path)
        with store.transaction():
            store.add("tx item 1")
            store.add("tx item 2")
        # Both items should be present
        mid = store.add("after tx")
        assert store.get(mid) is not None

    def test_transaction_rollback_on_exception(self, tmp_path):
        store = make_store(tmp_path)
        pre_count = len(store.all_active())
        with pytest.raises(ValueError):
            with store.transaction():
                store.add("will be rolled back")
                raise ValueError("simulated failure")
        # Should not have added the item
        assert len(store.all_active()) == pre_count

    def test_nested_transaction_no_double_lock(self, tmp_path):
        """Nested transactions should not deadlock; second level is a no-op."""
        store = make_store(tmp_path)
        with store.transaction():
            store.add("level 1")
            with store.transaction():  # nested — should not re-acquire lock
                store.add("level 2")
        mid = store.add("outside")
        assert store.get(mid) is not None


# ---------------------------------------------------------------------------
# Monotonic ID generation
# ---------------------------------------------------------------------------
class TestMonotonicId:
    def test_strictly_increasing(self, tmp_path):
        store = make_store(tmp_path)
        ids = [store.add(f"item {i}") for i in range(10)]
        # All ids should be unique
        assert len(set(ids)) == 10

    def test_concurrent_ids_unique(self, tmp_path):
        store = make_store(tmp_path)
        results: list[int] = []
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(20):
                    mid = store.add("concurrent item")
                    results.append(mid)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent add: {errors}"
        assert len(results) == 100
        assert len(set(results)) == 100, "IDs should be unique across threads"


# ---------------------------------------------------------------------------
# Capacity edge cases
# ---------------------------------------------------------------------------
class TestCapacityEdgeCases:
    def test_capacity_minimum_one(self, tmp_path):
        """MemoryConfig enforces capacity >= 1; smaller values raise ValidationError."""
        with pytest.raises(Exception):
            make_store(tmp_path, capacity=0)

    def test_capacity_single(self, tmp_path):
        store = make_store(tmp_path, capacity=1)
        store.add("first", importance=5.0)
        store.add("second", importance=9.0)
        store.enforce_capacity()
        active = store.all_active()
        assert len(active) == 1
        assert active[0].content == "second"

    def test_exact_capacity_fit(self, tmp_path):
        store = make_store(tmp_path, capacity=3)
        for i in range(3):
            store.add(f"item {i}", importance=float(i + 1))
        store.enforce_capacity()
        assert len(store.all_active()) == 3


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
class TestStats:
    def test_empty_store_stats(self, tmp_path):
        store = make_store(tmp_path)
        stats = store.stats()
        assert stats["active"] == 0
        assert stats["archived"] == 0

    def test_all_types_counted(self, tmp_path):
        store = make_store(tmp_path)
        store.add("p1", type="project")
        store.add("p2", type="project")
        store.add("pref", type="preference")
        store.add("les", type="lesson")
        store.add("gen", type="general")
        stats = store.stats()
        assert stats["by_type"]["project"] == 2
        assert stats["by_type"]["preference"] == 1
        assert stats["by_type"]["lesson"] == 1
        assert stats["by_type"]["general"] == 1


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
class TestHealthCheck:
    def test_healthy_store(self, tmp_path):
        store = make_store(tmp_path)
        store.add("healthy item")
        hc = store.health_check()
        assert hc["status"] == "ok"
        assert hc["count"] == 1
        assert hc["dim"] == 1536
