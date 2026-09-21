from __future__ import annotations

import asyncio
import re
import threading
import time

from spark.config import SparkConfig
from spark.memory.extractor import Candidate, consolidate_group, extract_facts, resolve_operations
from spark.memory.llm import embed_texts, messages_to_transcript
from spark.memory.store import MemoryRow, MemoryStore, tokenize
from spark.models import ChatMessage

EXPLICIT_RE = re.compile(r"记住|请记|remember|memorize|keep in mind", re.IGNORECASE)

EMBED_RETRY_SECS = 600


def _kw_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class MemoryService:
    def __init__(self, store: MemoryStore, cfg: SparkConfig) -> None:
        self.store = store
        self.cfg = cfg
        self.embedding_available: bool | None = None
        self.embedding_checked_at = 0.0
        self._lock = threading.RLock()

    # ---------- embeddings ----------

    def _llm_ready(self) -> bool:
        p = self.cfg.provider
        return p.name != "mock" and bool(p.base_url) and bool(p.api_key or p.name == "ollama")

    def _embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.cfg.memory.enabled:
            return None
        now = time.time()
        if self.embedding_available is False and now - self.embedding_checked_at < EMBED_RETRY_SECS:
            return None
        p = self.cfg.provider
        try:
            vecs = asyncio.run(
                embed_texts(
                    base_url=p.base_url,
                    api_key=p.api_key or "ollama",
                    model=self.cfg.memory.embedding_model,
                    texts=texts,
                )
            )
        except Exception:
            vecs = None
        self.embedding_checked_at = now
        if vecs is None:
            self.embedding_available = False
            return None
        self.embedding_available = True
        return vecs

    # ---------- retrieval ----------

    def search(self, query: str, top_k: int | None = None) -> list[tuple[MemoryRow, float]]:
        vecs = self._embed([query])
        q_emb = vecs[0] if vecs else None
        return self.store.search(query, top_k=top_k, query_embedding=q_emb)

    def retrieve_context(self, query: str, top_k: int | None = None) -> str | None:
        if not self.cfg.memory.enabled:
            return None
        with self._lock:
            hits = self.search(query, top_k=top_k)
            if not hits:
                return None
            lines = []
            for row, _score in hits:
                self.store.touch_access(row.id)
                lines.append(f"- [{row.type}] {row.content}")
        return "<long_term_memory>\n" + "\n".join(lines) + "\n</long_term_memory>"

    # ---------- recording ----------

    def record_turn(
        self,
        *,
        user_text: str,
        assistant_text: str,
        tool_summary: str,
        session_id: str,
    ) -> dict:
        if not self.cfg.memory.enabled or not self.cfg.memory.auto_extract or not self._llm_ready():
            return {"extracted": 0, "ops": []}
        pieces = [f"user: {user_text[:2000]}"]
        if assistant_text:
            pieces.append(f"assistant: {assistant_text[:2000]}")
        if tool_summary:
            pieces.append(f"tools: {tool_summary[:1500]}")
        explicit = bool(EXPLICIT_RE.search(user_text))
        p = self.cfg.provider
        try:
            candidates = asyncio.run(
                extract_facts(
                    transcript="\n".join(pieces),
                    base_url=p.base_url,
                    api_key=p.api_key or "ollama",
                    model=p.model,
                    explicit=explicit,
                )
            )
        except Exception as exc:
            return {"extracted": 0, "ops": [], "error": str(exc)}
        if not candidates:
            return {"extracted": 0, "ops": []}

        with self._lock:
            similar: dict[int, list[tuple[MemoryRow, float]]] = {}
            for i, cand in enumerate(candidates):
                similar[i] = self.search(cand.content, top_k=3)
            try:
                ops = asyncio.run(
                    resolve_operations(
                        candidates=candidates,
                        similar=similar,
                        base_url=p.base_url,
                        api_key=p.api_key or "ollama",
                        model=p.model,
                    )
                )
            except Exception as exc:
                return {"extracted": len(candidates), "ops": [], "error": str(exc)}

            new_texts = [op.content for op in ops if op.action == "ADD"]
            update_texts = [op.content for op in ops if op.action == "UPDATE" and op.target_id]
            all_texts = new_texts + update_texts
            vecs = self._embed(all_texts) if all_texts else None
            vec_map: dict[str, list[float]] = {}
            if vecs:
                for text, vec in zip(all_texts, vecs):
                    vec_map[text] = vec

            applied: list[dict] = []
            for op in ops:
                if op.action == "ADD":
                    mid = self.store.add(
                        op.content,
                        type=op.type,
                        importance=op.importance,
                        embedding=vec_map.get(op.content),
                        embedding_model=self.cfg.memory.embedding_model if op.content in vec_map else None,
                        source_session=session_id,
                    )
                    applied.append({"action": "ADD", "id": mid, "content": op.content})
                elif op.action == "UPDATE" and op.target_id:
                    if self.store.get(op.target_id):
                        self.store.update_content(
                            op.target_id,
                            op.content,
                            importance=op.importance,
                        )
                        applied.append({"action": "UPDATE", "id": op.target_id, "content": op.content})
            archived = self.store.enforce_capacity()
        return {"extracted": len(candidates), "ops": applied, "archived": archived}

    # ---------- optimization ----------

    def _similarity_clusters(self) -> list[list[MemoryRow]]:
        rows = self.store.all_active()
        token_sets = [set(r.keywords.split()) for r in rows]
        emb_map = {i: r.embedding for i, r in enumerate(rows) if r.embedding}
        parent = list(range(len(rows)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                kw = _kw_jaccard(token_sets[i], token_sets[j])
                close = kw > 0.5
                if not close and i in emb_map and j in emb_map and kw > 0.12:
                    from spark.memory.store import cosine

                    close = cosine(emb_map[i], emb_map[j]) > 0.88
                if close:
                    union(i, j)
        groups: dict[int, list[int]] = {}
        for i in range(len(rows)):
            groups.setdefault(find(i), []).append(i)
        return [[rows[i] for i in idxs] for idxs in groups.values() if len(idxs) >= 2]

    def optimize(self) -> dict:
        report: dict = {"consolidated_groups": 0, "archived": 0, "errors": []}
        if not self.cfg.memory.enabled:
            report["error"] = "memory disabled"
            return report
        with self._lock:
            llm_ok = self._llm_ready()
            if llm_ok:
                clusters = self._similarity_clusters()[:25]
                p = self.cfg.provider
                for cluster in clusters:
                    contents = [r.content for r in cluster]
                    try:
                        merged = asyncio.run(
                            consolidate_group(
                                contents=contents,
                                base_url=p.base_url,
                                api_key=p.api_key or "ollama",
                                model=p.model,
                            )
                        )
                    except Exception as exc:
                        report["errors"].append(str(exc))
                        continue
                    if not merged:
                        continue
                    content, importance = merged
                    vecs = self._embed([content])
                    mid = self.store.add(
                        content,
                        type=cluster[0].type,
                        importance=max(importance, max(r.importance for r in cluster)),
                        embedding=vecs[0] if vecs else None,
                        embedding_model=self.cfg.memory.embedding_model if vecs else None,
                    )
                    for row in cluster:
                        self.store.archive(row.id)
                    report["consolidated_groups"] += 1
            report["archived"] = self.store.enforce_capacity()
            report["stats"] = self.store.stats()
        return report

    # ---------- management ----------

    def add_manual(self, content: str, type: str = "general", importance: float = 8.0) -> int:
        vecs = self._embed([content])
        with self._lock:
            return self.store.add(
                content,
                type=type,
                importance=importance,
                embedding=vecs[0] if vecs else None,
                embedding_model=self.cfg.memory.embedding_model if vecs else None,
                source_session=None,
            )

    def delete(self, memory_id: int) -> None:
        with self._lock:
            self.store.delete(memory_id)

    def list_memories(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return [r.to_dict() for r in self.store.list_all(limit)]

    def stats(self) -> dict:
        with self._lock:
            stats = self.store.stats()
        stats["embedding_available"] = self.embedding_available
        stats["embedding_model"] = self.cfg.memory.embedding_model
        return stats

    def status_probe(self) -> None:
        self._embed(["memory embedding probe"])


__all__ = ["MemoryService", "MemoryStore", "messages_to_transcript", "tokenize", "ChatMessage", "Candidate"]
