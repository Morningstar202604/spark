from __future__ import annotations

from spark.models import ChatMessage, ToolCall


def estimate_tokens(text: str) -> int:
    """Heuristic token estimate: CJK chars ~1 token each, others ~4 chars per token."""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f":
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


def _message_chars(msg: ChatMessage) -> str:
    text = msg.content or ""
    if msg.tool_calls:
        for call in msg.tool_calls:
            text += call.name + str(call.arguments) + "function"
    return text


def estimate_message_tokens(msg: ChatMessage) -> int:
    return estimate_tokens(_message_chars(msg)) + 4


def estimate_history_tokens(messages: list[ChatMessage]) -> int:
    return sum(estimate_message_tokens(msg) for msg in messages)


def estimate_tool_overhead(schemas: list[dict]) -> int:
    import json

    return estimate_tokens(json.dumps(schemas, ensure_ascii=False)) if schemas else 0
