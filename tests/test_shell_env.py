"""子进程环境变量白名单：防止 API 密钥随 shell/PTY 子进程外泄。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from spark.shell_env import build_clean_env, is_env_allowed
from spark.store import SessionStore
from spark.web.server import AppState, create_app

# ---------- is_env_allowed ----------

def test_allowed_keys() -> None:
    for k in ("PATH", "HOME", "LANG", "TERM", "USER", "SHELL", "TMPDIR"):
        assert is_env_allowed(k), f"{k} 应被放行"


def test_denied_keys() -> None:
    for k in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_KEY",
        "GCP_TOKEN",
        "DOCKER_PASSWORD",
        "GITHUB_TOKEN",
        "MY_SECRET",
        "APIKEY",    # 边界：全大写无下划线也拒收
        "PIN",       # 单字后缀
        "DATABASE_PASSWORD",
    ):
        assert not is_env_allowed(k), f"{k} 应被拒收"


def test_denied_overrides_allowed() -> None:
    """PATH_KEY 形似 PATH 但以 _KEY 结尾——应拒收优先于放行。"""
    assert not is_env_allowed("PATH_KEY")
    assert not is_env_allowed("HOME_TOKEN")


def test_invalid_inputs() -> None:
    assert not is_env_allowed("")
    assert not is_env_allowed("   ")
    assert not is_env_allowed(None)  # type: ignore[arg-type]
    assert not is_env_allowed(123)  # type: ignore[arg-type]


# ---------- build_clean_env ----------

_HOST_SECRETS = {
    "OPENAI_API_KEY": "sk-host-secret-key",
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/test",
    "LANG": "C.UTF-8",
}


def test_clean_env_strips_secrets() -> None:
    env = build_clean_env(base=_HOST_SECRETS)
    assert "OPENAI_API_KEY" not in env
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/test"


def test_clean_env_accepts_extra() -> None:
    env = build_clean_env(base=_HOST_SECRETS, extra={"SPARK_TEST": "1"})
    assert env["SPARK_TEST"] == "1"


def test_clean_env_does_not_mutate_original() -> None:
    orig = dict(_HOST_SECRETS)
    build_clean_env(base=orig)
    assert orig == _HOST_SECRETS, "调用不应修改输入字典"


def test_clean_env_from_process_environ(monkeypatch) -> None:
    """默认 base 取当前进程环境；白名单过滤后再无敏感值。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-value")
    env = build_clean_env()
    assert "OPENAI_API_KEY" not in env
    assert "PATH" in env


def test_clean_env_returns_copy() -> None:
    """确保返回的是新字典，后续改动不影响内部过滤结果。"""
    env = build_clean_env(base=_HOST_SECRETS)
    env["NEW"] = "x"
    env2 = build_clean_env(base=_HOST_SECRETS)
    assert "NEW" not in env2


# ---------- WS Origin 校验 ----------

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


def test_ws_pty_origin_mismatch_rejected(tmp_path: Path) -> None:
    """跨域 Origin 应被拒绝——浏览器对 WS 无同源策略，这是主要防护点。"""
    client, _ = _client(tmp_path)
    with client.websocket_connect(
        "/ws/pty",
        headers={"Origin": "http://evil.example.com"},
    ) as ws:
        msg = __import__("json").loads(ws.receive_text())
        assert msg["type"] == "err"
        assert "Origin" in msg["message"]


def test_ws_pty_missing_origin_allowed(tmp_path: Path) -> None:
    """无 Origin 时（native CLI / curl）放行，不影响非浏览器客户端。"""
    client, state = _client(tmp_path)
    state.cfg["workdir"] = ""  # 走 4400 分支证明已过鉴权层
    with client.websocket_connect("/ws/pty") as ws:
        msg = __import__("json").loads(ws.receive_text())
        assert msg["type"] == "err" and "工作目录" in msg["message"]


def test_ws_pty_same_origin_allowed(tmp_path: Path) -> None:
    """同源 Origin 放行（模拟浏览器同站请求）。"""
    client, state = _client(tmp_path)
    state.cfg["workdir"] = ""
    with client.websocket_connect(
        "/ws/pty",
        headers={"Origin": "http://testserver"},
    ) as ws:
        msg = __import__("json").loads(ws.receive_text())
        assert msg["type"] == "err" and "工作目录" in msg["message"]
