from __future__ import annotations

from typing import AsyncIterator, Protocol

from spark.models import ChatDelta, ChatMessage


class Provider(Protocol):
    def stream(
        self, messages: list[ChatMessage], tools: list[dict]
    ) -> AsyncIterator[ChatDelta]: ...
