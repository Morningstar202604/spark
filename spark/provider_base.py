"""Provider 抽象协议：为多协议（Anthropic / Gemini）扩展做准备，不破坏现有 OpenAI 兼容路径。

任何实现 chat_stream() / summarize_messages() / estimate_tokens() / test_connection() 的对象
都可以作为 Spark 的 provider —— 不强制继承，按协议隐式适配（structural typing）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class ProviderError(Exception):
    """Provider 层错误；status 为 HTTP 状态码（如有）。"""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class BaseProvider(Protocol):
    """Provider 协议——后续 AnthropicProvider / GeminiProvider 按此实现即可接入。

    现有 OpenAI SDK 实现（provider.py 中的 stream_chat 等函数）已隐式满足此协议，
    无需改动即可通过 AgentLoop 调用。
    """

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_delta: int | None = None,
    ) -> AsyncIterator[dict]:
        """流式调用：产出 text / reasoning / tool_calls / usage 事件。"""
        ...

    async def estimate_tokens(self, text: str) -> int:
        """估算 token 数。"""
        ...

    async def summarize_messages(self, messages: list[dict]) -> str:
        """压缩旧对话。"""
        ...

    async def test_connection(self) -> tuple[bool, str]:
        """最小连通性测试，返回 (是否成功, 描述)."""
        ...
