"""消息操作：截断（编辑重发）与单条删除（持久化）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from spark2.store import SessionStore


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path)


def test_append_assigns_id(store: SessionStore) -> None:
    meta = store.create(workdir="/tmp")
    store.append(meta["id"], {"role": "user", "content": "hi"})
    msgs = store.messages(meta["id"])
    assert msgs and msgs[0].get("id")


def test_truncate_keeps_prefix(store: SessionStore) -> None:
    sid = store.create(workdir="/tmp")["id"]
    ids = []
    for i in range(4):
        store.append(sid, {"role": "user", "content": f"m{i}"})
        ids.append(store.messages(sid)[-1]["id"])
    keep = store.truncate(sid, ids[2])
    assert [m["content"] for m in keep] == ["m0", "m1"]
    assert store.messages(sid) == keep
    assert store.meta(sid)["messages"] == 2


def test_truncate_missing_returns_empty(store: SessionStore) -> None:
    sid = store.create(workdir="/tmp")["id"]
    assert store.truncate(sid, "nope") is None


def test_delete_message_keeps_order(store: SessionStore) -> None:
    sid = store.create(workdir="/tmp")["id"]
    ids = []
    for i in range(3):
        store.append(sid, {"role": "user", "content": f"m{i}"})
        ids.append(store.messages(sid)[-1]["id"])
    rest = store.delete_message(sid, ids[1])
    assert [m["content"] for m in rest] == ["m0", "m2"]


def test_delete_last_message_ok(store: SessionStore) -> None:
    sid = store.create(workdir="/tmp")["id"]
    store.append(sid, {"role": "user", "content": "only"})
    mid = store.messages(sid)[0]["id"]
    rest = store.delete_message(sid, mid)
    assert rest == []
    assert store.meta(sid)["messages"] == 0


def test_search_fulltext_content(store: SessionStore) -> None:
    sid = store.create(workdir="/tmp")["id"]
    store.append(sid, {"role": "user", "content": "帮我修一下登录接口的 bug"})
    rows = store.search("登录接口")
    assert rows and rows[0]["id"] == sid
    assert rows[0]["match"]["kind"] in ("title", "content")  # 首条消息即标题时按 title 命中
    m = rows[0]["match"]
    assert m["kind"] == "title" or "登录接口" in m["snippet"]


def test_search_title_hit(store: SessionStore) -> None:
    sid = store.create(workdir="/tmp")["id"]
    store.append(sid, {"role": "user", "content": "第一句话"})
    # 标题由 append 自动取首条 user 消息前 24 字
    rows = store.search("第一句话")
    assert rows and rows[0]["match"]["kind"] == "title"


def test_search_no_hit(store: SessionStore) -> None:
    sid = store.create(workdir="/tmp")["id"]
    store.append(sid, {"role": "user", "content": "abc"})
    assert store.search("zzz") == []
