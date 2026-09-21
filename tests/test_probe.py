from __future__ import annotations

import json

import httpx
import pytest

from spark.config import SparkConfig, require_api_key, save_config
from spark.providers.probe import probe_provider


def _complete_body() -> dict:
    return {
        "id": "c1",
        "choices": [{"message": {"role": "assistant", "content": "SPARK_OK local agents stay on your machine."}}],
    }


def _stream_body() -> str:
    chunks = [
        {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
        {"choices": [{"delta": {"content": "SPARK_OK "}}]},
        {"choices": [{"delta": {"content": "stable stream output."}, "finish_reason": "stop"}]},
    ]
    lines = [f"data: {json.dumps(c)}" for c in chunks]
    lines.append("data: [DONE]")
    return "\n".join(lines) + "\n"


@pytest.mark.asyncio
async def test_probe_real_content_and_stable_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        if payload.get("stream"):
            return httpx.Response(200, text=_stream_body(), headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json=_complete_body())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await probe_provider(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="demo",
            rounds=2,
            client=client,
        )
    assert result.ok
    assert "SPARK_OK" in result.content
    assert "SPARK_OK" in result.stream_content
    assert result.rounds == 2


@pytest.mark.asyncio
async def test_probe_rejects_empty_content() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await probe_provider(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="demo",
            rounds=1,
            client=client,
        )
    assert result.ok is False
    assert result.error


def test_config_inline_api_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.api_key = "inline-secret"
    path = save_config(cfg, tmp_path / "config.toml")
    assert path.exists()
    assert require_api_key(cfg) == "inline-secret"
