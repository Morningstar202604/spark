"""Conversation compaction backed by LlamaIndex ChatSummaryMemoryBuffer.

Replaces the hand-rolled summarisation previously implemented in
``AgentLoop._summarize`` with LlamaIndex's built-in summarisation buffer,
while preserving:

* ``ChatMessage(role="summary")`` message format
* ``compact_from`` cursor semantics on the session row
* Token-threshold trigger logic in ``AgentLoop._maybe_compact``
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import litellm

from llama_index.core.base.llms.types import (
    ChatMessage as LlamaChatMessage,
    ChatResponse,
    CompletionResponse,
    LLMMetadata,
    MessageRole,
)
from llama_index.core.llms import LLM
from llama_index.core.memory import ChatSummaryMemoryBuffer

from spark.models import ChatMessage as SparkChatMessage

# Spark's "summary" role is not part of LlamaIndex's ``MessageRole`` enum,
# so map it to ``system`` when handing history off to the buffer (the
# summary content itself is preserved verbatim).
_ROLE_TO_LLAMA: dict[str, MessageRole] = {
    "system": MessageRole.SYSTEM,
    "user": MessageRole.USER,
    "assistant": MessageRole.ASSISTANT,
    "tool": MessageRole.TOOL,
    "summary": MessageRole.SYSTEM,
}


def _spark_to_llama(msg: SparkChatMessage) -> LlamaChatMessage:
    """Convert a Spark ``ChatMessage`` into a LlamaIndex ``ChatMessage``.

    Tool-call metadata is preserved in ``additional_kwargs`` because the
    LlamaIndex summariser reads it from there when building the prompt.
    """
    role = _ROLE_TO_LLAMA.get(msg.role, MessageRole.USER)
    kwargs: dict[str, Any] = {}
    if msg.tool_calls:
        kwargs["tool_calls"] = [c.model_dump() for c in msg.tool_calls]
    if msg.tool_call_id:
        kwargs["tool_call_id"] = msg.tool_call_id
    if msg.name:
        kwargs["name"] = msg.name
    return LlamaChatMessage(role=role, content=msg.content, **kwargs)


def _llama_content(msg: LlamaChatMessage) -> str:
    """Extract the text content from a LlamaIndex ``ChatMessage``."""
    if msg.content is not None:
        return str(msg.content)
    parts: list[str] = []
    for block in (msg.blocks or []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Structured summary prompt (opencode 5-segment format)
# ---------------------------------------------------------------------------

STRUCTURED_SUMMARY_PROMPT = """You are a conversation summarizer. Given the conversation above, produce a structured summary in EXACTLY the following 5-section Markdown format. Be concise but complete. Do not include any other text outside these sections.

## Objective
What is the user trying to achieve? List each distinct goal.

## Important Details
Key facts, requirements, constraints, file paths, function names, error messages, or decisions made.

## Work State
- Completed: what has been done
- Active: what is currently being worked on
- Blocked: any blockers or unresolved issues

## Next Move
The single most important next action to take.

## Relevant Files
List every file path that was created, modified, or is otherwise relevant. Use `- path/to/file` format. If none, write (none)."""


class LlmAdapter(LLM):
    """Adapt Spark's async streaming ``Provider`` to LlamaIndex's sync ``LLM``.

    The provider returns ``ChatDelta`` events via an async iterator; we collect
    the text deltas and hand back a ``ChatResponse`` / ``CompletionResponse``.
    """

    def __init__(
        self,
        provider: Any,
        model_name: str = "mock",
        context_window: int = 32768,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._provider = provider
        self._model_name = model_name
        self._context_window = context_window

    # ------------------------------------------------------------------ metadata
    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            context_window=self._context_window,
            num_output=4096,
            is_chat_model=True,
            is_function_calling_model=False,
            model_name=self._model_name,
        )

    # ---------------------------------------------------------- stream collection
    async def _acollect_text(
        self,
        spark_messages: list[SparkChatMessage],
        tools: list[dict],
    ) -> str:
        parts: list[str] = []
        async for delta in self._provider.stream(spark_messages, tools):
            if delta.type == "text" and delta.text:
                parts.append(delta.text)
        return "".join(parts)

    def _collect_text_sync(
        self,
        spark_messages: list[SparkChatMessage],
        tools: list[dict],
    ) -> str:
        """Run the async collect safely whether or not a loop is already running."""
        import asyncio

        coro = self._acollect_text(spark_messages, tools)

        try:
            asyncio.get_running_loop()
            loop_running = True
        except RuntimeError:
            loop_running = False

        if loop_running:
            # Already inside an async context — run in a dedicated thread so
            # ``asyncio.run`` does not raise "loop already running".
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(coro)).result(timeout=120)
        return asyncio.run(coro)

    @staticmethod
    def _to_spark_messages(llama_messages: list[LlamaChatMessage]) -> list[SparkChatMessage]:
        """Convert a list of LlamaIndex messages back to Spark messages."""
        out: list[SparkChatMessage] = []
        for m in llama_messages:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            if role == "developer":
                role = "system"
            out.append(SparkChatMessage(role=role, content=_llama_content(m)))
        return out

    # ----------------------------------------------------------------- sync API
    def chat(self, messages: list[LlamaChatMessage], **kwargs: Any) -> ChatResponse:
        spark_msgs = self._to_spark_messages(messages)
        text = self._collect_text_sync(spark_msgs, [])
        return ChatResponse(message=LlamaChatMessage(role=MessageRole.ASSISTANT, content=text))

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        spark_msgs = [SparkChatMessage(role="user", content=prompt)]
        text = self._collect_text_sync(spark_msgs, [])
        return CompletionResponse(text=text)

    def stream_chat(self, messages: list[LlamaChatMessage], **kwargs: Any):
        spark_msgs = self._to_spark_messages(messages)
        text = self._collect_text_sync(spark_msgs, [])
        yield ChatResponse(
            message=LlamaChatMessage(role=MessageRole.ASSISTANT, content=text),
            delta=text,
        )

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        spark_msgs = [SparkChatMessage(role="user", content=prompt)]
        text = self._collect_text_sync(spark_msgs, [])
        yield CompletionResponse(text=text, delta=text)

    # ----------------------------------------------------------------- async API
    async def achat(self, messages: list[LlamaChatMessage], **kwargs: Any) -> ChatResponse:
        spark_msgs = self._to_spark_messages(messages)
        text = await self._acollect_text(spark_msgs, [])
        return ChatResponse(message=LlamaChatMessage(role=MessageRole.ASSISTANT, content=text))

    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        spark_msgs = [SparkChatMessage(role="user", content=prompt)]
        text = await self._acollect_text(spark_msgs, [])
        return CompletionResponse(text=text)

    async def astream_chat(self, messages: list[LlamaChatMessage], **kwargs: Any):
        spark_msgs = self._to_spark_messages(messages)
        text_parts: list[str] = []
        async for delta in self._provider.stream(spark_msgs, []):
            if delta.type == "text" and delta.text:
                text_parts.append(delta.text)
                partial = "".join(text_parts)
                yield ChatResponse(
                    message=LlamaChatMessage(role=MessageRole.ASSISTANT, content=partial),
                    delta=delta.text,
                )

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        spark_msgs = [SparkChatMessage(role="user", content=prompt)]
        async for delta in self._provider.stream(spark_msgs, []):
            if delta.type == "text" and delta.text:
                yield CompletionResponse(text=delta.text, delta=delta.text)


class Compactor:
    """Wraps LlamaIndex ``ChatSummaryMemoryBuffer`` for Spark compaction.

    Summarisation is fully delegated to LlamaIndex; this class handles only
    format translation between Spark / LlamaIndex message types and the
    thin glue needed to wire the adapter into the buffer.

    Supports structured 5-segment summaries (opencode format) and
    prior-summary merging for multi-round compaction contexts.
    """

    def __init__(self, provider: Any, cfg: Any) -> None:
        self._cfg = cfg
        model_name = getattr(cfg.provider, "model", "mock")
        token_limit = cfg.context.max_context_tokens
        self._adapter = LlmAdapter(
            provider=provider,
            model_name=model_name,
            context_window=token_limit,
        )

    # ------------------------------------------------------------------ summarise
    async def summarize(self, messages: list[SparkChatMessage]) -> str | None:
        """Use ``ChatSummaryMemoryBuffer`` to summarise a list of messages.

        Returns a structured 5-segment summary (opencode format) or ``None``
        if summarisation failed or there was nothing meaningful to summarise.

        If the input contains any ``role="summary"`` messages (prior summaries),
        they are extracted, the remaining messages are summarised into a new
        structured summary, and then ``merge_summaries`` is called to combine
        the prior summary with the new one.
        """
        if not messages:
            return None
        if all(m.role == "summary" for m in messages):
            return None

        # ── Extract prior summaries if present ──────────────────────────
        prior_summary = None
        non_summary_messages: list[SparkChatMessage] = []
        for m in messages:
            if m.role == "summary":
                # Strip XML tags if present
                content = (m.content or "").strip()
                if content.startswith("<conversation_summary>") and content.endswith("</conversation_summary>"):
                    content = content[len("<conversation_summary>"):-len("</conversation_summary>")].strip()
                prior_summary = content
            else:
                non_summary_messages.append(m)

        # If everything was summaries, nothing new to do
        if not non_summary_messages:
            return prior_summary

        # ── Build the structured prompt conversation ─────────────────────
        # Inject the structured prompt as a system message so the LLM
        # knows exactly what format to emit, then feed the transcript.
        transcript_lines: list[str] = []
        for m in non_summary_messages:
            role_label = m.role.upper()
            transcript_lines.append(f"[{role_label}] {m.content or ''}")
        transcript_text = "\n".join(transcript_lines)

        # ── Call litellm directly for the structured summary ────────────
        structured_summary = await self._call_litellm_structured(transcript_text)

        # ── Merge if there was a prior summary ───────────────────────────
        if prior_summary:
            return await self.merge_summaries(prior_summary, structured_summary)

        return structured_summary

    # --------------------------------------------------------- merge summaries
    async def merge_summaries(self, prior_summary: str, new_transcript: str) -> str:
        """Merge a previous (structured) summary with a new structured summary.

        Merge rules:
        * Preserve prior Objectives/Important Details/Work State/Next Move/Relevant Files
        * When new content conflicts with prior, the new content takes precedence
        * Work that appeared in ``Active`` but is now marked ``Completed`` is moved
          to the Completed list
        * ``Next Move`` is updated to the latest step
        * ``Relevant Files`` accumulates all previously and newly mentioned files

        Returns a single structured 5-section Markdown string.
        """
        model_name = getattr(self._cfg.provider, "model", "mock")
        base_url = getattr(self._cfg.provider, "base_url", None)
        api_key = getattr(self._cfg.provider, "api_key", None)

        merge_prompt = (
            "You are a conversation summarizer. You have an EXISTING structured summary "
            "and a NEW structured summary (both in the opencode 5-section format). "
            "Merge them into a single summary following these rules:\n\n"
            "1. Keep objectives from both; if they conflict, prefer the new one.\n"
            "2. Merge important details; keep all non-conflicting information.\n"
            "3. Work State: move items from Active→Completed when the new summary "
            "   marks them done. Active should reflect only current in-progress work.\n"
            "4. Next Move: use the latest (new) next move.\n"
            "5. Relevant Files: union of all files mentioned in both summaries.\n\n"
            "Output ONLY the merged summary in this exact 5-section format — no preamble:\n\n"
            + STRUCTURED_SUMMARY_PROMPT
            + "\n\n---\n\n## EXISTING SUMMARY\n\n"
            + prior_summary
            + "\n\n---\n\n## NEW SUMMARY\n\n"
            + new_transcript
        )

        try:
            response = await litellm.acompletion(
                model=model_name,
                messages=[{"role": "user", "content": merge_prompt}],
                api_base=base_url or None,
                api_key=api_key or None,
                timeout=120,
                num_retries=2,
                drop_params=True,
            )
            merged = response.choices[0].message.content  # type: ignore[attr-defined]
            if merged:
                return merged.strip()
        except Exception:
            # On any failure, fall back to concatenating
            pass

        # Fallback: return prior + new concatenated in a structured wrapper
        return (
            "## Objective\n"
            + prior_summary
            + "\n\n"
            + new_transcript
        )

    # ------------------------------------------------ direct structured LLM call
    async def _call_litellm_structured(self, transcript_text: str) -> str:
        """Call litellm with the structured summary prompt and return the result.

        On failure, falls back to the original ChatSummaryMemoryBuffer approach
        so compaction still works even if the provider doesn't support the
        direct litellm path.
        """
        model_name = getattr(self._cfg.provider, "model", "mock")
        base_url = getattr(self._cfg.provider, "base_url", None)
        api_key = getattr(self._cfg.provider, "api_key", None)

        prompt_text = STRUCTURED_SUMMARY_PROMPT + "\n\n---\n\n## CONVERSATION TRANSCRIPT\n\n" + transcript_text

        try:
            response = await litellm.acompletion(
                model=model_name,
                messages=[{"role": "user", "content": prompt_text}],
                api_base=base_url or None,
                api_key=api_key or None,
                timeout=120,
                num_retries=2,
                drop_params=True,
            )
            content = response.choices[0].message.content  # type: ignore[attr-defined]
            if content:
                return content.strip()
        except Exception:
            # litellm path failed — fall back to original buffer-based summarisation
            pass

        # ── Fallback: original ChatSummaryMemoryBuffer approach ───────────
        llama_messages = [_spark_to_llama(m) for m in [SparkChatMessage(role="user", content=transcript_text)]]

        buffer = ChatSummaryMemoryBuffer(
            llm=self._adapter,
            token_limit=1,  # force everything into the summarised bucket
        )
        buffer.set(llama_messages)

        result = buffer.get()

        if result and result[0].role in (MessageRole.SYSTEM, MessageRole.DEVELOPER, MessageRole.USER):
            content = _llama_content(result[0])
            return content.strip() or transcript_text[:500]

        combined = "\n".join(_llama_content(m) for m in result)
        return combined.strip() or transcript_text[:500]

    # ---------------------------------------------------------- budget trimming
    @staticmethod
    def trim_recent(
        recent: list[SparkChatMessage],
        budget_chars: int,
    ) -> list[SparkChatMessage]:
        """Token-aware budget trimming — replaces the char-only logic in ``_trim_recent_chars``.

        Falls back to the original char-based trimming so that callers without
        a full Compactor instance still get deterministic results.
        """
        kept: list[SparkChatMessage] = []
        used = 0
        for msg in reversed(recent):
            size = len(msg.content or "") + sum(
                len(str(c.arguments)) for c in (msg.tool_calls or [])
            )
            if used + size > budget_chars and kept:
                break
            kept.append(msg)
            used += size
        kept.reverse()
        return kept
