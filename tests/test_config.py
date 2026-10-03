"""配置测试：预设（2026 现役国产模型）、默认值、持久化。"""

from __future__ import annotations

from spark.config import PRESETS, _defaults, load_config, save_config


def test_presets_cover_domestic_models_2026() -> None:
    """国产模型预设齐全且指向现役型号。"""
    assert "deepseek" in PRESETS
    assert "deepseek-flash" in PRESETS
    assert "qwen" in PRESETS
    assert "glm" in PRESETS
    assert "kimi" in PRESETS
    assert "doubao" in PRESETS  # 豆包（火山方舟）
    assert "ollama" in PRESETS
    assert "custom" in PRESETS  # 自定义（OpenAI 兼容端点）
    assert PRESETS["custom"]["base_url"] == ""
    assert PRESETS["custom"]["model"] == ""
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
    assert cfg["proxy"] == ""
    assert "plan" in __import__("spark.config", fromlist=["APPROVAL_MODES"]).APPROVAL_MODES


def test_api_key_env_fallback(tmp_path, monkeypatch) -> None:
    """配置未填密钥时，依次回退 SPARK_API_KEY / <provider>_API_KEY。"""
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # 无任何 env：api_key 保持空
    assert load_config()["api_key"] == ""
    # SPARK_API_KEY 优先
    monkeypatch.setenv("SPARK_API_KEY", "sk-spark-env")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-env")
    cfg = load_config()
    assert cfg["api_key"] == "sk-spark-env"
    assert cfg.get("_env_api_key") is True
    # 去掉 SPARK_ 后走 provider 专属变量（配置文件 provider=deepseek）
    monkeypatch.delenv("SPARK_API_KEY")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-env")
    cfg = _defaults()
    cfg["provider"] = "deepseek"
    save_config(cfg)
    cfg2 = load_config()
    assert cfg2["api_key"] == "sk-ds-env"


def test_save_load_roundtrip_mcp_servers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    cfg = _defaults()
    cfg["mcp_servers"] = [
        {
            "name": "fs",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {},
        }
    ]
    save_config(cfg)
    loaded = load_config()
    assert loaded["mcp_servers"] == cfg["mcp_servers"]


def test_is_masked_key():
    from spark.config import is_masked_key, mask_key

    raw = "sk-1234567890abcdef"
    masked = mask_key(raw)
    assert is_masked_key(masked) is True          # 回显值禁止写回
    assert is_masked_key(raw) is False            # 真值可写
    assert is_masked_key("******") is True        # 短 key 全星号
    assert is_masked_key("sk-abcdef") is False    # 无 8 连星
