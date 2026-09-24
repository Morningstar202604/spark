from __future__ import annotations

import math
import re
import time
from array import array
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from spark.config import MemoryConfig


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if raw.isascii():
            if len(raw) > 1:
                tokens.append(raw)
        else:
            if len(raw) == 1:
                tokens.append(raw)
            else:
                tokens.extend(raw[i : i + 2] for i in range(len(raw) - 1))
    return tokens


def pack_vector(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack_vector(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    arr = array("f")
    arr.frombytes(blob)
    return list(arr)


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


class MemoryRow:
    def __init__(self, row) -> None:
        self.id = int(row["id"])
        self.type = row["type"]
        self.content = row["content"]
        self.keywords = row["keywords"] or ""
        self.embedding = unpack_vector(row["embedding"])
        self.embedding_model = row["embedding_model"]
        self.importance = float(row["importance"])
        self.status = row["status"]
        self.source_session = row["source_session"]
        self.access_count = int(row["access_count"])
        self.version = int(row["version"])
        self.created_at = int(row["created_at"])
        self.updated_at = int(row["updated_at"])
        self.last_accessed = int(row["last_accessed"])

    def to_dict(self, include_status: bool = True) -> dict:
        out = {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "importance": self.importance,
            "access_count": self.access_count,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed": self.last_accessed,
        }
        if include_status:
            out["status"] = self.status
        return out


class MemoryStore:
    def __init__(self, db_path: Path, cfg: MemoryConfig) -> None:
        import sqlite3

        self.cfg = cfg
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._transaction_depth = 0
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._transaction_depth:
            yield
            return
        self._conn.execute("BEGIN IMMEDIATE")
        self._transaction_depth = 1
        try:
            with self._conn:
                yield
        finally:
            self._transaction_depth = 0

    def _commit_if_needed(self) -> None:
        if self._transaction_depth == 0:
            self._conn.commit()

    def _init(self) -> None:
        with self.transaction():
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
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
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_accessed INTEGER NOT NULL
                )
                """
            )
            columns = {
                str(row["name"])
                for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "version" not in columns:
                self._conn.execute(
                    "ALTER TABLE memories ADD COLUMN version INTEGER NOT NULL DEFAULT 1"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)"
            )

    def close(self) -> None:
        self._conn.close()

    def _now(self) -> int:
        return int(time.time())

    def add(
        self,
        content: str,
        *,
        type: str = "general",
        importance: float = 5.0,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
        source_session: str | None = None,
    ) -> int:
        now = self._now()
        content = content.strip()[:400]
        cur = self._conn.execute(
            """INSERT INTO memories
               (type, content, keywords, embedding, embedding_model, importance, status,
                source_session, access_count, created_at, updated_at, last_accessed)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 0, ?, ?, ?)""",
            (
                type,
                content,
                " ".join(tokenize(content)[:40]),
                pack_vector(embedding) if embedding else None,
                embedding_model,
                max(0.0, min(10.0, float(importance))),
                source_session,
                now,
                now,
                now,
            ),
        )
        self._commit_if_needed()
        return int(cur.lastrowid)

    def _update_content_if_version(
        self,
        memory_id: int,
        content: str,
        expected_version: int,
        *,
        importance: float | None = None,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
    ) -> bool:
        now = self._now()
        sets = ["content = ?", "keywords = ?", "updated_at = ?"]
        params: list = [content.strip()[:400], " ".join(tokenize(content)[:40]), now]
        if importance is not None:
            sets.append("importance = ?")
            params.append(max(0.0, min(10.0, float(importance))))
        if embedding is not None:
            sets.extend(["embedding = ?", "embedding_model = ?"])
            params.append(pack_vector(embedding))
            params.append(embedding_model)
        sets.append("version = version + 1")
        params.extend((memory_id, expected_version))
        cur = self._conn.execute(
            f"UPDATE memories SET {', '.join(sets)} "
            "WHERE id = ? AND version = ? AND status = 'active'",
            params,
        )
        return cur.rowcount == 1

    def update_content(
        self,
        memory_id: int,
        content: str,
        *,
        importance: float | None = None,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
    ) -> None:
        row = self.get(memory_id)
        if row is None or row.status != "active":
            return
        self._update_content_if_version(
            memory_id,
            content,
            row.version,
            importance=importance,
            embedding=embedding,
            embedding_model=embedding_model,
        )
        self._commit_if_needed()

    def get(self, memory_id: int) -> MemoryRow | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return MemoryRow(row) if row else None

    def _archive_if_version(self, memory_id: int, expected_version: int) -> bool:
        cur = self._conn.execute(
            "UPDATE memories SET status = 'archived', updated_at = ?, "
            "version = version + 1 "
            "WHERE id = ? AND version = ? AND status = 'active'",
            (self._now(), memory_id, expected_version),
        )
        return cur.rowcount == 1

    def archive(self, memory_id: int) -> None:
        row = self.get(memory_id)
        if row is None or row.status != "active":
            return
        self._archive_if_version(memory_id, row.version)
        self._commit_if_needed()

    def delete(self, memory_id: int) -> None:
        self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._commit_if_needed()

    def touch_access(self, memory_id: int) -> None:
        self._conn.execute(
            "UPDATE memories SET access_count = access_count + 1, last_accessed = ?, "
            "version = version + 1 WHERE id = ?",
            (self._now(), memory_id),
        )
        self._commit_if_needed()

    def all_active(self) -> list[MemoryRow]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE status = 'active' ORDER BY id"
        ).fetchall()
        return [MemoryRow(r) for r in rows]

    def list_all(self, limit: int = 200) -> list[MemoryRow]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY (status = 'active') DESC, updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [MemoryRow(r) for r in rows]

    def stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM memories GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: int(r["n"]) for r in rows}
        types = self._conn.execute(
            "SELECT type, COUNT(*) AS n FROM memories WHERE status = 'active' GROUP BY type"
        ).fetchall()
        return {
            "active": by_status.get("active", 0),
            "archived": by_status.get("archived", 0),
            "by_type": {r["type"]: int(r["n"]) for r in types},
        }

    def _recency(self, ts: int) -> float:
        days = max(0.0, (self._now() - ts) / 86400.0)
        return math.exp(-days / 30.0)

    def _importance_eff(self, row: MemoryRow) -> float:
        boost = min(2.0, math.log2(1 + row.access_count))
        return min(10.0, row.importance + boost)

    def score(
        self, row: MemoryRow, query_tokens: list[str], query_emb: list[float] | None
    ) -> float:
        cfg_w = (0.5, 0.25, 0.15, 0.10)
        w_emb, w_kw, w_rec, w_imp = cfg_w
        if row.embedding is None or query_emb is None:
            w_emb, w_kw = 0.0, 0.75
        emb_score = 0.0
        if row.embedding is not None and query_emb is not None:
            emb_score = cosine(row.embedding, query_emb)
        row_tokens = set(row.keywords.split())
        kw_score = 0.0
        if query_tokens:
            hit = sum(1 for t in query_tokens if t in row_tokens)
            kw_score = hit / len(query_tokens)
        score = (
            w_emb * emb_score
            + w_kw * kw_score
            + w_rec * self._recency(row.last_accessed)
            + w_imp * (self._importance_eff(row) / 10.0)
        )
        return round(score, 4)

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        query_embedding: list[float] | None = None,
        min_score: float = 0.0,
        source_session: str | None = None,
        exclude_session: str | None = None,
    ) -> list[tuple[MemoryRow, float]]:
        top_k = top_k or self.cfg.top_k
        q_tokens = []
        seen: set[str] = set()
        for t in tokenize(query):
            if t not in seen:
                seen.add(t)
                q_tokens.append(t)
        results: list[tuple[MemoryRow, float]] = []
        for row in self.all_active():
            if source_session is not None and row.source_session not in (
                None,
                source_session,
            ):
                continue
            if exclude_session is not None and row.source_session == exclude_session:
                continue
            s = self.score(row, q_tokens, query_embedding)
            if s > min_score:
                results.append((row, s))
        results.sort(key=lambda pair: pair[1], reverse=True)
        return results[:top_k]

    def enforce_capacity(self, *, stale_days: float = 180.0) -> int:
        archived = 0
        with self.transaction():
            cutoff = self._now() - int(stale_days * 86400)
            stale = self._conn.execute(
                "SELECT id, version FROM memories WHERE status = 'active' "
                "AND last_accessed < ? AND importance < 7.0",
                (cutoff,),
            ).fetchall()
            for row in stale:
                if self._archive_if_version(int(row["id"]), int(row["version"])):
                    archived += 1
            count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE status = 'active'"
            ).fetchone()["n"]
            overflow = int(count) - self.cfg.capacity
            if overflow > 0:
                rows = self.all_active()
                rows.sort(
                    key=lambda r: (
                        self._importance_eff(r) * 0.6
                        + self._recency(r.last_accessed) * 0.4
                    )
                )
                for row in rows[:overflow]:
                    if self._archive_if_version(row.id, row.version):
                        archived += 1
        return archived
