from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx

from spark.models import ChatDelta, ChatMessage, ToolCall


def _to_openai(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "summary":
            out.append(
                {
                    "role": "user",
                    "content": f"<conversation_summary>\n{msg.content or ''}\n</conversation_summary>",
                }
            )
            continue
        item: dict[str, Any] = {"role": msg.role}
        if msg.images and msg.role == "user":
            parts: list[dict[str, Any]] = []
            if msg.content:
                parts.append({"type": "text", "text": msg.content})
            for image in msg.images:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image.media_type};base64,{image.data}"},
                    }
                )
            item["content"] = parts
        elif msg.content is not None:
            item["content"] = msg.content
        if msg.tool_calls:
            item["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in msg.tool_calls
            ]
        if msg.tool_call_id:
            item["tool_call_id"] = msg.tool_call_id
        if msg.name:
            item["name"] = msg.name
        out.append(item)
    return out


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OpenAICompatProvider:
    def __init__(self, base_url: str, model: str, api_key: str, max_retries: int = 3) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_retries = max_retries

    async def stream(self, messages: list[ChatMessage], tools: list[dict]) -> AsyncIterator[ChatDelta]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai(messages),
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        started = False
        attempt = 0
        delays = [1.0, 2.0, 4.0]
        while True:
            try:
                async for delta in self._stream_once(url, headers, body):
                    started = True
                    yield delta
                return
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if started or status not in RETRYABLE_STATUS or attempt >= self.max_retries:
                    raise
                delay = delays[min(attempt, len(delays) - 1)]
                attempt += 1
                await asyncio.sleep(delay)
            except (httpx.TransportError, httpx.ReadTimeout):
                if started or attempt >= self.max_retries:
                    raise
                delay = delays[min(attempt, len(delays) - 1)]
                attempt += 1
                await asyncio.sleep(delay)

    async def _stream_once(self, url: str, headers: dict, body: dict) -> AsyncIterator[ChatDelta]:
        tool_acc: dict[int, dict[str, str]] = {}
        think_state = {"open": False, "closed": False}
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield ChatDelta(type="reasoning", text=str(reasoning))
                    content = delta.get("content")
                    if content:
                        for piece, kind in self._split_think(str(content), think_state):
                            if kind == "reasoning":
                                yield ChatDelta(type="reasoning", text=piece)
                            elif piece:
                                yield ChatDelta(type="text", text=piece)
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index", 0))
                        acc = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        acc["id"] += tc.get("id") or ""
                        fn = tc.get("function") or {}
                        acc["name"] += fn.get("name") or ""
                        acc["arguments"] += fn.get("arguments") or ""
        for acc in tool_acc.values():
            try:
                args = json.loads(acc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            yield ChatDelta(
                type="tool_call",
                tool_call=ToolCall(id=acc["id"] or "call_0", name=acc["name"], arguments=args),
            )
        yield ChatDelta(type="end")

    @staticmethod
    def _split_think(text: str, state: dict) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        buf = text
        while buf:
            if state["open"]:
                end = buf.find("</think>")
                if end >= 0:
                    if end > 0:
                        out.append((buf[:end], "reasoning"))
                    buf = buf[end + 8 :]
                    state["open"] = False
                    state["closed"] = True
                    continue
                out.append((buf, "reasoning"))
                buf = ""
                continue
            start = buf.find("<think>")
            if start >= 0 and not state["closed"]:
                if start > 0:
                    out.append((buf[:start], "text"))
                buf = buf[start + 7 :]
                state["open"] = True
                continue
            if buf:
                out.append((buf, "text"))
            buf = ""
        return out


class OllamaProvider(OpenAICompatProvider):
    def __init__(self, base_url: str, model: str, api_key: str | None = None) -> None:
        super().__init__(base_url or "http://127.0.0.1:11434/v1", model, api_key or "ollama")
