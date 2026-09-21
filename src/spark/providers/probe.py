from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx

PROBE_PROMPT = (
    "Reply with exactly the token SPARK_OK followed by one short sentence "
    "about local coding agents. Do not refuse."
)
MIN_CONTENT_CHARS = 8


@dataclass
class ProbeResult:
    ok: bool
    model: str
    base_url: str
    content: str = ""
    stream_content: str = ""
    rounds: int = 0
    latency_ms: int = 0
    error: str | None = None
    details: list[str] = field(default_factory=list)

    def public_dict(self) -> dict:
        return {
            "ok": self.ok,
            "model": self.model,
            "base_url": self.base_url,
            "content": self.content,
            "stream_content": self.stream_content,
            "rounds": self.rounds,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "details": self.details,
        }


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _extract_message_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {None, "text"}:
                parts.append(str(item.get("text") or item.get("content") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")


def _chat_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


async def _complete_once(client: httpx.AsyncClient, url: str, api_key: str, model: str, prompt: str) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 120,
        "temperature": 0,
    }
    resp = await client.post(url, headers=_headers(api_key), json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
    payload = resp.json()
    text = _extract_message_content(payload).strip()
    if len(text) < MIN_CONTENT_CHARS:
        raise RuntimeError("model returned empty or too-short content")
    return text


async def _stream_once(client: httpx.AsyncClient, url: str, api_key: str, model: str, prompt: str) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": 120,
        "temperature": 0,
    }
    parts: list[str] = []
    finished = False
    async with client.stream("POST", url, headers=_headers(api_key), json=body) as resp:
        if resp.status_code >= 400:
            raw = (await resp.aread()).decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {resp.status_code}: {raw[:400]}")
        async for line in resp.aiter_lines():
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                finished = True
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                parts.append(content)
            if choice.get("finish_reason"):
                finished = True
    text = "".join(parts).strip()
    if not finished:
        raise RuntimeError("stream ended without finish_reason or [DONE]")
    if len(text) < MIN_CONTENT_CHARS:
        raise RuntimeError("stream returned empty or too-short content")
    return text


async def probe_provider(
    *,
    base_url: str,
    api_key: str,
    model: str,
    rounds: int = 2,
    client: httpx.AsyncClient | None = None,
) -> ProbeResult:
    if not base_url.strip():
        return ProbeResult(ok=False, model=model, base_url=base_url, error="base_url is required")
    if not api_key.strip():
        return ProbeResult(ok=False, model=model, base_url=base_url, error="api_key is required")
    if not model.strip():
        return ProbeResult(ok=False, model=model, base_url=base_url, error="model is required")
    url = _chat_url(base_url)
    timeout = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)
    started = time.monotonic()
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    contents: list[str] = []
    streams: list[str] = []
    details: list[str] = []
    try:
        for i in range(max(rounds, 1)):
            complete = await _complete_once(client, url, api_key, model, PROBE_PROMPT)
            stream = await _stream_once(client, url, api_key, model, PROBE_PROMPT)
            contents.append(complete)
            streams.append(stream)
            details.append(f"round {i + 1}: complete={len(complete)} chars, stream={len(stream)} chars, finished=yes")
        latency_ms = int((time.monotonic() - started) * 1000)
        return ProbeResult(
            ok=True,
            model=model,
            base_url=base_url.rstrip("/"),
            content=contents[-1],
            stream_content=streams[-1],
            rounds=len(contents),
            latency_ms=latency_ms,
            details=details,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return ProbeResult(
            ok=False,
            model=model,
            base_url=base_url.rstrip("/"),
            content=contents[-1] if contents else "",
            stream_content=streams[-1] if streams else "",
            rounds=len(contents),
            latency_ms=latency_ms,
            error=str(exc),
            details=details,
        )
    finally:
        if own_client:
            await client.aclose()


async def list_models(base_url: str, api_key: str, client: httpx.AsyncClient | None = None) -> list[str]:
    url = f"{base_url.rstrip('/')}/models"
    timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        resp = await client.get(url, headers=_headers(api_key))
        if resp.status_code >= 400:
            return []
        payload = resp.json()
        data = payload.get("data") or []
        return [str(item.get("id")) for item in data if item.get("id")]
    except Exception:
        return []
    finally:
        if own:
            await client.aclose()
