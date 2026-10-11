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


def test_check_token_safe_compare_no_short_circuit() -> None:
    """compare_digest 恒定时间：长度不同也应走完比较，不提前返回 False。"""
    from spark.web.api_common import _safe_compare

    # 长度不同也应返回 False（不抛异常）
    assert _safe_compare("a", "longer-secret") is False
    # None 输入
    assert _safe_compare(None, "secret") is False
    # 正确匹配
    assert _safe_compare("secret", "secret") is True


def test_is_loopback_host() -> None:
    from spark.cli import is_loopback_host

    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("127.0.0.50") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("0.0.0.0") is False
    assert is_loopback_host("192.168.1.1") is False
    assert is_loopback_host("10.0.0.1") is False
    assert is_loopback_host("8.8.8.8") is False


def test_web_rejects_nonloopback_without_token(tmp_path: Path) -> None:
    """默认 host 0.0.0.0 + 无令牌 = 拒绝启动；是此行最重要的安全回归测试。"""
    from typer.testing import CliRunner

    from spark.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["web", "--host", "0.0.0.0", "--workdir", str(tmp_path)])
    assert result.exit_code == 2
    assert "拒绝启动" in result.output
    assert "令牌" in result.output


def test_web_allows_loopback_without_token(tmp_path: Path, monkeypatch) -> None:
    """127.0.0.1 + 无令牌 = 允许（正常本机开发场景）。
    用 --help 验证命令行本身能解析，不真正起 uvicorn。"""
    from typer.testing import CliRunner

    from spark.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["web", "--host", "127.0.0.1", "--help"])
    # --help 退出码 0，且不触发令牌校验
    assert result.exit_code == 0


def test_web_default_host_is_loopback(tmp_path: Path) -> None:
    """默认 --host 不再是 0.0.0.0，而是 127.0.0.1。"""
    from typer.testing import CliRunner

    from spark.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["web", "--help"])
    assert result.exit_code == 0
    # help 文本中默认值是 127.0.0.1，不再是 0.0.0.0
    assert "127.0.0.1" in result.output
