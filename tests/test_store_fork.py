"""会话分叉（fork）测试：复制消息、独立会话、标题标注。"""
from __future__ import annotations

from pathlib import Path

from spark.models import ChatMessage
from spark.store import SessionStore


def test_fork_copies_messages(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    src = store.create_session(tmp_path / "proj", "mock")
    store.append_message(src, ChatMessage(role="user", content="帮我修一下登录接口"))
    store.append_message(src, ChatMessage(role="assistant", content="好的，我先看下代码"))

    fork = store.fork(src)
    assert fork is not None
    assert fork["id"] != src
    assert fork["workdir"] == str((tmp_path / "proj").resolve())
    assert "分叉" in fork["title"]
    assert fork.get("forked_from") == src
    # 消息完整复制且独立
    assert store.load_messages(fork["id"]) == store.load_messages(src)
    assert store.load_messages(fork["id"])[0].role == "user"
    # 在分叉上追加不影响源会话
    store.append_message(
        fork["id"], ChatMessage(role="assistant", content="这是分叉的新回复")
    )
    assert len(store.load_messages(fork["id"])) == 3
    assert len(store.load_messages(src)) == 2


def test_fork_missing_session_returns_none(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    assert store.fork("no-such-id") is None
