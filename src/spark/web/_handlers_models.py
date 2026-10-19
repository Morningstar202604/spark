"""模型档案 CRUD + 一键切换（/api/models/save|delete|activate）。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import uuid
from typing import cast

from spark.config import ProviderName
from spark.errors import SparkError
from spark.web._handlers_common import _save_config
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state


def _handle_models_save(handler: HandlerBase, body: dict) -> None:
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
