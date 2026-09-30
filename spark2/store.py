"""会话存储：每会话一个 JSONL（人可读、可审计），元信息一个 JSON。

与旧版 SQLite 相比：无迁移问题、文件即会话、可直接 grep 查看；
规模小且是本机单用户场景，JSONL 足够且更简单。
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from spark2.config import config_dir

_SID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _safe_sid(sid: str) -> bool:
    """会话 id 白名单校验：拒绝 "../" 与绝对路径穿越（Path(root)/绝对路径会被接管）。"""
    return bool(sid) and _SID_RE.fullmatch(sid) is not None


class SessionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (
            config_dir() / "sessions"
        )  # 动态取 SPARK2_HOME，见 config.py 契约
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, sid: str) -> Path:
        return self.root / sid

    def _meta_path(self, sid: str) -> Path:
        return self._dir(sid) / "meta.json"

    def _msgs_path(self, sid: str) -> Path:
        return self._dir(sid) / "messages.jsonl"

    def create(self, workdir: str, model: str = "") -> dict:
        sid = uuid.uuid4().hex[:12]
        d = self._dir(sid)
        d.mkdir(parents=True, exist_ok=True)
        meta = {
            "id": sid,
            "title": "新会话",
            "workdir": workdir,
            "model": model,
            "created": _now(),
            "updated": _now(),
            "messages": 0,
        }
        self._write_meta(sid, meta)
        self._msgs_path(sid).touch()
        return meta

    def _write_meta(self, sid: str, meta: dict) -> None:
        self._meta_path(sid).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def append(self, sid: str, msg: dict) -> None:
        if not _safe_sid(sid):
            return
        # 消息 id：删除/编辑重发等操作依赖稳定 id；无 id 的旧消息按顺序补 legacy 位次
        if not msg.get("id"):
            msg["id"] = uuid.uuid4().hex[:12]
        with self._msgs_path(sid).open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        meta = self.meta(sid) or {}
        meta["messages"] = meta.get("messages", 0) + 1
        meta["updated"] = _now()
        if (
            meta.get("title") == "新会话"
            and msg.get("role") == "user"
            and msg.get("content")
        ):
            meta["title"] = str(msg["content"]).strip().replace("\n", " ")[:24]
        self._write_meta(sid, meta)

    def messages(self, sid: str) -> list[dict]:
        if not _safe_sid(sid):
            return []
        p = self._msgs_path(sid)
        if not p.exists():
            return []
        out = []
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not msg.get("id"):
                # 旧版本消息没有 id：按位次生成稳定 legacy id（删除/编辑仍可定位）
                msg["id"] = f"legacy_{i:04d}"
            out.append(msg)
        return out

    def _rewrite_messages(self, sid: str, msgs: list[dict]) -> None:
        """整段重写消息文件（截断/删除后），并刷新元信息计数。"""
        with self._msgs_path(sid).open("w", encoding="utf-8") as f:
            for m in msgs:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        meta = self.meta(sid) or {}
        meta["messages"] = len(msgs)
        meta["updated"] = _now()
        self._write_meta(sid, meta)

    def truncate(self, sid: str, message_id: str) -> list[dict] | None:
        """编辑重发：删除 message_id 及之后所有消息，返回保留的消息列表。

        None = 消息不存在；[] = 截断成功且无保留消息（截断到首条）。"""
        msgs = self.messages(sid)
        idx = next((i for i, m in enumerate(msgs) if m.get("id") == message_id), None)
        if idx is None:
            return None
        keep = msgs[:idx]
        self._rewrite_messages(sid, keep)
        return keep

    def delete_message(self, sid: str, message_id: str) -> list[dict] | None:
        """删除单条消息（其余保持原顺序），返回剩余消息列表。

        None = 消息不存在；[] = 删除成功且会话已空。"""
        msgs = self.messages(sid)
        rest = [m for m in msgs if m.get("id") != message_id]
        if len(rest) == len(msgs):
            return None
        self._rewrite_messages(sid, rest)
        return rest

    def meta(self, sid: str) -> dict | None:
        if not _safe_sid(sid):
            return None
        p = self._meta_path(sid)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def list(self, limit: int = 50) -> list[dict]:
        rows: list[dict] = []
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return rows
        stamped: list[tuple[float, Path]] = []
        for d in entries:
            # 目录可能在遍历间隙被删除（并发 delete）：跳过而不是把列表接口打挂
            try:
                if d.is_dir():
                    stamped.append((d.stat().st_mtime, d))
            except OSError:
                continue
        stamped.sort(key=lambda x: x[0], reverse=True)
        for _, d in stamped:
            m = self.meta(d.name)
            if m:
                rows.append(m)
            if len(rows) >= limit:
                break
        return rows

    def search(self, q: str, limit: int = 20) -> list[dict]:
        """会话全文搜索：标题/目录命中优先，其次消息正文命中。

        命中的 meta 附带 match 字段（kind: title|content；content 带 role + 片段），
        供前端展示匹配位置；标题/目录命中按最近优先。
        """
        ql = q.lower()
        out: list[dict] = []
        for m in self.list(limit=200):
            title = str(m.get("title") or "")
            wd = str(m.get("workdir") or "")
            if ql in title.lower() or ql in wd.lower():
                m2 = dict(m)
                m2["match"] = {"kind": "title"}
                out.append(m2)
                if len(out) >= limit:
                    break
                continue
            for msg in self.messages(m.get("id", "")):
                txt = msg.get("content") or ""
                if isinstance(txt, list):  # 多模态 parts：取 text 段
                    txt = " ".join(
                        str(p.get("text") or "")
                        for p in txt
                        if isinstance(p, dict) and p.get("text")
                    )
                txt = str(txt)
                pos = txt.lower().find(ql)
                if pos < 0:
                    continue
                start = max(0, pos - 24)
                snippet = ("…" if start else "") + txt[start : start + 84] + ("…" if start + 84 < len(txt) else "")
                m2 = dict(m)
                m2["match"] = {"kind": "content", "role": str(msg.get("role") or ""), "snippet": snippet}
                out.append(m2)
                break
            if len(out) >= limit:
                break
        return out

    def delete(self, sid: str) -> bool:
        """删除会话（目录 + 文件），成功返回 True。"""
        import shutil

        if not _safe_sid(sid):
            return False
        d = self._dir(sid)
        if not d.exists():
            return False
        shutil.rmtree(d)
        return True

    def rename(self, sid: str, title: str) -> bool:
        """重命名会话标题，成功返回 True（会话不存在返回 False）。"""
        if not _safe_sid(sid):
            return False
        meta = self.meta(sid)
        if not meta:
            return False
        title = (title or "").strip()
        if not title:
            return False
        meta["title"] = title[:60]
        meta["updated"] = _now()
        self._write_meta(sid, meta)
        return True

    def fork(self, sid: str) -> dict | None:
        """从 sid 分叉出新会话：复制全部消息与工作目录，标题标注"分叉"。

        返回新会话 meta；源会话不存在返回 None。
        """
        if not _safe_sid(sid):
            return None
        src = self.meta(sid)
        if not src:
            return None
        new = self.create(
            workdir=str(src.get("workdir") or ""), model=str(src.get("model") or "")
        )
        for m in self.messages(sid):
            self.append(new["id"], m)
        meta = self.meta(new["id"])
        assert meta is not None
        meta["title"] = f"{str(src.get('title') or '会话')} · 分叉"
        meta["forked_from"] = sid
        self._write_meta(meta["id"], meta)
        return meta


def _now() -> str:
    import datetime

    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")
