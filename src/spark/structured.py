"""Structured-output收口 (P1-1).

Thin wrapper over `instructor.from_litellm()` that turns the existing
LiteLLM completion path into a schema-validated output channel:

* 首轮即合法 → 直接返回 Pydantic 模型实例。
* 首轮非法 / 缺字段 / 类型错 → instructor 自动把 Pydantic 校验错误回灌进下一轮 prompt，
  让模型自我修正（repair loop），直到通过或耗尽 ``max_retries``。
* 始终失败 → 抛 :class:`StructuredOutputError`，调用方可以回退到老的
  ``parse_json_object`` 路径或降级为 None。

设计原则
--------
* **不自研 repair 轮子**：instructor 是工业级实现，覆盖了 JSON mode / tool calling /
  function calling / partial JSON / 流式 partial 等多种模式，社区在维护。
* **默认 ``Mode.JSON``**：把 Pydantic schema 注入 system prompt，对所有 OpenAI 兼容端点
  （DeepSeek / Qwen / Kimi / GLM / Ollama vLLM …）都能工作，不要求 provider 原生支持
  tool calling。需要更强约束时可显式传 ``mode=instructor.Mode.TOOLS``。
* **不触碰现有 Provider / Mock 路径**：本模块是一条独立的 opt-in 通道，老代码里
  ``chat_complete`` + ``parse_json_object`` 的兜底逻辑保持原样，新节点（memory 抽取、
  plan/verify 等）优先走这里。
"""

from __future__ import annotations

import logging
from typing import Any, Generic, TypeVar

import instructor
import litellm
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class StructuredOutputError(RuntimeError):
    """Raised when schema-validated output cannot be produced.

    Wraps both network/transport failures and the final ``InstructorRetryException``
    raised after exhausting ``max_retries``.  Callers should catch this and fall
    back to whatever degradation strategy they have (e.g. the legacy
    ``parse_json_object`` path).
    """


# ---------------------------------------------------------------------------
# Client caching
# ---------------------------------------------------------------------------

# Cache one AsyncInstructor per mode so we don't rebuild on every call.
_CLIENT_CACHE: dict[Any, Any] = {}


def get_async_client(
    mode: instructor.Mode | None = None,
) -> instructor.AsyncInstructor:
    """Return a cached AsyncInstructor wrapping ``litellm.acompletion``.

    Defaults to :data:`instructor.Mode.JSON` (prompt-injected schema), which
    works on every OpenAI-compatible endpoint without requiring native tool
    calling.  Pass ``mode=instructor.Mode.TOOLS`` for providers that support
    OpenAI-style function calling (OpenAI, DeepSeek, vLLM with tool support).
    """
    key = mode or instructor.Mode.JSON
    client = _CLIENT_CACHE.get(key)
    if client is None:
        client = instructor.from_litellm(litellm.acompletion, mode=key)
        _CLIENT_CACHE[key] = client
    return client


def _reset_client_cache() -> None:
    """Test hook: drop cached clients so monkeypatching litellm takes effect."""
    _CLIENT_CACHE.clear()


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------


async def complete(
    response_model: type[T],
    *,
    system: str,
    user: str,
    model: str,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    max_retries: int = 3,
    mode: instructor.Mode | None = None,
    **extra_kwargs: Any,
) -> T:
    """Call the LLM and return a Pydantic-validated instance of *response_model*.

    Parameters
    ----------
    response_model:
        A Pydantic BaseModel subclass.  Its schema is injected into the prompt
        (``Mode.JSON``) or registered as a tool (``Mode.TOOLS``).
    system, user:
        Standard two-turn messages.  ``system`` should describe the task;
        instructor appends its own schema instructions.
    model:
        LiteLLM model string (e.g. ``"deepseek-chat"``, ``"openai/gpt-4o-mini"``,
        ``"ollama/qwen2.5-coder"``).
    base_url, api_key:
        Passed through to LiteLLM so the call hits the configured endpoint.
    max_retries:
        Number of *additional* repair attempts after the first failed parse.
        ``0`` means "validate once, no repair loop".
    mode:
        Optional override; defaults to ``instructor.Mode.JSON``.

    Raises
    ------
    StructuredOutputError
        If the request fails after ``max_retries`` repair attempts, or if the
        underlying transport raises.  The original exception is chained.
    """
    client = get_async_client(mode)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        max_retries=max_retries,
        strict=False,  # JSON mode; strict=True is TOOLS-mode only
    )
    if base_url:
        kwargs["api_base"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    kwargs.update(extra_kwargs)
    try:
        return await client.create(response_model=response_model, **kwargs)
    except StructuredOutputError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raise as our public type
        # Distinguish "repair loop exhausted" from a raw transport error so
        # callers can log them differently if they care.
        logger.warning(
            "structured_output_failed model=%s retries=%d err=%s",
            model,
            max_retries,
            str(exc)[:200],
        )
        raise StructuredOutputError(str(exc)) from exc


__all__ = [
    "StructuredOutputError",
    "get_async_client",
    "complete",
]
