"""P2：上下文火焰图单元测试。"""
from __future__ import annotations

from pathlib import Path

from spark.config import SparkConfig
from spark.core.context_profile import profile_context
from spark.models import ChatMessage, ToolCall
from spark.store import SessionStore


def _mk_store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "s.db")


def test_profile_totals_and_segments(tmp_path: Path):
    store = _mk_store(tmp_path)
    cfg = SparkConfig()
    sid = store.create_session(tmp_path, "m1")
    store.append_message(sid, ChatMessage(role="user", content="hello " * 50))
    store.append_message(
        sid,
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(id="1", name="read_file", arguments={"path": "a.py"}),
                ToolCall(id="2", name="read_file", arguments={"path": "a.py"}),
            ],
        ),
    )
    store.append_message(sid, ChatMessage(role="tool", content="x" * 400))
    p = profile_context(store, sid, tmp_path, cfg)
    assert p.total > 0
    kinds = {s.kind for s in p.segments}
    assert {"system", "user", "assistant", "tool"} <= kinds
    assert p.segments[0].kind == "system"  # system 段在最前


def test_waste_signal_repeated_reads(tmp_path: Path):
    store = _mk_store(tmp_path)
    cfg = SparkConfig()
    sid = store.create_session(tmp_path, "m1")
    for _ in range(3):
        store.append_message(
            sid,
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="x", name="read_file", arguments={"path": "same.py"})
                ],
            ),
        )
    p = profile_context(store, sid, tmp_path, cfg)
    assert any("重复 3 次" in w for w in p.waste_signals)


def test_render_contains_bars(tmp_path: Path):
    store = _mk_store(tmp_path)
    sid = store.create_session(tmp_path, "m1")
    store.append_message(sid, ChatMessage(role="user", content="hi"))
    p = profile_context(store, sid, tmp_path, SparkConfig())
    text = p.render()
    assert "上下文剖面" in text and "█" in text
