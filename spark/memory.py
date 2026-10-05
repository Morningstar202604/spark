"""长期记忆：SQLite + FTS5，显式记住/忘记，按工作目录隔离。

原则（与旧版相反）：
- 零隐性 LLM 调用：只有模型显式调用 remember/forget 才写记忆；
  每轮检索默认是纯本地 FTS5/LIKE，不花钱、不联网。
- 不造轮子：直接用 stdlib sqlite3 与 SQLite 内置 FTS5，不用外部向量库。
- 中文优先：FTS5 对 CJK 分词不友好，检索按"用户问题是否包含记忆关键词"来判断
  相关（英文按词、中文按双字），保证中文短记忆也能稳定命中。

语义记忆（可选，v0.4.0+）：
- memory_embedding=off  （默认）：纯关键词检索，零依赖；
- memory_embedding=api ：复用主模型 base_url/api_key 调火山方舟 doubao-embedding（推荐，免配置）；
- memory_embedding=local：本地 sentence-transformers + BGE-M3 类国产开源模型（离线）。
- 未配置/调用失败自动退回关键词检索，语义只是加分项，从不阻断。
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


class MemoryStore:
    def __init__(self, path: Path | None = None, embedder=None) -> None:
        self.path = path or (config_dir() / "memory.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        # 嵌入向量缓存：key=raw JSON string, value=已decode的list[float]
        # 避免同一 search 内重复 json.loads（全表扫描时每条 embedding 都解析一次）
        self._emb_cache: dict[str, list[float]] = {}
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # busy_timeout：并发写时等待而非立即报 "database is locked"
        # 默认 5000ms，WAL 模式下读不阻塞写，但写仍串行；此设置减少 SQLITE_BUSY 爆出的概率
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_V1)
            # ---------- Schema 版本化迁移（不清零数据） ----------
            ver = self._schema_version(conn)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
            if ver < 2 and "embedding" not in cols:
                conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
            if ver < 3 and "level" not in cols:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN level TEXT NOT NULL DEFAULT 'semantic'"
                )
            self._set_schema_version(conn, CURRENT_SCHEMA_VERSION)

            # ---------- FTS 索引健康检查（极端情况：结构损坏则重建索引） ----------
            # 仅重建 FTS 索引（不含源数据），memories 表数据完整保留。
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
                    # 源数据回灌：把 memories 表已有数据同步到新 FTS 索引
                    conn.execute(
                        "INSERT INTO memories_fts(rowid, key, value) "
                        "SELECT id, key, value FROM memories"
                    )
            except Exception:  # noqa: BLE001 —— 极端情况不阻断记忆读写
                pass

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        """返回当前库的 schema 版本；未建版本表返回 0（旧库兼容）。"""
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
        """写 schema 版本记录（先建表，再 UPSERT）。"""
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
        """记住一条（同 workdir+key 覆盖更新，符合"记住了就更新"的人的直觉）。

        level 为记忆分层：situational=情景 / semantic=语义(默认) / episodic=事件 / procedural=程序。
        """
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
        """按 key 忘记；返回删除条数。"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE workdir=? AND key=?", (workdir, key.strip()))
            return cur.rowcount

    def delete_by_id(self, memory_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            return cur.rowcount > 0

    def _decode_emb(self, raw: str) -> list[float] | None:
        """带缓存的 embedding 解码：raw JSON → list[float]。

        缓存 _emb_cache 在多次 search 调用间持续生效；remember() 写入后
        进程不重启即保留缓存，同一条 embedding 仅 decode 一次。
        """
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
        # 防止缓存无限增长（极端高频新写入场景）：上限 10000 条
        if len(self._emb_cache) > 10000:
            # 简易 FIFO 清除：移除前 2000 条
            for k in list(self._emb_cache)[:2000]:
                self._emb_cache.pop(k, None)
        return emb

    def _rank_sem(self, vec: list[float], rows, limit: int) -> list[tuple[float, dict]]:
        """对一组带 embedding 的记忆行做余弦相似度排序；返回 [(sim, row_dict)]（降序）。"""
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
        """检索：语义（若启用）+ 精确 key + FTS5 短语 + 关键词相关。

        多路召回结果通过 RRF（Reciprocal Rank Fusion）融合排序，
        避免弱路径污染强路径；同分时按时间新→旧。
        """
        q = (query or "").strip()
        if not q:
            return []

        # 收集各召回路径的 ranked lists（有序，index=rank）
        fts_ranked: list[dict] = []     # FTS5 短语匹配
        kw_ranked: list[dict] = []      # 关键词 LIKE
        sem_ranked: list[tuple[float, dict]] = []  # 语义向量 (sim, row)
        exact_ids: set[int] = set()     # 精确 key 命中

        with self._connect() as conn:
            # 0) key 精确匹配（直接收集 ID，不参与 RRF 排名但保底）
            for r in conn.execute(
                "SELECT id FROM memories WHERE workdir=? AND key=? LIMIT 1",
                (workdir, q),
            ).fetchall():
                exact_ids.add(r["id"])

            # 1) FTS5 短语匹配
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

            # 2) 关键词 LIKE
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

            # 3) 语义向量（可选）：全表缓存避免重复 json.loads
            if self.embedder is not None:
                vec = self.embedder.embed([q])
                if vec and vec[0]:
                    rows = conn.execute(
                        "SELECT id, key, value, level, embedding, created_at FROM memories "
                        "WHERE workdir=? AND embedding IS NOT NULL",
                        (workdir,),
                    ).fetchall()
                    sem_ranked = self._rank_sem(vec[0], rows, limit * 2)

        # ---- RRF 融合（k=60 是常用默认值；精确匹配加权置顶）----
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

        # 精确 key 命中额外加权（相当于 RRF 里排名 0）
        for rid in exact_ids:
            if rid in all_rows:
                rrf_scores[rid] += 1.0 / (RRF_K + 0)

        # 按 RRF 分降序；同分按语义分 → 时间新→旧
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


def _tokens(q: str) -> set[str]:
    """从问题里抽出检索用关键词：英文按整词，中文连续段按多粒度滑窗。

    改进（相对原纯 2-gram）：
    - 长度 2~4：原文直接加入（避免碎片化 token 查不到短记忆）。
    - 长度 ≥5：2-gram 与 3-gram 联合滑窗——兼顾召回（2-gram 更宽松）与精度（3-gram 更窄）。
    - 英文连续段进一步尝试驼峰拆分（camelCase → camel, case），提升代码相关查询的命中率。
    """
    toks = set(re.findall(r"[A-Za-z0-9_.\-/]+", q))
    # 驼峰拆分：camelCase → camel, case
    expanded: set[str] = set()
    for t in toks:
        parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", t).split()
        expanded.update(p for p in parts if len(p) >= 2)
    toks |= expanded
    # 中文连续段：多粒度滑窗
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", q):
        if len(seg) <= 4:
            toks.add(seg)
        else:
            # 2-gram：更高召回（任意两个字都能命中）
            for i in range(len(seg) - 1):
                toks.add(seg[i : i + 2])
            # 3-gram：更高精度（减少跨语义边界的误命中，如"异步任务调度器"里"步任"这种无意义 2-gram）
            for i in range(len(seg) - 2):
                toks.add(seg[i : i + 3])
    return toks
