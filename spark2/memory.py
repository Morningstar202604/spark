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

from spark2.config import config_dir

_SCHEMA = """
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

# 语义向量列（旧库迁移：缺列时 ALTER 补上）
_EMBED_COLUMN = "ALTER TABLE memories ADD COLUMN embedding TEXT"


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（两向量均为非空数值列表）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


class ApiEmbedder:
    """火山方舟 doubao-embedding（OpenAI 兼容 /embeddings，同步调用）。

    复用主模型的 base_url 与 api_key，无需单独配密钥。
    """

    def __init__(self, base_url: str, api_key: str, model: str = "doubao-embedding-vision", timeout: float = 30.0) -> None:
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


class LocalEmbedder:
    """本地语义嵌入：sentence-transformers + BGE-M3 类国产开源模型（离线）。

    依赖较重（需 pip install sentence-transformers），构造失败会抛 RuntimeError，
    调用方捕获后自动退化为纯关键词检索。
    """

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "本地嵌入需要安装 sentence-transformers（pip install sentence-transformers），"
                "或把 memory_embedding 改为 api 复用豆包向量模型"
            ) from e
        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        try:
            return self.model.encode(texts, normalize_embeddings=True).tolist()
        except Exception:  # noqa: BLE001
            return None


def make_embedder(cfg: dict):
    """按配置构造嵌入器；失败/未启用返回 None（纯关键词检索）。"""
    mode = (cfg.get("memory_embedding") or "off").strip().lower()
    if mode == "api":
        return ApiEmbedder(
            cfg.get("base_url") or "",
            cfg.get("api_key") or "",
            cfg.get("memory_embed_model") or "doubao-embedding-vision",
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
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # 旧库迁移：补 embedding 列（幂等）
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
            if "embedding" not in cols:
                conn.execute(_EMBED_COLUMN)

    # ---------- 写 ----------

    def remember(self, workdir: str, key: str, value: str) -> None:
        """记住一条（同 workdir+key 覆盖更新，符合"记住了就更新"的人的直觉）。"""
        embedding = self._embed_for(key, value)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO memories(workdir, key, value, embedding, created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(workdir, key) DO UPDATE SET value=excluded.value, "
                "embedding=excluded.embedding, created_at=excluded.created_at",
                (workdir, key.strip(), value.strip(), embedding, _now()),
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

    # ---------- 读 ----------

    def list(self, workdir: str, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, key, value, created_at FROM memories WHERE workdir=? "
                "ORDER BY created_at DESC LIMIT ?",
                (workdir, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def search(self, workdir: str, query: str, limit: int = 5) -> list[dict]:
        """检索：语义（若启用）+ 精确 key + FTS5 短语 + 关键词相关；语义命中置顶。"""
        q = (query or "").strip()
        if not q:
            return []
        found: dict[int, dict] = {}
        sem_ids: set[int] = set()

        with self._connect() as conn:
            # 0) 语义检索（可选）：余弦 top-k，阈值过滤低相关噪音
            if self.embedder is not None:
                vec = self.embedder.embed([q])
                if vec and vec[0]:
                    rows = conn.execute(
                        "SELECT id, key, value, embedding, created_at FROM memories "
                        "WHERE workdir=? AND embedding IS NOT NULL",
                        (workdir,),
                    ).fetchall()
                    scored: list[tuple[float, dict]] = []
                    for r in rows:
                        try:
                            emb = json.loads(r["embedding"])
                        except (json.JSONDecodeError, TypeError):
                            continue
                        sim = _cosine(vec[0], emb)
                        if sim > 0.3:  # 阈值：低于此视为无关
                            scored.append((sim, {"id": r["id"], "key": r["key"], "value": r["value"], "created_at": r["created_at"]}))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    for sim, row in scored[:limit]:
                        found[row["id"]] = row
                        sem_ids.add(row["id"])

            # 1) key 精确匹配
            rows = conn.execute(
                "SELECT id, key, value, created_at FROM memories WHERE workdir=? AND key=? LIMIT ?",
                (workdir, q, limit),
            ).fetchall()
            for r in rows:
                found[r["id"]] = dict(r)

            # 2) FTS5 短语匹配（把用户输入当完整短语，避免分词歧义）
            phrase = '"' + q.replace('"', '""') + '"'
            try:
                rows = conn.execute(
                    "SELECT m.id, m.key, m.value, m.created_at FROM memories_fts f "
                    "JOIN memories m ON m.id=f.rowid "
                    "WHERE m.workdir=? AND memories_fts MATCH ? ORDER BY m.created_at DESC LIMIT ?",
                    (workdir, phrase, limit),
                ).fetchall()
                for r in rows:
                    found[r["id"]] = dict(r)
            except sqlite3.OperationalError:
                pass

            # 3) 关键词相关：问题里是否出现记忆的 key/重要词（中文按双字切）
            toks = _tokens(q)
            if toks:
                conds, params = [], [workdir]
                for t in toks:
                    conds.append("(key LIKE ? OR value LIKE ?)")
                    params += [f"%{t}%", f"%{t}%"]
                rows = conn.execute(
                    "SELECT id, key, value, created_at FROM memories WHERE workdir=? "
                    f"AND ({' OR '.join(conds)}) ORDER BY created_at DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
                for r in rows:
                    found[r["id"]] = dict(r)

        # 排序：语义命中置顶，其余按时间新→旧
        items = sorted(
            found.values(),
            key=lambda m: (m["id"] in sem_ids, m["created_at"]),
            reverse=True,
        )
        return items[:limit]

    def count(self, workdir: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM memories WHERE workdir=?", (workdir,)).fetchone()
            return int(row["n"])


def _tokens(q: str) -> set[str]:
    """从问题里抽出检索用关键词：英文按整词，中文连续段按双字切（含 3~4 字片段）。"""
    toks = set(re.findall(r"[A-Za-z0-9_.\-/]+", q))
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", q):
        if len(seg) <= 4:
            toks.add(seg)
        for i in range(len(seg) - 1):
            toks.add(seg[i : i + 2])
    return toks
