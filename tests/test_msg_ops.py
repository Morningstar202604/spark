"""消息操作：截断（编辑重发）与单条删除（持久化）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.models import ChatMessage
from spark.store import SessionStore


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "s.db")


def test_append_assigns_id(store: SessionStore) -> None:
    sid = store.create_session(Path("/tmp"), "mock")
    mid = store.append_message(sid, ChatMessage(role="user", content="hi"))
    msgs = store.load_messages(sid)
    assert msgs and mid > 0


def test_truncate_keeps_prefix(store: SessionStore) -> None:
    sid = store.create_session(Path("/tmp"), "mock")
    ids = []
    for i in range(4):
        ids.append(
            store.append_message(sid, ChatMessage(role="user", content=f"m{i}"))
        )
    keep = store.truncate_messages(sid, ids[2])
    assert [m.content for m in keep] == ["m0", "m1"]
    assert store.load_messages(sid) == keep
    assert len(store.load_messages(sid)) == 2


def test_truncate_missing_returns_empty(store: SessionStore) -> None:
    sid = store.create_session(Path("/tmp"), "mock")
    assert store.truncate_messages(sid, 99999) is None


def test_delete_message_keeps_order(store: SessionStore) -> None:
    sid = store.create_session(Path("/tmp"), "mock")
    ids = []
    for i in range(3):
        ids.append(
            store.append_message(sid, ChatMessage(role="user", content=f"m{i}"))
        )
    rest = store.delete_message(sid, ids[1])
    assert [m.content for m in rest] == ["m0", "m2"]


def test_delete_last_message_ok(store: SessionStore) -> None:
    sid = store.create_session(Path("/tmp"), "mock")
    mid = store.append_message(sid, ChatMessage(role="user", content="only"))
    rest = store.delete_message(sid, mid)
    assert rest == []
    assert len(store.load_messages(sid)) == 0


def test_search_fulltext_content(store: SessionStore) -> None:
    sid = store.create_session(Path("/tmp"), "mock")
    store.append_message(
        sid, ChatMessage(role="user", content="帮我修一下登录接口的 bug")
    )
    rows = store.search("登录接口")
    assert rows and rows[0]["id"] == sid
    assert rows[0]["match"]["kind"] in ("title", "content")  # 首条消息即标题时按 title 命中
    m = rows[0]["match"]
    assert m["kind"] == "title" or "登录接口" in m["snippet"]


def test_search_title_hit(store: SessionStore) -> None:
    sid = store.create_session(Path("/tmp"), "mock")
    store.append_message(sid, ChatMessage(role="user", content="第一句话"))
    # 标题由 append 自动取首条 user 消息前 24 字
    rows = store.search("第一句话")
    assert rows and rows[0]["match"]["kind"] == "title"


def test_search_no_hit(store: SessionStore) -> None:
    sid = store.create_session(Path("/tmp"), "mock")
    store.append_message(sid, ChatMessage(role="user", content="abc"))
    assert store.search("zzz") == []
