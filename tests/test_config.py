"""配置测试：预设（2026 现役国产模型）、默认值、持久化。"""
from __future__ import annotations

from spark2.config import PRESETS, _defaults, load_config, save_config


def test_presets_cover_domestic_models_2026() -> None:
    """国产模型预设齐全且指向现役型号。"""
    assert "deepseek" in PRESETS
    assert "deepseek-flash" in PRESETS
    assert "qwen" in PRESETS
    assert "glm" in PRESETS
    assert "kimi" in PRESETS
    assert "doubao" in PRESETS  # 豆包（火山方舟）
    assert "ollama" in PRESETS
    assert "mock" in PRESETS
    assert _defaults()["model_fast"] == ""
    assert _defaults()["memory_embedding"] == "off"
    assert PRESETS["glm"]["model"] == "glm-4.6"
    assert PRESETS["kimi"]["model"] == "kimi-k3"  # k2 系列已下线，现役 k3
    assert PRESETS["deepseek"]["model"] == "deepseek-v4-pro"  # chat/reasoner 已弃用
    assert PRESETS["deepseek-flash"]["model"] == "deepseek-flash"
    assert PRESETS["doubao"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert PRESETS["doubao"]["model"] == "doubao-1-5-pro-32k"
    assert PRESETS["ollama"]["model"] == "qwen3-coder"


def test_defaults_have_mcp_and_token(tmp_path) -> None:
    cfg = _defaults()
    assert cfg["mcp_servers"] == []
    assert "token" in cfg
    assert cfg["approval_mode"] == "suggest"


def test_save_load_roundtrip_mcp_servers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK2_HOME", str(tmp_path))
    cfg = _defaults()
    cfg["mcp_servers"] = [
        {"name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"], "env": {}}
    ]
    save_config(cfg)
    loaded = load_config()
    assert loaded["mcp_servers"] == cfg["mcp_servers"]
