import json
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ChatDelta, ChatMessage, ImageRef, ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def _loop(tmp_path: Path, provider, store: SessionStore | None = None, session_id: str | None = None) -> AgentLoop:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = store or SessionStore(tmp_path / "s.db")
    sid = session_id or store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )


@pytest.mark.asyncio
async def test_task_spawns_subagent_and_returns_summary(tmp_path: Path) -> None:
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="t1", name="task", arguments={"prompt": "find where config lives"}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )
    loop = _loop(tmp_path, provider)
    events = await loop.run("explore the codebase")
    task_end = next(e for e in events if e.type == "tool_end" and e.tool_call and e.tool_call.name == "task")
    assert task_end.result and task_end.result.ok
    assert task_end.result.payload["summary"] == "done"
    assert events[-1].type == "turn_end"
    assert provider.calls == 3


def test_notebook_read_and_edit(tmp_path: Path) -> None:
    from spark.tools import notebook

    nb = {
        "cells": [
            {"cell_type": "code", "source": ["print('v1')\n"], "outputs": [], "metadata": {}},
            {"cell_type": "markdown", "source": "# Title\n", "metadata": {}},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    nb_path = tmp_path / "demo.ipynb"
    nb_path.write_text(json.dumps(nb), encoding="utf-8")
    sandbox = WorkdirSandbox(tmp_path)

    read = notebook.read_notebook(sandbox, notebook.ReadNotebookArgs(path="demo.ipynb"))
    assert read.ok
    assert read.payload["cells"][0]["source"] == "print('v1')\n"
    assert read.payload["cells"][1]["type"] == "markdown"

    edit = notebook.notebook_edit(
        sandbox,
        notebook.NotebookEditArgs(path="demo.ipynb", cell_index=0, new_source="print('v2')"),
    )
    assert edit.ok
    data = json.loads(nb_path.read_text(encoding="utf-8"))
    assert "".join(data["cells"][0]["source"]) == "print('v2')"

    change_type = notebook.notebook_edit(
        sandbox,
        notebook.NotebookEditArgs(path="demo.ipynb", cell_index=1, new_source="now code", cell_type="code"),
    )
    assert change_type.ok
    data = json.loads(nb_path.read_text(encoding="utf-8"))
    assert data["cells"][1]["cell_type"] == "code"

    bad = notebook.notebook_edit(
        sandbox,
        notebook.NotebookEditArgs(path="demo.ipynb", cell_index=9, new_source="x"),
    )
    assert not bad.ok


def test_store_images_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    msg = ChatMessage(
        role="user",
        content="look at this",
        images=[ImageRef(media_type="image/png", data="aGVsbG8=")],
    )
    store.append_message(sid, msg)
    loaded = store.load_messages(sid)
    assert loaded[0].images is not None
    assert loaded[0].images[0].data == "aGVsbG8="


def test_provider_multimodal_content() -> None:
    from spark.providers.openai_compat import _to_openai

    msgs = [
        ChatMessage(role="user", content="what is this", images=[ImageRef(media_type="image/png", data="abc")]),
        ChatMessage(role="user", content="plain text only"),
    ]
    out = _to_openai(msgs)
    assert isinstance(out[0]["content"], list)
    assert out[0]["content"][0] == {"type": "text", "text": "what is this"}
    assert out[0]["content"][1]["image_url"]["url"] == "data:image/png;base64,abc"
    assert out[1] == {"role": "user", "content": "plain text only"}


def test_build_messages_collapses_old_images(tmp_path: Path) -> None:
    from spark.core.context import build_messages

    cfg = SparkConfig()
    history = [
        ChatMessage(role="user", content="first", images=[ImageRef(media_type="image/png", data="a")]),
        ChatMessage(role="assistant", content="answer one"),
        ChatMessage(role="user", content="second", images=[ImageRef(media_type="image/png", data="b")]),
    ]
    out = build_messages(workdir=tmp_path, cfg=cfg, history=history)
    user_msgs = [m for m in out if m.role == "user"]
    assert user_msgs[0].images is None
    assert "[image attached earlier]" in (user_msgs[0].content or "")
    assert user_msgs[1].images is not None


def test_task_not_recursive_in_subagent(tmp_path: Path) -> None:
    provider = MockProvider()
    loop = _loop(tmp_path, provider)
    assert loop.registry.ctx.task_runner is not None
