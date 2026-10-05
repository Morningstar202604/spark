"""全链路 trace_id + 结构化日志。

- trace_id 通过 contextvars 在 asyncio 任务内隐式传播，无需手动传参。
- 进入 stream_chat 时自动生成 trace_id，贯穿 provider / loop / tool / sanitize。
- 关键节点（loop 进入/退出、工具调用/返回、审批决策、provider 重试）发 INFO 级 JSON 日志。
"""
from __future__ import annotations

import contextvars
import logging
import time
import uuid

_logger = logging.getLogger("spark")

# contextvars：asyncio 任务内隐式传播，子任务自动继承
_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_trace_start: contextvars.ContextVar[float] = contextvars.ContextVar("trace_start", default=0.0)


def new_trace_id() -> str:
    """生成新的 trace_id（32 位 hex）。"""
    return uuid.uuid4().hex


def set_trace(tid: str) -> None:
    """设置当前上下文的 trace_id（在当前 asyncio 任务生命周期内持续）。"""
    _trace_start.set(time.monotonic())
    _trace_id.set(tid)


def trace_id() -> str:
    """取当前上下文的 trace_id。"""
    return _trace_id.get()


def _elapsed_ms() -> int:
    """当前 trace 已耗时（ms）。"""
    start = _trace_start.get()
    if not start:
        return 0
    return int((time.monotonic() - start) * 1000)


def _log(level: str, event: str, **fields: object) -> None:
    """写一行结构化 JSON 日志（仅在 trace_id 存在时输出）。"""
    tid = _trace_id.get()
    if not tid:
        return
    rec = {
        "level": level,
        "trace_id": tid,
        "event": event,
        "elapsed_ms": _elapsed_ms(),
    }
    rec.update(fields)
    getattr(_logger, level, _logger.info)(rec)


def info(event: str, **fields: object) -> None:
    _log("info", event, **fields)


def warning(event: str, **fields: object) -> None:
    _log("warning", event, **fields)


def error(event: str, **fields: object) -> None:
    _log("error", event, **fields)


def configure_logging(level: str = "WARNING") -> None:
    """配置 spark logger：若 root 已有 handler 则跳过（尊重调用方配置）。"""
    if _logger.handlers:
        return
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.WARNING),
        format="%(message)s",
    )
