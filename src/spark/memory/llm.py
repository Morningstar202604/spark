"""Shared memory-side LLM helpers: chat completion, embeddings, JSON parsing.

传输统一走 :mod:`spark.providers.auxiliary`（LiteLLM 单一通道）；本模块保留
原函数签名与返回契约（``embed_texts`` 失败返回 None、``chat_complete``
无 choices 返回空串），供 extractor/service 按旧语义调用。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def chat_complete(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.0,
    max_tokens: int = 1000,
) -> str:
    from spark.providers.auxiliary import chat_once

    return await chat_once(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
    )


async def embed_texts(
    *,
    base_url: str,
    api_key: str,
    model: str,
    texts: list[str],
) -> list[list[float]] | None:
    from spark.providers.auxiliary import embed_once

    return await embed_once(base_url=base_url, api_key=api_key, model=model, texts=texts)


def parse_json_object(text: str) -> dict | None:
    """Extract the first JSON object from *text*, stripping fences if present."""
    text = text.strip()
    # Strip fenced code blocks ("```json" / "```")
    if text.startswith("```"):
        # Remove the opening fence (``` or ```json)
        end_fence = text.find("\n", 3)
        if end_fence != -1:
            fence_tag = text[3:end_fence].strip().lower()
            if fence_tag in ("", "json"):
                text = text[end_fence + 1 :]
        # Remove closing ```
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
