"""会话全文检索：FTS5 命中消息正文，而不只是标题。

侧栏此前只在已加载的 `sessions` 行（title/keywords/model）里做子串过滤，
用户搜聊天里出现过的话永远搜不到。store.search() 早就有 FTS5 实现，
这里覆盖它的检索语义与降级路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.models import ChatMessage
from spark.store import SessionStore


@pytest.fixture()
def store(tmp_path: Path):
    s = SessionStore(tmp_path / "sessions.db")
    work = tmp_path / "work"
    work.mkdir()
    yield s, work
    s.close()


def test_search_matches_message_body(store) -> None:
    s, work = store
    sid = s.create_session(work, "mock", title="无关标题")
    s.append_message(sid, ChatMessage(role="user", content="帮我看看这个鉴权流程怎么走的"))
    s.append_message(sid, ChatMessage(role="assistant", content="鉴权分为三层作用域"))

    hits = s.search("鉴权流程")
    assert hits, "should find the message body"
    assert hits[0]["id"] == sid
    assert hits[0]["match"]["kind"] == "content"
    assert "鉴权" in hits[0]["match"]["snippet"]


def test_search_matches_title(store) -> None:
    s, work = store
    sid = s.create_session(work, "mock", title="部署脚本排查")
    hits = s.search("部署脚本")
    assert hits and hits[0]["id"] == sid
    assert hits[0]["match"]["kind"] == "title"


def test_search_multi_byte_chinese_substring(store) -> None:
    """trigram 索引要能命中中文子串（不是整词）。"""
    s, work = store
    sid = s.create_session(work, "mock", title="t")
    s.append_message(sid, ChatMessage(role="user", content="数据库连接池的最大连接数应该设多少"))
    hits = s.search("连接池")
    assert hits and hits[0]["id"] == sid


def test_search_short_query_falls_back(store) -> None:
    """2 字符查询 trigram 无匹配，必须走 LIKE 兜底而不是返回空。"""
    s, work = store
    s.create_session(work, "mock", title="ab测试")
    hits = s.search("ab")
    assert hits, "short query must still match via LIKE fallback"


def test_search_empty_returns_empty(store) -> None:
    s, _ = store
    assert s.search("") == []
    assert s.search("   ") == []


def test_search_no_match(store) -> None:
    s, work = store
    s.create_session(work, "mock", title="hello")
    assert s.search("完全不存在的词xyz") == []


def test_search_dedupes_session(store) -> None:
    """同一会话多条消息命中，只应出现一次。"""
    s, work = store
    sid = s.create_session(work, "mock", title="dup")
    s.append_message(sid, ChatMessage(role="user", content="关键词甲乙丙"))
    s.append_message(sid, ChatMessage(role="assistant", content="关键词甲乙丙 又出现一次"))
    hits = s.search("关键词甲乙丙")
    assert len([h for h in hits if h["id"] == sid]) == 1


def test_search_limit_respected(store) -> None:
    s, work = store
    for i in range(8):
        sid = s.create_session(work, "mock", title=f"s{i}")
        s.append_message(sid, ChatMessage(role="user", content=f"共同关键词内容 {i}"))
    hits = s.search("共同关键词内容", limit=3)
    assert len(hits) <= 3


def test_search_records_workdir(store) -> None:
    """路由层按 workdir 过滤，store 必须把 workdir 带出来。"""
    s, work = store
    sid = s.create_session(work, "mock", title="w")
    s.append_message(sid, ChatMessage(role="user", content="定位测试内容"))
    hits = s.search("定位测试内容")
    assert hits[0]["workdir"] == str(work)


def test_search_survives_broken_fts(tmp_path: Path) -> None:
    """FTS 表被破坏时应降级到 LIKE，而不是抛异常。"""
    s = SessionStore(tmp_path / "s.db")
    try:
        work = tmp_path / "w"
        work.mkdir()
        sid = s.create_session(work, "mock", title="fallback标题")
        s.append_message(sid, ChatMessage(role="user", content="降级测试正文"))
        s._conn.execute("DROP TABLE IF EXISTS messages_fts")
        s._conn.execute("DROP TABLE IF EXISTS sessions_fts")
        s._conn.commit()
        hits = s.search("fallback", limit=5)
        assert isinstance(hits, list)
    finally:
        s.close()
