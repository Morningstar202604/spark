from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path

from spark.config import SparkConfig
from spark.core.context import build_messages, history_token_usage
from spark.core.tokens import estimate_message_tokens
from spark.hooks import HookRegistry
from spark.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChatMessage,
    ToolCall,
    ToolResult,
    TurnEvent,
)
from spark.policy import decide
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
        self._call_window: deque[str] = deque(maxlen=32)
        self._call_counts: dict[str, int] = {}
        self._turn_tokens = 0
        self._active_children: list[AgentLoop] = []
        self.hooks = HookRegistry.from_config(
            [hook.model_dump() for hook in getattr(cfg, "hooks", [])], workdir
        )

    def cancel(self) -> None:
        self.cancelled = True
        for child in list(self._active_children):
            child.cancel()

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

    def _usage(self) -> dict:
        return history_token_usage(
            cfg=self.cfg,
            history=self.history,
            workdir=self.workdir,
            tool_overhead_tokens=self._tool_overhead(),
        )

    def _tool_overhead(self) -> int:
        import json

        from spark.core.tokens import estimate_tokens

        schemas = self.registry.schemas()
        return (
            estimate_tokens(json.dumps(schemas, ensure_ascii=False)) if schemas else 0
        )

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
        summary_text = await self._summarize(old_part)
        if not summary_text:
            return None
        summary_msg = ChatMessage(
            role="summary",
            content=f"<conversation_summary>\n{summary_text}\n</conversation_summary>",
        )
        summary_id = self.store.append_message(self.session_id, summary_msg)
        self.store.set_compact_from(self.session_id, summary_id)
        before_tokens = usage["used"]
        self.history = self.store.load_messages(self.session_id)
        after_usage = self._usage()
        return {
            "before_tokens": before_tokens,
            "after_tokens": after_usage["used"],
            "limit": limit,
            "summarized_messages": len(old_part),
            "usage": after_usage,
        }

    async def _summarize(self, messages: list[ChatMessage]) -> str | None:
        lines: list[str] = []
        for msg in messages:
            content = (msg.content or "").strip()
            if msg.role == "summary":
                lines.append(f"[prior summary]\n{content[:4000]}")
                continue
            if msg.tool_calls:
                calls = ", ".join(c.name for c in msg.tool_calls)
                lines.append(f"[assistant tool calls: {calls}]\n{content[:800]}")
                continue
            if msg.role == "tool":
                lines.append(f"[tool {msg.name}]\n{content[:1200]}")
                continue
            label = "user" if msg.role == "user" else "assistant"
            lines.append(f"[{label}]\n{content[:2000]}")
        conversation = "\n\n".join(lines)
        if not conversation.strip():
            return None
        prompt = (
            "你是编码 Agent 的上下文压缩器。把以下对话历史压缩为后续工作所需的结构化摘要，"
            "用中文 Markdown 输出，严格包含以下小节：\n\n"
            "## 主要目标\n## 关键决定与约束\n## 涉及的文件与代码（写明路径与关键函数）\n"
            "## 已完成\n## 未完成 / 待办\n## 错误与修复\n## 当前状态\n## 下一步\n\n"
            "保留所有文件路径、命令、报错信息的原文。不要输出任何小节之外的内容。\n\n"
            f"对话历史：\n{conversation[:60000]}"
        )
        messages_req = [ChatMessage(role="user", content=prompt)]
        parts: list[str] = []
        try:
            async for delta in self.provider.stream(messages_req, []):
                if delta.type == "text" and delta.text:
                    parts.append(delta.text)
        except Exception:
            return None
        return "".join(parts).strip() or None

    async def iter_turn(self, user_text: str, images=None):
        compacted = await self._maybe_compact()
        if compacted:
            yield TurnEvent(type="compaction", data=compacted)
        usage = self._usage()
        yield TurnEvent(type="context", data={"usage": usage})
        memory_block = None
        if self.memory is not None:
            try:
                memory_block = self.memory.retrieve_context(user_text)
            except Exception:
                memory_block = None
        user = ChatMessage(role="user", content=user_text, images=images)
        self.history.append(user)
        self.store.append_message(self.session_id, user)
        if (
            self.store.get_session(self.session_id)
            and self.store.get_session(self.session_id).get("title") == "untitled"
        ):
            self.store.touch(self.session_id, title=user_text[:80])

        rounds = 0
        self._turn_tokens = 0
        self._call_window.clear()
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
                self.history.append(assistant)
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
            self.history.append(assistant)
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
        self.history = self.store.load_messages(self.session_id)
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
        decision = decide(
            self.cfg.agent.approval,
            call,
            allow_always=self.allow_always,
            readonly_mcp=self.registry.readonly_mcp,
        )
        approval_label = "allow"
        if decision == "prompt":
            summary, diff = self.registry.approval_summary(call)
            request = ApprovalRequest(tool_call=call, summary=summary, diff=diff)
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
            if verdict.action == "allow_always":
                self.allow_always.add(call.name)
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
        self.history.append(tool_msg)
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
        pending = set(tasks)

        def drain():
            while not queue.empty():
                _idx, event = queue.get_nowait()
                if event is not None:
                    yield event

        while pending or not queue.empty():
            for ev in drain():
                yield ev
            if not pending:
                break
            done, pending = await _asyncio.wait(
                pending, return_when=_asyncio.FIRST_COMPLETED
            )
            for t in done:
                try:
                    index, summary = t.result()
                except Exception as exc:
                    index, summary = -1, f"subtask failed: {exc}"
                summaries[index] = summary
        for ev in drain():
            yield ev
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
