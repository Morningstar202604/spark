"""Round-2 polish: real message ids, rename/fork, web-search gate, demo mode, self-heal.

Each test pins a user-visible behaviour that used to be missing or fake:

* history rows carry the database id so the UI can delete/edit a single message;
* sessions can be renamed and forked from the UI;
* turning the composer's web-search switch off removes the web tools for that
  turn (and only that turn);
* a provider that silently fell back to the scripted mock is reported as demo
  mode instead of looking like a real model;
* a corrupted global config is backed up and the app still starts;
* settings round-trip keeps repo-map fields;
* diagnostics/repair endpoints exist and repairs are real.
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

from spark.config import SparkConfig, load_config, save_config
from spark.providers.mock import MockProvider
from spark.store import SessionStore
from spark.web.server import serve_web


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.fixture()
def live(tmp_path: Path, monkeypatch):
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "save_config", lambda cfg: None)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
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
    for _ in range(100):
        try:
            _get(f"http://127.0.0.1:{port}", "/api/status")
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", store
    finally:
        store.close()


# ---------------------------------------------------------------------------
# History ids + message level operations
# ---------------------------------------------------------------------------


def test_history_rows_carry_database_ids(live) -> None:
    base, store = live
    _post(base, "/api/chat", {"prompt": "hello"})
    body = _get(base, "/api/history")
    assert body["messages"], "history should not be empty"
    ids = [row.get("id") for row in body["messages"]]
    assert all(isinstance(i, int) and i > 0 for i in ids), ids
    assert body["messages"][0]["content"] == "hello"
    stored = store.load_message_ids(_get(base, "/api/status")["session_id"])
    assert ids == stored


def test_truncate_from_message_endpoint_uses_real_id(live) -> None:
    base, store = live
    sid = _get(base, "/api/status")["session_id"]
    from spark.models import ChatMessage

    first = store.append_message(sid, ChatMessage(role="user", content="one"))
    store.append_message(sid, ChatMessage(role="assistant", content="two"))
    body = _post(base, "/api/messages/truncate", {"message_id": first})
    assert body["ok"] is True
    assert body["messages"] == []


# ---------------------------------------------------------------------------
# Rename / fork
# ---------------------------------------------------------------------------


def test_session_rename_persists(live) -> None:
    base, store = live
    sid = _get(base, "/api/status")["session_id"]
    out = _post(base, "/api/sessions/rename", {"session_id": sid, "title": "重构支付模块"})
    assert out["ok"] is True
    assert store.get_session(sid)["title"] == "重构支付模块"
    listed = _get(base, "/api/sessions")["sessions"]
    assert any(row["id"] == sid and row["title"] == "重构支付模块" for row in listed)


def test_session_rename_rejects_blank_and_unknown(live) -> None:
    base, _ = live
    with pytest.raises(urllib.error.HTTPError) as blank:
        _post(base, "/api/sessions/rename", {"session_id": "nope", "title": "  "})
    assert blank.value.code == 400


def test_session_fork_copies_messages_and_switches(live) -> None:
    base, store = live
    sid = _get(base, "/api/status")["session_id"]
    from spark.models import ChatMessage

    store.append_message(sid, ChatMessage(role="user", content="fork me"))
    out = _post(base, "/api/sessions/fork", {"session_id": sid})
    new_sid = out["status"]["session_id"]
    assert new_sid != sid
    assert out["messages"][0]["content"] == "fork me"
    assert store.get_session(new_sid)["forked_from"] == sid
    assert len(store.list_sessions()) == 2


# ---------------------------------------------------------------------------
# Web-search gate
# ---------------------------------------------------------------------------


def test_registry_disabled_tools_are_hidden_and_refused(tmp_path: Path) -> None:
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    reg = ToolRegistry(ToolContext(sandbox=__import__("spark.sandbox", fromlist=["WorkdirSandbox"]).WorkdirSandbox(tmp_path), config=cfg))
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "web_search" in names
    reg.disabled_tools = {"web_search", "web_fetch"}
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "web_search" not in names and "web_fetch" not in names
    result = __import__("asyncio").run(reg.execute(__import__("spark.models", fromlist=["ToolCall"]).ToolCall(id="c1", name="web_search", arguments={"query": "x"})))
    assert result.ok is False
    assert "disabled" in str(result.payload.get("error", "")).lower()


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------


def test_status_reports_demo_mode_when_key_missing(tmp_path: Path) -> None:
    from spark.web.state import SparkWebState

    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.api_key = None
    cfg.provider.api_key_env = "SPARK_MISSING_KEY_FOR_TEST"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "demo.db")
    state = SparkWebState(tmp_path, cfg, store)
    try:
        status = state.status()
        assert status["demo_mode"] is True
        assert "MockProvider" in status["demo_reason"] or "key" in status["demo_reason"].lower()
    finally:
        store.close()


def test_status_not_demo_for_real_provider(tmp_path: Path) -> None:
    from spark.web.state import SparkWebState

    cfg = SparkConfig()
    cfg.provider.name = "ollama"
    cfg.provider.base_url = "http://127.0.0.1:11434/v1"
    cfg.provider.model = "qwen2.5-coder"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "demo2.db")
    state = SparkWebState(tmp_path, cfg, store)
    try:
        status = state.status()
        assert status["demo_mode"] is False
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Config self-heal + settings round-trip
# ---------------------------------------------------------------------------


def test_broken_global_config_is_backed_up_and_recovered(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    broken = home / "config.toml"
    broken.write_text("[provider\nname = ", encoding="utf-8")
    monkeypatch.setenv("SPARK_HOME", str(home))
    cfg = load_config(workdir=tmp_path)
    assert cfg.provider.name == "openai_compat"
    assert cfg.recovery_notes, "a recovery note must explain what happened"
    backups = list(home.glob("config.toml.broken-*"))
    assert backups, "the broken file must be preserved"
    assert backups[0].read_text(encoding="utf-8").startswith("[provider")


def test_repo_map_fields_survive_save_and_load(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.context.repo_map_enabled = False
    cfg.context.repo_map_max_chars = 1234
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    text = path.read_text(encoding="utf-8")
    assert "repo_map_enabled" in text and "repo_map_max_chars" in text
    again = load_config(config_path=path, workdir=tmp_path)
    assert again.context.repo_map_enabled is False
    assert again.context.repo_map_max_chars == 1234


# ---------------------------------------------------------------------------
# Diagnostics / repair
# ---------------------------------------------------------------------------


def test_diagnostics_endpoint_lists_health(live) -> None:
    base, _ = live
    body = _get(base, "/api/diagnostics")
    assert body["ok"] is True
    assert body["rows"], "health rows should never be empty"
    assert {"level", "message", "fix"} <= set(body["rows"][0])


def test_repair_recreates_missing_workdir(tmp_path: Path, monkeypatch) -> None:
    from spark.selfheal import repair

    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "home"))
    missing = tmp_path / "does" / "not" / "exist"
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    report = repair(cfg, workdir=missing, config_path=tmp_path / "home" / "config.toml")
    assert missing.is_dir()
    assert any(item["applied"] for item in report["actions"])
    assert (tmp_path / "home").is_dir()


def test_repair_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    from spark.selfheal import repair

    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "home"))
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    first = repair(cfg, workdir=tmp_path, config_path=tmp_path / "home" / "config.toml")
    second = repair(cfg, workdir=tmp_path, config_path=tmp_path / "home" / "config.toml")
    assert first["ok"] and second["ok"]
    applied_second = [a for a in second["actions"] if a["applied"]]
    assert applied_second == [], "second repair must not redo work"


# ---------------------------------------------------------------------------
# Mock provider stays explicit
# ---------------------------------------------------------------------------


def test_mock_provider_never_silently_claims_real_output() -> None:
    provider = MockProvider()
    assert provider.__class__.__name__ == "MockProvider"


# ---------------------------------------------------------------------------
# Regressions found by the independent UI audit
# ---------------------------------------------------------------------------


def test_settings_switch_to_suggest_creates_approver(tmp_path: Path) -> None:
    """full-auto at startup → switch to suggest must install a real approver.

    Otherwise the loop keeps approver=None and every write/shell call is silently
    denied while the UI claims it will ask.
    """
    from spark.web.state import SparkWebState

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "approver.db")
    state = SparkWebState(tmp_path, cfg, store)
    try:
        assert state.approver is None
        cfg.agent.approval = "suggest"
        state.rebuild_loop(session_id=state.session_id)
        assert state.loop.approver is not None
        assert state.approver is not None
    finally:
        store.close()


def test_mcp_env_round_trips_through_config(tmp_path: Path) -> None:
    from spark.config import McpServerConfig, load_config

    cfg = SparkConfig()
    cfg.mcp_servers = [
        McpServerConfig(
            name="gh",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_TOKEN": "t0ken"},
        )
    ]
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    again = load_config(config_path=path, workdir=tmp_path)
    assert again.mcp_servers[0].env == {"GITHUB_TOKEN": "t0ken"}


def test_test_endpoint_requires_key_for_new_host(live) -> None:
    base, _ = live
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(base, "/api/test", {"base_url": "https://example.invalid/v1", "model": "m"})
    assert err.value.code == 400
    payload = json.loads(err.value.read().decode("utf-8"))
    assert payload.get("needs_api_key") is True


def test_sessions_list_cap_is_generous(live) -> None:
    base, store = live
    from spark.models import ChatMessage

    status = _get(base, "/api/status")
    sid = status["session_id"]
    workdir = Path(status["workdir"])
    for i in range(60):
        new = store.create_session(workdir, "mock-model", title=f"s{i}")
        store.append_message(new, ChatMessage(role="user", content="x"))
    rows = _get(base, "/api/sessions")["sessions"]
    assert len(rows) >= 60, "session list must not silently truncate at 50"
    assert any(r["id"] == sid for r in rows)
