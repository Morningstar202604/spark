"""会话生命周期：取消 / 审批回应 / 新建·删除·切换 / 检查点回滚。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import json
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


def _session_markdown(state) -> str:
    """Render the current session as a portable Markdown transcript.

    Same shape opencode/codex use for ``/export``: a heading with the session
    metadata, then one section per turn with the user prompt, the assistant
    answer, and a compact list of the tools that ran (name + first line of the
    result, so the file stays readable).
    """
    from datetime import datetime

    session = state.store.get_session(state.session_id) or {}
    title = session.get("title") or "untitled"
    lines = [
        f"# {title}",
        "",
        f"- 会话：`{state.session_id}`",
        f"- 工作目录：`{state.workdir}`",
        f"- 模型：`{session.get('model') or state.cfg.provider.model}`",
        f"- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]
    tool_queue: list[dict] = []
    try:
        for row in state.store.list_tool_events(state.session_id):
            tool_queue.append(row)
    except Exception:  # pragma: no cover - export must not fail on a side query
        logger.debug("list_tool_events failed", exc_info=True)

    for msg in state.loop.history if state.loop else []:
        if msg.role == "user" and msg.content:
            lines += ["## 用户", "", msg.content, ""]
        elif msg.role == "assistant" and msg.content:
            lines += ["## Spark", "", msg.content, ""]
        elif msg.role == "tool":
            name = msg.name or "tool"
            # Stored tool content is the full JSON payload; show arguments plus a
            # clipped result instead of dumping escaped JSON at the reader.
            snippet = _tool_snippet(msg.content or "")
            lines += [f"**工具** `{name}`", "", f"```\n{snippet}\n```", ""]
    lines += ["---", "", f"导出工具事件 {len(tool_queue)} 条。", ""]
    return "\n".join(lines)


def _tool_snippet(raw: str, limit: int = 400) -> str:
    """Compact, readable rendering of one stored tool payload."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return raw.strip()[:limit]
    if not isinstance(payload, dict):
        return raw.strip()[:limit]
    args = payload.get("arguments")
    result = payload.get("result")
    parts: list[str] = []
    if isinstance(args, dict) and args:
        parts.append("args: " + json.dumps(args, ensure_ascii=False)[:200])
    if result is not None:
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        parts.append("result: " + text[:limit])
    return "\n".join(parts) if parts else raw.strip()[:limit]


def _handle_session_export(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    fmt = str(body.get("format") or "markdown").strip().lower()
    if fmt not in {"markdown", "md", "json"}:
        handler._json(400, {"error": "format must be markdown or json"})
        return
    if fmt == "json":
        payload = {
            "session_id": state.session_id,
            "title": (state.store.get_session(state.session_id) or {}).get("title"),
            "workdir": str(state.workdir),
            "messages": _history_messages(state),
        }
        handler._json(200, {"ok": True, "format": "json", "content": json.dumps(payload, ensure_ascii=False, indent=2)})
        return
    handler._json(
        200,
        {"ok": True, "format": "markdown", "content": _session_markdown(state)},
    )


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
