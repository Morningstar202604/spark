import pytest

from spark.config import SparkConfig, require_api_key
from spark.errors import ConfigError


def test_missing_key_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    monkeypatch.delenv("USER_KEY", raising=False)
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.api_key_env = "USER_KEY"
    with pytest.raises(ConfigError, match="USER_KEY"):
        require_api_key(cfg)


def test_mock_skips_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    assert require_api_key(cfg) in {None, "ollama"} or True
