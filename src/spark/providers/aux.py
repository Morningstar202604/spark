"""One-shot auxiliary LLM calls (summaries / titles / memory / embeddings).

历史背景：memory/llm.py、web/state.py 曾各自用裸 httpx 直发
``chat/completions``（手写鉴权头、响应解析、错误处理），与 Provider 主通道
（LiteLLM）并存成三套传输。本模块收敛为唯一实现，统一走 LiteLLM：

* 裸模型名按 OpenAI 兼容处理，规则与 :func:`spark.providers.openai_compat.litellm_model_name`
  同源（单一真相源）；
* 非流式调用，``temperature`` / ``max_tokens`` 可控（记忆抽取、标题生成
  需要确定性输出）；
* ``embed_once`` 失败返回 ``None`` 由调用方降级（保持 embed_texts 契约），
  ``chat_once`` 抛出原始异常由调用方决定回退策略（与旧 chat_complete 一致）。

裸 httpx 仅保留在 :mod:`spark.providers.probe`——探活的功能本身就是要
验证端点的原始线上协议（finish_reason / [DONE] / SSE 帧格式），
若经 LiteLLM 的宽容解析反而掩盖不兼容端点，属功能定位而非重复实现。
"""

from __future__ import annotations

import logging
from typing import Any

import litellm

from spark.providers.openai_compat import litellm_model_name

logger = logging.getLogger(__name__)


def _extract_content(resp: Any) -> str:
    """从 litellm 响应提取文本 content（str 与多模态 block 列表两种形态）。"""
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


async def chat_once(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str = "",
    user: str = "",
    temperature: float = 0.0,
    max_tokens: int = 1000,
    timeout: float = 60.0,
) -> str:
    """一次非流式补全，返回 assistant 文本（无 choices 时返回空串）。"""
    model_arg, provider_hint = litellm_model_name(model)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    kwargs: dict[str, Any] = dict(
        model=model_arg,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        num_retries=2,
        drop_params=True,
    )
    if base_url:
        kwargs["api_base"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    if provider_hint:
        kwargs["custom_llm_provider"] = provider_hint
    resp = await litellm.acompletion(**kwargs)
    return _extract_content(resp)


async def embed_once(
    *,
    base_url: str,
    api_key: str,
    model: str,
    texts: list[str],
    timeout: float = 30.0,
) -> list[list[float]] | None:
    """一次 embedding 调用；请求失败或返回条数不符时返回 None（调用方降级）。"""
    model_arg, provider_hint = litellm_model_name(model)
    kwargs: dict[str, Any] = dict(model=model_arg, input=texts, timeout=timeout)
    if base_url:
        kwargs["api_base"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    if provider_hint:
        kwargs["custom_llm_provider"] = provider_hint
    try:
        resp = await litellm.aembedding(**kwargs)
    except Exception as exc:
        logger.warning("Embedding request failed: %s", exc)
        return None
    out: list[list[float]] = []
    for item in getattr(resp, "data", None) or []:
        vec = getattr(item, "embedding", None)
        if isinstance(vec, list) and vec:
            out.append([float(x) for x in vec])
    if len(out) != len(texts):
        return None
    return out


__all__ = ["chat_once", "embed_once"]
