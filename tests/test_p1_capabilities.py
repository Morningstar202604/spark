import asyncio
import time
from pathlib import Path

from spark.config import AgentConfig, SparkConfig
from spark.core.checkpoints import restore_workdir, snapshot_workdir
from spark.models import ChatMessage
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools import bg
from spark.tools.registry import ToolContext, ToolRegistry


def _cfg(mode: str = "workspace") -> SparkConfig:
    return SparkConfig(agent=AgentConfig(sandbox_mode=mode))  # type: ignore[arg-type]


def test_snapshot_and_restore_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("world")
    sid = snapshot_workdir(tmp_path)
    assert sid
    # mutate then restore
    (tmp_path / "a.txt").write_text("changed")
    (tmp_path / "sub" / "b.txt").unlink()
    report = restore_workdir(tmp_path, sid)
    assert report["restored_files"] >= 2
    assert (tmp_path / "a.txt").read_text() == "hello"
    assert (tmp_path / "sub" / "b.txt").read_text() == "world"


def test_restore_rejects_traversal(tmp_path: Path) -> None:
    import tarfile

    from spark.core.checkpoints import SNAPSHOT_DIR

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    evil = SNAPSHOT_DIR / "evil-test.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("pwn")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escape.txt")
    try:
        import pytest

        with pytest.raises(ValueError):
            restore_workdir(tmp_path, "evil-test")
    finally:
        evil.unlink()


def test_checkpoint_store_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(":memory:")
    sid = store.create_session(tmp_path, "test-model")
    mid = store.append_message(sid, ChatMessage(role="user", content="hello"))
    store.append_message(sid, ChatMessage(role="assistant", content="hi there"))
    cp_id = store.add_checkpoint(sid, "test point", mid, '{"snapshot_id": "x"}')
    cps = store.list_checkpoints(sid)
    assert len(cps) == 1 and cps[0]["id"] == cp_id
    record = store.get_checkpoint(sid, cp_id)
    assert record is not None and record["label"] == "test point"
    removed = store.delete_messages_after(sid, mid)
    assert removed == 1
    assert len(store.load_messages(sid)) == 1
    store.close()


def test_auto_checkpoint_and_rollback_e2e(tmp_path: Path) -> None:
    """Write file -> turn ends -> checkpoint auto-created; mutate; rollback restores v1."""
    from spark.config import SparkConfig as SC
    from spark.core.loop import AgentLoop as AL

    (tmp_path / "hello.txt").write_text("v1")
    cfg = SC(provider={"name": "mock", "model": "mock"}, agent=AgentConfig(sandbox_mode="workspace", approval="full-auto"))
    store = SessionStore(":memory:")
    sid = store.create_session(tmp_path, "mock", title="t")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    loop = AL(workdir=tmp_path, cfg=cfg, provider=None, registry=ToolRegistry(ctx), store=store, session_id=sid)  # type: ignore[arg-type]

    store.append_message(sid, ChatMessage(role="user", content="write hello file"))
    store.append_message(sid, ChatMessage(role="tool", name="write_file", content='{"ok": true}'))
    mid = store.append_message(sid, ChatMessage(role="assistant", content="done"))
    loop.history = store.load_messages(sid)
    loop._maybe_auto_checkpoint(mid)
    cps = store.list_checkpoints(sid)
    assert len(cps) == 1
    cp_id = cps[0]["id"]

    (tmp_path / "hello.txt").write_text("v2 changed")
    (tmp_path / "extra.txt").write_text("extra")

    report = loop.rollback_to_checkpoint(cp_id)
    assert report["files"]["restored_files"] >= 1
    assert (tmp_path / "hello.txt").read_text() == "v1"
    store.close()


def test_bg_start_output_kill(tmp_path: Path) -> None:
    sandbox = WorkdirSandbox(tmp_path, _cfg())
    r = bg.bg_start_tool(sandbox, {"command": "echo started && sleep 30"})
    assert r.ok, r.payload
    job_id = r.payload["job_id"]
    deadline = time.time() + 5
    out = None
    while time.time() < deadline:
        out = bg.bg_output_tool({"job_id": job_id})
        if "started" in str(out.payload.get("output", "")):
            break
        time.sleep(0.1)
    assert out is not None and "started" in str(out.payload.get("output", ""))
    assert out.payload["running"] is True
    kill = bg.bg_kill_tool({"job_id": job_id})
    assert kill.ok and kill.payload["killed"] is True
    deadline = time.time() + 5
    after = None
    while time.time() < deadline:
        after = bg.bg_output_tool({"job_id": job_id})
        if after.payload["running"] is False:
            break
        time.sleep(0.1)
    assert after is not None and after.payload["running"] is False


def test_bg_blocked_by_sandbox_policy(tmp_path: Path) -> None:
    sandbox = WorkdirSandbox(tmp_path, _cfg("workspace"))
    r = bg.bg_start_tool(sandbox, {"command": "cat /etc/passwd"})
    assert not r.ok
    assert "blocked" in r.payload["error"]


def test_bg_unknown_job() -> None:
    r = bg.bg_output_tool({"job_id": "nope"})
    assert not r.ok


def test_registry_has_new_tools(tmp_path: Path) -> None:
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, _cfg()), config=_cfg())
    reg = ToolRegistry(ctx)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert {"bg_start", "bg_output", "bg_kill", "bg_list", "task"} <= names


def test_task_schema_supports_parallel() -> None:
    from spark.tools.registry import _schema  # noqa: F401

    ctx = ToolContext(sandbox=WorkdirSandbox(Path("/tmp"), _cfg()), config=_cfg())
    reg = ToolRegistry(ctx)
    task = next(s for s in reg.schemas() if s["function"]["name"] == "task")
    props = task["function"]["parameters"]["properties"]
    assert "tasks" in props and "prompt" in props


def test_parallel_subtask_runner_smoke(tmp_path: Path) -> None:
    """spawn_subtasks_parallel splits summaries by index (no provider needed for structure)."""
    from spark.core.loop import AgentLoop

    store = SessionStore(":memory:")
    sid = store.create_session(tmp_path, "mock", title="t")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, _cfg()), config=_cfg())
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=_cfg(),
        provider=None,  # type: ignore[arg-type]
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )
    assert callable(loop.spawn_subtask)
    assert callable(loop.spawn_subtasks_parallel)
    store.close()


def test_parallel_subtasks_e2e_with_mock(tmp_path: Path) -> None:
    """Two sub-agents run concurrently with a scripted mock provider; both summaries returned."""
    from spark.config import SparkConfig as SC
    from spark.core.loop import AgentLoop as AL
    from spark.providers.mock import MockProvider

    async def run() -> tuple[int, int]:
        cfg = SC(
            provider={"name": "mock", "model": "mock"},
            agent=AgentConfig(sandbox_mode="workspace", approval="full-auto", max_tool_rounds=10),
        )
        store = SessionStore(":memory:")
        sid = store.create_session(tmp_path, "mock", title="t")
        ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
        provider = MockProvider()
        loop = AL(workdir=tmp_path, cfg=cfg, provider=provider, registry=ToolRegistry(ctx), store=store, session_id=sid)
        events = []
        async for ev in loop.spawn_subtasks_parallel(["task A: list files", "task B: read readme"]):
            events.append(ev)
        store.close()
        return provider.calls, len([e for e in events if e.type == "turn_end"])

    import asyncio

    calls, ends = asyncio.new_event_loop().run_until_complete(run())
    assert calls == 3  # sub-agent A: list tool + final; sub-agent B: final
    assert ends == 2


def test_turn_event_roundtrip_serializable() -> None:
    from spark.models import ToolCall, ToolResult, TurnEvent

    ev = TurnEvent(type="tool_end", tool_call=ToolCall(id="1", name="bg_start", arguments={"command": "x"}), result=ToolResult(ok=True, payload={"job_id": "abc"}))
    data = ev.model_dump()
    assert TurnEvent.model_validate(data).type == "tool_end"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)
