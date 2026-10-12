from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ChatDelta, ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def _loop(tmp_path: Path, provider: MockProvider, **overrides) -> AgentLoop:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    cfg.agent.max_tool_rounds = 30
    for key, value in overrides.items():
        section, field = key.split("__")
        setattr(getattr(cfg, section), field, value)
    store = SessionStore(tmp_path / "graph.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )
    loop._use_graph = True
    return loop


@pytest.mark.asyncio
async def test_graph_path_executes_tool_without_nameerror(tmp_path: Path) -> None:
    """R2: graph path must not raise NameError on dict-shaped tool calls."""
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )
    loop = _loop(tmp_path, provider)
    events = await loop.run("list files")
    types = [e.type for e in events]
    assert "turn_error" not in types
    assert types[-1] == "turn_end"
    store = loop.store
    store.close()


@pytest.mark.asyncio
async def test_graph_path_handles_dict_shaped_tool_calls(tmp_path: Path) -> None:
    """R2: a dict-shaped tool call (as after checkpoint serde) must not NameError."""
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="ok"), ChatDelta(type="end")],
        ]
    )
    loop = _loop(tmp_path, provider)
    events = await loop.run("list files")
    errors = [e for e in events if e.type == "turn_error"]
    assert not errors, f"unexpected turn_error: {[e.text for e in errors]}"
    store = loop.store
    store.close()


@pytest.mark.asyncio
async def test_graph_path_sets_session_title(tmp_path: Path) -> None:
    """The graph path must set the session title from the first user message."""
    provider = MockProvider(
        rounds=[[ChatDelta(type="text", text="hi"), ChatDelta(type="end")]]
    )
    loop = _loop(tmp_path, provider)
    await loop.run("my first message")
    session = loop.store.get_session(loop.session_id)
    assert session is not None
    assert session.get("title") == "my first message"
    store = loop.store
    store.close()


@pytest.mark.asyncio
async def test_graph_path_decide_uses_keyword_args(tmp_path: Path) -> None:
    """graph._node_tools must call policy.decide with keyword args only."""
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="ok"), ChatDelta(type="end")],
        ]
    )
    loop = _loop(tmp_path, provider)
    events = await loop.run("list files")
    errors = [e for e in events if e.type == "turn_error"]
    assert not errors, f"unexpected turn_error: {[e.text for e in errors]}"
    store = loop.store
    store.close()
