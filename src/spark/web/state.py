from __future__ import annotations

import asyncio
import importlib
import logging
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from spark.asyncutil import run_coroutine_sync
from spark.config import (
    ModelProfile,
    SparkConfig,
    env_api_key,
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
        self._tool_call_id = ""

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


def _generate_session_meta_async(
    state: SparkWebState, user_text: str, assistant_text: str
) -> None:
    """After a turn completes, ask the model for a short title + keywords and store them."""

    def run() -> None:
        import json as _json
        import re

        from spark.asyncutil import run_coroutine_sync
        from spark.providers.auxiliary import chat_once

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
            text = run_coroutine_sync(
                chat_once(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    user=prompt,
                    temperature=0.2,
                    max_tokens=120,
                ),
                timeout=60.0,
            )
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
            run_coroutine_sync(self.mcp_bridge.close())
            self.mcp_bridge = None
        if not self.cfg.mcp_servers:
            return []
        bridge = McpBridge()
        dummy = ToolRegistry(ToolContext(sandbox=self.sandbox, config=self.cfg))
        run_coroutine_sync(bridge.start(self.cfg.mcp_servers, dummy))
        self.mcp_bridge = bridge
        return list(bridge.errors)

    def _ensure_checkpointer(self):
        """One persistent turn checkpointer per process (A10 crash recovery).

        Turns run on the process-wide runtime loop, so a single aiosqlite
        connection can outlive a turn and survive a restart: approvals pending
        at crash time are listed from ``$SPARK_HOME/checkpoints.db`` and can be
        resumed. Falls back to in-memory state when the durable store fails.
        """
        from spark.core.turn_store import persistent_checkpointer

        return persistent_checkpointer()

    def _notify_hook(self, event: str, payload: dict) -> None:
        """Best-effort lifecycle notification (never breaks the request)."""
        try:
            loop = self.loop
            if loop is not None:
                loop._notify_safe(event, payload)
        except Exception:
            logger.debug("hook notify failed: %s", event, exc_info=True)

    def rebuild_loop(
        self, session_id: str | None = None, reuse_latest: bool = False
    ) -> None:
        tool_ctx = ToolContext(sandbox=self.sandbox, config=self.cfg)
        registry = ToolRegistry(tool_ctx)
        if self.mcp_bridge is not None:
            self.mcp_bridge.register_into(registry)
            tool_ctx.mcp_call = self.mcp_bridge.call
            tool_ctx.mcp_bridge = self.mcp_bridge
        provider = create_provider(self.cfg)
        if self.mcp_bridge is not None:
            # P2：server 反向 sampling 以当前 Provider 为大脑
            self.mcp_bridge.set_sampling_handler(provider)
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
                self.workdir, self.cfg.provider.model, title="新对话"
            )
        else:
            self.session_id = session_id
        # The approver is created once (in __init__ only when the startup mode
        # was not full-auto). Reusing that decision meant switching full-auto →
        # suggest left approver=None, so every write/shell call was silently
        # denied: the UI promised approvals that could never appear.
        if self.cfg.agent.approval == "full-auto":
            approver = None
        else:
            if self.approver is None:
                self.approver = WebApprover()
            approver = self.approver
        previous_session = getattr(self, "session_id", "")
        self.loop = AgentLoop(
            workdir=self.workdir,
            cfg=self.cfg,
            provider=provider,
            registry=registry,
            store=self.store,
            session_id=self.session_id,
            approver=approver,
            memory=self.memory,
            checkpointer=self._ensure_checkpointer(),
        )
        if previous_session and previous_session != self.session_id:
            self._notify_hook(
                "session_switch",
                {"from": previous_session, "to": self.session_id},
            )

    def status(self) -> dict:
        # 凭据可来自配置或环境变量（SPARK_* / DEEPSEEK_API_KEY 等）：只读配置字段
        # 会把"环境变量供 key"的部署误显示成未配置。
        key = env_api_key(self.cfg) or self.cfg.provider.api_key or ""
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
            "session_tokens": self.store.session_token_usage(
                self.session_id, model=self.cfg.provider.model
            ),
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
            **self._demo_flags(key),
        }

    def _demo_flags(self, key: str) -> dict:
        """Say out loud when the configured provider silently fell back to mock.

        ``create_provider`` degrades to ``MockProvider`` when an openai_compat
        provider has no key, which kept the UI looking like a live model was
        answering while every reply was scripted. The flag is derived from the
        loop's actual provider instance, not from config, so it cannot drift.
        """
        provider_name = str(self.cfg.provider.name)
        live = self.loop.provider if self.loop is not None else None
        is_mock = live is not None and live.__class__.__name__ == "MockProvider"
        if provider_name == "mock":
            return {"demo_mode": True, "demo_reason": "当前使用 Mock 演示模型"}
        if is_mock:
            env_name = self.cfg.provider.api_key_env or "SPARK_API_KEY"
            return {
                "demo_mode": True,
                "demo_reason": f"缺少 API Key（{env_name}），已退回 Mock 演示模型",
            }
        if provider_name == "openai_compat" and not key:
            return {"demo_mode": True, "demo_reason": "缺少 API Key，尚未连接真实模型"}
        return {"demo_mode": False, "demo_reason": ""}

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
                "system_prompt": self.cfg.agent.system_prompt,
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
                # The Project tab edits these; without them in the response the
                # inputs read undefined and the toggle silently never persisted.
                "repo_map_enabled": self.cfg.context.repo_map_enabled,
                "repo_map_max_chars": self.cfg.context.repo_map_max_chars,
            },
            "mcp_servers": [
                {
                    "name": s.name,
                    "command": s.command,
                    "args": s.args,
                    "readonly_tools": s.readonly_tools,
                    "transport": s.transport,
                    "allowed_tools": list(s.allowed_tools),
                    "http": (
                        {
                            "url": s.http.url,
                            "headers": dict(s.http.headers),
                            "timeout_sec": s.http.timeout_sec,
                        }
                        if s.http
                        else None
                    ),
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
