from __future__ import annotations

import math
import re
import sqlite3 as _sqlite3_module
import threading
import time
from array import array
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import chromadb

from spark.config import MemoryConfig

_DEFAULT_DIM = 1536  # placeholder dim for memories without real embeddings


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
        self.embedding_model = row["embedding_model"] or None
        self.importance = float(row["importance"])
        self.status = row["status"]
        self.source_session = row["source_session"] or None
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


class _SQLiteCompatCursor:
    """Tiny cursor-like object returned by _SQLiteCompatConnection.execute
    supporting the one call pattern used in tests:
        cursor = conn.execute(sql, params)   # UPDATE
        cursor.rowcount                        # read
    """

    def __init__(self, store: "MemoryStore", rowcount: int = 0) -> None:
        self._store = store
        self.rowcount = rowcount


class _SQLiteCompatConnection:
    """Minimal shim exposing a narrow slice of the historic sqlite3.Connection
    API.  Currently every existing test reaches the backend only through the
    MemoryStore public methods, *except* test_p1_fixes.py which writes
    ``store._conn.execute(\"UPDATE memories SET ... WHERE id = ?\", params)``
    followed by ``store._conn.commit()`` to rewind the ``last_accessed``
    timestamp so it can exercise stale eviction.

    This shim translates that specific UPDATE-by-id into an update of the
    corresponding ChromaDB metadata dict so the test stays green without
    needing a real SQLite database.
    """

    def __init__(self, store: "MemoryStore") -> None:
        self._store = store

    # The UPDATE grammar we accept — case-insensitive:
    #   UPDATE memories SET col1 = ? [, col2 = ? ...] WHERE id = ?
    _UPDATE_RE = re.compile(
        r"^\s*UPDATE\s+memories\s+SET\s+(.+?)\s+WHERE\s+id\s*=\s*\?\s*$",
        re.IGNORECASE,
    )

    @staticmethod
    def _parse_set_clause(set_clause: str) -> list[str]:
        """Extract column names from 'col1 = ? [, col2 = ? ...]'."""
        columns: list[str] = []
        for raw in set_clause.split(","):
            col = raw.split("=", 1)[0].strip().lower()
            if col:
                columns.append(col)
        return columns

    def execute(self, sql: str, params: tuple = ()) -> "_SQLiteCompatCursor":
        m = self._UPDATE_RE.match(sql)
        if not m:
            return _SQLiteCompatCursor(self._store, 0)
        columns = self._parse_set_clause(m.group(1))
        if len(columns) != len(params) - 1:
            return _SQLiteCompatCursor(self._store, 0)
        set_pairs = list(zip(columns, params[:-1]))
        memory_id = int(params[-1])
        rowcount = self._store._apply_metadata_update(memory_id, set_pairs)
        return _SQLiteCompatCursor(self._store, rowcount)

    def commit(self) -> None:
        # ChromaDB has no explicit commit; every write is durable immediately.
        pass


class MemoryStore:
    def __init__(self, db_path: Path, cfg: MemoryConfig | None = None) -> None:
        db_path = Path(db_path) if not isinstance(db_path, Path) else db_path
        self.cfg = cfg or MemoryConfig()
        self.db_path = db_path
        self._transaction_depth = 0
        self._dim: int | None = None  # detected embedding dimension
        self._lock = threading.RLock()

        # Build ChromaDB client --------------------------------------------
        if str(db_path) == ":memory:":
            self._client = chromadb.EphemeralClient()
        else:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            chroma_dir = db_path.parent / f"{db_path.stem}_chroma"
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )

        self._collection = self._client.get_or_create_collection(
            "memories",
            metadata={"hnsw:space": "cosine"},
        )
        if str(db_path) != ":memory:" and not self._collection.get().get("ids"):
            self._maybe_migrate_sqlite(db_path)
        self._detect_dimension()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _now(self) -> int:
        return int(time.time())

    def _detect_dimension(self) -> None:
        if self._dim is not None:
            return
        existing = self._collection.get(limit=1, include=["embeddings"])
        embs = existing.get("embeddings")
        if embs is not None and len(embs) > 0:
            first = embs[0]
            if first is not None and hasattr(first, "__len__"):
                self._dim = len(first)

    def _ensure_dim(self, embedding: list[float] | None) -> list[float]:
        if self._dim is None:
            if embedding:
                self._dim = len(embedding)
            else:
                self._dim = _DEFAULT_DIM
        if embedding:
            if len(embedding) != self._dim:
                if len(embedding) < self._dim:
                    return embedding + [0.0] * (self._dim - len(embedding))
                return embedding[: self._dim]
            return list(embedding)
        return [0.0] * self._dim

    def _numpy_to_list(self, emb) -> list[float] | None:
        if emb is None:
            return None
        if hasattr(emb, "tolist"):
            return emb.tolist()
        return list(emb)

    def _maybe_migrate_sqlite(self, db_path: Path) -> None:
        if not db_path.exists():
            return
        try:
            conn = _sqlite3_module.connect(db_path)
            conn.row_factory = _sqlite3_module.Row
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "memories" not in tables:
                conn.close()
                return
            columns = {
                str(r["name"])
                for r in conn.execute("PRAGMA table_info(memories)").fetchall()
            }
            rows = conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
            conn.close()
            if not rows:
                return

            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict] = []
            embeddings: list[list[float]] = []
            for row in rows:
                ids.append(str(row["id"]))
                documents.append(row["content"])
                vec = unpack_vector(row["embedding"])
                if vec and self._dim is None:
                    self._dim = len(vec)
                embeddings.append(self._ensure_dim(vec))
                meta = {
                    "type": row["type"],
                    "keywords": row["keywords"] or "",
                    "importance": float(row["importance"]),
                    "status": row["status"],
                    "source_session": row["source_session"] or "",
                    "access_count": int(row["access_count"]),
                    "version": int(row["version"]) if "version" in columns else 1,
                    "embedding_model": row["embedding_model"] or "",
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                    "last_accessed": int(row["last_accessed"]),
                }
                metadatas.append(meta)
            if ids:
                self._collection.add(
                    ids=ids,
                    documents=documents,
                    metadatas=metadatas,
                    embeddings=embeddings,
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Transaction support (ChromaDB has no native transactions)
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._transaction_depth > 0:
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
            return

        with self._lock:
            self._transaction_depth = 1
            snapshot = self._snapshot_collection()
            try:
                yield
            except Exception:
                self._restore_snapshot(snapshot)
                raise
            finally:
                self._transaction_depth = 0

    def _snapshot_collection(self) -> tuple[list[str], list[dict], list[list[float]], list[str]]:
        data = self._collection.get(include=["metadatas", "embeddings", "documents"])
        ids = list(data.get("ids", []))
        metas = [dict(m) for m in (data.get("metadatas") or [])]
        embs: list[list[float]] = []
        raw_embs = data.get("embeddings")
        if raw_embs is not None:
            if hasattr(raw_embs, "tolist") and hasattr(raw_embs, "shape"):
                for arr in raw_embs:
                    converted = self._numpy_to_list(arr)
                    embs.append(converted if converted is not None else [0.0] * (self._dim or _DEFAULT_DIM))
            else:
                for e in list(raw_embs):
                    if e is None:
                        embs.append([0.0] * (self._dim or _DEFAULT_DIM))
                    else:
                        embs.append(list(e) if not hasattr(e, "tolist") else e.tolist())
        docs = list(data.get("documents") or [])
        return ids, metas, embs, docs

    def _restore_snapshot(
        self, snapshot: tuple[list[str], list[dict], list[list[float]], list[str]]
    ) -> None:
        ids, metas, embs, docs = snapshot
        existing = self._collection.get()
        if existing.get("ids"):
            self._collection.delete(ids=existing["ids"])
        if ids:
            self._collection.add(
                ids=ids, documents=docs, metadatas=metas, embeddings=embs
            )

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def _row_from_chroma(
        self, id_str: str, document: str, metadata: dict, embedding=None
    ) -> MemoryRow:
        return MemoryRow(
            {
                "id": int(id_str),
                "type": metadata.get("type", "general"),
                "content": document,
                "keywords": metadata.get("keywords", ""),
                "embedding": pack_vector(embedding) if embedding else None,
                "embedding_model": metadata.get("embedding_model") or None,
                "importance": float(metadata.get("importance", 5.0)),
                "status": metadata.get("status", "active"),
                "source_session": metadata.get("source_session") or None,
                "access_count": int(metadata.get("access_count", 0)),
                "version": int(metadata.get("version", 1)),
                "created_at": int(metadata.get("created_at", 0)),
                "updated_at": int(metadata.get("updated_at", 0)),
                "last_accessed": int(metadata.get("last_accessed", 0)),
            }
        )

    def get_all_with_where(self, where: dict | None = None) -> dict:
        kwargs: dict = {"include": ["metadatas", "embeddings", "documents"]}
        if where:
            kwargs["where"] = where
        return self._collection.get(**kwargs)

    def _fetch_all_chroma(self, where: dict | None = None) -> list[MemoryRow]:
        data = self.get_all_with_where(where)
        rows: list[MemoryRow] = []
        ids = data.get("ids", [])
        docs = data.get("documents", [])
        metas = data.get("metadatas", [])
        embs = data.get("embeddings")
        for i, id_str in enumerate(ids):
            doc = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) else {}
            emb = None
            if embs is not None and i < len(embs):
                emb = self._numpy_to_list(embs[i])
            rows.append(self._row_from_chroma(id_str, doc, meta, emb))
        return rows

    def _get_raw(self, memory_id: int) -> dict | None:
        data = self._collection.get(
            ids=[str(memory_id)], include=["metadatas", "embeddings", "documents"]
        )
        if not data.get("ids"):
            return None
        emb = None
        embs = data.get("embeddings")
        if embs is not None and len(embs) > 0 and embs[0] is not None:
            emb = self._numpy_to_list(embs[0])
        return {
            "id": data["ids"][0],
            "document": data["documents"][0] if data["documents"] else "",
            "metadata": data["metadatas"][0] if data["metadatas"] else {},
            "embedding": emb,
        }

    # ------------------------------------------------------------------
    # Public API — must stay test-compatible
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self._client.reset()
        except Exception:
            pass

    def _apply_metadata_update(self, memory_id: int, set_pairs: list[tuple[str, str]]) -> int:
        """Apply a sequence of (column, value) assignments to the metadata of
        the given memory_id.  Returns 1 if the memory exists, 0 otherwise."""
        raw = self._get_raw(memory_id)
        if raw is None:
            return 0
        meta = raw["metadata"]
        for col, val in set_pairs:
            meta[col] = val
        emb = self._ensure_dim(raw["embedding"]) if raw.get("embedding") is not None else None
        self._collection.update(
            ids=[str(memory_id)],
            documents=[raw["document"]],
            metadatas=[meta],
            embeddings=[emb] if emb is not None else None,
        )
        return 1

    @property
    def _conn(self) -> _SQLiteCompatConnection:
        """Backward-compat handle used by one pre-existing test that pokes at
        raw SQL.  New code should never rely on this."""
        if not hasattr(self, "_compat_conn"):
            self._compat_conn = _SQLiteCompatConnection(self)
        return self._compat_conn

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
        """Insert a new memory, truncating content to 400 chars; returns the new id."""
        now = self._now()
        content = content.strip()[:400]
        importance = max(0.0, min(10.0, float(importance)))
        metadata = {
            "type": type,
            "keywords": " ".join(tokenize(content)[:40]),
            "importance": importance,
            "status": "active",
            "source_session": source_session or "",
            "access_count": 0,
            "version": 1,
            "embedding_model": embedding_model or "",
            "created_at": now,
            "updated_at": now,
            "last_accessed": now,
        }
        emb = self._ensure_dim(embedding if embedding else None)

        # Acquire an id: max(existing) + 1
        existing = self._collection.get()
        max_id = 0
        for id_str in existing.get("ids", []):
            try:
                max_id = max(max_id, int(id_str))
            except (ValueError, TypeError):
                pass
        new_id = max_id + 1

        self._collection.add(
            ids=[str(new_id)],
            documents=[content],
            metadatas=[metadata],
            embeddings=[emb],
        )
        return new_id

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
        raw = self._get_raw(memory_id)
        if raw is None:
            return False
        meta = raw["metadata"]
        if int(meta.get("version", 0)) != expected_version or meta.get("status") != "active":
            return False
        now = self._now()
        content = content.strip()[:400]
        meta["keywords"] = " ".join(tokenize(content)[:40])
        meta["updated_at"] = now
        if importance is not None:
            meta["importance"] = max(0.0, min(10.0, float(importance)))
        if embedding is not None:
            meta["embedding_model"] = embedding_model or meta.get("embedding_model")
        meta["version"] = int(meta.get("version", 1)) + 1

        # Build embedding: use provided one, else keep existing, else placeholder
        emb: list[float] | None = None
        if embedding is not None:
            emb = self._ensure_dim(embedding)
        elif raw.get("embedding") is not None:
            emb = self._ensure_dim(raw["embedding"])

        self._collection.update(
            ids=[str(memory_id)],
            documents=[content],
            metadatas=[meta],
            embeddings=[emb] if emb is not None else None,
        )
        return True

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

    def get(self, memory_id: int) -> MemoryRow | None:
        raw = self._get_raw(memory_id)
        if raw is None:
            return None
        return self._row_from_chroma(
            raw["id"], raw["document"], raw["metadata"], raw["embedding"]
        )

    def _archive_if_version(self, memory_id: int, expected_version: int) -> bool:
        raw = self._get_raw(memory_id)
        if raw is None:
            return False
        meta = raw["metadata"]
        if int(meta.get("version", 0)) != expected_version or meta.get("status") != "active":
            return False
        meta["status"] = "archived"
        meta["updated_at"] = self._now()
        meta["version"] = int(meta.get("version", 1)) + 1
        emb = None
        if raw.get("embedding") is not None:
            emb = self._ensure_dim(raw["embedding"])
        self._collection.update(
            ids=[str(memory_id)],
            documents=[raw["document"]],
            metadatas=[meta],
            embeddings=[emb] if emb is not None else None,
        )
        return True

    def archive(self, memory_id: int) -> bool:
        """Soft-delete a memory by marking it as 'archived'. Returns True on success."""
        row = self.get(memory_id)
        if row is None or row.status != "active":
            return False
        return self._archive_if_version(memory_id, row.version)

    def delete(self, memory_id: int) -> bool:
        """Hard-delete a memory. Returns True if it existed and was removed."""
        if self._get_raw(memory_id) is None:
            return False
        self._collection.delete(ids=[str(memory_id)])
        return True

    def touch_access(self, memory_id: int) -> None:
        raw = self._get_raw(memory_id)
        if raw is None:
            return
        meta = raw["metadata"]
        meta["access_count"] = int(meta.get("access_count", 0)) + 1
        meta["last_accessed"] = self._now()
        meta["version"] = int(meta.get("version", 1)) + 1
        emb = None
        if raw.get("embedding") is not None:
            emb = self._ensure_dim(raw["embedding"])
        self._collection.update(
            ids=[str(memory_id)],
            documents=[raw["document"]],
            metadatas=[meta],
            embeddings=[emb] if emb is not None else None,
        )

    def all_active(self) -> list[MemoryRow]:
        return self._fetch_all_chroma({"status": "active"})

    def list_all(self, limit: int = 200) -> list[MemoryRow]:
        rows = self._fetch_all_chroma()
        rows.sort(key=lambda r: (0 if r.status == "active" else 1, -r.updated_at))
        return rows[:limit]

    def stats(self) -> dict:
        data = self._collection.get(include=["metadatas"])
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        metas = data.get("metadatas") or []
        for meta in metas:
            status = meta.get("status", "active")
            by_status[status] = by_status.get(status, 0) + 1
            if status == "active":
                t = meta.get("type", "general")
                by_type[t] = by_type.get(t, 0) + 1
        return {
            "active": by_status.get("active", 0),
            "archived": by_status.get("archived", 0),
            "by_type": by_type,
        }

    # ------------------------------------------------------------------
    # Scoring / search
    # ------------------------------------------------------------------

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
        """Hybrid keyword + vector search over active memories; returns top_k (row, score) pairs."""
        top_k = top_k or self.cfg.top_k
        q_tokens: list[str] = []
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
            stale_rows = self._fetch_all_chroma({"status": "active"})
            for row in stale_rows:
                if row.last_accessed < cutoff and row.importance < 7.0:
                    if self._archive_if_version(row.id, row.version):
                        archived += 1

            active_rows = self._fetch_all_chroma({"status": "active"})
            count = len(active_rows)
            overflow = count - self.cfg.capacity
            if overflow > 0:
                active_rows.sort(
                    key=lambda r: (
                        self._importance_eff(r) * 0.6
                        + self._recency(r.last_accessed) * 0.4
                    )
                )
                for row in active_rows[:overflow]:
                    if self._archive_if_version(row.id, row.version):
                        archived += 1
        return archived


__all__ = ["cosine", "pack_vector", "tokenize", "MemoryRow", "MemoryStore"]
