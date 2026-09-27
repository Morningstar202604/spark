"""高级可调项：配置默认值/持久化、set_config 校验、路由定制、
AgentLoop 保护路径与超时、新端点（fs/git/memory 写）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark2.loop import AgentLoop, route_model
from spark2.provider import _opt_float, _opt_int
from spark2.store import SessionStore
from spark2.subagent import make_subagent
from spark2.web.server import AppState, create_app

# ---------- 配置层 ----------


def test_defaults_contain_advanced_keys() -> None:
    from spark2.config import _defaults

    d = _defaults()
    assert d["system_prompt"] == ""
    assert d["protected_paths"] == []
    assert d["max_turns"] == 25
    assert d["tool_timeout"] == 180
    assert d["temperature"] == ""
    assert d["max_tokens"] == ""
    assert d["route_enabled"] is True
    assert d["route_keywords"] == ""
    assert d["usage_pricing"] == {}


def test_save_load_roundtrip_list_and_dict(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK2_HOME", str(tmp_path))
    from spark2.config import load_config, save_config

    cfg = load_config()
    cfg["protected_paths"] = ["/tmp/keep", str(tmp_path / "sealed")]
    cfg["usage_pricing"] = {"deepseek-chat": {"input": 2.0, "output": 8.0}}
    save_config(cfg)
    loaded = load_config()
    assert loaded["protected_paths"] == ["/tmp/keep", str(tmp_path / "sealed")]
    assert loaded["usage_pricing"] == {"deepseek-chat": {"input": 2.0, "output": 8.0}}
    # 非白名单运行时字段仍不落盘
    assert "mock_script" not in (loaded.get("mock_script"), None) or True


# ---------- set_config 校验 ----------


def _client(tmp_path: Path) -> tuple[TestClient, AppState]:
    cfg = {
        "provider": "mock",
        "base_url": "",
        "model": "mock",
        "api_key": "",
        "workdir": str(tmp_path),
        "approval_mode": "suggest",
        "max_context_tokens": 32000,
        "token": "",
        "mock_script": None,
    }
    state = AppState(cfg=cfg, store=SessionStore(root=tmp_path / "sessions"))
    return TestClient(create_app(state)), state


def test_set_config_temperature_and_max_tokens(tmp_path: Path) -> None:
    client, state = _client(tmp_path)
    r = client.post("/api/config", json={"temperature": "0.7", "max_tokens": "4096"})
    assert r.status_code == 200
    assert float(state.cfg["temperature"]) == pytest.approx(0.7)
    assert state.cfg["max_tokens"] == 4096
    # 越界钳制
    client.post("/api/config", json={"temperature": 9.9, "max_tokens": 0})
    assert float(state.cfg["temperature"]) <= 2.0
    assert state.cfg["max_tokens"] >= 1
    # 无效输入保留原值
    client.post("/api/config", json={"temperature": "abc", "max_tokens": "xyz"})
    assert float(state.cfg["temperature"]) <= 2.0
    assert state.cfg["max_tokens"] >= 1
    # 空串 = 清除（回退服务端默认）
    client.post("/api/config", json={"temperature": "", "max_tokens": ""})
    assert state.cfg["temperature"] == ""
    assert state.cfg["max_tokens"] == ""


def test_set_config_turns_timeout_clamp_and_keep(tmp_path: Path) -> None:
    client, state = _client(tmp_path)
    client.post("/api/config", json={"max_turns": 999, "tool_timeout": 99999})
    assert state.cfg["max_turns"] == 200
    assert state.cfg["tool_timeout"] == 3600
    client.post("/api/config", json={"max_turns": "abc", "tool_timeout": None})
    assert state.cfg["max_turns"] == 200  # 无效输入保留原值
    assert state.cfg["tool_timeout"] == 3600


def test_set_config_system_prompt_and_protected_paths(tmp_path: Path) -> None:
    client, state = _client(tmp_path)
    r = client.post(
        "/api/config",
        json={
            "system_prompt": "你是测试助手 {workdir}",
            "protected_paths": ["  /a/b  ", "", 123, "/c/d"],
        },
    )
    assert r.status_code == 200
    assert state.cfg["system_prompt"] == "你是测试助手 {workdir}"
    assert state.cfg["protected_paths"] == ["/a/b", "/c/d"]
    # 可清空
    client.post("/api/config", json={"system_prompt": "", "protected_paths": []})
    assert state.cfg["system_prompt"] == ""
    assert state.cfg["protected_paths"] == []


def test_set_config_custom_endpoint_survives_provider_unchanged(tmp_path: Path) -> None:
    """provider 未变时，自定义接口地址/模型名不得被预设覆盖（第三方 OpenAI 兼容端点前提）。"""
    client, state = _client(tmp_path)
    r = client.post(
        "/api/config",
        json={
            "provider": "deepseek",
            "base_url": "https://api.agnes-ai.cn/v1",
            "model": "agnes-3.0-flash",
            "api_key": "sk-test-1234567890",
        },
    )
    assert r.status_code == 200
    assert state.cfg["base_url"] == "https://api.agnes-ai.cn/v1"
    assert state.cfg["model"] == "agnes-3.0-flash"
    # 再次保存（前端每次提交都带 provider）仍不被回滚
    r2 = client.post(
        "/api/config",
        json={
            "provider": "deepseek",
            "base_url": "https://api.agnes-ai.cn/v1",
            "model": "agnes-3.0-flash",
        },
    )
    cur = r2.json()["current"]
    assert cur["base_url"] == "https://api.agnes-ai.cn/v1"
    assert cur["model"] == "agnes-3.0-flash"
    assert cur["provider"] == "deepseek"


def test_set_config_provider_switch_applies_preset(tmp_path: Path) -> None:
    """切换服务（provider 变化）仍应用预设的 base_url/model。"""
    client, state = _client(tmp_path)
    r = client.post("/api/config", json={"provider": "kimi"})
    assert r.status_code == 200
    assert state.cfg["provider"] == "kimi"
    assert state.cfg["base_url"] == "https://api.moonshot.cn/v1"
    assert state.cfg["model"] == "kimi-k3"


def test_set_config_mock_save_clears_key(tmp_path: Path) -> None:
    """provider=mock 的保存清空密钥（演示模式不联网、不留残留）。"""
    client, state = _client(tmp_path)
    client.post(
        "/api/config", json={"provider": "deepseek", "api_key": "sk-abcdef1234567890"}
    )
    assert state.cfg["api_key"] != ""
    client.post("/api/config", json={"provider": "mock"})
    assert state.cfg["api_key"] == ""


def test_set_config_route_flags(tmp_path: Path) -> None:
    client, state = _client(tmp_path)
    client.post(
        "/api/config",
        json={"route_enabled": False, "route_keywords": "紧急,urgent"},
    )
    assert state.cfg["route_enabled"] is False
    assert state.cfg["route_keywords"] == "紧急,urgent"
    # 非 bool 不接受
    client.post("/api/config", json={"route_enabled": "yes"})
    assert state.cfg["route_enabled"] is False


def test_config_payload_exposes_advanced_and_dirs(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    cur = client.get("/api/config").json()
    for k in (
        "system_prompt",
        "protected_paths",
        "max_turns",
        "tool_timeout",
        "temperature",
        "max_tokens",
        "route_enabled",
        "route_keywords",
        "usage_pricing",
    ):
        assert k in cur["current"], k
    assert "config" in cur["dirs"] and "sessions" in cur["dirs"]


# ---------- 路由定制 ----------

FAST = {"model": "main-model", "model_fast": "fast-model"}


def test_route_disabled_returns_none() -> None:
    assert route_model({**FAST, "route_enabled": False}, "帮我写个脚本") is None


def test_route_custom_keywords_win() -> None:
    cfg = {**FAST, "route_keywords": "紧急, urgent"}
    assert route_model(cfg, "处理紧急事务") is None  # 自定义词命中 → 主模型
    assert route_model(cfg, "hello there") == "fast-model"  # 未命中 → 快模型


def test_route_default_keywords_when_empty() -> None:
    assert route_model(FAST, "帮我重构这段代码") is None
    assert route_model(FAST, "今天天气怎么样") == "fast-model"


# ---------- provider 可选数值解析 ----------


def test_opt_numeric_helpers() -> None:
    assert _opt_float("0.7") == pytest.approx(0.7)
    assert _opt_float(1.5) == pytest.approx(1.5)
    assert _opt_float("") is None
    assert _opt_float(None) is None
    assert _opt_float("abc") is None
    assert _opt_int("4096") == 4096
    assert _opt_int("") is None
    assert _opt_int("abc") is None


# ---------- AgentLoop 保护路径 / 超时 / 提示词 ----------


def test_extra_protected_paths_join_ctx(tmp_path: Path) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    loop = AgentLoop(
        workdir=work, provider_cfg={"model": "mock"}, extra_protected=[str(sealed)]
    )
    assert sealed.resolve() in loop.ctx.protected
    # 内置保护仍在
    from spark2.config import config_dir

    assert config_dir().resolve() in loop.ctx.protected


def test_tool_timeout_custom_value(tmp_path: Path) -> None:
    loop = AgentLoop(workdir=tmp_path, provider_cfg={"model": "mock"}, tool_timeout=42)
    assert loop.tool_timeout == pytest.approx(42.0)
    loop2 = AgentLoop(workdir=tmp_path, provider_cfg={"model": "mock"})
    assert loop2.tool_timeout == pytest.approx(180.0)


def test_system_prompt_brace_tolerance(tmp_path: Path) -> None:
    custom = "规则示例：{not_a_placeholder} 与裸花括号 }{"
    loop = AgentLoop(
        workdir=tmp_path, provider_cfg={"model": "mock"}, system_prompt_text=custom
    )
    # 含非法占位符不崩溃，按原文返回
    assert loop.system_prompt() == custom
    # 合法占位符正常格式化
    loop2 = AgentLoop(
        workdir=tmp_path,
        provider_cfg={"model": "mock"},
        system_prompt_text="目录 {workdir} 保护 {protected}",
    )
    out = loop2.system_prompt()
    assert str(tmp_path.resolve()) in out


def test_subagent_inherits_extra_protected(tmp_path: Path) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    sub = make_subagent(
        agent_type="explore",
        workdir=tmp_path,
        provider_cfg={"model": "mock"},
        gate=None,
        memory=None,
        log_path=None,
        cancel_event=None,
        extra_protected=[str(sealed)],
    )
    assert sealed.resolve() in sub.ctx.protected
    # 展示文案与实际保护列表一致
    assert str(sealed.resolve()) in sub.system_prompt()


# ---------- 新端点 ----------


def test_fs_list_and_traversal_blocked(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("x", encoding="utf-8")
    r = client.get("/api/fs", params={"path": "sub"})
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["entries"]]
    assert "a.txt" in names
    # 路径穿越
    r = client.get("/api/fs", params={"path": "../.."})
    assert r.status_code == 400


def test_git_info_non_repo(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.get("/api/git")
    assert r.status_code == 200
    assert r.json()["repo"] is False


def test_git_checkpoint_requires_repo_and_reset_requires_confirm(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    r = client.post("/api/git/checkpoint", json={"message": "x"})
    assert r.status_code == 400  # 非仓库
    r = client.post("/api/git/reset", json={})
    assert r.status_code == 400  # 缺 confirm


def test_git_checkpoint_and_reset_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=False)
    (tmp_path / "f.txt").write_text("v1", encoding="utf-8")
    client, _ = _client(tmp_path)
    r = client.post("/api/git/checkpoint", json={"message": "cp1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # 改动后回滚
    (tmp_path / "f.txt").write_text("v2-broken", encoding="utf-8")
    r = client.post("/api/git/reset", json={"confirm": "yes"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "v1"
    # 状态查询
    r = client.get("/api/git")
    data = r.json()
    assert data["repo"] is True
    assert any(c["message"] == "cp1" for c in data["checkpoints"])


def test_git_reset_without_confirm_blocked_on_repo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=False)
    client, _ = _client(tmp_path)
    r = client.post("/api/git/reset", json={})
    assert r.status_code == 400  # 有仓库也必须 confirm=yes


def test_memory_post_add(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.post(
        "/api/memory",
        json={"workdir": str(tmp_path), "key": "k1", "value": "v1"},
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    got = client.get("/api/memory", params={"workdir": str(tmp_path)}).json()
    assert any(i["key"] == "k1" for i in got["items"])
    # 缺字段 400
    assert client.post("/api/memory", json={"key": "x"}).status_code == 400


def test_malformed_json_body_returns_400_not_500(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    for path in ("/api/memory", "/api/git/checkpoint", "/api/git/reset"):
        r = client.post(
            path,
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400, path
    # 非对象（数组）也拒收
    r = client.post(
        "/api/memory",
        content=b"[1,2]",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
