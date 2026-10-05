"""Provider HTTP 可靠性：瞬态错误重试、usage 优先真实值、不可重试 4xx 直接失败。

重构注记：provider.py 已改用 OpenAI SDK，本测试改为直接 mock
openai SDK 的异步接口（比 mock httpx 更贴近真实故障注入路径）。
"""

from __future__ import annotations

import pytest

from spark import provider


class FakeStreamChunk:
    """模拟 openai SDK 的 StreamChunk（choices + usage）。"""

    class _Choice:
        def __init__(self, content: str = "", tool_calls=None, finish_reason=None):
            self.delta = type(
                "Delta",
                (),
                {"content": content, "tool_calls": tool_calls, "reasoning_content": None},
            )()
            self.finish_reason = finish_reason

    def __init__(self, choices=None, usage=None, model="test-model"):
        self.choices = choices or []
        self.usage = usage
        self.model = model


class FakeStream:
    """模拟 openai SDK 的异步 stream 返回值。"""

    def __init__(self, chunks: list[FakeStreamChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeCompletions:
    def __init__(self, attempts: list) -> None:
        self._attempts = attempts
        self.calls = 0

    async def create(self, **kwargs) -> FakeStream:
        self.calls += 1
        item = self._attempts[self.calls - 1]
        if isinstance(item, Exception):
            raise item
        return item


class FakeChat:
    def __init__(self, attempts: list) -> None:
        self.completions = FakeCompletions(attempts)


class FakeChatClient:
    def __init__(self, attempts: list) -> None:
        self.chat = FakeChat(attempts)


def _cfg() -> dict:
    return {
        "model": "agnes-3.0-flash",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-test",
    }


def _usage_chunk(prompt: int = 42, completion: int = 7) -> FakeStreamChunk:
    return FakeStreamChunk(
        choices=[FakeStreamChunk._Choice(content="hi")],
        usage=type("Usage", (), {"prompt_tokens": prompt, "completion_tokens": completion})(),
    )


async def test_stream_chat_retries_transient_failure_before_any_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx 连接错误在未产出内容时应重试（最多 2 次）。"""

    class _Err503(Exception):
        status_code = 503

    def _fake_make_client(cfg):
        return FakeChatClient(
            [
                _Err503("上游 503"),
                FakeStream([_usage_chunk()]),
            ]
        )

    monkeypatch.setattr(provider, "_make_client", _fake_make_client)
    events = [ev async for ev in provider.stream_chat(_cfg(), [{"role": "user", "content": "hi"}])]

    usage_evs = [e for e in events if e["type"] == "usage"]
    assert len(usage_evs) >= 1
    assert usage_evs[0]["prompt_tokens"] == 42
    assert usage_evs[0]["completion_tokens"] == 7


async def test_stream_chat_does_not_retry_bad_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """400 配置类错误不应重试，直接抛 ProviderError。"""

    class _Err400(Exception):
        status_code = 400

        def __str__(self):
            return "Bad Request (status 400)"

    def _fake_make_client(cfg):
        return FakeChatClient([_Err400("Bad Request")])

    monkeypatch.setattr(provider, "_make_client", _fake_make_client)

    with pytest.raises(provider.ProviderError) as exc:
        _ = [ev async for ev in provider.stream_chat(_cfg(), [{"role": "user", "content": "hi"}])]

    assert "400" in str(exc.value)


async def test_stream_chat_prefers_real_usage_from_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """网关返回的真实用量优先于本地估算。"""
    def _fake_make_client(cfg):
        return FakeChatClient([FakeStream([_usage_chunk(42, 7)])])

    monkeypatch.setattr(provider, "_make_client", _fake_make_client)
    got = [ev async for ev in provider.stream_chat(_cfg(), [{"role": "user", "content": "hi"}])]
    usage = next(ev for ev in got if ev["type"] == "usage")

    assert usage["prompt_tokens"] == 42
    assert usage["completion_tokens"] == 7


async def test_stream_chat_tool_call_arguments_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_calls 的 arguments 应解析成 dict（SDK 已做，此处验证封装层）。"""

    class _FakeTC:
        def __init__(self):
            self.id = "call_1"
            self.function = type("F", (), {"name": "run_shell", "arguments": '{"cmd": "ls"}'})()

    class _FakeDelta:
        def __init__(self):
            self.content = None
            self.reasoning_content = None
            self.tool_calls = [_FakeTC()]

    class _Chunk:
        def __init__(self):
            self.choices = [type("C", (), {"delta": _FakeDelta(), "finish_reason": None})()]
            self.usage = None
            self.model = "test"

    def _fake_make_client(cfg):
        return FakeChatClient([FakeStream([_Chunk(), _usage_chunk()])])

    monkeypatch.setattr(provider, "_make_client", _fake_make_client)
    got = [ev async for ev in provider.stream_chat(_cfg(), [{"role": "user", "content": "hi"}])]
    tc = next(ev for ev in got if ev["type"] == "tool_calls")
    assert tc["calls"][0]["name"] == "run_shell"
    assert tc["calls"][0]["arguments"] == {"cmd": "ls"}


def test_estimate_tokens_fallback_without_tiktoken(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 tiktoken 时应回退估算，不抛异常。"""
    monkeypatch.setattr(provider, "_ENCODER", None)
    monkeypatch.setattr(provider, "_ENCODER_TRIED", True)

    assert provider.estimate_tokens("hello world") > 0
    assert provider.estimate_tokens("你好世界") > 0
    assert provider.estimate_tokens("") == 0


def test_provider_error_status_passthrough() -> None:
    """ProviderError 应透传 HTTP status。"""
    err = provider.ProviderError("boom", status=429)
    assert err.status == 429
