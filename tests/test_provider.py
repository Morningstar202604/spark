"""Provider HTTP 可靠性：瞬态错误重试、usage 优先真实值、不可重试 4xx 直接失败。"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Sequence

import httpx
import pytest

from spark2 import provider
class FakeResponse:
    def __init__(self, status_code: int, sse_events: Sequence[dict[str, Any]]) -> None:
        self.status_code = status_code
        self._sse_events = sse_events

    async def aread(self) -> bytes:
        return json.dumps({"error": {"message": "failed"}}, ensure_ascii=False).encode()

    async def aiter_lines(self) -> AsyncIterator[str]:
        for event in self._sse_events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}"
        yield "data: [DONE]"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeAsyncClient:
    def __init__(self, attempts: list[Any]) -> None:
        self.attempts = attempts
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str, **kwargs):
        self.calls += 1
        item = self.attempts[self.calls - 1]
        if isinstance(item, Exception):
            raise item
        return item


def _cfg() -> dict:
    return {
        "model": "agnes-3.0-flash",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-test",
    }


async def test_stream_chat_retries_transient_failure_before_any_output(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAsyncClient([httpx.ConnectError("boom"), FakeResponse(200, [{"choices": [{"delta": {"content": "ok"}}]}])])
    monkeypatch.setattr(provider.httpx, "AsyncClient", lambda *args, **kwargs: fake)

    events = [ev async for ev in provider.stream_chat(_cfg(), [{"role": "user", "content": "hi"}])]

    assert fake.calls == 2
    assert any(ev["type"] == "text" and ev["text"] == "ok" for ev in events)


async def test_stream_chat_does_not_retry_bad_request(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAsyncClient([FakeResponse(400, [])])
    monkeypatch.setattr(provider.httpx, "AsyncClient", lambda *args, **kwargs: fake)

    with pytest.raises(provider.ProviderError) as exc:
        _ = [ev async for ev in provider.stream_chat(_cfg(), [{"role": "user", "content": "hi"}])]

    assert fake.calls == 1
    assert "400" in str(exc.value)


async def test_stream_chat_prefers_real_usage_from_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        {"choices": [{"delta": {"content": "hello"}}]},
        {"choices": [], "usage": {"prompt_tokens": 42, "completion_tokens": 7}},
    ]
    fake = FakeAsyncClient([FakeResponse(200, events)])
    monkeypatch.setattr(provider.httpx, "AsyncClient", lambda *args, **kwargs: fake)

    got = [ev async for ev in provider.stream_chat(_cfg(), [{"role": "user", "content": "hi"}])]
    usage = next(ev for ev in got if ev["type"] == "usage")

    assert usage["prompt_tokens"] == 42
    assert usage["completion_tokens"] == 7
