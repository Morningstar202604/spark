"""OpenTelemetry 观测（Langfuse 自托管 = OTLP 端点，无需重复实现）。

GenAI 语义约定（master plan §5）：trace=turn、span=tool、metric=token/成本/延迟。

**默认关**：``SPARK_OTEL_ENDPOINT`` 未设置时全链路 NoOp（零依赖导入、零开销）；
设置后 OTLP 导出——自托管 Langfuse 原生接受 OTLP，因此这里不做 Langfuse 专属
client。``SPARK_OTEL_ENDPOINT=inmemory`` 保留给测试（InMemory exporter，进程内
可读取断言）。

环境变量：
- SPARK_OTEL_ENABLED      ``1`` 显式开启（与 ENDPOINT 二选一即可，同时给出以
  ENDPOINT 为准；两者皆无 = 关）
- SPARK_OTEL_ENDPOINT     OTLP 端点（http://host:4318 grpc://host:4317 或
  字面量 ``inmemory``）
- SPARK_OTEL_SERVICE_NAME 服务名，默认 spark-agent
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_tracer: Any | None = None
_meter: Any | None = None
_processor: Any | None = None
_exporter: Any | None = None
_lock = threading.Lock()
_INITIALIZED = False

_NOOP_CONTEXT = object()


def telemetry_enabled() -> bool:
    """是否显式开启（环境变量判定；未开启时全链路 NoOp）。"""
    if os.environ.get("SPARK_OTEL_ENABLED", "").strip() == "1":
        return True
    endpoint = os.environ.get("SPARK_OTEL_ENDPOINT", "").strip()
    return bool(endpoint) and endpoint.lower() != "0"


def _noop_tracer() -> Any:
    """NoOp tracer（自管 NoOpTracerProvider，不碰全局）。"""
    try:
        from opentelemetry.trace import NoOpTracerProvider

        return NoOpTracerProvider().get_tracer("spark-agent-noop")
    except Exception:  # pragma: no cover - api 缺失
        return None


def init_telemetry(service_name: str | None = None) -> Any | None:
    """初始化 tracer/meter（幂等；**自管 provider**，不依赖 OTel 全局单例）。

    OTel 的 ``trace.set_tracer_provider`` 全局只生效一次——测试与多实例场景
    无法重置，因此这里直接持有 provider 并从其取 tracer/meter（官方依赖
    注入模式）。返回 Tracer；未启用时返回 NoOp tracer（观测永不阻塞主流程）。
    """
    global _tracer, _meter, _INITIALIZED, _exporter, _processor
    if _INITIALIZED:
        return _tracer
    with _lock:
        if _INITIALIZED:
            return _tracer
        _INITIALIZED = True
        service = service_name or os.environ.get("SPARK_OTEL_SERVICE_NAME", "spark-agent")
        if not telemetry_enabled():
            try:
                from opentelemetry.trace import NoOpTracerProvider

                _tracer = NoOpTracerProvider().get_tracer(service)
            except Exception:  # pragma: no cover - api 缺失
                _tracer = None
            return _tracer
        try:
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            endpoint = os.environ.get("SPARK_OTEL_ENDPOINT", "").strip()
            provider = TracerProvider()
            if endpoint and endpoint.lower() != "inmemory":
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                _exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            else:
                from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
                    InMemorySpanExporter,
                )

                # 无端点/显式 inmemory：记录在本进程内，测试可读取
                _exporter = InMemorySpanExporter()
            _processor = BatchSpanProcessor(_exporter)
            provider.add_span_processor(_processor)
            _tracer = provider.get_tracer(service)

            meter_provider = MeterProvider()
            _meter = meter_provider.get_meter("spark-agent")
            logger.info("telemetry enabled (endpoint=%s)", endpoint or "in-memory")
        except Exception as exc:  # pragma: no cover - 环境差异
            logger.debug("telemetry unavailable, using NoOp: %s", exc)
            try:
                from opentelemetry.trace import NoOpTracerProvider

                _tracer = NoOpTracerProvider().get_tracer(service)
            except Exception:  # pragma: no cover
                _tracer = None
            _meter = None
        return _tracer


def flush() -> None:
    """冲刷待导出 span（进程退出前自动调用；未初始化时为 no-op）。"""
    if _processor is not None:
        try:
            _processor.force_flush()
        except Exception:  # pragma: no cover
            pass


atexit.register(flush)


def get_tracer() -> Any:
    """获取全局 tracer（未初始化时惰性初始化；未启用返回 NoOp）。"""
    if _tracer is None:
        init_telemetry()
    assert _tracer is not None
    return _tracer


def get_meter() -> Any | None:
    """获取全局 meter（未启用/初始化失败时返回 None，调用方须判空）。"""
    if not _INITIALIZED:
        init_telemetry()
    return _meter


def in_memory_exporter() -> Any | None:
    """测试专用：读取 in-memory exporter（未处于 in-memory 模式时返回 None）。"""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    return _exporter if isinstance(_exporter, InMemorySpanExporter) else None


def reset_for_tests() -> None:
    """测试专用：还原未初始化状态（清空全局单例）。"""
    global _tracer, _meter, _INITIALIZED, _exporter, _processor
    with _lock:
        _tracer = None
        _meter = None
        _exporter = None
        _processor = None
        _INITIALIZED = False


# ---------------------------------------------------------------------------
# GenAI 语义约定 helper（trace=turn、span=tool、metric=tokens/延迟）
#
# 未启用时 tracer 是 NoOp、meter 是 None——helper 直接调用即可，无需判开关；
# NoOp span 的 set_attribute/end 全部零成本。显式 parent context 传递：
# LangGraph 节点可能在新 task 中运行，contextvar 链不可依赖。
# ---------------------------------------------------------------------------


def start_turn_span(
    *,
    session_id: str,
    model: str,
    provider_name: str,
    workdir: str,
    resumed: bool = False,
) -> Any | None:
    """trace=turn：一次用户 turn 一个 span。返回 Span（调用方保存并 end）。"""
    try:

        span = get_tracer().start_span(
            "spark.turn",
            attributes={
                "gen_ai.system": provider_name,
                "gen_ai.request.model": model,
                "gen_ai.conversation.id": session_id,
                "spark.session_id": session_id,
                "spark.workdir": workdir,
                "spark.turn.resumed": resumed,
            },
        )
        return span
    except Exception:  # pragma: no cover - 观测永不阻塞主流程
        logger.debug("turn span start failed", exc_info=True)
        return None


def end_turn_span(
    span: Any | None,
    *,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    latency_ms: int,
    error: str | None = None,
    source: str = "estimated",
) -> None:
    """turn 终态：补 usage/延迟 attributes、记录 metrics、结束 span。"""
    if span is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        span.set_attribute("gen_ai.usage.reasoning_tokens", reasoning_tokens)
        span.set_attribute("spark.usage.source", source)
        span.set_attribute("spark.turn.latency_ms", latency_ms)
        if error:
            span.set_attribute("spark.turn.error", error)
            span.set_status(Status(StatusCode.ERROR, error))
        span.end()

        meter = get_meter()
        if meter is not None:
            common = span.attributes or {}
            attrs = {
                "gen_ai.system": common.get("gen_ai.system", ""),
                "gen_ai.request.model": common.get("gen_ai.request.model", ""),
            }
            for kind, value in (
                ("input", input_tokens),
                ("output", output_tokens),
                ("reasoning", reasoning_tokens),
            ):
                if value:
                    meter.create_histogram(
                        "spark.turn.tokens", unit="tokens"
                    ).record(value, {**attrs, "kind": kind})
            meter.create_histogram("spark.turn.latency_ms", unit="ms").record(
                latency_ms, attrs
            )
    except Exception:  # pragma: no cover
        logger.debug("turn span end failed", exc_info=True)


def start_tool_span(parent: Any | None, *, tool_name: str, args_summary: str) -> Any | None:
    """span=tool：turn span 的子 span（显式 context，不受 task 边界影响）。"""
    if parent is None:
        return None
    try:
        from opentelemetry import trace

        return get_tracer().start_span(
            "spark.tool",
            attributes={
                "gen_ai.tool.name": tool_name,
                "spark.tool.arguments": args_summary,
            },
            context=trace.set_span_in_context(parent),
        )
    except Exception:  # pragma: no cover
        logger.debug("tool span start failed", exc_info=True)
        return None


def end_tool_span(
    span: Any | None,
    *,
    ok: bool,
    approval_label: str,
    latency_ms: int,
) -> None:
    """工具终态：结果 attributes、metrics、结束 span。"""
    if span is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_attribute("spark.tool.ok", ok)
        span.set_attribute("spark.tool.approval", approval_label)
        span.set_attribute("spark.tool.duration_ms", latency_ms)
        if not ok:
            span.set_status(Status(StatusCode.ERROR, "tool failed"))
        span.end()

        meter = get_meter()
        if meter is not None:
            name = (span.attributes or {}).get("gen_ai.tool.name", "")
            meter.create_histogram("spark.tool.duration_ms", unit="ms").record(
                latency_ms, {"gen_ai.tool.name": name, "ok": ok}
            )
    except Exception:  # pragma: no cover
        logger.debug("tool span end failed", exc_info=True)
