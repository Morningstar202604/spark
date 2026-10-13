"""P0-9：OpenTelemetry 观测埋点测试（monkeypatch 注入 tracer，顺序无关）。

不依赖全局 TracerProvider（OTel 只允许设置一次，会被其它测试抢占），
直接给 spark.otel 注入测试专属 tracer（SimpleSpanProcessor 同步导出）。
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _reset_otel():
    import spark.otel as otel

    otel._tracer = None
    otel._INITIALIZED = False
    otel._exporter = None
    otel._processor = None
    yield
    otel._tracer = None
    otel._INITIALIZED = False
    otel._exporter = None
    otel._processor = None


def _make_provider():
    from spark.providers.openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(
        model="deepseek-chat",
        base_url="http://localhost:9999",  # 不会被请求（走 monkeypatch 的 _stream_impl）
        api_key="sk-test",
    )


def _inject_test_tracer(monkeypatch):
    """给 spark.otel.get_tracer 注入测试专属 tracer，返回其 in-memory exporter。"""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    import spark.otel as otel

    monkeypatch.setattr(otel, "get_tracer", lambda: provider.get_tracer("spark-test"))
    return exporter


def test_init_telemetry_defaults_in_memory():
    from spark.otel import init_telemetry

    tracer = init_telemetry()
    assert tracer is not None
    assert init_telemetry() is tracer  # 幂等


def test_llm_stream_spans_success_and_error(monkeypatch):
    exporter = _inject_test_tracer(monkeypatch)

    from spark.models import ChatDelta
    from spark.providers.openai_compat import OpenAICompatProvider

    async def fake_impl(self, messages, tools, effort):
        yield ChatDelta(type="text", text="hi")
        yield ChatDelta(type="end")

    monkeypatch.setattr(OpenAICompatProvider, "_stream_impl", fake_impl)

    provider_obj = _make_provider()

    async def collect_ok():
        out = []
        async for delta in provider_obj.stream([], [], "medium"):
            out.append(delta)
        return out

    deltas = asyncio.run(collect_ok())
    assert [d.type for d in deltas] == ["text", "end"]

    # 失败场景：mock _stream_impl 抛错
    async def broken_impl(self, messages, tools, effort):
        raise RuntimeError("boom")
        yield ChatDelta(type="end")  # pragma: no cover

    monkeypatch.setattr(OpenAICompatProvider, "_stream_impl", broken_impl)

    async def collect_broken():
        async for _ in provider_obj.stream([], [], None):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(collect_broken())

    spans = exporter.get_finished_spans()
    llm_spans = [s for s in spans if s.name == "llm.stream"]
    assert len(llm_spans) == 2, "应有 2 个 llm.stream span（成功 + 失败）"

    ok_span = next(s for s in llm_spans if s.status.status_code.name == "OK")
    assert ok_span.attributes.get("llm.model") == "deepseek-chat"
    assert ok_span.attributes.get("spark.effort") == "medium"

    err_span = next(s for s in llm_spans if s.status.status_code.name == "ERROR")
    assert err_span.events, "失败 span 应记录 exception event"
