"""Standalone POST route handlers — factored out of routes.py to keep the
Handler class focused on HTTP plumbing / security / route dispatch.

Each handler is a pure ``(handler, body) -> None`` function that:

1. reads ``SparkWebState`` via ``_route_state`` (from ``spark.web._routing``),
2. validates the request body,
3. performs the action (or returns 4xx immediately),
4. writes exactly one JSON response via ``handler._json``.

The dispatch table in ``routes.py`` wires each path to the corresponding
function from this module.
"""

from __future__ import annotations

import asyncio as _asyncio
import base64 as _b64mod
import logging
import socket
import threading
from typing import Literal, cast

from spark.config import (
    ApprovalMode,
    HookConfig,
    ProviderName,
    SandboxMode,
    require_api_key,
)
from spark.errors import ConfigError, SparkError
from spark.models import ApprovalAction, ImageRef
from spark.providers.probe import probe_provider
from spark.sandbox import WorkdirSandbox
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.web.sse import SSE_DISCONNECT_ERRORS, _SSESession
from spark.web.state import _generate_session_meta_async
from spark.web.utils import (
    HOOK_EVENTS,
    _event_payload,
    _probe_target_allowed,
    _safe_agents_md_name,
    parse_mcp_servers,
)

logger = logging.getLogger(__name__)


def _save_config(cfg) -> None:
    """Resolve save_config through the server module so that tests can
    monkeypatch ``spark.web.server.save_config`` and intercept calls."""
    import importlib

    importlib.import_module("spark.web.server").save_config(cfg)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
def _handle_models_save(handler: HandlerBase, body: dict) -> None:
    import uuid

    from spark.config import ModelProfile

    name = str(body.get("name") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    model = str(body.get("model") or "").strip()
    provider_name = str(body.get("provider") or "openai_compat").strip()
    api_key = str(body.get("api_key") or "").strip()
    if provider_name not in {"openai_compat", "ollama", "mock"}:
        handler._json(400, {"error": "provider must be openai_compat, ollama or mock"})
        return
    if not base_url or not model:
        handler._json(400, {"error": "base_url and model are required"})
        return
    state = _route_state(handler)
    pid = str(body.get("id") or "").strip()
    existing = next(
        (p for p in state.cfg.model_profiles if p.id == pid), None
    )
    if existing:
        existing.name = name or existing.name or existing.model
        existing.provider = cast(ProviderName, provider_name)
        existing.base_url = base_url
        existing.model = model
        if api_key:
            existing.api_key = api_key
    else:
        if not api_key:
            api_key = state.cfg.provider.api_key or ""
        existing = ModelProfile(
            id=uuid.uuid4().hex[:8],
            name=name or model,
            provider=cast(ProviderName, provider_name),
            base_url=base_url,
            model=model,
            api_key=api_key,
        )
        state.cfg.model_profiles.append(existing)
    _save_config(state.cfg)
    handler._json(200, {"ok": True, "id": existing.id})


def _handle_models_delete(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    pid = str(body.get("id") or "").strip()
    before = len(state.cfg.model_profiles)
    state.cfg.model_profiles = [
        p for p in state.cfg.model_profiles if p.id != pid
    ]
    if len(state.cfg.model_profiles) == before:
        handler._json(404, {"error": "unknown profile"})
        return
    if state.cfg.active_profile_id == pid:
        state.cfg.active_profile_id = ""
    _save_config(state.cfg)
    handler._json(200, {"ok": True})


def _handle_models_activate(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    pid = str(body.get("id") or "").strip()
    profile = next(
        (p for p in state.cfg.model_profiles if p.id == pid), None
    )
    if not profile:
        handler._json(404, {"error": "unknown profile"})
        return
    state.cfg.provider.name = profile.provider
    state.cfg.provider.base_url = profile.base_url
    state.cfg.provider.model = profile.model
    if profile.api_key:
        state.cfg.provider.api_key = profile.api_key
    state.cfg.active_profile_id = profile.id
    _save_config(state.cfg)
    try:
        with state.turn_lock:
            state.rebuild_loop(session_id=state.session_id)
    except SparkError as exc:
        handler._json(400, {"error": str(exc)})
        return
    handler._json(200, {"ok": True, "status": state.status()})


# ---------------------------------------------------------------------------
# Agents.md / memory
# ---------------------------------------------------------------------------
def _handle_agents_md(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    content = str(body.get("content") or "")
    filename = _safe_agents_md_name(state.cfg.context.agents_md)
    if filename is None:
        handler._json(
            400,
            {
                "error": (
                    "invalid agents_md filename: it must be a bare filename "
                    "inside the workdir"
                )
            },
        )
        return
    target = state.workdir / filename
    if content.strip():
        target.write_text(content, encoding="utf-8")
    elif target.exists():
        target.write_text("", encoding="utf-8")
    handler._json(
        200,
        {
            "ok": True,
            "chars": len(content),
            "max_fragment_chars": state.cfg.context.max_fragment_chars,
        },
    )


def _handle_memory(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    action = str(body.get("action") or "").strip()
    if action == "add":
        content = str(body.get("content") or "").strip()
        if not content:
            handler._json(400, {"error": "content required"})
            return
        mtype = str(body.get("type") or "general")
        if mtype not in {"preference", "project", "lesson", "general"}:
            mtype = "general"
        try:
            importance = float(body.get("importance", 8))
        except (TypeError, ValueError):
            importance = 8.0
        mid = state.memory.add_manual(content, mtype, importance)
        handler._json(200, {"ok": True, "id": mid, "stats": state.memory.stats()})
        return
    if action == "delete":
        mem_id = body.get("id")
        if not isinstance(mem_id, int):
            handler._json(400, {"error": "id required"})
            return
        state.memory.delete(mem_id)
        handler._json(200, {"ok": True, "stats": state.memory.stats()})
        return
    if action == "optimize":
        if state.busy:
            handler._json(409, {"error": "agent is busy"})
            return
        report = state.memory.optimize()
        handler._json(200, {"ok": True, "report": report})
        return
    handler._json(400, {"error": "action must be add, delete or optimize"})


# ---------------------------------------------------------------------------
# Cancel / approval
# ---------------------------------------------------------------------------
def _handle_cancel(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.loop:
        state.loop.cancel()
    if state.approver is not None:
        state.approver.cancel()
    handler._json(200, {"ok": True})


def _handle_approval(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.approver is None:
        handler._json(400, {"error": "approval disabled (full-auto)"})
        return
    action = str(body.get("decision") or "").strip()
    if action not in {"allow", "allow_always", "deny"}:
        handler._json(400, {"error": "decision must be allow, allow_always or deny"})
        return
    state.approver.respond(cast(ApprovalAction, action))
    handler._json(200, {"ok": True})


# ---------------------------------------------------------------------------
# Sessions / checkpoints
# ---------------------------------------------------------------------------
def _handle_sessions_new(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    with state.turn_lock:
        state.rebuild_loop()
    handler._json(200, {"ok": True, "status": state.status()})


def _handle_sessions_delete(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        handler._json(400, {"error": "session_id required"})
        return
    row = state.store.get_session(session_id)
    if not row or row.get("workdir") != str(state.workdir):
        handler._json(404, {"error": "unknown session"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    deleting_active = session_id == state.session_id
    state.store.delete_session(session_id)
    if deleting_active:
        with state.turn_lock:
            remaining = [
                r
                for r in state.store.list_sessions()
                if r["workdir"] == str(state.workdir)
            ]
            if remaining:
                state.rebuild_loop(session_id=remaining[0]["id"])
            else:
                state.rebuild_loop()
    handler._json(200, {"ok": True, "status": state.status()})


def _handle_sessions_switch(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    session_id = str(body.get("session_id") or "").strip()
    row = state.store.get_session(session_id)
    if not row or row.get("workdir") != str(state.workdir):
        handler._json(404, {"error": "unknown session"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    with state.turn_lock:
        state.rebuild_loop(session_id=session_id)
    messages = []
    loop = state.loop
    for msg in (loop.history if loop else []):
        if msg.role in {"user", "assistant"} and msg.content:
            messages.append({"role": msg.role, "content": msg.content})
        elif msg.role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "name": msg.name or "",
                    "content": msg.content or "",
                }
            )
    handler._json(200, {"ok": True, "status": state.status(), "messages": messages})


def _history_messages(state) -> list[dict]:
    """Extract serializable message list from current loop history."""
    messages: list[dict] = []
    if state.loop is None:
        return messages
    for msg in state.loop.history or []:
        if msg.role in {"user", "assistant"} and msg.content:
            messages.append({"role": msg.role, "content": msg.content})
        elif msg.role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "name": msg.name or "",
                    "content": msg.content or "",
                }
            )
    return messages


def _handle_checkpoints_rollback(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    cp_id = body.get("checkpoint_id")
    if not isinstance(cp_id, int):
        handler._json(400, {"error": "checkpoint_id (int) required"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return
    try:
        with state.turn_lock:
            report = state.loop.rollback_to_checkpoint(cp_id)
    except ValueError as exc:
        handler._json(404, {"error": str(exc)})
        return
    except Exception as exc:
        handler._json(500, {"error": f"rollback failed: {exc}"})
        return
    handler._json(
        200,
        {
            "ok": True,
            "report": report,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )


# ---------------------------------------------------------------------------
# Chat (non-streaming)
# ---------------------------------------------------------------------------
def _handle_chat(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        handler._json(400, {"error": "prompt required"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return
    try:
        with state.turn_lock:
            events = _asyncio.run(state.loop.run(prompt))
    except Exception as exc:
        handler._json(500, {"error": str(exc)})
        return
    handler._json(200, {"events": [_event_payload(e) for e in events]})


# ---------------------------------------------------------------------------
# Chat (streaming via SSE)
# ---------------------------------------------------------------------------
def _handle_chat_stream(handler: HandlerBase, body: dict) -> None:
    import json as _json

    state = _route_state(handler)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt and not body.get("images"):
        handler._json(400, {"error": "prompt required"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return

    images: list[dict] = []
    for item in body.get("images") or []:
        if not isinstance(item, str) or not item.startswith("data:image/"):
            continue
        try:
            head, _, b64part = item.partition(",")
            media_type = (
                head.removeprefix("data:").split(";")[0] or "image/png"
            )
            _b64mod.b64decode(b64part, validate=True)
            images.append({"media_type": media_type, "data": b64part})
        except Exception:
            logger.debug("图片 base64 校验失败，跳过该项", exc_info=True)
            continue

    image_refs = [ImageRef.model_validate(i) for i in images] or None
    if not prompt and image_refs:
        prompt = "请分析这些图片。"
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
    except SSE_DISCONNECT_ERRORS:
        handler.close_connection = True
        return

    turn_loop = state.loop
    approver = state.approver

    def close_stream() -> None:
        handler.close_connection = True
        connection = getattr(handler, "connection", None)
        if connection is None:
            return
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except SSE_DISCONNECT_ERRORS:
            try:
                connection.close()
            except SSE_DISCONNECT_ERRORS:
                pass

    def _sse_secrets() -> tuple[str, ...]:
        """SSE 出口脱敏用的已知密钥集（控制令牌 + 模型 API Key）。"""
        api_key = ""
        try:
            api_key = state.cfg.provider.api_key or ""
        except AttributeError:
            pass
        token = getattr(state, "control_token", "") or ""
        return tuple(s for s in (token, api_key) if s)

    def cancel_turn() -> None:
        if turn_loop is not None:
            turn_loop.cancel()
        if approver is not None:
            approver.cancel()

    stream = _SSESession(
        handler.wfile,
        connection=handler.connection,
        on_disconnect=cancel_turn,
        close_connection=close_stream,
        secrets=_sse_secrets(),
    )
    stream.start()

    memory_collector: dict = {"text": "", "tools": []}

    def collect_event(event) -> None:
        if event.type == "text_delta" and event.text:
            memory_collector["text"] += event.text
        elif event.type == "turn_end" and event.text:
            memory_collector["text"] = event.text
        elif event.type == "tool_end" and event.tool_call:
            memory_collector["tools"].append(
                f"{event.tool_call.name}: {_json.dumps(event.result.payload if event.result else {}, ensure_ascii=False)[:200]}"
            )

    try:
        if stream.disconnected:
            return
        with state.turn_lock:
            if stream.disconnected:
                return
            state.busy = True
            try:
                for event in turn_loop.iter_turn_sync(
                    prompt, images=image_refs
                ):
                    collect_event(event)
                    if not stream.submit(event):
                        break
            finally:
                state.busy = False
        if stream.disconnected:
            return
        if not stream.finish():
            return
        turn_text = memory_collector["text"]
        if turn_text:
            _generate_session_meta_async(state, prompt, turn_text)
        if (
            state.cfg.memory.enabled
            and state.cfg.memory.auto_extract
            and state.memory is not None
            and state.cfg.provider.name != "mock"
        ):

            def run_memory() -> None:
                try:
                    state.memory.record_turn(
                        user_text=prompt,
                        assistant_text=turn_text,
                        tool_summary=" | ".join(memory_collector["tools"]),
                        session_id=state.session_id,
                    )
                except Exception:
                    logger.warning("SSE 后台 memory 写入失败", exc_info=True)

            threading.Thread(target=run_memory, daemon=True).start()
    except SSE_DISCONNECT_ERRORS as exc:
        if stream.disconnected:
            cancel_turn()
            stream.abort()
            return
        if stream.submit_payload({"type": "turn_error", "text": str(exc)}):
            stream.finish()
        else:
            stream.abort()
    except Exception as exc:
        if stream.disconnected:
            stream.abort()
            return
        if stream.submit_payload({"type": "turn_error", "text": str(exc)}):
            stream.finish()
        else:
            stream.abort()
    finally:
        if stream.disconnected:
            stream.abort()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _handle_settings(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return

    provider_raw = body.get("provider")
    provider_in: dict = provider_raw if isinstance(provider_raw, dict) else {}
    agent_raw = body.get("agent")
    agent_in: dict = agent_raw if isinstance(agent_raw, dict) else {}

    base_url = str(
        provider_in.get("base_url")
        or body.get("base_url")
        or state.cfg.provider.base_url
    ).strip()
    model = str(
        provider_in.get("model")
        or body.get("model")
        or state.cfg.provider.model
    ).strip()
    api_key = str(
        provider_in.get("api_key") or body.get("api_key") or ""
    ).strip()
    provider_name = str(
        provider_in.get("name") or state.cfg.provider.name
    ).strip()

    if provider_name not in {"openai_compat", "ollama", "mock"}:
        handler._json(400, {"error": "provider must be openai_compat, ollama or mock"})
        return
    if not base_url or not model:
        handler._json(400, {"error": "base_url and model are required"})
        return
    if provider_name == "openai_compat":
        api_key = api_key or state.cfg.provider.api_key or ""
        if not api_key:
            try:
                api_key = require_api_key(state.cfg) or ""
            except ConfigError:
                handler._json(400, {"error": "api_key is required"})
                return

    state.cfg.provider.name = cast(ProviderName, provider_name)
    state.cfg.provider.base_url = base_url
    state.cfg.provider.model = model
    state.cfg.provider.api_key = api_key or None

    approval = str(
        agent_in.get("approval") or state.cfg.agent.approval
    ).strip()
    if approval not in {"suggest", "auto-edit", "full-auto", "plan-only", "approve-plan", "manual"}:
        handler._json(
            400,
            {"error": "approval must be suggest, auto-edit or full-auto"},
        )
        return
    state.cfg.agent.approval = cast(ApprovalMode, approval)

    sandbox_mode = str(
        agent_in.get("sandbox_mode") or state.cfg.agent.sandbox_mode
    ).strip()
    if sandbox_mode not in {
        "sandbox-only",
        "workspace",
        "full-access",
        "unrestricted",
    }:
        handler._json(
            400,
            {
                "error": "sandbox_mode must be sandbox-only, workspace, full-access or unrestricted"
            },
        )
        return
    state.cfg.agent.sandbox_mode = cast(SandboxMode, sandbox_mode)

    effort = str(
        agent_in.get("effort") or state.cfg.agent.effort
    ).strip()
    if effort not in {"low", "medium", "high"}:
        handler._json(400, {"error": "effort must be low, medium or high"})
        return
    state.cfg.agent.effort = cast(Literal["low", "medium", "high"], effort)

    display_flags = {
        k: bool(agent_in[k])
        for k in (
            "show_thinking",
            "show_tools",
            "show_plan",
            "show_context",
            "show_keywords",
            "show_notices",
        )
        if k in agent_in
    }
    for key, value in display_flags.items():
        setattr(state.cfg.agent, key, value)

    if "protected_paths" in agent_in:
        pp = agent_in.get("protected_paths")
        if isinstance(pp, list):
            state.cfg.agent.protected_paths = [
                str(x).strip() for x in pp if str(x).strip()
            ]

    state.sandbox = WorkdirSandbox(state.workdir, state.cfg)
    try:
        state.cfg.agent.workdir_only = bool(
            agent_in.get("workdir_only", state.cfg.agent.workdir_only)
        )
        state.cfg.agent.shell_timeout_sec = max(
            1,
            int(
                agent_in.get(
                    "shell_timeout_sec", state.cfg.agent.shell_timeout_sec
                )
            ),
        )
        state.cfg.agent.max_tool_rounds = max(
            1,
            int(
                agent_in.get(
                    "max_tool_rounds", state.cfg.agent.max_tool_rounds
                )
            ),
        )
        state.cfg.agent.max_output_chars = max(
            200,
            int(
                agent_in.get(
                    "max_output_chars", state.cfg.agent.max_output_chars
                )
            ),
        )
        repeat_in = int(
            agent_in.get(
                "max_repeat_calls", state.cfg.agent.max_repeat_calls
            )
        )
        token_in = int(
            agent_in.get("max_turn_tokens", state.cfg.agent.max_turn_tokens)
        )
        if repeat_in < 0 or token_in < 0:
            raise ValueError("negative reliability limit")
        state.cfg.agent.max_repeat_calls = repeat_in
        state.cfg.agent.max_turn_tokens = token_in
    except (TypeError, ValueError):
        handler._json(400, {"error": "invalid agent numbers"})
        return

    mcp_in = body.get("mcp_servers")
    mcp_errors: list[str] = []
    if isinstance(mcp_in, list):
        try:
            servers = parse_mcp_servers(mcp_in)
        except ValueError as exc:
            handler._json(400, {"error": str(exc)})
            return
        state.cfg.mcp_servers = servers
        try:
            mcp_errors = state.boot_mcp()
        except Exception as exc:
            mcp_errors = [f"MCP boot failed: {exc}"]

    hooks_in = body.get("hooks")
    if isinstance(hooks_in, list):
        parsed_hooks = []
        for item in hooks_in:
            if not isinstance(item, dict):
                handler._json(400, {"error": "invalid hook entry"})
                return
            event_name = str(item.get("event") or "").strip()
            command = str(item.get("command") or "").strip()
            if event_name not in HOOK_EVENTS or not command:
                handler._json(
                    400,
                    {
                        "error": (
                            f"invalid hook: event must be one of "
                            f"{', '.join(HOOK_EVENTS)} and command is required"
                        )
                    },
                )
                return
            try:
                timeout_sec = max(1, int(item.get("timeout_sec") or 15))
            except (TypeError, ValueError):
                handler._json(400, {"error": "invalid hook timeout_sec"})
                return
            raw_args = item.get("args") or []
            if not isinstance(raw_args, list):
                handler._json(400, {"error": "invalid hook args"})
                return
            parsed_hooks.append(
                HookConfig(
                    event=event_name,
                    command=command,
                    args=[str(part) for part in raw_args],
                    name=str(item.get("name") or "")[:120],
                    timeout_sec=timeout_sec,
                )
            )
        state.cfg.hooks = parsed_hooks

    _save_config(state.cfg)
    try:
        with state.turn_lock:
            state.rebuild_loop(session_id=state.session_id)
    except SparkError as exc:
        handler._json(400, {"error": str(exc)})
        return
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "mcp_errors": mcp_errors,
            "config": state.full_config(),
        },
    )


# ---------------------------------------------------------------------------
# Test (provider probe)
# ---------------------------------------------------------------------------
def _handle_test(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    base_url = str(
        body.get("base_url") or state.cfg.provider.base_url
    ).strip()
    model = str(body.get("model") or state.cfg.provider.model).strip()
    stored_key = state.cfg.provider.api_key or ""
    if not _probe_target_allowed(base_url, state.cfg.provider.base_url):
        handler._json(
            400,
            {
                "error": (
                    "refusing to send the stored credential to a different "
                    "endpoint; change the provider base_url explicitly, or "
                    "supply an explicit api_key for this address"
                )
            },
        )
        return
    api_key = str(body.get("api_key") or "").strip() or stored_key
    if not api_key:
        try:
            api_key = require_api_key(state.cfg) or ""
        except ConfigError as exc:
            handler._json(400, {"ok": False, "error": str(exc)})
            return
    result = _asyncio.run(
        probe_provider(
            base_url=base_url, api_key=api_key, model=model, rounds=2
        )
    )
    handler._json(200 if result.ok else 502, result.public_dict())
