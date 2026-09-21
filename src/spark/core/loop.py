from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from spark.config import SparkConfig
from spark.core.context import build_messages, history_token_usage
from spark.core.tokens import estimate_history_tokens, estimate_message_tokens
from spark.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChatDelta,
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

    def cancel(self) -> None:
        self.cancelled = True

    async def run(self, user_text: str, images=None) -> list[TurnEvent]:
        self.cancelled = False
        events: list[TurnEvent] = []
        async for event in self.iter_turn(user_text, images=images):
            events.append(event)
        return events

    def iter_turn_sync(self, user_text: str, images=None):
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
        return estimate_tokens(json.dumps(schemas, ensure_ascii=False)) if schemas else 0

    async def _maybe_compact(self):
        """Auto-compact: when usage crosses threshold, summarize old turns into one summary message."""
        usage = self._usage()
        limit = self.cfg.context.max_context_tokens
        threshold = self.cfg.context.compact_threshold
        keep = max(2, self.cfg.context.keep_recent_messages)
        if usage["used"] <= limit * threshold or len(self.history) <= keep:
            return None
        boundary = len(self.history) - keep
        while boundary < len(self.history) and self.history[boundary].role != "user":
            boundary += 1
        if boundary >= len(self.history) or boundary == 0:
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
        self.cancelled = False
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
        if self.store.get_session(self.session_id) and self.store.get_session(self.session_id).get("title") == "untitled":
            self.store.touch(self.session_id, title=user_text[:80])

        rounds = 0
        while True:
            if self.cancelled:
                yield TurnEvent(type="turn_error", text="Turn cancelled")
                return
            if rounds >= self.cfg.agent.max_tool_rounds:
                yield TurnEvent(type="turn_error", text="Reached max_tool_rounds")
                return
            messages = build_messages(workdir=self.workdir, cfg=self.cfg, history=self.history, memory_block=memory_block)
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            try:
                async for delta in self.provider.stream(messages, self.registry.schemas()):
                    if self.cancelled:
                        yield TurnEvent(type="turn_error", text="Turn cancelled")
                        return
                    if delta.type == "reasoning" and delta.text:
                        yield TurnEvent(type="reasoning_delta", text=delta.text)
                    elif delta.type == "text" and delta.text:
                        text_parts.append(delta.text)
                        yield TurnEvent(type="text_delta", text=delta.text)
                    elif delta.type == "tool_call" and delta.tool_call:
                        tool_calls.append(delta.tool_call)
            except Exception as exc:
                yield TurnEvent(type="turn_error", text=str(exc))
                return

            if tool_calls:
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
            yield TurnEvent(type="context", data={"usage": self._usage()})
            yield TurnEvent(type="turn_end", text=assistant.content)
            return

    def _maybe_auto_checkpoint(self, assistant_id: int) -> None:
        """Snapshot the workdir + conversation after turns that touched files or ran shell."""
        try:
            from spark.core.checkpoints import make_checkpoint_record

            recent_tools = [m.name for m in self.history[-12:] if m.role == "tool"]
            if not any(name in {"write_file", "apply_patch", "run_shell", "notebook_edit"} for name in recent_tools):
                return
            snapshot_id, files_hash = make_checkpoint_record(self.workdir)
            last_user = next((m for m in reversed(self.history) if m.role == "user"), None)
            label = (last_user.content or "")[:60].strip() if last_user else "checkpoint"
            self.store.add_checkpoint(self.session_id, label, assistant_id, json.dumps({
                "snapshot_id": snapshot_id,
                "files_hash": files_hash,
                "message_id": assistant_id,
            }))
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
        removed = self.store.delete_messages_after(self.session_id, int(record["message_id"]))
        self.history = self.store.load_messages(self.session_id)
        return {
            "checkpoint_id": checkpoint_id,
            "label": record["label"],
            "files": files,
            "removed_messages": removed,
        }

    async def _run_tool(self, call: ToolCall, message_id: int):
        yield TurnEvent(type="tool_start", tool_call=call)
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
                result = ToolResult(ok=False, payload={"error": "denied", "reason": "no approver"})
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
                prompts = [str(t.get("prompt") or "").strip() for t in raw_tasks if isinstance(t, dict)]
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
                    if sub_event.type in {"tool_start", "tool_end", "plan"}:
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
                    if sub_event.type in {"tool_start", "tool_end", "plan"}:
                        yield sub_event
                    elif sub_event.type == "turn_end":
                        summary_parts.append(sub_event.text or "")
                result = ToolResult(ok=True, payload={"summaries": summary_parts})
        else:
            result = await self.registry.execute(call)
        await self._persist_tool(call, message_id, result, approval_label)
        yield TurnEvent(type="tool_end", tool_call=call, result=result)

    async def _persist_tool(self, call: ToolCall, message_id: int, result: ToolResult, approval: str) -> None:
        tool_msg = ChatMessage(
            role="tool",
            name=call.name,
            tool_call_id=call.id,
            content=json.dumps(result.payload, ensure_ascii=False),
        )
        self.history.append(tool_msg)
        self.store.append_message(self.session_id, tool_msg)
        self.store.append_tool_event(self.session_id, message_id, call.name, call.arguments, result, approval)

    async def spawn_subtask(self, prompt: str):
        """Run a self-contained sub-agent with a fresh context; yield its events, final turn_end carries the summary."""
        from pathlib import Path as _Path

        sub_cfg = self.cfg.model_copy(deep=True)
        sub_cfg.agent.max_tool_rounds = min(15, max(5, sub_cfg.agent.max_tool_rounds))
        sub_ctx = ToolContext(sandbox=self.registry.ctx.sandbox, config=sub_cfg)
        sub_registry = ToolRegistry(sub_ctx)
        sub_store = SessionStore(_Path(":memory:"))
        sid = sub_store.create_session(self.workdir, sub_cfg.provider.model, title="subtask")
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
        final_text = ""
        try:
            async for event in sub_loop.iter_turn(prompt):
                if event.type in {"tool_start", "tool_end", "plan"}:
                    yield event
                elif event.type == "turn_end":
                    final_text = event.text or ""
                elif event.type == "turn_error":
                    final_text = f"subtask failed: {event.text}"
                    yield TurnEvent(type="turn_end", text=final_text)
                    return
            yield TurnEvent(type="turn_end", text=final_text)
        finally:
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
            summary = await self._collect_subtask_events(index, prompt, queue)
            return index, summary

        tasks = [_asyncio.create_task(runner(i, p)) for i, p in enumerate(prompts)]
        finished_count = 0
        pending = set(tasks)
        while pending:
            done, pending = await _asyncio.wait(pending, return_when=_asyncio.FIRST_COMPLETED)
            for t in done:
                index, summary = t.result()
                summaries[index] = summary
                finished_count += 1
            # drain any queued live events
            while not queue.empty():
                _idx, event = queue.get_nowait()
                if event is not None:
                    yield event
        for i in range(len(prompts)):
            label = f"[子任务 {i + 1}]"
            yield TurnEvent(type="turn_end", text=f"{label} {summaries.get(i, 'no output')}")

    async def _collect_subtask_events(self, index: int, prompt: str, queue) -> str:
        """Run one subtask; stream its tool events into the queue tagged by index; return summary."""
        from pathlib import Path as _Path

        sub_cfg = self.cfg.model_copy(deep=True)
        sub_cfg.agent.max_tool_rounds = min(15, max(5, sub_cfg.agent.max_tool_rounds))
        sub_ctx = ToolContext(sandbox=self.registry.ctx.sandbox, config=sub_cfg)
        sub_registry = ToolRegistry(sub_ctx)
        sub_store = SessionStore(_Path(":memory:"))
        sid = sub_store.create_session(self.workdir, sub_cfg.provider.model, title=f"subtask-{index}")
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
        final_text = ""
        try:
            async for event in sub_loop.iter_turn(prompt):
                if event.type in {"tool_start", "tool_end", "plan"}:
                    await queue.put((index, event))
                elif event.type == "turn_end":
                    final_text = event.text or ""
                elif event.type == "turn_error":
                    final_text = f"subtask failed: {event.text}"
            await queue.put((index, None))
            return final_text
        finally:
            sub_store.close()