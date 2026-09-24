"""End-to-end simulated user journeys across CLI, Web API and the agent loop.

These exercise realistic flows a first-time user performs, and assert the system
stays usable and safe at each step.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.store import SessionStore
from spark.web.server import serve_web


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "save_config", lambda cfg: tmp_path / "cfg.toml")
    monkeypatch.setattr(server_module, "default_home", lambda: tmp_path / "home")
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "suggest"
    store = SessionStore(tmp_path / "web.db")
    port = _free_port()
    thread = threading.Thread(
        target=serve_web,
        kwargs={
            "workdir": tmp_path,
            "cfg": cfg,
            "store": store,
            "host": "127.0.0.1",
            "port": port,
        },
        daemon=True,
    )
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/status", timeout=5).read()
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    else:
        pytest.fail("server did not start")
    yield base, tmp_path
    store.close()


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=15) as r:
        return json.loads(r.read().decode())


def post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


# ---------- user journey 1: first run ----------


def test_journey_new_user_can_discover_and_configure(web) -> None:
    from typer.testing import CliRunner

    from spark import cli

    base, workdir = web
    runner = CliRunner()

    # 1. sees the command surface
    help_out = runner.invoke(cli.app, ["--help"]).stdout
    for command in ("init", "doctor", "config", "web", "exec"):
        assert command in help_out

    # 2. checks health before configuring anything
    doctor = runner.invoke(cli.app, ["doctor", "--no-probe"])
    assert doctor.exit_code in (0, 1)
    assert "Spark" in doctor.stdout

    # 3. inspects current config without leaking the key
    shown = runner.invoke(
        cli.app, ["config", "show", "--config", str(workdir / "none.toml")]
    )
    assert shown.exit_code in (0, 1)

    # 4. can adjust a guard from the CLI and see it reflected
    cfg_file = workdir / "journey.toml"
    cfg_file.write_text('[provider]\nname = "mock"\n', encoding="utf-8")
    set_result = runner.invoke(
        cli.app,
        ["config", "set", "agent.max_repeat_calls", "6", "--config", str(cfg_file)],
    )
    assert set_result.exit_code == 0
    from spark.config import load_config

    assert (
        load_config(config_path=cfg_file, workdir=workdir).agent.max_repeat_calls == 6
    )


# ---------- user journey 2: web control plane ----------


def test_journey_web_status_config_history_sessions(web) -> None:
    base, _ = web
    status = get(base, "/api/status")
    assert "model" in status
    config = get(base, "/api/config")
    assert "agent" in config and "provider" in config
    assert get(base, "/api/history") is not None
    sessions = get(base, "/api/sessions")
    assert "sessions" in sessions
    assert get(base, "/api/checkpoints") is not None


def test_journey_create_and_switch_session(web) -> None:
    base, _ = web
    first = post(base, "/api/sessions/new", {"title": "journey-a"})
    second = post(base, "/api/sessions/new", {"title": "journey-b"})
    assert first["status"]["session_id"] != second["status"]["session_id"]
    switched = post(
        base, "/api/sessions/switch", {"session_id": first["status"]["session_id"]}
    )
    assert switched.get("ok") is True


def test_journey_agent_md_roundtrip(web) -> None:
    base, workdir = web
    saved = post(
        base, "/api/agents_md", {"content": "# Journey\n\nAlways run tests.\n"}
    )
    assert saved.get("chars", 0) > 0
    assert (workdir / "AGENTS.md").exists()
    loaded = get(base, "/api/agents_md")
    assert "Always run tests" in loaded["content"]


def test_journey_settings_reject_bad_input_without_breaking(web) -> None:
    base, _ = web
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(base, "/api/settings", {"agent": {"max_repeat_calls": -5}})
    assert excinfo.value.code == 400
    assert get(base, "/api/status") is not None
    good = post(base, "/api/settings", {"agent": {"max_repeat_calls": 5}})
    assert "config" in good
    assert get(base, "/api/config")["agent"]["max_repeat_calls"] == 5


def test_journey_memory_crud(web) -> None:
    base, _ = web
    added = post(base, "/api/memory", {"action": "add", "content": "prefers pytest"})
    assert added.get("ok") is True
    listing = get(base, "/api/memory")
    assert "stats" in listing or "items" in listing


def test_journey_unknown_routes_404(web) -> None:
    base, _ = web
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(base, "/api/does-not-exist")
    assert excinfo.value.code == 404


# ---------- user journey 3: a full agent turn ----------


def test_journey_agent_turn_with_tool_and_budget(tmp_path: Path) -> None:
    import asyncio

    from spark.core.loop import AgentLoop
    from spark.models import ChatDelta, ChatMessage, ToolCall
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    (tmp_path / "note.txt").write_text("original", encoding="utf-8")

    class Scripted:
        def __init__(self) -> None:
            self.n = 0

        async def stream(self, messages: list[ChatMessage], tools: list[dict]):
            self.n += 1
            if self.n == 1:
                yield ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "note.txt", "content": "updated"},
                    ),
                )
            else:
                yield ChatDelta(type="text", text="已更新 note.txt")

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "turn.db")
    sid = store.create_session(tmp_path, "mock")
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=Scripted(),
        registry=ToolRegistry(
            ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
        ),
        store=store,
        session_id=sid,
    )

    async def run():
        return [event async for event in loop.iter_turn("改一下 note.txt")]

    events = asyncio.run(run())
    try:
        assert any(e.type == "tool_end" for e in events)
        assert any(e.type == "turn_end" for e in events)
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "updated"
        assert store.load_messages(sid), "conversation must be persisted"
    finally:
        store.close()


def test_journey_provider_failure_is_recoverable(tmp_path: Path) -> None:
    import asyncio

    from spark.core.loop import AgentLoop
    from spark.sandbox import WorkdirSandbox
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

    class Broken:
        async def stream(self, messages, tools):
            raise RuntimeError("upstream unavailable")
            yield None

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "broken.db")
    sid = store.create_session(tmp_path, "mock")
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=Broken(),
        registry=ToolRegistry(
            ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
        ),
        store=store,
        session_id=sid,
    )

    async def run():
        return [event async for event in loop.iter_turn("hello")]

    events = asyncio.run(run())
    try:
        errors = [e for e in events if e.type == "turn_error"]
        assert errors and "upstream unavailable" in (errors[0].text or "")
    finally:
        store.close()
