"""会话分叉（fork）测试：复制消息、独立会话、标题标注。"""
from __future__ import annotations

from pathlib import Path

from spark2.store import SessionStore


def test_fork_copies_messages(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    src = store.create(str(tmp_path / "proj"))
    store.append(src["id"], {"role": "user", "content": "帮我修一下登录接口"})
    store.append(src["id"], {"role": "assistant", "content": "好的，我先看下代码"})

    fork = store.fork(src["id"])
    assert fork is not None
    assert fork["id"] != src["id"]
    assert fork["workdir"] == str(tmp_path / "proj")
    assert "分叉" in fork["title"]
    assert fork.get("forked_from") == src["id"]
    # 消息完整复制且独立
    assert store.messages(fork["id"]) == store.messages(src["id"])
    assert store.messages(fork["id"])[0]["role"] == "user"
    # 在分叉上追加不影响源会话
    store.append(fork["id"], {"role": "assistant", "content": "这是分叉的新回复"})
    assert len(store.messages(fork["id"])) == 3
    assert len(store.messages(src["id"])) == 2


def test_fork_missing_session_returns_none(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    assert store.fork("no-such-id") is None
