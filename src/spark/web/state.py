from __future__ import annotations

import asyncio
import contextvars
import importlib
import logging
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from spark.config import (
    ModelProfile,
    SparkConfig,
    mask_secret,
    save_config,
)
from spark.core.loop import AgentLoop
from spark.memory.service import MemoryService
from spark.memory.store import MemoryStore
from spark.models import ApprovalAction, ApprovalDecision
from spark.providers.factory import create_provider
from spark.sandbox import WorkdirSandbox
from spark.tools import bg as bg_tools
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)


class WebApprover:
    """Bridges approval prompts from the agent loop thread to HTTP handlers."""

    def __init__(self) -> None:
        self.pending = False
        self._event = threading.Event()
        self._cancel_event = threading.Event()
        self._action: ApprovalAction = "deny"

    async def __call__(self, request) -> ApprovalDecision:
        self.pending = True
        self._action = "deny"
        cancelled = self._cancel_event.is_set()
        if cancelled:
            self._cancel_event.clear()
        self._event = threading.Event()
        self._tool_call_id = request.tool_call.id
        deadline = time.monotonic() + 600.0
        try:
            while (
                not self._event.is_set()
                and not cancelled
                and not self._cancel_event.is_set()
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))
        finally:
            self.pending = False
            if self._cancel_event.is_set():
                cancelled = True
            self._cancel_event.clear()
        if cancelled:
            self._action = "deny"
        return ApprovalDecision(tool_call_id=self._tool_call_id, action=self._action)

    def respond(self, action: ApprovalAction) -> None:
        self._action = action
        self._event.set()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._event.set()


def _run_coroutine_safe(coro) -> Any:
    """Run an async coroutine safely regardless of event loop state.

    Handles the case where an event loop is already running (``asyncio.run``
    would raise ``RuntimeError``) by delegating to a thread-owned loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Already inside a running loop — delegate to a thread-owned loop and wait.
    result: list = []
    error: list = []

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            ctx = contextvars.copy_context()
            result.append(ctx.run(loop.run_until_complete, coro))
        except Exception as exc:  # noqa: BLE001
            error.append(exc)
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    th = threading.Thread(target=_runner, daemon=False, name="run-coroutine-safe")
    th.start()
    th.join(timeout=120)
    if error:
        raise error[0]
    return result[0] if result else None


def _generate_session_meta_async(
    state: SparkWebState, user_text: str, assistant_text: str
) -> None:
    """After a turn completes, ask the model for a short title + keywords and store them."""

    def run() -> None:
        import json as _json
        import re

        import httpx

        from spark.web.utils import _extract_text

        base_url = state.cfg.provider.base_url.rstrip("/")
        # Ensure the URL ends with /v1 for OpenAI-compatible APIs. Users may
        # configure "https://api.deepseek.com" without the "/v1" suffix.
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        api_key = state.cfg.provider.api_key or ""
        model = state.cfg.provider.model
        if not model or (state.cfg.provider.name == "openai_compat" and not api_key):
            return
        try:
            convo = (
                f"[user]\n{user_text[:2000]}\n\n[assistant]\n{assistant_text[:2000]}"
            )
            prompt = (
                "为这段对话生成元信息。严格输出一行 JSON，格式："
                '{"title": "不超过12字的标题", "keywords": ["关键词1", "关键词2", "关键词3"]}。'
                "标题概括用户核心意图，关键词提炼 2-4 个主题词。只输出 JSON，不要其他内容。\n\n"
                f"对话：\n{convo}"
            )
            timeout = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)
            resp = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "max_tokens": 120,
                    "temperature": 0.2,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            text = _extract_text(resp.json())
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return
            meta = _json.loads(match.group(0))
            title = str(meta.get("title") or "").strip().strip("\"'")[:40]
            raw_kws = meta.get("keywords")
            keywords = (
                [str(k).strip()[:20] for k in raw_kws if str(k).strip()][:4]
                if isinstance(raw_kws, list)
                else []
            )
            if title:
                state.store.set_title(state.session_id, title)
            if keywords:
                state.store.set_keywords(state.session_id, " ".join(keywords))
        except Exception as exc:
            logger.warning("Failed to update session meta: %s", exc)

    threading.Thread(target=run, daemon=True, name="session-meta").start()


class SparkWebState:
    def __init__(self, workdir: Path, cfg: SparkConfig, store: Any) -> None:
        self.workdir = workdir
        self.cfg = cfg
        self.store = store
        self.sandbox = WorkdirSandbox(workdir, cfg)
        self.session_id = ""
        self.loop: AgentLoop | None = None
        self.turn_lock = threading.Lock()
        self.busy = False
        self.approver = WebApprover() if cfg.agent.approval != "full-auto" else None
        self.mcp_bridge: McpBridge | None = None
        self.control_token = secrets.token_urlsafe(32)
        self.memory_store = MemoryStore(
            importlib.import_module("spark.web.server").default_home()
            / "memory.db",
            cfg.memory,
        )
        self.memory = MemoryService(self.memory_store, cfg)
        self.env_info = self._detect_env()
        bg_tools.attach_store(self.store)
        self.boot_mcp()
        self.rebuild_loop(reuse_latest=True)

    @staticmethod
    def _detect_env() -> dict:
        from spark.tools.shell import detect_env

        try:
            return detect_env(Path.cwd())
        except Exception:
            # 环境检测失败不影响状态页
            logger.debug("环境检测失败，返回空字典", exc_info=True)
            return {}

    def boot_mcp(self) -> list[str]:
        if self.mcp_bridge is not None:
            _run_coroutine_safe(self.mcp_bridge.close())
            self.mcp_bridge = None
        if not self.cfg.mcp_servers:
            return []
        bridge = McpBridge()
        dummy = ToolRegistry(ToolContext(sandbox=self.sandbox, config=self.cfg))
        _run_coroutine_safe(bridge.start(self.cfg.mcp_servers, dummy))
        self.mcp_bridge = bridge
        return list(bridge.errors)

    def rebuild_loop(
        self, session_id: str | None = None, reuse_latest: bool = False
    ) -> None:
        tool_ctx = ToolContext(sandbox=self.sandbox, config=self.cfg)
        registry = ToolRegistry(tool_ctx)
        if self.mcp_bridge is not None:
            self.mcp_bridge.register_into(registry)
            tool_ctx.mcp_call = self.mcp_bridge.call
        provider = create_provider(self.cfg)
        if session_id is None and reuse_latest:
            latest = next(
                (
                    row
                    for row in self.store.list_sessions()
                    if row["workdir"] == str(self.workdir)
                ),
                None,
            )
            session_id = latest["id"] if latest else None
        if session_id is None:
            self.session_id = self.store.create_session(
                self.workdir, self.cfg.provider.model, title="web preview"
            )
        else:
            self.session_id = session_id
        approver = self.approver if self.cfg.agent.approval != "full-auto" else None
        self.loop = AgentLoop(
            workdir=self.workdir,
            cfg=self.cfg,
            provider=provider,
            registry=registry,
            store=self.store,
            session_id=self.session_id,
            approver=approver,
            memory=self.memory,
        )

    def status(self) -> dict:
        key = self.cfg.provider.api_key or ""
        usage = {
            "used": 0,
            "limit": self.cfg.context.max_context_tokens,
            "percent": 0.0,
        }
        plan: list[dict] = []
        if self.loop is not None:
            try:
                usage = self.loop._usage()
            except Exception:
                # usage 查询失败时降级为 0
                logger.debug("查询 loop usage 失败", exc_info=True)
            plan = list(self.loop.registry.plan)
        return {
            "session_id": self.session_id,
            "workdir": str(self.workdir),
            "sandbox_mode": self.cfg.agent.sandbox_mode,
            "display": {
                "show_thinking": self.cfg.agent.show_thinking,
                "show_tools": self.cfg.agent.show_tools,
                "show_plan": self.cfg.agent.show_plan,
                "show_context": self.cfg.agent.show_context,
                "show_keywords": self.cfg.agent.show_keywords,
                "show_notices": self.cfg.agent.show_notices,
            },
            "env": self.env_info,
            "model": self.cfg.provider.model,
            "provider": self.cfg.provider.name,
            "approval": self.cfg.agent.approval,
            "base_url": self.cfg.provider.base_url,
            "api_key_masked": mask_secret(key),
            "has_api_key": bool(key),
            "mcp_servers": [s.name for s in self.cfg.mcp_servers],
            "mcp_errors": list(self.mcp_bridge.errors) if self.mcp_bridge else [],
            "context": usage,
            "plan": plan,
        }

    def ensure_default_profile(self) -> None:
        if self.cfg.model_profiles:
            return
        if not self.cfg.provider.model:
            return
        self.cfg.model_profiles = [
            ModelProfile(
                id=uuid.uuid4().hex[:8],
                name=self.cfg.provider.model,
                provider=self.cfg.provider.name,
                base_url=self.cfg.provider.base_url,
                model=self.cfg.provider.model,
                api_key=self.cfg.provider.api_key or "",
            )
        ]
        self.cfg.active_profile_id = self.cfg.model_profiles[0].id
        save_config(self.cfg)

    def full_config(self) -> dict:
        return {
            "workdir": str(self.workdir),
            "provider": {
                "name": self.cfg.provider.name,
                "base_url": self.cfg.provider.base_url,
                "model": self.cfg.provider.model,
                "api_key_env": self.cfg.provider.api_key_env,
                "api_key_masked": mask_secret(self.cfg.provider.api_key or ""),
                "has_api_key": bool(self.cfg.provider.api_key),
            },
            "agent": {
                "approval": self.cfg.agent.approval,
                "workdir_only": self.cfg.agent.workdir_only,
                "sandbox_mode": self.cfg.agent.sandbox_mode,
                "protected_paths": list(self.cfg.agent.protected_paths),
                "shell_timeout_sec": self.cfg.agent.shell_timeout_sec,
                "max_tool_rounds": self.cfg.agent.max_tool_rounds,
                "max_repeat_calls": self.cfg.agent.max_repeat_calls,
                "max_turn_tokens": self.cfg.agent.max_turn_tokens,
                "max_output_chars": self.cfg.agent.max_output_chars,
                "effort": self.cfg.agent.effort,
                "show_thinking": self.cfg.agent.show_thinking,
                "show_tools": self.cfg.agent.show_tools,
                "show_plan": self.cfg.agent.show_plan,
                "show_context": self.cfg.agent.show_context,
                "show_keywords": self.cfg.agent.show_keywords,
                "show_notices": self.cfg.agent.show_notices,
            },
            "context": {
                "agents_md": self.cfg.context.agents_md,
                "max_fragment_chars": self.cfg.context.max_fragment_chars,
                "history_budget_chars": self.cfg.context.history_budget_chars,
                "max_context_tokens": self.cfg.context.max_context_tokens,
                "compact_threshold": self.cfg.context.compact_threshold,
                "keep_recent_messages": self.cfg.context.keep_recent_messages,
            },
            "mcp_servers": [
                {
                    "name": s.name,
                    "command": s.command,
                    "args": s.args,
                    "readonly_tools": s.readonly_tools,
                }
                for s in self.cfg.mcp_servers
            ],
            "mcp_errors": list(self.mcp_bridge.errors) if self.mcp_bridge else [],
            "hooks": [
                {
                    "event": h.event,
                    "command": h.command,
                    "args": list(h.args),
                    "name": h.name,
                    "timeout_sec": h.timeout_sec,
                }
                for h in self.cfg.hooks
            ],
        }
