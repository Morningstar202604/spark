"""会话存储：每会话一个 JSONL（人可读、可审计），元信息一个 JSON。

与旧版 SQLite 相比：无迁移问题、文件即会话、可直接 grep 查看；
规模小且是本机单用户场景，JSONL 足够且更简单。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path


class SessionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (Path.home() / ".spark2" / "sessions")
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
        with self._msgs_path(sid).open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        meta = self.meta(sid) or {}
        meta["messages"] = meta.get("messages", 0) + 1
        meta["updated"] = _now()
        if meta.get("title") == "新会话" and msg.get("role") == "user" and msg.get("content"):
            meta["title"] = str(msg["content"]).strip().replace("\n", " ")[:24]
        self._write_meta(sid, meta)

    def messages(self, sid: str) -> list[dict]:
        p = self._msgs_path(sid)
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def meta(self, sid: str) -> dict | None:
        p = self._meta_path(sid)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def list(self, limit: int = 50) -> list[dict]:
        rows = []
        for d in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            m = self.meta(d.name)
            if m:
                rows.append(m)
            if len(rows) >= limit:
                break
        return rows

    def delete(self, sid: str) -> bool:
        """删除会话（目录 + 文件），成功返回 True。"""
        import shutil

        d = self._dir(sid)
        if not d.exists():
            return False
        shutil.rmtree(d)
        return True

    def rename(self, sid: str, title: str) -> bool:
        """重命名会话标题，成功返回 True（会话不存在返回 False）。"""
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
        src = self.meta(sid)
        if not src:
            return None
        new = self.create(workdir=str(src.get("workdir") or ""), model=str(src.get("model") or ""))
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
