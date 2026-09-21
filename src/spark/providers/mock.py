from __future__ import annotations

from typing import AsyncIterator

from spark.models import ChatDelta, ChatMessage, ToolCall


class MockProvider:
    """Scripted provider for demos and tests. Performs no HTTP."""

    def __init__(
        self,
        script: list[ChatDelta] | None = None,
        rounds: list[list[ChatDelta]] | None = None,
    ) -> None:
        self.script = script
        self.rounds = list(rounds) if rounds is not None else None
        self.calls = 0

    async def stream(self, messages: list[ChatMessage], tools: list[dict]) -> AsyncIterator[ChatDelta]:
        self.calls += 1
        if self.rounds is not None:
            if self.rounds:
                batch = self.rounds.pop(0)
            else:
                batch = [ChatDelta(type="text", text="Done."), ChatDelta(type="end")]
            for delta in batch:
                yield delta
            return
        if self.script is not None:
            for delta in self.script:
                yield delta
            return
        last = messages[-1] if messages else None
        if last and last.role == "tool":
            yield ChatDelta(type="text", text="Done. Tool results applied.")
            yield ChatDelta(type="end")
            return
        user_text = ""
        for msg in reversed(messages):
            if msg.role == "user" and msg.content and not msg.content.startswith("<project_instructions"):
                user_text = msg.content
                break
        lowered = user_text.lower()
        if "list" in lowered:
            yield ChatDelta(
                type="tool_call",
                tool_call=ToolCall(id="call_list", name="list_dir", arguments={"path": "."}),
            )
            yield ChatDelta(type="end")
            return
        yield ChatDelta(type="text", text="I am Spark (mock provider). Ask me to list files or edit code.")
        yield ChatDelta(type="end")
