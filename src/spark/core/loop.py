from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import deque
from collections.abc import Awaitable, Callable
from importlib.resources import files
from pathlib import Path
from stat import S_ISREG

from spark.config import SparkConfig
from spark.core.compact import Compactor
from spark.core.graph import build_react_graph
from spark.core.context import build_messages, load_agents_md, load_system_prompt
from spark.core.tokens import estimate_message_tokens, estimate_tool_overhead
from spark.hooks import HookRegistry
from spark.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChatMessage,
    ToolCall,
    ToolResult,
    TurnEvent,
)
from spark.policy import NEVER_PERSIST_ALLOW, call_signature, decide
from spark.providers.base import Provider
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry

Approver = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


class AgentLoop:
    def __init__(
        self,
        *,
        workdir: Path,
        cfg: SparkConfig,
        provider: Provider,
        registry: ToolRegistry,
        store: SessionStore,
        session_id: str,
        approver: Approver | None = None,
        memory=None,
    ) -> None:
        self.workdir = workdir
        self.cfg = cfg
        self.provider = provider
        self.registry = registry
        self.store = store
        self.session_id = session_id
        self.approver = approver
        self.memory = memory
        self.allow_always: set[str] = set()
        self.cancelled = False
        self.registry.ctx.task_runner = self.spawn_subtask
        self.registry.ctx.task_runner_parallel = self.spawn_subtasks_parallel
        self.history: list[ChatMessage] = store.load_messages(session_id)
        self._history_source = self.history
        self._history_tokens = self._calculate_history_tokens(self.history)
        self._history_length = len(self.history)
        self._history_tail = self.history[-1] if self.history else None
        self._system_prompt_resource = files("spark.prompts").joinpath("system.md")
        self._system_prompt_cache_key: tuple | None = None
        self._system_prompt_tokens = 0
        self._agents_cache_key: tuple = ()
        self._agents_tokens = 0
        self._tool_schema_revision = -1
        self._tool_overhead_tokens = 0
        self._call_window: deque[str] = deque(maxlen=self._breaker_window())
        self._call_counts: dict[str, int] = {}
        self._turn_tokens = 0
        self._active_children: list[AgentLoop] = []
        self.hooks = HookRegistry.from_config(
            [hook.model_dump() for hook in getattr(cfg, "hooks", [])], workdir
        )
        self._compactor = Compactor(provider=provider, cfg=cfg)
        # ── LangGraph orchestration (opt-in) ──────────────────────────────
        # When enabled, the core ReAct cycle (LLM → tools → repeat) is run
        # as a LangGraph StateGraph via ``_iter_turn_graph``.
        self._use_graph: bool = getattr(
            cfg.agent, "use_langgraph", False
        ) or os.environ.get("SPARK_USE_LANGGRAPH", "").lower() in {"1", "true", "yes"}
        # --- lifecycle hooks (best-effort) ---
        self._notify_safe("app_start", {"workdir": str(workdir)})
        if len(self.history) == 0:
            self._notify_safe("session_create", {"session_id": session_id})
        else:
            self._notify_safe("session_load", {"session_id": session_id})

    def _notify_safe(self, event: str, payload: dict) -> None:
        """Fire hook event; swallows all errors to protect the main loop."""
        try:
            self._hooks.notify(event, payload)
        except Exception:
            pass  # hooks are best-effort

    def cancel(self) -> None:
        self.cancelled = True
        for child in list(self._active_children):
            child.cancel()

    def _breaker_window(self) -> int:
        """Window must hold at least max_repeat_calls+1 entries, or the breaker
        can never observe a repeat when the limit exceeds the window size."""
        limit = int(getattr(self.cfg.agent, "max_repeat_calls", 0) or 0)
        return max(32, limit + 1)

    def _fingerprint(self, call: ToolCall) -> str:
        payload = json.dumps(
            {"name": call.name, "arguments": call.arguments},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def _record_call(self, call: ToolCall) -> tuple[str, int] | None:
        """Track a tool-call signature; return the over-limit signature when the
        same (name, arguments) pair repeats past max_repeat_calls. 0 disables the guard."""
        limit = getattr(self.cfg.agent, "max_repeat_calls", 0) or 0
        if limit <= 0:
            return None
        fingerprint = self._fingerprint(call)
        if len(self._call_window) == self._call_window.maxlen:
            required = self._breaker_window()
            if required != self._call_window.maxlen:
                self._call_window = deque(self._call_window, maxlen=required)
        self._call_window.append(fingerprint)
        count = sum(1 for item in self._call_window if item == fingerprint)
        self._call_counts[fingerprint] = count
        if count > limit:
            return fingerprint, count
        return None

    def _charge_tokens(self, text_parts: list[str], reasoning_parts: list[str]) -> int:
        added = estimate_message_tokens(
            ChatMessage(role="assistant", content="".join(text_parts) or None)
        ) + estimate_message_tokens(
            ChatMessage(role="assistant", content="".join(reasoning_parts) or None)
        )
        self._turn_tokens += added
        return added

    def _budget_exceeded(self) -> bool:
        limit = getattr(self.cfg.agent, "max_turn_tokens", 0) or 0
        return limit > 0 and self._turn_tokens >= limit

    def _budget_state(self) -> dict:
        limit = getattr(self.cfg.agent, "max_turn_tokens", 0) or 0
        return {
            "turn_tokens": self._turn_tokens,
            "turn_token_limit": limit or None,
        }

    async def run(self, user_text: str, images=None) -> list[TurnEvent]:
        self.cancelled = False
        events: list[TurnEvent] = []
        async for event in self.iter_turn(user_text, images=images):
            events.append(event)
        return events

    def iter_turn_sync(self, user_text: str, images=None):
        self.cancelled = False
        agen = self.iter_turn(user_text, images=images)
        loop = asyncio.new_event_loop()
        try:
            while True:
                try:
                    yield loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
        finally:
            loop.run_until_complete(agen.aclose())
            loop.close()

    @staticmethod
    def _calculate_history_tokens(history: list[ChatMessage]) -> int:
        return sum(
            estimate_message_tokens(message)
            for message in history
            if message.role != "system"
        )

    def _set_history_cache(self) -> None:
        self._history_source = self.history
        self._history_tokens = self._calculate_history_tokens(self.history)
        self._history_length = len(self.history)
        self._history_tail = self.history[-1] if self.history else None

    def _sync_history_tokens(self) -> None:
        tail = self.history[-1] if self.history else None
        if (
            self.history is self._history_source
            and len(self.history) == self._history_length
            and tail is self._history_tail
        ):
            return
        self._set_history_cache()

    def _replace_history(self, history: list[ChatMessage]) -> None:
        self.history = history
        self._set_history_cache()

    def _append_history(self, message: ChatMessage) -> None:
        self._sync_history_tokens()
        self.history.append(message)
        if message.role != "system":
            self._history_tokens += estimate_message_tokens(message)
        self._history_length = len(self.history)
        self._history_tail = message

    def _system_prompt_file_key(self) -> tuple | None:
        try:
            stat_result = self._system_prompt_resource.stat()
        except (OSError, NotImplementedError):
            return None
        return (
            "ok",
            stat_result.st_mtime_ns,
            stat_result.st_size,
            getattr(stat_result, "st_ino", 0),
        )

    def _agents_file_key(self) -> tuple:
        name = self.cfg.context.agents_md
        base = self.workdir.resolve()
        prefix = (name, self.cfg.context.max_fragment_chars, str(base))
        if (
            not name
            or any(separator in name for separator in ("/", "\\", ":"))
            or ".." in name
        ):
            return prefix + ("invalid",)
        try:
            resolved = (base / name).resolve()
            stat_result = resolved.stat()
        except OSError as exc:
            return prefix + ("error", type(exc).__name__)
        if not resolved.is_relative_to(base) or not S_ISREG(stat_result.st_mode):
            return prefix + ("not_file", str(resolved))
        return prefix + (
            "ok",
            str(resolved),
            stat_result.st_mtime_ns,
            stat_result.st_size,
            getattr(stat_result, "st_ino", 0),
        )

    def _prompt_tokens(self) -> int:
        system_key = self._system_prompt_file_key()
        if system_key is None or system_key != self._system_prompt_cache_key:
            system = load_system_prompt() + f"\n\nWorkdir: {self.workdir.resolve()}"
            self._system_prompt_tokens = estimate_message_tokens(
                ChatMessage(role="system", content=system)
            )
            self._system_prompt_cache_key = system_key
        agents_key = self._agents_file_key()
        if agents_key != self._agents_cache_key:
            agents = load_agents_md(self.workdir, self.cfg)
            self._agents_tokens = (
                estimate_message_tokens(ChatMessage(role="user", content=agents))
                if agents
                else 0
            )
            self._agents_cache_key = agents_key
        return self._system_prompt_tokens + self._agents_tokens + 512

    def _usage(self) -> dict:
        self._sync_history_tokens()
        limit = self.cfg.context.max_context_tokens
        total = self._prompt_tokens() + self._history_tokens + self._tool_overhead()
        return {
            "used": total,
            "limit": limit,
            "percent": round(total * 100 / max(1, limit), 1),
        }

    def _tool_overhead(self) -> int:
        revision = self.registry.schema_revision
        if revision != self._tool_schema_revision:
            self._tool_overhead_tokens = estimate_tool_overhead(self.registry.schemas())
            self._tool_schema_revision = revision
        return self._tool_overhead_tokens

    async def _maybe_compact(self):
        """Auto-compact: when usage crosses threshold, summarize old turns into one summary message."""
        usage = self._usage()
        limit = self.cfg.context.max_context_tokens
        threshold = self.cfg.context.compact_threshold
        keep = max(2, self.cfg.context.keep_recent_messages)
        if usage["used"] <= limit * threshold or len(self.history) <= keep:
            return None
        boundary = len(self.history) - keep
        if boundary <= 0:
            return None
        found = None
        for i in range(boundary, len(self.history)):
            if self.history[i].role == "user":
                found = i
                break
        if found is not None:
            boundary = found
        if boundary >= len(self.history) or boundary <= 0:
            return None
        old_part = self.history[:boundary]
        if all(m.role == "summary" for m in old_part):
            return None
        summary_text = await self._compactor.summarize(old_part)
        if not summary_text:
            return None
        summary_msg = ChatMessage(
            role="summary",
            content=f"<conversation_summary>\n{summary_text}\n</conversation_summary>",
        )
        summary_id = self.store.append_message(self.session_id, summary_msg)
        self.store.set_compact_from(self.session_id, summary_id)
        before_tokens = usage["used"]
        self._replace_history(self.store.load_messages(self.session_id))
        after_usage = self._usage()
        self._notify_safe(
            "compaction",
            {
                "message_count": len(old_part),
                "before_tokens": before_tokens,
                "after_tokens": after_usage["used"],
                "limit": limit,
            },
        )
        return {
            "before_tokens": before_tokens,
            "after_tokens": after_usage["used"],
            "limit": limit,
            "summarized_messages": len(old_part),
            "usage": after_usage,
        }

    async def iter_turn(self, user_text: str, images=None):
        self._notify_safe(
            "turn_start",
            {"session_id": self.session_id, "message": user_text},
        )

        # ── LangGraph opt-in path ─────────────────────────────────────────
        # When ``_use_graph`` is True (cfg flag or env var), delegate the
        # core ReAct cycle to a LangGraph StateGraph.  The original while-loop
        # below remains the default and is only turned off explicitly.
        if self._use_graph:
            async for event in self._iter_turn_graph(user_text, images=images):
                yield event
            return

        compacted = await self._maybe_compact()
        if compacted:
            yield TurnEvent(type="compaction", data=compacted)
        usage = self._usage()
        yield TurnEvent(type="context", data={"usage": usage})
        memory_block = None
        if self.memory is not None:
            try:
                memory_block = self.memory.retrieve_context(user_text)
                self._notify_safe(
                    "memory_extract",
                    {"action": "retrieve", "query": user_text},
                )
            except Exception:
                memory_block = None
        user = ChatMessage(role="user", content=user_text, images=images)
        self._append_history(user)
        self.store.append_message(self.session_id, user)
        if (
            self.store.get_session(self.session_id)
            and self.store.get_session(self.session_id).get("title") == "untitled"
        ):
            self.store.touch(self.session_id, title=user_text[:80])

        rounds = 0
        self._turn_tokens = 0
        self._call_window = deque(maxlen=self._breaker_window())
        self._call_counts.clear()
        while True:
            if self.cancelled:
                yield TurnEvent(type="turn_error", text="Turn cancelled")
                return
            if rounds >= self.cfg.agent.max_tool_rounds:
                yield TurnEvent(
                    type="turn_error",
                    text="Reached max_tool_rounds",
                    data=self._budget_state(),
                )
                return
            if self._budget_exceeded():
                yield TurnEvent(
                    type="turn_error",
                    text=(
                        "Token budget exceeded for this turn "
                        f"({self._turn_tokens}/{self.cfg.agent.max_turn_tokens})"
                    ),
                    data=self._budget_state(),
                )
                return
            messages = build_messages(
                workdir=self.workdir,
                cfg=self.cfg,
                history=self.history,
                memory_block=memory_block,
                _compactor=self._compactor,
            )
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            try:
                async for delta in self.provider.stream(
                    messages, self.registry.schemas()
                ):
                    if self.cancelled:
                        yield TurnEvent(type="turn_error", text="Turn cancelled")
                        return
                    if delta.type == "reasoning" and delta.text:
                        reasoning_parts.append(delta.text)
                        yield TurnEvent(type="reasoning_delta", text=delta.text)
                    elif delta.type == "text" and delta.text:
                        text_parts.append(delta.text)
                        yield TurnEvent(type="text_delta", text=delta.text)
                    elif delta.type == "tool_call" and delta.tool_call:
                        tool_calls.append(delta.tool_call)
            except Exception as exc:
                yield TurnEvent(type="turn_error", text=str(exc))
                return

            self._charge_tokens(text_parts, reasoning_parts)

            if tool_calls:
                tripped = None
                for call in tool_calls:
                    guard = self._record_call(call)
                    if guard is not None:
                        tripped = (call.name, guard[1])
                        break
                if tripped is not None:
                    name, count = tripped
                    yield TurnEvent(
                        type="context",
                        data={
                            "usage": self._usage(),
                            "circuit_breaker": {
                                "tool": name,
                                "repeats": count,
                                "limit": self.cfg.agent.max_repeat_calls,
                            },
                            **self._budget_state(),
                        },
                    )
                    yield TurnEvent(
                        type="turn_error",
                        text=(
                            f"Circuit breaker: tool '{name}' repeated with identical "
                            f"arguments {count} times (limit "
                            f"{self.cfg.agent.max_repeat_calls})"
                        ),
                        data=self._budget_state(),
                    )
                    return
                assistant = ChatMessage(
                    role="assistant",
                    content="".join(text_parts) or None,
                    tool_calls=tool_calls,
                )
                self._append_history(assistant)
                msg_id = self.store.append_message(self.session_id, assistant)
                for call in tool_calls:
                    async for event in self._run_tool(call, msg_id):
                        yield event
                        if event.type == "turn_error":
                            return
                    if call.name == "update_plan" and self.registry.plan:
                        yield TurnEvent(type="plan", data={"steps": self.registry.plan})
                rounds += 1
                continue

            assistant = ChatMessage(role="assistant", content="".join(text_parts))
            self._append_history(assistant)
            assistant_id = self.store.append_message(self.session_id, assistant)
            self._maybe_auto_checkpoint(assistant_id)
            yield TurnEvent(
                type="context",
                data={"usage": self._usage(), **self._budget_state()},
            )
            if self._budget_exceeded():
                yield TurnEvent(
                    type="turn_error",
                    text=(
                        "Token budget exceeded for this turn "
                        f"({self._turn_tokens}/{self.cfg.agent.max_turn_tokens})"
                    ),
                    data=self._budget_state(),
                )
                return
            yield TurnEvent(type="turn_end", text=assistant.content)
            return

    def _maybe_auto_checkpoint(self, assistant_id: int) -> None:
        """Snapshot the workdir + conversation after turns that touched files or ran shell."""
        try:
            from spark.core.checkpoints import make_checkpoint_record

            recent_tools = [m.name for m in self.history[-12:] if m.role == "tool"]
            if not any(
                name in {"write_file", "apply_patch", "run_shell", "notebook_edit"}
                for name in recent_tools
            ):
                return
            snapshot_id, files_hash = make_checkpoint_record(self.workdir)
            last_user = next(
                (m for m in reversed(self.history) if m.role == "user"), None
            )
            label = (
                (last_user.content or "")[:60].strip() if last_user else "checkpoint"
            )
            self.store.add_checkpoint(
                self.session_id,
                label,
                assistant_id,
                json.dumps(
                    {
                        "snapshot_id": snapshot_id,
                        "files_hash": files_hash,
                        "message_id": assistant_id,
                    }
                ),
            )
        except Exception:
            pass

    def rollback_to_checkpoint(self, checkpoint_id: int) -> dict:
        """Restore files and conversation history to a stored checkpoint."""
        from spark.core.checkpoints import restore_workdir

        record = self.store.get_checkpoint(self.session_id, checkpoint_id)
        if record is None:
            raise ValueError(f"checkpoint not found: {checkpoint_id}")
        meta = json.loads(record["snapshot_json"])
        files = restore_workdir(self.workdir, str(meta.get("snapshot_id") or ""))
        removed = self.store.delete_messages_after(
            self.session_id, int(record["message_id"])
        )
        try:
            cid = int(record["message_id"])
            if self.store.get_compact_from(self.session_id) > cid:
                self.store.set_compact_from(self.session_id, 0)
        except Exception:
            pass
        self._replace_history(self.store.load_messages(self.session_id))
        return {
            "checkpoint_id": checkpoint_id,
            "label": record["label"],
            "files": files,
            "removed_messages": removed,
        }

    async def _run_tool(self, call: ToolCall, message_id: int):
        yield TurnEvent(type="tool_start", tool_call=call)
        if self.hooks.enabled:
            verdict = self.hooks.notify(
                "pre_tool",
                {"tool": call.name, "arguments": call.arguments},
            )
            if not verdict.allow:
                result = ToolResult(ok=False, payload={"error": verdict.reason})
                await self._persist_tool(call, message_id, result, "hook_block")
                yield TurnEvent(type="tool_end", tool_call=call, result=result)
                return
        rules = self.cfg.permission_rules or None
        decision = decide(
            self.cfg.agent.approval,
            call,
            allow_always=self.allow_always,
            readonly_mcp=self.registry.readonly_mcp,
            rules=rules,
        )
        approval_label = "allow"
        if decision == "prompt":
            summary, diff = self.registry.approval_summary(call)
            request = ApprovalRequest(tool_call=call, summary=summary, diff=diff)
            self._notify_safe(
                "approval_needed",
                {
                    "tool_call": call.name,
                    "arguments": call.arguments,
                    "summary": summary,
                },
            )
            yield TurnEvent(type="approval_needed", approval=request, tool_call=call)
            if self.approver is None:
                result = ToolResult(
                    ok=False, payload={"error": "denied", "reason": "no approver"}
                )
                await self._persist_tool(call, message_id, result, "deny")
                yield TurnEvent(type="tool_end", tool_call=call, result=result)
                return
            verdict = await self.approver(request)
            if verdict.action == "deny":
                result = ToolResult(ok=False, payload={"error": "denied"})
                await self._persist_tool(call, message_id, result, "deny")
                yield TurnEvent(type="tool_end", tool_call=call, result=result)
                return
            if (
                verdict.action == "allow_always"
                and call.name not in NEVER_PERSIST_ALLOW
            ):
                self.allow_always.add(call_signature(call))
            approval_label = verdict.action
        if call.name == "task" and self.registry.ctx.task_runner is not None:
            prompts: list[str] = []
            raw_tasks = call.arguments.get("tasks")
            if isinstance(raw_tasks, list):
                prompts = [
                    str(t.get("prompt") or "").strip()
                    for t in raw_tasks
                    if isinstance(t, dict)
                ]
                prompts = [p for p in prompts if p]
            else:
                prompt = str(call.arguments.get("prompt") or "").strip()
                if prompt:
                    prompts = [prompt]
            if not prompts:
                result = ToolResult(ok=False, payload={"error": "prompt required"})
            elif len(prompts) == 1:
                summary_text = ""
                error_text = ""
                async for sub_event in self.registry.ctx.task_runner(prompts[0]):
                    if sub_event.type in {
                        "tool_start",
                        "tool_end",
                        "plan",
                        "approval_needed",
                    }:
                        yield sub_event
                    elif sub_event.type == "turn_end":
                        summary_text = sub_event.text or ""
                    elif sub_event.type == "turn_error":
                        error_text = sub_event.text or ""
                result = (
                    ToolResult(ok=True, payload={"summary": summary_text})
                    if not error_text
                    else ToolResult(ok=False, payload={"error": error_text})
                )
            else:
                if len(prompts) > 4:
                    prompts = prompts[:4]
                summary_parts: list[str] = []
                async for sub_event in self.registry.ctx.task_runner_parallel(prompts):
                    if sub_event.type in {
                        "tool_start",
                        "tool_end",
                        "plan",
                        "approval_needed",
                    }:
                        yield sub_event
                    elif sub_event.type == "turn_end":
                        summary_parts.append(sub_event.text or "")
                result = ToolResult(ok=True, payload={"summaries": summary_parts})
        else:
            result = await self.registry.execute(call)
        if not result.ok:
            self._notify_safe(
                "tool_error",
                {
                    "tool_call": call.name,
                    "arguments": call.arguments,
                    "error": result.payload.get("error", ""),
                },
            )
        await self._persist_tool(call, message_id, result, approval_label)
        yield TurnEvent(type="tool_end", tool_call=call, result=result)

    async def _persist_tool(
        self, call: ToolCall, message_id: int, result: ToolResult, approval: str
    ) -> None:
        tool_msg = ChatMessage(
            role="tool",
            name=call.name,
            tool_call_id=call.id,
            content=json.dumps(result.payload, ensure_ascii=False),
        )
        self._append_history(tool_msg)
        self.store.append_message(self.session_id, tool_msg)
        self.store.append_tool_event(
            self.session_id, message_id, call.name, call.arguments, result, approval
        )

    def _make_sub_context(self) -> ToolContext:
        sub_cfg = self.cfg.model_copy(deep=True)
        sub_cfg.agent.max_tool_rounds = min(15, max(5, sub_cfg.agent.max_tool_rounds))
        sub_ctx = ToolContext(sandbox=self.registry.ctx.sandbox, config=sub_cfg)
        sub_ctx.mcp_call = self.registry.ctx.mcp_call
        return sub_ctx

    def _inherit_registry(self, sub_registry: ToolRegistry) -> None:
        for schema in self.registry.schemas():
            name = str(((schema.get("function") or {}).get("name")) or "")
            if name.startswith("mcp__"):
                sub_registry.add_mcp_schema(schema)
        sub_registry.readonly_mcp |= set(self.registry.readonly_mcp)

    async def spawn_subtask(self, prompt: str):
        """Run a self-contained sub-agent with a fresh context; yield its events, final turn_end carries the summary."""
        from pathlib import Path as _Path

        sub_ctx = self._make_sub_context()
        sub_registry = ToolRegistry(sub_ctx)
        self._inherit_registry(sub_registry)
        sub_cfg = sub_ctx.config
        sub_store = SessionStore(_Path(":memory:"))
        sid = sub_store.create_session(
            self.workdir, sub_cfg.provider.model, title="subtask"
        )
        sub_loop = AgentLoop(
            workdir=self.workdir,
            cfg=sub_cfg,
            provider=self.provider,
            registry=sub_registry,
            store=sub_store,
            session_id=sid,
            approver=self.approver,
            memory=None,
        )
        sub_loop.allow_always = set(self.allow_always)
        self._active_children.append(sub_loop)
        final_text = ""
        try:
            async for event in sub_loop.iter_turn(prompt):
                if event.type in {"tool_start", "tool_end", "plan", "approval_needed"}:
                    yield event
                elif event.type == "turn_end":
                    final_text = event.text or ""
                elif event.type == "turn_error":
                    final_text = f"subtask failed: {event.text}"
                    yield TurnEvent(type="turn_end", text=final_text)
                    return
            yield TurnEvent(type="turn_end", text=final_text)
        finally:
            self._active_children.remove(sub_loop)
            sub_store.close()

    async def spawn_subtasks_parallel(self, prompts: list[str]):
        """Run several sub-agents concurrently; each gets its own context. Yields each sub-agent's
        tool/plan events live, then one combined turn_end carrying all summaries."""
        import asyncio as _asyncio

        if len(prompts) == 1:
            async for event in self.spawn_subtask(prompts[0]):
                yield event
            return

        queue: _asyncio.Queue[tuple[int, TurnEvent | None]] = _asyncio.Queue()
        summaries: dict[int, str] = {}

        async def runner(index: int, prompt: str) -> tuple[int, str]:
            try:
                summary = await self._collect_subtask_events(index, prompt, queue)
                return index, summary
            except Exception as exc:
                await queue.put((index, None))
                return index, f"subtask failed: {exc}"

        tasks = [_asyncio.create_task(runner(i, p)) for i, p in enumerate(prompts)]
        try:
            # Drive the whole fan-out from the queue so a sub-agent that is waiting
            # on approval or a long tool still delivers its events immediately;
            # waiting on task completion alone can deadlock on the approval path.
            remaining = len(tasks)
            while remaining > 0:
                index, event = await queue.get()
                if event is not None:
                    yield event
                    continue
                remaining -= 1
                task = tasks[index] if index < len(tasks) else None
                if task is not None and task.done():
                    try:
                        done_index, summary = task.result()
                        summaries[done_index] = summary
                    except Exception as exc:
                        summaries[index] = f"subtask failed: {exc}"
                elif task is not None:
                    try:
                        done_index, summary = await task
                        summaries[done_index] = summary
                    except Exception as exc:
                        summaries[index] = f"subtask failed: {exc}"
            while not queue.empty():
                index, event = queue.get_nowait()
                if event is not None:
                    yield event
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await _asyncio.gather(*tasks, return_exceptions=True)
        for i in range(len(prompts)):
            label = f"[子任务 {i + 1}]"
            yield TurnEvent(
                type="turn_end", text=f"{label} {summaries.get(i, 'no output')}"
            )

    async def _collect_subtask_events(self, index: int, prompt: str, queue) -> str:
        """Run one subtask; stream its tool events into the queue tagged by index; return summary."""
        from pathlib import Path as _Path

        sub_ctx = self._make_sub_context()
        sub_registry = ToolRegistry(sub_ctx)
        self._inherit_registry(sub_registry)
        sub_cfg = sub_ctx.config
        sub_store = SessionStore(_Path(":memory:"))
        sid = sub_store.create_session(
            self.workdir, sub_cfg.provider.model, title=f"subtask-{index}"
        )
        sub_loop = AgentLoop(
            workdir=self.workdir,
            cfg=sub_cfg,
            provider=self.provider,
            registry=sub_registry,
            store=sub_store,
            session_id=sid,
            approver=self.approver,
            memory=None,
        )
        sub_loop.allow_always = set(self.allow_always)
        self._active_children.append(sub_loop)
        final_text = ""
        try:
            async for event in sub_loop.iter_turn(prompt):
                if event.type in {"tool_start", "tool_end", "plan", "approval_needed"}:
                    await queue.put((index, event))
                elif event.type == "turn_end":
                    final_text = event.text or ""
                elif event.type == "turn_error":
                    final_text = f"subtask failed: {event.text}"
            await queue.put((index, None))
            return final_text
        finally:
            self._active_children.remove(sub_loop)
            sub_store.close()

    # ── LangGraph-integrated ReAct loop ──────────────────────────────────
    # When ``_use_graph`` is True the outer turn-cycle is identical to
    # ``iter_turn`` (compact → user msg → inner loop → auto-checkpoint) but
    # the **inner loop** is delegated to a LangGraph ``StateGraph``.  The
    # graph provides:
    #   • typed intermediate state (ReActState)
    #   • interrupt_before on the tools node for human approval
    #   • optional checkpointer persistence per thread_id (= session_id)
    #   • astream streaming for incremental event emission
    # All helper methods (``_run_tool``, ``_record_call``,
    # ``_budget_exceeded``, ``_charge_tokens``, ``_maybe_compact``,
    # ``_budget_state``) remain shared between the two paths.

    async def _iter_turn_graph(self, user_text: str, images=None) -> AsyncIterator[TurnEvent]:
        """LangGraph-backed variant of the inner ReAct loop.

        Mirrors the flow of ``iter_turn`` but drives each
        *think → act → observe* cycle through LangGraph nodes.  All
        Spark-specific behaviour (token counting, circuit-breaker, approval,
        compaction, auto-checkpoint) is preserved.
        """
        # 1. Pre-loop: compaction, memory, user message — same as iter_turn
        compacted = await self._maybe_compact()
        if compacted:
            yield TurnEvent(type="compaction", data=compacted)
        yield TurnEvent(type="context", data={"usage": self._usage()})
        memory_block = None
        if self.memory is not None:
            try:
                memory_block = self.memory.retrieve_context(user_text)
                self._notify_safe(
                    "memory_extract",
                    {"action": "retrieve", "query": user_text},
                )
            except Exception:
                memory_block = None
        user_msg = ChatMessage(role="user", content=user_text)
        if images:
            user_msg.images = images  # type: ignore[assignment]
        self._append_history(user_msg)
        self.store.append_message(self.session_id, user_msg)
        if not self.store.session_title(self.session_id):
            self.store.set_title(self.session_id, (user_text or "")[:80])

        # 2. Inner ReAct loop via LangGraph
        rounds = 0
        self._turn_tokens = 0
        self._call_window = deque(maxlen=self._breaker_window())
        self._call_counts.clear()

        checkpointer = None
        try:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
        except Exception:
            pass  # run without checkpointer

        graph = build_react_graph(
            provider=self.provider,
            registry=self.registry,
            workdir=self.workdir,
            cfg=self.cfg,
            approver=self.approver,
            compactor=self._compactor,
            store=self.store,
            session_id=self.session_id,
            memory_obj=self.memory,
            checkpointer=checkpointer,
        )

        config = {"configurable": {"thread_id": self.session_id}}
        state: dict = {
            "provider": self.provider,
            "registry": self.registry,
            "workdir": self.workdir,
            "cfg": self.cfg,
            "approver": self.approver,
            "compactor": self._compactor,
            "store": self.store,
            "session_id": self.session_id,
            "memory_obj": self.memory,
            "history": list(self.history),
            "messages": [],
            "tool_calls": [],
            "tool_results": [],
            "text_parts": [],
            "reasoning_parts": [],
            "rounds": 0,
            "turn_tokens": 0,
            "compacted_this_turn": False,
            "allow_always": set(self.allow_always),
        }

        while True:
            if self.cancelled:
                yield TurnEvent(type="turn_error", text="Turn cancelled")
                return
            if rounds >= self.cfg.agent.max_tool_rounds:
                yield TurnEvent(
                    type="turn_error",
                    text="Reached max_tool_rounds",
                    data=self._budget_state(),
                )
                return
            if self._budget_exceeded():
                yield TurnEvent(
                    type="turn_error",
                    text=(
                        "Token budget exceeded for this turn "
                        f"({self._turn_tokens}/{self.cfg.agent.max_turn_tokens})"
                    ),
                    data=self._budget_state(),
                )
                return

            try:
                async for chunk in graph.astream(state, config=config):
                    # LangGraph emits node-level updates; detect LLM delta
                    # patches and re-emit as TurnEvents for compatibility.
                    if "llm" in chunk:
                        llm_out = chunk["llm"]
                        for txt in llm_out.get("text_parts", []):
                            if txt:
                                yield TurnEvent(type="text_delta", text=txt)
                        for rsn in llm_out.get("reasoning_parts", []):
                            if rsn:
                                yield TurnEvent(type="reasoning_delta", text=rsn)
                        for tc in llm_out.get("tool_calls", []):
                            pass  # handled below after node completes
                    elif "tools" in chunk:
                        pass  # tool node finished
            except Exception as exc:
                # LangGraph interrupts for approval surface here as a dict.
                exc_str = str(exc)
                if "interrupt" in exc_str.lower():
                    # Extract approval info from the interrupt payload and
                    # surface it as approval_needed (same as _run_tool path).
                    interrupt_data = getattr(exc, "value", None)
                    if interrupt_data:
                        yield TurnEvent(
                            type="approval_needed",
                            data={"approval": interrupt_data, "tool": None},
                            tool=None,
                        )
                        # Wait for caller to provide Command(resume=…) via
                        # graph input — for now we simply end the turn.
                        return
                yield TurnEvent(type="turn_error", text=exc_str)
                return

            # LLM node may have terminated the stream naturally
            llm_state = {}
            state.update(llm_state)

            # After LLM node completes the graph ends (no more nodes after
            # tools → END).  The outer loop mimics the original `iter_turn`:
            if not state.get("tool_calls"):
                # LLM finished without tool calls → turn complete
                assistant = ChatMessage(
                    role="assistant",
                    content="".join(state.get("text_parts", [])),
                )
                self._append_history(assistant)
                assistant_id = self.store.append_message(self.session_id, assistant)
                self._maybe_auto_checkpoint(assistant_id)
                yield TurnEvent(
                    type="context",
                    data={"usage": self._usage(), **self._budget_state()},
                )
                if self._budget_exceeded():
                    yield TurnEvent(
                        type="turn_error",
                        text=(
                            "Token budget exceeded for this turn "
                            f"({self._turn_tokens}/{self.cfg.agent.max_turn_tokens})"
                        ),
                        data=self._budget_state(),
                    )
                    return
                yield TurnEvent(type="turn_end", text=assistant.content)
                return

            # Circuit breaker
            tool_calls: list = state["tool_calls"]  # type: ignore[assignment]
            tripped = None
            from spark.models import ToolCall as _TC

            for raw in tool_calls:
                call = raw if isinstance(raw, _TC) else _TC(**raw)
                guard = self._record_call(call)
                if guard is not None:
                    tripped = (call.name, guard[1])
                    break
            if tripped is not None:
                name, count = tripped
                yield TurnEvent(
                    type="context",
                    data={
                        "usage": self._usage(),
                        "circuit_breaker": {
                            "tool": name,
                            "repeats": count,
                            "limit": self.cfg.agent.max_repeat_calls,
                        },
                        **self._budget_state(),
                    },
                )
                yield TurnEvent(
                    type="turn_error",
                    text=(
                        f"Circuit breaker: tool '{name}' repeated with identical "
                        f"arguments {count} times (limit "
                        f"{self.cfg.agent.max_repeat_calls})"
                    ),
                    data=self._budget_state(),
                )
                return

            # Persist assistant message + emit tool events (same as iter_turn)
            assistant = ChatMessage(
                role="assistant",
                content="".join(state.get("text_parts", [])) or None,
                tool_calls=[c if isinstance(c, _TC) else _TC(**c) for c in tool_calls],
            )
            self._append_history(assistant)
            msg_id = self.store.append_message(self.session_id, assistant)
            for raw in tool_calls:
                call = raw if isinstance(raw, _TC) else _TC(**c)
                async for event in self._run_tool(call, msg_id):
                    yield event
                    if event.type == "turn_error":
                        return
                if call.name == "update_plan" and self.registry.plan:
                    yield TurnEvent(type="plan", data={"steps": self.registry.plan})

            # Token accounting
            self._turn_tokens += state.get("turn_tokens", 0)
            rounds += 1

            # Refresh history from persistent store for next iteration
            state["history"] = list(self.history)
            state["tool_calls"] = []
            state["text_parts"] = []
            state["reasoning_parts"] = []
            state["turn_tokens"] = 0
            state["messages"] = []

