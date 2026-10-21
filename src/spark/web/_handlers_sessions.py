"""会话生命周期：取消 / 审批回应 / 新建·删除·切换 / 检查点回滚。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import logging
from typing import cast

from spark.models import ApprovalAction
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state

logger = logging.getLogger(__name__)


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
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )


def _handle_sessions_rename(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    session_id = str(body.get("session_id") or "").strip()
    title = str(body.get("title") or "").strip()
    if not title:
        handler._json(400, {"error": "title required"})
        return
    if len(title) > 200:
        handler._json(400, {"error": "title too long (max 200)"})
        return
    row = state.store.get_session(session_id)
    if not row or row.get("workdir") != str(state.workdir):
        handler._json(404, {"error": "unknown session"})
        return
    state.store.set_title(session_id, title)
    handler._json(200, {"ok": True, "status": state.status()})


def _handle_sessions_fork(handler: HandlerBase, body: dict) -> None:
    """Fork a session: copy every message into a new session and switch to it.

    The UI needs a branch point ("继续从这条消息往下试另一个方向") without the
    user hand-rolling a new chat and re-pasting context.
    """
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    session_id = str(body.get("session_id") or state.session_id).strip()
    row = state.store.get_session(session_id)
    if not row or row.get("workdir") != str(state.workdir):
        handler._json(404, {"error": "unknown session"})
        return
    forked = state.store.fork(session_id)
    if forked is None:
        handler._json(404, {"error": "unknown session"})
        return
    with state.turn_lock:
        state.rebuild_loop(session_id=str(forked["id"]))
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )


def _history_messages(state) -> list[dict]:
    """Extract serializable message list from current loop history.

    Each row carries its database id when in-memory history and stored rows are
    index aligned, so the UI can delete or rewind from an exact message instead
    of guessing an index. Ids are omitted (never fabricated) when compaction or
    a race left the two lists out of sync.
    """
    messages: list[dict] = []
    if state.loop is None:
        return messages
    history = list(state.loop.history or [])
    ids: list[int] = []
    try:
        ids = state.store.load_message_ids(state.session_id)
    except Exception:  # pragma: no cover - ids are a convenience, never a blocker
        logger.debug("load_message_ids failed", exc_info=True)
    aligned = len(ids) == len(history)
    for index, msg in enumerate(history):
        row_id = ids[index] if aligned else None
        if msg.role in {"user", "assistant"} and msg.content:
            entry: dict = {"role": msg.role, "content": msg.content}
            if row_id is not None:
                entry["id"] = row_id
            if msg.role == "user" and msg.images:
                entry["images"] = [
                    f"data:{i.media_type};base64,{i.data}" for i in msg.images
                ]
            messages.append(entry)
        elif msg.role == "tool":
            entry = {
                "role": "tool",
                "name": msg.name or "",
                "content": msg.content or "",
            }
            if row_id is not None:
                entry["id"] = row_id
            messages.append(entry)
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


def _handle_messages_delete(handler: HandlerBase, body: dict) -> None:
    """Delete exactly one message (and its tool events) from the current session."""
    state = _route_state(handler)
    message_id = body.get("message_id")
    if not isinstance(message_id, int) or message_id <= 0:
        handler._json(400, {"error": "message_id (int) required"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return
    with state.turn_lock:
        remaining = state.store.delete_message(state.session_id, message_id)
        if remaining is None:
            handler._json(404, {"error": f"message not found: {message_id}"})
            return
        state.loop._replace_history(remaining)
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )


def _handle_messages_truncate(handler: HandlerBase, body: dict) -> None:
    """P2 双粒度时间机器：消息级 undo——删除指定消息及其后全部消息。

    与检查点回滚互补：检查点恢复文件快照 + 对话；消息 undo 只回退对话
    （文件保持现状），适合"对话跑偏但改动仍想保留"的场景。
    """
    state = _route_state(handler)
    message_id = body.get("message_id")
    if not isinstance(message_id, int) or message_id <= 0:
        handler._json(400, {"error": "message_id (int) required"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return
    with state.turn_lock:
        remaining = state.store.truncate_messages(state.session_id, message_id)
        if remaining is None:
            handler._json(404, {"error": f"message not found: {message_id}"})
            return
        state.loop._replace_history(remaining)
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )
