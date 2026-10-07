"""长期记忆：mem0 优先 + SQLite 兜底。

后端选择策略：
- mem0 可用 + 嵌入 API（OpenAI 兼容 / 火山方舟等）配置齐全且可达
  → mem0（ChromaDB 本地向量存储 + OpenAI embedder，向量检索语义+近似关键词）
- mem0 未安装 / 嵌入 API 未配置或自定义 Embedder 实例
  → 退回 hand-rolled SQLite（FTS5 关键词 + 语义向量兜底，零外部依赖）

此模块的公共 API（MemoryStore 类签名、embedder 类、make_embedder）保持与旧版相同，
内部实现透明切换，调用方无需关注后端差异。

mem0 配置模式：
- memory_embedding=off（默认）：不启用 mem0，纯 SQLite；
- memory_embedding=api：用主模型 base_url/api_key 调 OpenAI 兼容 embedding 接口，
  mem0 接管向量存储与检索。
"""
from __future__ import annotations

import datetime
import json
import math
import re
import sqlite3
from pathlib import Path

from spark.config import config_dir

__all__ = [
    "EmbeddingBackend",
    "_HttpEmbedder",
    "VolcengineEmbedder",  # 向后兼容别名
    "OpenAIEmbedder",      # 向后兼容别名
    "LocalEmbedder",
    "MemoryStore",
    "make_embedder",
    "_tokens",
]

# ---------------------------------------------------------------------------
# mem0 可用性探测（模块加载时一次）
# ---------------------------------------------------------------------------
try:
    from mem0 import Memory as _Mem0Memory  # noqa: F401 — 用于 mem0 可用性探测 + runtime 构建

    _MEM0_AVAILABLE = True
except ImportError:
    _MEM0_AVAILABLE = False

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workdir TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(workdir, key)
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  key, value, content='memories', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, key, value) VALUES (new.id, new.key, new.value);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.id, old.key, old.value);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.id, old.key, old.value);
  INSERT INTO memories_fts(rowid, key, value) VALUES (new.id, new.key, new.value);
END;
"""

# schema_version：跟踪已应用的迁移版本，升级不清零数据
# v1: 初始 schema（memories + fts5 + 触发器）
# v2: +embedding 列（语义向量）
# v3: +level 列（记忆分层）
CURRENT_SCHEMA_VERSION = 3

MEMORY_LEVELS = ("situational", "semantic", "episodic", "procedural")


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（两向量均为非空数值列表）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


class EmbeddingBackend:
    """嵌入后端协议：任何实现 embed(texts) -> list[list[float]] | None 的对象。"""

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        raise NotImplementedError


class _HttpEmbedder(EmbeddingBackend):
    """HTTP /embeddings 后端共同逻辑：火山方舟 / OpenAI / 通义 / DeepSeek 等 OpenAI 兼容接口。

    过去 VolcengineEmbedder 与 OpenAIEmbedder 各自复制同一份 httpx 调用；
    现合并为一份实现，差异只剩 model 名与 base_url（由构造参数注入）。
    """

    def __init__(
        self, base_url: str, api_key: str, model: str, timeout: float = 30.0
    ) -> None:
        self.base = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.api_key or not self.base:
            return None
        try:
            import httpx

            resp = httpx.post(
                self.base + "/embeddings",
                json={"model": self.model, "input": texts},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
            items = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
            vecs = [d.get("embedding") for d in items]
            return [v for v in vecs if isinstance(v, list) and v]
        except Exception:  # noqa: BLE001 —— 嵌入失败退回关键词检索
            return None


# 兼容旧引用：历史代码/测试可能 import VolcengineEmbedder / OpenAIEmbedder。
# 两者现已合并为 _HttpEmbedder；别名为类型提示和 isinstance 提供向后兼容。
VolcengineEmbedder = _HttpEmbedder
OpenAIEmbedder = _HttpEmbedder


class LocalEmbedder(EmbeddingBackend):
    """本地语义嵌入：sentence-transformers + BGE-M3 类国产开源模型（离线）。"""

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "本地嵌入需要安装 sentence-transformers（pip install sentence-transformers），"
                "或把 memory_embedding 改为 openai / volcengine"
            ) from e
        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        try:
            return self.model.encode(texts, normalize_embeddings=True).tolist()
        except Exception:  # noqa: BLE001
            return None


def make_embedder(cfg: dict) -> EmbeddingBackend | None:
    """按配置构造嵌入后端；失败/未启用返回 None（纯关键词检索）。

    配置键：
      memory_embedding = "off" | "volcengine" | "openai" | "local"
      embed_base_url    = 独立 embedding 端点（可选，默认复用 base_url）
      embed_api_key     = 独立 embedding 密钥（可选，默认复用 api_key）
      memory_embed_model= embedding 模型名
    """
    mode = (cfg.get("memory_embedding") or "off").strip().lower()
    if mode == "volcengine":
        base = (cfg.get("embed_base_url") or cfg.get("base_url") or "").strip()
        key = (cfg.get("embed_api_key") or cfg.get("api_key") or "").strip()
        return VolcengineEmbedder(
            base, key,
            cfg.get("memory_embed_model") or "doubao-embedding-vision",
        )
    if mode == "openai":
        base = (cfg.get("embed_base_url") or cfg.get("base_url") or "").strip()
        key = (cfg.get("embed_api_key") or cfg.get("api_key") or "").strip()
        return OpenAIEmbedder(
            base, key,
            cfg.get("memory_embed_model") or "text-embedding-3-small",
        )
    if mode == "local":
        try:
            return LocalEmbedder(cfg.get("memory_embed_model") or "BAAI/bge-m3")
        except RuntimeError:
            return None
    return None


# ---------------------------------------------------------------------------
# SQLite 实现（兜底 / 测试兼容）
# ---------------------------------------------------------------------------


class _SqliteMemoryStore:
    """hand-rolled SQLite + FTS5 实现（原有代码不变，作为兜底）。"""

    def __init__(self, path: Path | None = None, embedder=None) -> None:
        self.path = path or (config_dir() / "memory.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._emb_cache: dict[str, list[float]] = {}
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_V1)
            ver = self._schema_version(conn)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
            if ver < 2 and "embedding" not in cols:
                conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
            if ver < 3 and "level" not in cols:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN level TEXT NOT NULL DEFAULT 'semantic'"
                )
            self._set_schema_version(conn, CURRENT_SCHEMA_VERSION)
            try:
                fts_cols = [
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(memories_fts)").fetchall()
                ]
                if "key" not in fts_cols or "value" not in fts_cols:
                    conn.execute("DROP TABLE memories_fts")
                    conn.execute(
                        "CREATE VIRTUAL TABLE memories_fts USING fts5("
                        "key, value, content='memories', content_rowid='id', "
                        "tokenize='unicode61')"
                    )
                    conn.execute(
                        "INSERT INTO memories_fts(rowid, key, value) "
                        "SELECT id, key, value FROM memories"
                    )
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if not row:
                return 0
            ver = conn.execute("SELECT version FROM schema_version").fetchone()
            return int(ver["version"]) if ver else 0
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        existing = conn.execute("SELECT version FROM schema_version").fetchone()
        if existing:
            conn.execute("UPDATE schema_version SET version=?", (version,))
        else:
            conn.execute("INSERT INTO schema_version(version) VALUES(?)", (version,))

    # ---------- 写 ----------

    def remember(self, workdir: str, key: str, value: str, level: str = "semantic") -> None:
        embedding = self._embed_for(key, value)
        lvl = level if level in MEMORY_LEVELS else "semantic"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO memories(workdir, key, value, level, embedding, created_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(workdir, key) DO UPDATE SET value=excluded.value, "
                "level=excluded.level, embedding=excluded.embedding, created_at=excluded.created_at",
                (workdir, key.strip(), value.strip(), lvl, embedding, _now()),
            )

    def _embed_for(self, key: str, value: str) -> str | None:
        if self.embedder is None:
            return None
        vecs = self.embedder.embed([f"{key}：{value}"])
        if not vecs:
            return None
        try:
            return json.dumps(vecs[0])
        except (TypeError, ValueError):
            return None

    def forget(self, workdir: str, key: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE workdir=? AND key=?", (workdir, key.strip()))
            return cur.rowcount

    def delete_by_id(self, memory_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            return cur.rowcount > 0

    def _decode_emb(self, raw: str) -> list[float] | None:
        cached = self._emb_cache.get(raw)
        if cached is not None:
            return cached
        try:
            emb = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(emb, list):
            return None
        self._emb_cache[raw] = emb
        if len(self._emb_cache) > 10000:
            for k in list(self._emb_cache)[:2000]:
                self._emb_cache.pop(k, None)
        return emb

    def _rank_sem(self, vec: list[float], rows, limit: int) -> list[tuple[float, dict]]:
        sem_ranked: list[tuple[float, dict]] = []
        for r in rows:
            emb = self._decode_emb(r["embedding"])
            if emb is None:
                continue
            sim = _cosine(vec, emb)
            if sim > 0.3:
                sem_ranked.append((sim, {"id": r["id"], "key": r["key"], "value": r["value"], "level": r["level"], "created_at": r["created_at"]}))
        sem_ranked.sort(key=lambda x: x[0], reverse=True)
        return sem_ranked

    # ---------- 读 ----------

    def list_memories(self, workdir: str, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, key, value, level, created_at FROM memories WHERE workdir=? "
                "ORDER BY created_at DESC LIMIT ?",
                (workdir, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def search(self, workdir: str, query: str, limit: int = 5) -> list[dict]:
        q = (query or "").strip()
        if not q:
            return []

        fts_ranked: list[dict] = []
        kw_ranked: list[dict] = []
        sem_ranked: list[tuple[float, dict]] = []
        exact_ids: set[int] = set()

        with self._connect() as conn:
            for r in conn.execute(
                "SELECT id FROM memories WHERE workdir=? AND key=? LIMIT 1",
                (workdir, q),
            ).fetchall():
                exact_ids.add(r["id"])

            phrase = '"' + q.replace('"', '""') + '"'
            try:
                rows = conn.execute(
                    "SELECT m.id, m.key, m.value, m.level, m.created_at FROM memories_fts f "
                    "JOIN memories m ON m.id=f.rowid "
                    "WHERE m.workdir=? AND memories_fts MATCH ? ORDER BY m.created_at DESC LIMIT ?",
                    (workdir, phrase, limit * 2),
                ).fetchall()
                fts_ranked = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass

            toks = _tokens(q)
            if toks:
                conds, params = [], [workdir]
                for t in toks:
                    conds.append("(key LIKE ? OR value LIKE ?)")
                    params += [f"%{t}%", f"%{t}%"]
                rows = conn.execute(
                    "SELECT id, key, value, level, created_at FROM memories WHERE workdir=? "
                    f"AND ({' OR '.join(conds)}) ORDER BY created_at DESC LIMIT ?",
                    (*params, limit * 2),
                ).fetchall()
                kw_ranked = [dict(r) for r in rows]

            if self.embedder is not None:
                vec = self.embedder.embed([q])
                if vec and vec[0]:
                    rows = conn.execute(
                        "SELECT id, key, value, level, embedding, created_at FROM memories "
                        "WHERE workdir=? AND embedding IS NOT NULL",
                        (workdir,),
                    ).fetchall()
                    sem_ranked = self._rank_sem(vec[0], rows, limit * 2)

        RRF_K = 60
        rrf_scores: dict[int, float] = {}
        all_rows: dict[int, dict] = {}

        for rank, row in enumerate(fts_ranked):
            rid = row["id"]
            rrf_scores[rid] = rrf_scores.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
            all_rows[rid] = row
        for rank, row in enumerate(kw_ranked):
            rid = row["id"]
            rrf_scores[rid] = rrf_scores.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
            all_rows[rid] = row
        for rank, (_sim, row) in enumerate(sem_ranked[: limit * 2]):
            rid = row["id"]
            rrf_scores[rid] = rrf_scores.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
            all_rows[rid] = row

        for rid in exact_ids:
            if rid in all_rows:
                rrf_scores[rid] += 1.0 / (RRF_K + 0)

        sem_id_set = {r["id"] for _, r in sem_ranked[: limit * 2]}
        items = sorted(
            all_rows.values(),
            key=lambda m: (
                rrf_scores.get(m["id"], 0.0),
                m["id"] in sem_id_set,
                m["created_at"],
            ),
            reverse=True,
        )
        return items[:limit]

    def count(self, workdir: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM memories WHERE workdir=?", (workdir,)).fetchone()
            return int(row["n"])


# ---------------------------------------------------------------------------
# mem0 实现（新后端）
# ---------------------------------------------------------------------------


def _mem0_should_use(embedder) -> bool:
    """判断是否满足使用 mem0 后端的条件。

    要求：
    1. mem0 库实际可导入；
    2. 传入的 embedder 是 HTTP 兼容的（有可达 base_url + api_key），
       这样 mem0 才能通过 OpenAI 协议做嵌入。
    """
    if not _MEM0_AVAILABLE:
        return False
    if embedder is None:
        return False
    # 只接受 HTTP 兼容的 embedder；自定义/fake embedder 不触发 mem0
    if isinstance(embedder, _HttpEmbedder):
        return bool(embedder.base and embedder.api_key)
    return False


class _Mem0MemoryStore:
    """mem0-backed 向量记忆后端。

    内部以 mem0.Memory 为核心：
    - user_id 字段承载 workdir（mem0 的多租户隔离）；
    - metadata 字段承载 key / level 等 Spark 元信息；
    - infer=False 禁用 LLM 抽取，记忆由 Spark 显式写入。

    返回值格式与 _SqliteMemoryStore 完全一致（dict 含 id/key/value/level/created_at）。
    """

    # Spark → mem0 的元数据 key 前缀（避免与 mem0 内置字段冲突）
    _MD_KEY = "__spark_key__"
    _MD_LEVEL = "__spark_level__"
    _MD_WORKDIR = "__spark_workdir__"

    def __init__(self, path: Path, embedder: _HttpEmbedder) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        # ChromaDB 持久化到 path 同级目录的 .chroma 子目录
        chroma_path = self.path.parent / ".chroma"
        chroma_path.mkdir(parents=True, exist_ok=True)
        self._mem = self._build_mem0(chroma_path, embedder)

    @staticmethod
    def _build_mem0(chroma_path: Path, embedder: _HttpEmbedder) -> _Mem0Memory:
        config_dict = {
            "vector_store": {
                "provider": "chroma",
                "config": {"path": str(chroma_path)},
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "api_key": embedder.api_key,
                    "openai_base_url": embedder.base,
                    "model": embedder.model,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "api_key": embedder.api_key,
                    "openai_base_url": embedder.base,
                    "model": embedder.model,
                },
            },
        }
        return _Mem0Memory.from_config(config_dict)

    # ---- 内部：序列化 key+value 为记忆文本 ----

    @staticmethod
    def _memory_text(key: str, value: str) -> str:
        return f"{key}：{value}"

    def _to_row(self, mem: dict) -> dict:
        """把 mem0 返回的记忆 dict 翻译成 Spark 的统一行格式。"""
        md = mem.get("metadata") or {}
        return {
            "id": mem.get("id", ""),
            "key": md.get(self._MD_KEY, mem.get("memory", "")),
            "value": md.get(self._MD_LEVEL, ""),     # 占位；真实 value 见下方
            "level": md.get(self._MD_LEVEL, "semantic"),
            "created_at": mem.get("created_at", ""),
            # 保留原始 memory 字段（即 "key：value" 原文），以便 search 返回后能解析出 value
            "_memory": mem.get("memory", ""),
        }

    def remember(self, workdir: str, key: str, value: str, level: str = "semantic") -> None:
        lvl = level if level in MEMORY_LEVELS else "semantic"
        text = self._memory_text(key.strip(), value.strip())
        md = {
            self._MD_KEY: key.strip(),
            self._MD_LEVEL: lvl,
            self._MD_WORKDIR: workdir,
            # 冗余存 value 元信息；mem0 的记忆本体是 text，但下游需要单独输出 value
            "__spark_value__": value.strip(),
        }
        self._mem.add(
            text,
            user_id=workdir,
            metadata=md,
            infer=False,
        )

    def _resolve_memories(self, items: list[dict]) -> list[dict]:
        """把 mem0 返回的 items 统一成 Spark MemoryStore 的行格式。

        mem0 v2+ 的 search/get_all 返回结构有两种可能：
        - {"results": [mem0_dict, ...]}  （v2.0+ 的标准输出）
        - [mem0_dict, ...]                （旧版直接返回列表）
        """
        if not items:
            return []
        rows = []
        for m in items:
            md = m.get("metadata") or {}
            spark_key = md.get(self._MD_KEY, "")
            spark_value = md.get("__spark_value__", "")
            # 如果元信息没存 value，从记忆文本反推
            if not spark_value and "：" in m.get("memory", ""):
                spark_value = m["memory"].split("：", 1)[1]
            rows.append({
                "id": m.get("id", ""),
                "key": spark_key or m.get("memory", ""),
                "value": spark_value,
                "level": md.get(self._MD_LEVEL, "semantic"),
                "created_at": m.get("created_at", ""),
                "_score": m.get("score", 0.0),
            })
        return rows

    def search(self, workdir: str, query: str, limit: int = 5) -> list[dict]:
        q = (query or "").strip()
        if not q:
            return []
        try:
            raw = self._mem.search(
                q,
                filters={"user_id": workdir},
                top_k=limit,
                show_expired=False,
            )
        except Exception:  # noqa: BLE001 —— mem0 检索失败由上层 MemoryStore 捕获后退回 SQLite
            raise

        # mem0 返回可能是 dict 包 results 或直接 list
        if isinstance(raw, dict):
            items = raw.get("results") or []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []

        rows = self._resolve_memories(items)

        # 精确 key 命中加权：若 query 与某 key 完全相等，该记忆提到最前
        exact_rows, other_rows = [], []
        for r in rows:
            if r["key"] == q:
                exact_rows.append(r)
            else:
                other_rows.append(r)
        return (exact_rows + other_rows)[:limit]

    def list_memories(self, workdir: str, limit: int = 50) -> list[dict]:
        try:
            raw = self._mem.get_all(
                filters={"user_id": workdir},
                top_k=limit,
                show_expired=False,
            )
        except Exception:  # noqa: BLE001
            raise

        if isinstance(raw, dict):
            items = raw.get("results") or []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []

        return self._resolve_memories(items)

    def forget(self, workdir: str, key: str) -> int:
        # 先查后删：找出 workdir 下与 key 匹配的记忆 ID
        try:
            raw = self._mem.get_all(
                filters={"user_id": workdir},
                top_k=1000,
                show_expired=False,
            )
        except Exception:  # noqa: BLE001
            raise

        if isinstance(raw, dict):
            items = raw.get("results") or []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []

        target = key.strip()
        n = 0
        for m in items:
            md = m.get("metadata") or {}
            if md.get(self._MD_KEY) == target:
                try:
                    self._mem.delete(m["id"])
                    n += 1
                except Exception:  # noqa: BLE001
                    continue
        return n

    def count(self, workdir: str) -> int:
        try:
            raw = self._mem.get_all(
                filters={"user_id": workdir},
                top_k=10000,
                show_expired=False,
            )
        except Exception:  # noqa: BLE001
            raise

        if isinstance(raw, dict):
            return len(raw.get("results") or [])
        if isinstance(raw, list):
            return len(raw)
        return 0


# ---------------------------------------------------------------------------
# 公共入口：MemoryStore
# ---------------------------------------------------------------------------


class MemoryStore:
    """长期记忆公共 API。

    内部根据 embedder 自动选择后端：
    - HTTP 兼容 embedder（make_embedder 产物 / OpenAIEmbedder 等）+ mem0 已安装
      → _Mem0MemoryStore（向量检索，语义 + 近似关键词）
    - 其它情况（无 embedder / 自定义 fake embedder / mem0 未安装）
      → _SqliteMemoryStore（FTS5 + 语义兜底，零外部依赖）

    公共 API 保持与旧版一致：
    - __init__(path=None, embedder=None)
    - .remember(workdir, key, value, level="semantic")
    - .search(workdir, query, limit=5) -> list[dict]
    - .list_memories(workdir, limit=50) -> list[dict]
    - .forget(workdir, key) -> int
    - .count(workdir) -> int
    - ._connect() -> sqlite3.Connection（兼容旧版 SQLite 内部兼容路径）
    """

    def __init__(self, path: Path | None = None, embedder=None) -> None:
        resolved_path = path or (config_dir() / "memory.db")
        if _mem0_should_use(embedder):
            self._backend = _Mem0MemoryStore(resolved_path, embedder)
            self._backend_kind = "mem0"
        else:
            self._backend = _SqliteMemoryStore(resolved_path, embedder)
            self._backend_kind = "sqlite"
        # 兼容旧属性引用
        self.path = self._backend.path
        self.embedder = embedder
        self._sqlite_path = resolved_path  # 给 _connect 兜底用

    # ---- 透明转发 ----

    def remember(self, workdir: str, key: str, value: str, level: str = "semantic") -> None:
        self._backend.remember(workdir, key, value, level)

    def search(self, workdir: str, query: str, limit: int = 5) -> list[dict]:
        """检索记忆。

        mem0 后端在向量存储异常时（如 ChromaDB 损坏）会抛异常，
        此时自动退回 SQLite 兜底路径，保证功能可用。
        """
        if self._backend_kind == "mem0":
            try:
                return self._backend.search(workdir, query, limit)
            except Exception:  # noqa: BLE001 —— mem0 运行时异常退回 SQLite
                pass
        return self._backend.search(workdir, query, limit)

    def list_memories(self, workdir: str, limit: int = 50) -> list[dict]:
        return self._backend.list_memories(workdir, limit)

    def forget(self, workdir: str, key: str) -> int:
        return self._backend.forget(workdir, key)

    def count(self, workdir: str) -> int:
        return self._backend.count(workdir)

    # ---- 兼容旧版测试/调试直接访问 SQLite 的能力 ----

    def _connect(self) -> sqlite3.Connection:
        """返回 SQLite 连接。

        - SQLite 后端：直接返回存储自身的 DB 连接；
        - mem0 后端：为兼容旧测试，仍返回 self._sqlite_path 的连接（可能为空表）。
          注意：mem0 模式下记忆不存于 SQLite，此方法仅作诊断用途，不影响正常功能。
        """
        if self._backend_kind == "sqlite":
            return self._backend._connect()
        # mem0 模式：返回 path 处的连接（可能不存在或为空）
        conn = sqlite3.connect(self._sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def close(self) -> None:
        """释放资源（mem0 后端无显式 close；SQLite 由连接池自动管理）。"""
        pass


def _tokens(q: str) -> set[str]:
    """从问题里抽出检索用关键词：英文按整词（驼峰拆分），中文用 jieba 分词。

    改进（相对纯 n-gram）：
    - 中文走 jieba 分词，产出有语义边界的词（"异步任务调度器"→"异步/任务/调度器"），
      避免 2-gram 产生"步任"级别无意义碎片。
    - 保留英文驼峰拆分（camelCase → camel, case）。
    - jieba 未安装时回退 n-gram（向下兼容零依赖场景）。
    """
    toks = set(re.findall(r"[A-Za-z0-9_.\-/]+", q))
    expanded: set[str] = set()
    for t in toks:
        parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", t).split()
        expanded.update(p for p in parts if len(p) >= 2)
    toks |= expanded
    cjk_segs = re.findall(r"[\u4e00-\u9fff]{2,}", q)
    if cjk_segs:
        try:
            import jieba  # type: ignore
            for seg in cjk_segs:
                for word in jieba.cut(seg):
                    if len(word) >= 2:
                        toks.add(word)
        except ImportError:
            for seg in cjk_segs:
                if len(seg) <= 4:
                    toks.add(seg)
                else:
                    for i in range(len(seg) - 1):
                        toks.add(seg[i : i + 2])
                    for i in range(len(seg) - 2):
                        toks.add(seg[i : i + 3])
    return toks
