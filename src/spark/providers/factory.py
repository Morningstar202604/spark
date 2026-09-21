from __future__ import annotations

from spark.config import SparkConfig, require_api_key
from spark.providers.base import Provider
from spark.providers.mock import MockProvider
from spark.providers.openai_compat import OllamaProvider, OpenAICompatProvider


def create_provider(cfg: SparkConfig, mock: MockProvider | None = None) -> Provider:
    if cfg.provider.name == "mock":
        return mock or MockProvider()
    key = require_api_key(cfg)
    if cfg.provider.name == "ollama":
        base = cfg.provider.base_url
        if not base or base == "https://api.deepseek.com/v1":
            base = "http://127.0.0.1:11434/v1"
        return OllamaProvider(base, cfg.provider.model, key)
    return OpenAICompatProvider(cfg.provider.base_url, cfg.provider.model, key or "")


def apply_custom_provider(
    cfg: SparkConfig,
    *,
    base_url: str,
    model: str,
    api_key: str | None = None,
) -> SparkConfig:
    cfg.provider.name = "openai_compat"
    cfg.provider.base_url = base_url.rstrip("/")
    cfg.provider.model = model
    if api_key:
        cfg.provider.api_key = api_key
    return cfg
