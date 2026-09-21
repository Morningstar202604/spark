from __future__ import annotations

import json
from typing import Any

import httpx

from spark.models import ChatMessage


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


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
    url = f"{base_url.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=_headers(api_key), json=body)
        resp.raise_for_status()
        data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


async def embed_texts(
    *,
    base_url: str,
    api_key: str,
    model: str,
    texts: list[str],
) -> list[list[float]] | None:
    url = f"{base_url.rstrip('/')}/embeddings"
    body = {"model": model, "input": texts}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=_headers(api_key), json=body)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None
    out: list[list[float]] = []
    items = data.get("data") or []
    for item in items:
        vec = item.get("embedding")
        if isinstance(vec, list) and vec:
            out.append([float(x) for x in vec])
    if len(out) != len(texts):
        return None
    return out


def parse_json_object(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def messages_to_transcript(messages: list[ChatMessage], max_chars: int = 6000) -> str:
    lines: list[str] = []
    used = 0
    for msg in messages:
        if msg.role not in {"user", "assistant", "tool"}:
            continue
        body = (msg.content or "").strip()
        if msg.role == "tool" and msg.name:
            body = f"[tool:{msg.name}] {body[:300]}"
        if not body:
            continue
        line = f"{msg.role}: {body[:1200]}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)
