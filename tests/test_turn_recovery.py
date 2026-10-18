"""Turn 级恢复（A10）端到端测试：审批挂起 → 模拟崩溃 → 全新实例恢复。

覆盖：
- 持久 checkpointer 下审批挂起 → 新 AgentLoop（同库）resume → 行为完整
- pending 登记生命周期（turn 开始登记 / 终态清除 / 挂起保留）
- deny via interrupt（恢复后拒绝执行）
- discard 丢弃挂起 turn
- 无审批 turn 正常结束 → checkpoint 清理无残留
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.core.turn_store import TurnCheckpointer
from spark.models import ApprovalDecision, ApprovalRequest, ChatDelta, ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def _tool_call_provider() -> MockProvider:
    return MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="c1", name="run_shell", arguments={"command": "echo hi > out.txt"}
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )


async def _make_loop(
    tmp_path: Path,
    cp: TurnCheckpointer,
    provider: MockProvider,
    approver=None,
    approval: str = "suggest",
) -> AgentLoop:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = approval  # type: ignore[assignment]
    store = SessionStore(Path(":memory:"))
    sid = store.create_session(tmp_path, cfg.provider.model, title="t")
    registry = ToolRegistry(
        ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    )
    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=registry,
        store=store,
        session_id=sid,
        approver=approver,
        checkpointer=cp,
    )


@pytest.fixture
async def cp(tmp_path: Path):
    checkpointer = TurnCheckpointer(tmp_path / "checkpoints.db")
    await checkpointer.start()
    yield checkpointer
    await checkpointer.close()


async def test_approval_suspension_and_crash_recovery(
    tmp_path: Path, cp: TurnCheckpointer
) -> None:
    """审批挂起 → 模拟崩溃（放弃原 loop）→ 全新 loop 恢复 → 工具执行完整。"""
    # 进程 A：跑到审批挂起后中断（模拟崩溃：审批处放弃 loop_a）
    provider = _tool_call_provider()
    loop_a = await _make_loop(tmp_path, cp, provider, approver=None, approval="suggest")

    events_a: list = []
    async for event in loop_a.iter_turn("write file"):
        events_a.append(event)
        if event.type == "approval_needed":
            break  # 模拟崩溃：审批处中断，丢弃 loop_a
    assert events_a[-1].type == "approval_needed"

    # 挂起登记在案
    pending = await cp.list_pending()
    assert len(pending) == 1
    thread_id = pending[0]["thread_id"]
    assert pending[0]["session_id"] == loop_a.session_id

    # 进程 B：全新 loop（同 checkpointer 库），从挂起点恢复
    allow_calls: list[str] = []

    async def approver(request: ApprovalRequest) -> ApprovalDecision:
        allow_calls.append(request.tool_call.name)
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="allow")

    loop_b = await _make_loop(tmp_path, cp, provider, approver=approver, approval="suggest")
    events_b = [event async for event in loop_b.resume_pending_turn(thread_id)]
    types = [e.type for e in events_b]

    # 恢复后重新走审批 → 执行 → 收尾
    assert "approval_needed" in types
    assert "tool_start" in types and "tool_end" in types
    assert types[-1] == "turn_end"
    assert allow_calls == ["run_shell"]
    assert (tmp_path / "out.txt").exists()

    # 恢复完成 → 登记清除
    assert await cp.list_pending() == []


async def test_recovery_deny_does_not_execute(tmp_path: Path, cp: TurnCheckpointer) -> None:
    """恢复后拒绝审批：工具不执行，loop 继续收尾。"""
    provider = _tool_call_provider()
    loop_a = await _make_loop(tmp_path, cp, provider, approver=None, approval="suggest")
    async for event in loop_a.iter_turn("write file"):
        if event.type == "approval_needed":
            break
    thread_id = (await cp.list_pending())[0]["thread_id"]

    async def deny(request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="deny")

    loop_b = await _make_loop(tmp_path, cp, provider, approver=deny, approval="suggest")
    events = [event async for event in loop_b.resume_pending_turn(thread_id)]

    tool_end = next(e for e in events if e.type == "tool_end")
    assert tool_end.result is not None and tool_end.result.ok is False
    assert tool_end.result.payload.get("error") == "denied"
    assert not (tmp_path / "out.txt").exists()
    assert [e.type for e in events][-1] == "turn_end"


async def test_clean_turn_leaves_no_pending(tmp_path: Path, cp: TurnCheckpointer) -> None:
    """无审批（full-auto）turn 正常结束：pending 清空，checkpoint 无残留。"""
    provider = MockProvider(rounds=[[ChatDelta(type="text", text="hi"), ChatDelta(type="end")]])
    loop = await _make_loop(tmp_path, cp, provider, approval="full-auto")
    events = [event async for event in loop.iter_turn("hello")]
    assert events[-1].type == "turn_end"
    assert await cp.list_pending() == []


async def test_discard_pending_turn(tmp_path: Path, cp: TurnCheckpointer) -> None:
    """丢弃挂起 turn：登记与 checkpoint 一并清理，恢复时明确报错。"""
    provider = _tool_call_provider()
    loop_a = await _make_loop(tmp_path, cp, provider, approver=None, approval="suggest")
    async for event in loop_a.iter_turn("write file"):
        if event.type == "approval_needed":
            break
    thread_id = (await cp.list_pending())[0]["thread_id"]

    await loop_a.discard_pending_turn(thread_id)
    assert await cp.list_pending() == []

    loop_b = await _make_loop(tmp_path, cp, provider, approval="suggest")
    with pytest.raises(ValueError, match="no pending turn"):
        async for _ in loop_b.resume_pending_turn(thread_id):
            pass


async def test_recovery_message_chain(tmp_path: Path, cp: TurnCheckpointer) -> None:
    """恢复的 turn 落库一致：assistant(tool_calls)→tool→assistant 消息链完整。"""
    provider = _tool_call_provider()
    loop_a = await _make_loop(tmp_path, cp, provider, approver=None, approval="suggest")
    async for event in loop_a.iter_turn("write file"):
        if event.type == "approval_needed":
            break
    thread_id = (await cp.list_pending())[0]["thread_id"]

    async def allow(request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="allow")

    loop_b = await _make_loop(tmp_path, cp, provider, approver=allow, approval="suggest")
    # 注意：恢复使用 loop_b 的 store（测试内 :memory: 独立）——消息链在 loop_b 落库
    [event async for event in loop_b.resume_pending_turn(thread_id)]
    roles = [m.role for m in loop_b.store.load_messages(loop_b.session_id)]
    # 恢复重放：approve 重跑无副作用；execute 落 assistant(tool_calls)→tool→assistant(text)
    assert roles[-3:] == ["assistant", "tool", "assistant"]
    assert loop_b.history[-1].content == "done"
