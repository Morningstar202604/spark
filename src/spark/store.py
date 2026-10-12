"""SQLite-backed session store with WAL mode and schema migrations."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from spark.models import ChatMessage, ToolCall, ToolResult


class SessionStore:
    def __init__(self, db_path: Path | str) -> None:
        # 修复：统一用 str() 比较，避免 Path(":memory:") != ":memory:" (str) 判空失效
        db_path = (
            Path(db_path)
            if isinstance(db_path, str) and db_path != ":memory:"
            else db_path
        )
        if isinstance(db_path, Path) and str(db_path) == ":memory:":
            # Path(":memory:") 不应触发父目录创建，转回字符串统一处理
            db_path = ":memory:"
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    workdir TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    title TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    created_at INTEGER NOT NULL,
                    payload_json TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS tool_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    message_id INTEGER,
                    name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    result_json TEXT,
                    approval TEXT,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bg_jobs (
                    id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    exit_code INTEGER,
                    output TEXT NOT NULL DEFAULT ''
                );
                """
            )
            # WAL + composite indexes: long sessions otherwise degrade to full
            # table scans on every load and pay two fsyncs per appended message.
            # 修复：用 str() 统一比较，防止 Path(":memory:") != ":memory:" 误判
            if str(self.db_path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session_id
                    ON messages(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_tool_events_session_message
                    ON tool_events(session_id, message_id);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_session_id
                    ON checkpoints(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
                    ON sessions(updated_at DESC);
                """
            )
            try:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN compact_from INTEGER"
                )
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN keywords TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN forked_from TEXT")
            except sqlite3.OperationalError:
                pass
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_session(self, workdir: Path, model: str, title: str = "untitled") -> str:
        """Create a new session row and return its id."""
        sid = uuid.uuid4().hex[:12]
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, workdir, model, created_at, updated_at, title) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, str(workdir.resolve()), model, now, now, title),
            )
            self._conn.commit()
        return sid

    def list_sessions(self) -> list[dict]:
        """Return all sessions ordered by most-recently-updated first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, workdir, model, created_at, updated_at, title, keywords FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict | None:
        """Fetch a single session dict, or None if the id is unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_session(self, session_id: str) -> None:
        """Remove a session and its messages/tool-events permanently."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM tool_events WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    def touch(self, session_id: str, title: str | None = None) -> None:
        """Update the session timestamp (and optionally its title)."""
        now = int(time.time())
        with self._lock:
            if title:
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                    (now, title, session_id),
                )
            else:
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
                )
            self._conn.commit()

    def set_title(self, session_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?", (title, session_id)
            )
            self._conn.commit()

    def set_keywords(self, session_id: str, keywords: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET keywords = ? WHERE id = ?", (keywords, session_id)
            )
            self._conn.commit()

    def append_message(self, session_id: str, message: ChatMessage) -> int:
        """Persist a message and bump the session timestamp; return the new row id."""
        now = int(time.time())
        payload = {
            "tool_calls": [c.model_dump() for c in message.tool_calls]
            if message.tool_calls
            else None,
            "tool_call_id": message.tool_call_id,
            "name": message.name,
            "images": [i.model_dump() for i in message.images]
            if message.images
            else None,
        }
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at, payload_json) VALUES (?, ?, ?, ?, ?)",
                (session_id, message.role, message.content, now, json.dumps(payload)),
            )
            last_id = int(cur.lastrowid or 0)
            # 自动标题：首个 user 消息取前 24 字，替代 "untitled"（与会话搜索的标题命中配套）
            if message.role == "user" and message.content:
                self._conn.execute(
                    "UPDATE sessions SET title = ? WHERE id = ? AND title = 'untitled'",
                    (message.content.strip()[:24], session_id),
                )
            # The message and its session timestamp must land together, otherwise a
            # crash between the two commits leaves a message with stale ordering.
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
            self._conn.commit()
        return last_id

    def append_tool_event(
        self,
        session_id: str,
        message_id: int | None,
        name: str,
        arguments: dict,
        result: ToolResult | None,
        approval: str | None,
    ) -> None:
        """Record one tool invocation (name, arguments, result, approval)."""
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """INSERT INTO tool_events
                   (session_id, message_id, name, arguments_json, result_json, approval, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    message_id,
                    name,
                    json.dumps(arguments),
                    json.dumps(result.model_dump()) if result else None,
                    approval,
                    now,
                ),
            )
            self._conn.commit()

    def set_compact_from(self, session_id: str, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET compact_from = ? WHERE id = ?",
                (message_id, session_id),
            )
            self._conn.commit()

    def get_compact_from(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT compact_from FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            return int(row["compact_from"]) if row and row["compact_from"] else 0

    def load_message_ids(self, session_id: str) -> list[int]:
        """Row ids of exactly the messages :meth:`load_messages` would return.

        Compaction needs this to store the cursor as *the id of the first
        retained message*; the in-memory history and the table must be index
        aligned for that to be safe, so the caller compares lengths.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT id FROM messages
                   WHERE session_id = ?
                     AND id >= COALESCE((SELECT compact_from FROM sessions WHERE id = ?), 0)
                   ORDER BY id""",
                (session_id, session_id),
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def load_messages(self, session_id: str) -> list[ChatMessage]:
        """Return messages for a session, honouring the compact-from cut-off."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, role, content, payload_json FROM messages
                   WHERE session_id = ?
                     AND id >= COALESCE((SELECT compact_from FROM sessions WHERE id = ?), 0)
                   ORDER BY id""",
                (session_id, session_id),
            ).fetchall()
        messages: list[ChatMessage] = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            # 修复：json.loads 可能解析出 list/str/number，需校验为 dict 才可 .get()
            if not isinstance(payload, dict):
                payload = {}
            tool_calls = None
            if payload.get("tool_calls"):
                tool_calls = [ToolCall.model_validate(c) for c in payload["tool_calls"]]
            images = None
            if payload.get("images"):
                from spark.models import ImageRef

                images = [ImageRef.model_validate(i) for i in payload["images"]]
            messages.append(
                ChatMessage(
                    role=row["role"],
                    content=row["content"],
                    tool_calls=tool_calls,
                    tool_call_id=payload.get("tool_call_id"),
                    name=payload.get("name"),
                    images=images,
                )
            )
        return messages

    # ---- checkpoints ----

    def add_checkpoint(
        self, session_id: str, label: str, message_id: int, snapshot_json: str
    ) -> int:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO checkpoints (session_id, label, message_id, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, label, message_id, snapshot_json, now),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def list_checkpoints(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, label, message_id, created_at FROM checkpoints WHERE session_id = ? ORDER BY id DESC LIMIT 50",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_checkpoint(self, session_id: str, checkpoint_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? AND id = ?",
                (session_id, checkpoint_id),
            ).fetchone()
            return dict(row) if row else None

    def delete_messages_after(self, session_id: str, message_id: int) -> int:
        """Delete messages with id > message_id (and their tool events). Returns removed count."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND id > ?",
                (session_id, message_id),
            )
            removed = cur.rowcount
            self._conn.execute(
                "DELETE FROM tool_events WHERE session_id = ? AND message_id > ?",
                (session_id, message_id),
            )
            self._conn.commit()
        return removed

    # ---- background jobs ----

    def upsert_bg_job(
        self, job_id: str, command: str, cwd: str, started_at: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO bg_jobs (id, command, cwd, started_at, finished_at, exit_code, output)
                   VALUES (?, ?, ?, ?, NULL, NULL, '')
                   ON CONFLICT(id) DO NOTHING""",
                (job_id, command, cwd, started_at),
            )
            self._conn.commit()

    def update_bg_job(
        self,
        job_id: str,
        *,
        output: str,
        finished_at: float | None,
        exit_code: int | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE bg_jobs SET output = ?, finished_at = ?, exit_code = ? WHERE id = ?",
                (output, finished_at, exit_code, job_id),
            )
            self._conn.commit()

    def list_bg_jobs(self, running_only: bool = False) -> list[dict]:
        sql = "SELECT id, command, cwd, started_at, finished_at, exit_code FROM bg_jobs"
        if running_only:
            sql += " WHERE finished_at IS NULL"
        sql += " ORDER BY started_at DESC LIMIT 50"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_bg_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM bg_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    # ---- fork / message ops / search ----

    def fork(self, session_id: str) -> dict | None:
        """从指定会话分叉出新会话：复制全部消息与工作目录，标题标注"分叉"。

        返回新会话 meta；源会话不存在返回 None。
        """
        with self._lock:
            src = self.get_session(session_id)
            if src is None:
                return None
            new_sid = uuid.uuid4().hex[:12]
            now = int(time.time())
            self._conn.execute(
                "INSERT INTO sessions (id, workdir, model, created_at, updated_at, title, keywords, forked_from)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_sid,
                    src["workdir"],
                    src["model"],
                    now,
                    now,
                    f"{src['title']}（分叉）",
                    src.get("keywords"),
                    session_id,
                ),
            )
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at, payload_json)"
                " SELECT ?, role, content, created_at, payload_json FROM messages WHERE session_id = ?",
                (new_sid, session_id),
            )
            self._conn.commit()
            return self.get_session(new_sid)

    def truncate_messages(self, session_id: str, message_id: int) -> list[ChatMessage] | None:
        """截断：删除指定消息及其后全部消息（编辑重发用）。返回剩余消息；消息不存在返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? AND id = ?",
                (session_id, message_id),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND id >= ?",
                (session_id, message_id),
            )
            self._conn.execute(
                "DELETE FROM tool_events WHERE session_id = ? AND message_id >= ?",
                (session_id, message_id),
            )
            self._conn.commit()
        return self.load_messages(session_id)

    def delete_message(self, session_id: str, message_id: int) -> list[ChatMessage] | None:
        """删除单条消息（持久生效）。返回剩余消息；消息不存在返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? AND id = ?",
                (session_id, message_id),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND id = ?",
                (session_id, message_id),
            )
            self._conn.execute(
                "DELETE FROM tool_events WHERE session_id = ? AND message_id = ?",
                (session_id, message_id),
            )
            self._conn.commit()
        return self.load_messages(session_id)

    def search(self, q: str, limit: int = 20) -> list[dict]:
        """会话搜索：标题命中 kind=title，消息正文命中 kind=content（含 snippet 片段）。

        本地单用户库规模下用 LIKE 子串匹配（对中文友好且零分词配置）；
        数据量上来后可升级 SQLite FTS5 trigram。
        """
        needle = q.strip()
        if not needle:
            return []
        like = f"%{needle}%"
        rows: list[dict] = []
        with self._lock:
            sessions = self._conn.execute(
                "SELECT id, workdir, title, updated_at FROM sessions"
                " WHERE title LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (like, limit),
            ).fetchall()
            for r in sessions:
                rows.append(
                    {
                        "id": r["id"],
                        "workdir": r["workdir"],
                        "title": r["title"],
                        "updated_at": r["updated_at"],
                        "match": {"kind": "title", "snippet": r["title"]},
                    }
                )
            if len(rows) < limit:
                msg_rows = self._conn.execute(
                    "SELECT m.session_id, m.content, s.workdir, s.title, s.updated_at"
                    " FROM messages m JOIN sessions s ON s.id = m.session_id"
                    " WHERE m.content LIKE ? AND s.title NOT LIKE ?"
                    " ORDER BY s.updated_at DESC LIMIT ?",
                    (like, like, limit - len(rows)),
                ).fetchall()
                seen: set[str] = set()
                for r in msg_rows:
                    if r["session_id"] in seen:
                        continue
                    seen.add(r["session_id"])
                    content = r["content"] or ""
                    pos = content.find(needle)
                    start = max(0, pos - 20)
                    snippet = (
                        ("…" if start > 0 else "")
                        + content[start : pos + len(needle) + 20]
                        + ("…" if start + len(needle) + 20 < len(content) else "")
                    )
                    rows.append(
                        {
                            "id": r["session_id"],
                            "workdir": r["workdir"],
                            "title": r["title"],
                            "updated_at": r["updated_at"],
                            "match": {"kind": "content", "snippet": snippet},
                        }
                    )
        return rows[:limit]
