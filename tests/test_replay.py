"""P2 上下文合同：TurnReplayRecorder 归一化 + AgentLoop 接线 + CLI 读取。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.core.replay import (
    SCHEMA_ID,
    TurnReplayRecorder,
    list_replay_sessions,
    load_replays,
    replay_path,
)
from spark.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChatDelta,
    ToolCall,
    ToolResult,
    TurnEvent,
)
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry

# ---------------------------------------------------------------------------
# 记录器归一化
# ---------------------------------------------------------------------------


def _recorder(tmp_path: Path, **kwargs) -> TurnReplayRecorder:
    defaults = dict(
        session_id="sess1",
        workdir=str(tmp_path),
        model="mock",
        prompt="do things",
        home=tmp_path,
    )
    defaults.update(kwargs)
    return TurnReplayRecorder(**defaults)


def test_recorder_normalizes_text_tools_and_approvals(tmp_path: Path) -> None:
    r = _recorder(tmp_path)
    r.observe(TurnEvent(type="text_delta", text="hel"))
    r.observe(TurnEvent(type="text_delta", text="lo"))
    r.observe(TurnEvent(type="reasoning_delta", text="think"))
    call = ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})
    r.observe(TurnEvent(type="tool_start", tool_call=call))
    r.observe(
        TurnEvent(
            type="tool_end",
            tool_call=call,
            result=ToolResult(ok=True, payload={"content": "x"}),
        )
    )
    r.observe(
        TurnEvent(
            type="approval_needed",
            approval=ApprovalRequest(tool_call=call, summary="read a.py"),
            tool_call=call,
        )
    )
    r.record_approval(
        ApprovalRequest(tool_call=call, summary="read a.py"),
        ApprovalDecision(tool_call_id="c1", action="allow"),
    )
    r.observe(TurnEvent(type="plan", data={"steps": [{"title": "s", "status": "completed"}]}))
    r.observe(
        TurnEvent(
            type="compaction",
            data={"before_tokens": 100, "after_tokens": 50, "summarized_messages": 3},
        )
    )
    record = r.flush(
        usage={"input_tokens": 10, "output_tokens": 5, "reasoning_tokens": 1, "source": "reported"},
        context_after={"used": 100, "limit": 32768},
        messages_after=6,
        checkpoint_id=None,
    )
    assert record is not None
    turn = record["turn"]
    assert record["schema"] == SCHEMA_ID
    assert turn["text"] == "hello"
    assert turn["reasoning"] == "think"
    assert turn["tool_calls"][0]["ok"] is True
    assert turn["tool_calls"][0]["result"] == {"content": "x"}
    assert turn["approvals"][0]["decision"] == "allow"
    assert turn["plan"][0]["title"] == "s"
    assert turn["compaction"]["before_tokens"] == 100
    assert turn["usage"]["source"] == "reported"
    assert turn["messages_after"] == 6

    loaded = load_replays("sess1", home=tmp_path)
    assert len(loaded) == 1 and loaded[0]["schema"] == SCHEMA_ID


def test_recorder_survives_orphan_tool_end_and_bad_lines(tmp_path: Path) -> None:
    r = _recorder(tmp_path)
    call = ToolCall(id="ghost", name="ls", arguments={})
    r.observe(TurnEvent(type="tool_end", tool_call=call, result=ToolResult(ok=False, payload={})))
    record = r.flush()
    assert record is not None
    assert record["turn"]["tool_calls"][0]["name"] == "ls"
    # 坏行不影响读取
    path = replay_path("sess1", home=tmp_path)
    path.write_text(path.read_text() + "{broken json\n", encoding="utf-8")
    assert len(load_replays("sess1", home=tmp_path)) == 1


def test_recorder_disabled_never_writes(tmp_path: Path) -> None:
    r = _recorder(tmp_path)
    r.observe(TurnEvent(type="turn_error", text="Turn cancelled"))
    record = r.flush()
    assert record is not None and record["turn"]["status"] == "cancelled"


# ---------------------------------------------------------------------------
# AgentLoop 接线：真实 turn 产出一条记录
# ---------------------------------------------------------------------------


def _make_loop(tmp_path: Path, provider, **kwargs):
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    for key, value in kwargs.items():
        setattr(cfg.agent, key, value)
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    return (
        AgentLoop(
            workdir=tmp_path,
            cfg=cfg,
            provider=provider,
            registry=ToolRegistry(ctx),
            store=store,
            session_id=sid,
        ),
        store,
    )


@pytest.mark.asyncio
async def test_iter_turn_writes_replay_record(tmp_path: Path) -> None:
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="t1", name="list_dir", arguments={"path": "."}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )
    loop, store = _make_loop(tmp_path, provider)
    # conftest 已隔离 SPARK_HOME：replay 直接写隔离目录
    events = [e async for e in loop.iter_turn("list files")]
    assert events[-1].type == "turn_end"
    records = load_replays(loop.session_id)
    assert len(records) == 1
    turn = records[0]["turn"]
    assert turn["prompt"] == "list files"
    assert turn["status"] == "ok"
    assert turn["text"] == "done"
    assert turn["tool_calls"][0]["name"] == "list_dir"
    assert turn["tool_calls"][0]["ok"] is True
    assert turn["usage"]["input_tokens"] >= 0
    assert turn["messages_after"] >= 4
    store.close()


@pytest.mark.asyncio
async def test_replay_disabled_writes_nothing(tmp_path: Path) -> None:
    provider = MockProvider(rounds=[[ChatDelta(type="text", text="hi"), ChatDelta(type="end")]])
    loop, store = _make_loop(tmp_path, provider)
    loop.cfg.replay.enabled = False
    _ = [e async for e in loop.iter_turn("hello")]
    assert list_replay_sessions() == [] or all(
        row["session_id"] != loop.session_id for row in list_replay_sessions()
    )
    store.close()


def test_replay_jsonl_line_is_valid_json(tmp_path: Path) -> None:
    r = _recorder(tmp_path)
    r.flush()
    path = replay_path("sess1", home=tmp_path)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["turn"]["index"] == 0
