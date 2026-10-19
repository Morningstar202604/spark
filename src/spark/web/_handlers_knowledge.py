"""项目知识：AGENTS.md 编辑 + 长期记忆管理（/api/agents_md, /api/memory）。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.web.utils import _safe_agents_md_name


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
