"""设置保存（/api/settings）与模型探活（/api/test）。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import asyncio as _asyncio
from typing import Any, Literal, cast

from spark.config import (
    ApprovalMode,
    HookConfig,
    ProviderName,
    SandboxMode,
    require_api_key,
)
from spark.errors import ConfigError, SparkError
from spark.providers.probe import probe_provider
from spark.sandbox import WorkdirSandbox
from spark.web._handlers_common import _save_config
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.web.utils import HOOK_EVENTS, _probe_target_allowed, parse_mcp_servers


def _resolve_provider_fields(
    state, provider_name: str, base_url: str, model: str, api_key: str
) -> tuple[str, str | None]:
    """校验 provider 字段；openai_compat 缺钥时回落配置/env。返回 (api_key, error)。"""
    if provider_name not in {"openai_compat", "ollama", "mock"}:
        return api_key, "provider must be openai_compat, ollama or mock"
    if not base_url or not model:
        return api_key, "base_url and model are required"
    if provider_name == "openai_compat":
        api_key = api_key or state.cfg.provider.api_key or ""
        if not api_key:
            try:
                api_key = require_api_key(state.cfg) or ""
            except ConfigError:
                return api_key, "api_key is required"
    return api_key, None


def _agent_validation_error(approval: str, sandbox_mode: str, effort: str) -> str | None:
    if approval not in {"suggest", "auto-edit", "full-auto", "plan-only", "approve-plan", "manual"}:
        return "approval must be suggest, auto-edit, full-auto, plan-only, approve-plan or manual"
    if sandbox_mode not in {
        "sandbox-only",
        "workspace",
        "full-access",
        "unrestricted",
    }:
        return "sandbox_mode must be sandbox-only, workspace, full-access or unrestricted"
    if effort not in {"low", "medium", "high"}:
        return "effort must be low, medium or high"
    return None


def _parse_agent_numbers(agent_in: dict, state) -> dict[str, Any] | str:
    """解析 agent 数值字段（含下限钳制）；失败返回错误字符串。"""
    try:
        workdir_only = bool(
            agent_in.get("workdir_only", state.cfg.agent.workdir_only)
        )
        shell_timeout_sec = max(
            1,
            int(
                agent_in.get(
                    "shell_timeout_sec", state.cfg.agent.shell_timeout_sec
                )
            ),
        )
        max_tool_rounds = max(
            1,
            int(
                agent_in.get(
                    "max_tool_rounds", state.cfg.agent.max_tool_rounds
                )
            ),
        )
        max_output_chars = max(
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
    except (TypeError, ValueError):
        return "invalid agent numbers"
    return {
        "workdir_only": workdir_only,
        "shell_timeout_sec": shell_timeout_sec,
        "max_tool_rounds": max_tool_rounds,
        "max_output_chars": max_output_chars,
        "repeat_in": repeat_in,
        "token_in": token_in,
    }


def _context_updates(context_in: Any) -> tuple[dict[str, Any], str | None]:
    """Project 页上下文设置：返回 (updates, error)。非 dict 输入视为不更新。"""
    updates: dict[str, Any] = {}
    if not isinstance(context_in, dict):
        return updates, None
    try:
        if "compact_threshold" in context_in:
            threshold = float(context_in["compact_threshold"])
            if not 0.1 <= threshold <= 0.99:
                raise ValueError("compact_threshold must be within 0.1-0.99")
            updates["compact_threshold"] = threshold
        if "max_context_tokens" in context_in:
            tokens = int(context_in["max_context_tokens"])
            if not 1024 <= tokens <= 10_000_000:
                raise ValueError("max_context_tokens out of range")
            updates["max_context_tokens"] = tokens
        if "keep_recent_messages" in context_in:
            keep = int(context_in["keep_recent_messages"])
            if not 2 <= keep <= 200:
                raise ValueError("keep_recent_messages out of range")
            updates["keep_recent_messages"] = keep
        if "history_budget_chars" in context_in:
            budget = int(context_in["history_budget_chars"])
            if not 1000 <= budget <= 100_000_000:
                raise ValueError("history_budget_chars out of range")
            updates["history_budget_chars"] = budget
        if "repo_map_enabled" in context_in:
            updates["repo_map_enabled"] = bool(context_in["repo_map_enabled"])
    except (TypeError, ValueError) as exc:
        return {}, f"invalid context setting: {exc}"
    return updates, None


def _apply_mcp_servers(state, mcp_in: Any) -> tuple[list[str], str | None]:
    """更新 MCP servers 并重启；返回 (mcp_errors, fatal_error)。非 list 输入不更新。"""
    if not isinstance(mcp_in, list):
        return [], None
    try:
        servers = parse_mcp_servers(mcp_in)
    except ValueError as exc:
        return [], str(exc)
    state.cfg.mcp_servers = servers
    try:
        return state.boot_mcp(), None
    except Exception as exc:  # aqg: top-level boundary
        return [f"MCP boot failed: {exc}"], None


def _parse_hooks(hooks_in: Any) -> tuple[list[HookConfig] | None, str | None]:
    """校验 hooks 列表；返回 (parsed, error)。非 list 输入返回 (None, None) 表示不更新。"""
    if not isinstance(hooks_in, list):
        return None, None
    parsed_hooks: list[HookConfig] = []
    for item in hooks_in:
        if not isinstance(item, dict):
            return None, "invalid hook entry"
        event_name = str(item.get("event") or "").strip()
        command = str(item.get("command") or "").strip()
        if event_name not in HOOK_EVENTS or not command:
            return None, (
                f"invalid hook: event must be one of "
                f"{', '.join(HOOK_EVENTS)} and command is required"
            )
        try:
            timeout_sec = max(1, int(item.get("timeout_sec") or 15))
        except (TypeError, ValueError):
            return None, "invalid hook timeout_sec"
        raw_args = item.get("args") or []
        if not isinstance(raw_args, list):
            return None, "invalid hook args"
        parsed_hooks.append(
            HookConfig(
                event=event_name,
                command=command,
                args=[str(part) for part in raw_args],
                name=str(item.get("name") or "")[:120],
                timeout_sec=timeout_sec,
            )
        )
    return parsed_hooks, None


def _extract_request_fields(state, body: dict) -> dict[str, Any]:
    """从请求体提取本次保存的全部标量字段（缺省回落当前配置）。"""
    provider_raw = body.get("provider")
    provider_in: dict = provider_raw if isinstance(provider_raw, dict) else {}
    agent_raw = body.get("agent")
    agent_in: dict = agent_raw if isinstance(agent_raw, dict) else {}
    return {
        "provider_in": provider_in,
        "agent_in": agent_in,
        "base_url": str(
            provider_in.get("base_url")
            or body.get("base_url")
            or state.cfg.provider.base_url
        ).strip(),
        "model": str(
            provider_in.get("model")
            or body.get("model")
            or state.cfg.provider.model
        ).strip(),
        "api_key": str(
            provider_in.get("api_key") or body.get("api_key") or ""
        ).strip(),
        "provider_name": str(
            provider_in.get("name") or state.cfg.provider.name
        ).strip(),
        "approval": str(
            agent_in.get("approval") or state.cfg.agent.approval
        ).strip(),
        "sandbox_mode": str(
            agent_in.get("sandbox_mode") or state.cfg.agent.sandbox_mode
        ).strip(),
        "effort": str(
            agent_in.get("effort") or state.cfg.agent.effort
        ).strip(),
    }


def _apply_validated_settings(
    state,
    fields: dict[str, Any],
    numbers: dict[str, Any],
    display_flags: dict[str, bool],
    protected_paths_update: list[str] | None,
    system_prompt: str,
    context_updates: dict[str, Any],
) -> None:
    """全部校验通过后统一提交：任何失败都不会走到这里，不留下半改状态。"""
    cfg = state.cfg
    cfg.provider.name = cast(ProviderName, fields["provider_name"])
    cfg.provider.base_url = fields["base_url"]
    cfg.provider.model = fields["model"]
    cfg.provider.api_key = fields["api_key"] or None
    cfg.agent.approval = cast(ApprovalMode, fields["approval"])
    cfg.agent.sandbox_mode = cast(SandboxMode, fields["sandbox_mode"])
    cfg.agent.effort = cast(Literal["low", "medium", "high"], fields["effort"])
    cfg.agent.workdir_only = numbers["workdir_only"]
    cfg.agent.shell_timeout_sec = numbers["shell_timeout_sec"]
    cfg.agent.max_tool_rounds = numbers["max_tool_rounds"]
    cfg.agent.max_output_chars = numbers["max_output_chars"]
    cfg.agent.max_repeat_calls = numbers["repeat_in"]
    cfg.agent.max_turn_tokens = numbers["token_in"]
    cfg.agent.system_prompt = system_prompt
    for key, value in display_flags.items():
        setattr(cfg.agent, key, value)
    if protected_paths_update is not None:
        cfg.agent.protected_paths = protected_paths_update
    for key, value in context_updates.items():
        # Field types were validated above; setattr keeps this version-tolerant
        # if a new context field is added to the model.
        setattr(cfg.context, key, value)  # type: ignore[arg-type]
    state.sandbox = WorkdirSandbox(state.workdir, state.cfg)


def _handle_settings(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return

    fields = _extract_request_fields(state, body)
    agent_in: dict = fields["agent_in"]

    api_key, provider_error = _resolve_provider_fields(
        state, fields["provider_name"], fields["base_url"], fields["model"],
        fields["api_key"],
    )
    if provider_error:
        handler._json(400, {"error": provider_error})
        return

    agent_error = _agent_validation_error(
        fields["approval"], fields["sandbox_mode"], fields["effort"]
    )
    if agent_error:
        handler._json(400, {"error": agent_error})
        return

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
    # Deferred until every validator below passes: applying early meant a request
    # that later returned 400 had already cleared `protected_paths` and flipped
    # display flags. A rejected save must change nothing.
    protected_paths_update: list[str] | None = None
    if "protected_paths" in agent_in:
        pp = agent_in.get("protected_paths")
        if isinstance(pp, list):
            protected_paths_update = [str(x).strip() for x in pp if str(x).strip()]

    numbers = _parse_agent_numbers(agent_in, state)
    if isinstance(numbers, str):
        handler._json(400, {"error": numbers})
        return

    if "system_prompt" in agent_in:
        system_prompt = str(agent_in.get("system_prompt") or "")
    else:
        system_prompt = state.cfg.agent.system_prompt

    # ── 上下文管理（Project 页）：此前前端编辑了却从不提交，改完刷新即丢 ──
    context_updates, context_error = _context_updates(body.get("context"))
    if context_error:
        handler._json(400, {"error": context_error})
        return

    # ── 全部校验通过后统一提交：任何失败都不留下半改的内存状态 ──
    _apply_validated_settings(
        state, fields, numbers, display_flags, protected_paths_update,
        system_prompt, context_updates,
    )

    mcp_errors, mcp_error = _apply_mcp_servers(state, body.get("mcp_servers"))
    if mcp_error:
        handler._json(400, {"error": mcp_error})
        return

    parsed_hooks, hooks_error = _parse_hooks(body.get("hooks"))
    if hooks_error:
        handler._json(400, {"error": hooks_error})
        return
    if parsed_hooks is not None:
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


def _handle_test(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    base_url = str(
        body.get("base_url") or state.cfg.provider.base_url
    ).strip()
    model = str(body.get("model") or state.cfg.provider.model).strip()
    explicit_key = str(body.get("api_key") or "").strip()
    stored_key = state.cfg.provider.api_key or ""
    # The stored credential may only travel to the endpoint it belongs to. When
    # the user is testing a *different* endpoint before saving it, they must
    # bring their own key — otherwise "Test connection" could exfiltrate the
    # saved key to any host the form names.
    if not _probe_target_allowed(base_url, state.cfg.provider.base_url) and not explicit_key:
        handler._json(
            400,
            {
                "error": (
                    "切换端点测试时请一并填入该端点的 API Key；"
                    "存储的密钥只会发往当前已配置的地址"
                ),
                "needs_api_key": True,
            },
        )
        return
    api_key = explicit_key or stored_key
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
