"""访问令牌可选化：默认（未设置令牌）本机免登录直接可用；
设置后才强制校验；可通过设置 API 设置与清除；WS 同样遵守。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from spark.store import SessionStore
from spark.web.server import AppState, create_app


def _client(tmp_path: Path, token: str = "") -> tuple[TestClient, AppState]:
    cfg = {
        "provider": "mock",
        "base_url": "",
        "model": "mock",
        "api_key": "",
        "workdir": str(tmp_path),
        "approval_mode": "suggest",
        "max_context_tokens": 32000,
        "token": token,
        "mock_script": None,
    }
    state = AppState(cfg=cfg, store=SessionStore(root=tmp_path / "sessions"))
    return TestClient(create_app(state)), state


def test_no_token_means_open_access(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.get("/api/config")  # 不带头部
    assert r.status_code == 200
    assert r.json()["current"]["token_set"] is False


def test_token_set_enforced(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, token="secret")
    assert client.get("/api/config").status_code == 401
    r = client.get("/api/config", headers={"X-Spark-Token": "secret"})
    assert r.status_code == 200
    assert r.json()["current"]["token_set"] is True


def test_token_set_and_clear_via_api(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.post("/api/config", json={"token": "abc123"})
    assert r.status_code == 200
    assert client.get("/api/config").status_code == 401  # 设置后立即强制
    h = {"X-Spark-Token": "abc123"}
    assert client.get("/api/config", headers=h).status_code == 200
    r = client.post("/api/config", headers=h, json={"token": ""})
    assert r.status_code == 200
    assert client.get("/api/config").status_code == 200  # 清除后恢复免登录


def test_load_config_does_not_autogenerate_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    from spark.config import load_config

    assert load_config()["token"] == ""
    assert load_config()["token"] == ""  # 重复加载也不得偷偷生成


def test_ws_pty_no_token_passes_auth(tmp_path: Path) -> None:
    """未设置令牌时 WS 不再校验；用空 workdir 触发 4400 分支证明已过鉴权。"""
    client, state = _client(tmp_path, token="")
    state.cfg["workdir"] = ""
    with client.websocket_connect("/ws/pty") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "err" and "工作目录" in msg["message"]


def test_ws_pty_wrong_token_rejected(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, token="secret")
    with client.websocket_connect("/ws/pty?token=wrong") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "err" and "令牌" in msg["message"]
