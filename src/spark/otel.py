"""P0-9：OpenTelemetry 观测初始化（Langfuse 自托管 = OTLP 端点，无需重复实现）。

范式对齐：OpenAI/Anthropic 的 agent 观测都是 OTel GenAI 语义约定。
Langfuse 自托管原生接受 OTLP，所以这里不做 Langfuse 专属 client——
配置 ``SPARK_OTEL_ENDPOINT``（任意 OTLP 端点，含自托管 Langfuse）即可接入；
未配置时用 in-memory exporter（单测可见、零外部依赖）。

环境变量：
- SPARK_OTEL_ENDPOINT   OTLP 端点（http://host:4318 或 grpc://host:4317）；
  未设置 → in-memory（记录在本进程内，可被测试读取）
- SPARK_OTEL_SERVICE_NAME  服务名，默认 spark-agent
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_tracer: object | None = None
_lock = threading.Lock()
_INITIALIZED = False
# 模块级导出通道：测试/调试可读取（in-memory 时）
_exporter: object | None = None
_processor: object | None = None


def init_telemetry(service_name: str | None = None) -> object | None:
    """初始化全局 tracer provider（幂等）。

    返回全局 Tracer；任何失败都降级为 NoOp（观测不能阻塞主流程）。
    """
    global _tracer, _INITIALIZED, _exporter, _processor
    if _INITIALIZED:
        return _tracer
    with _lock:
        if _INITIALIZED:
            return _tracer
        _INITIALIZED = True
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider()
            endpoint = os.environ.get("SPARK_OTEL_ENDPOINT", "").strip()
            if endpoint:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                _exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            else:
                from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
                    InMemorySpanExporter,
                )

                # 无端点时保持安静（in-memory），不向 stdout 写输出
                _exporter = InMemorySpanExporter()
            _processor = BatchSpanProcessor(_exporter)
            provider.add_span_processor(_processor)
            trace.set_tracer_provider(provider)
            _tracer = trace.get_tracer(os.environ.get("SPARK_OTEL_SERVICE_NAME", "spark-agent"))
            logger.info("telemetry initialized (endpoint=%s)", endpoint or "in-memory")
        except Exception as exc:  # pragma: no cover - 环境差异
            logger.debug("telemetry unavailable, using NoOp: %s", exc)
            from opentelemetry import trace

            _tracer = trace.get_tracer("spark-agent")
        return _tracer


def flush() -> None:
    """冲刷待导出 span（测试/关闭前调用）。"""
    if _processor is not None:
        try:
            _processor.force_flush()
        except Exception:  # pragma: no cover
            pass


def get_tracer():
    """获取全局 tracer（未初始化时惰性初始化）。"""
    if _tracer is None:
        init_telemetry()
    return _tracer
