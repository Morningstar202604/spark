"""内置终端（PTY）HTTP/SSE 端点。

架构说明（诚实底线）：现有 web 服务器是标准库 ``ThreadingHTTPServer``，
不支持 WebSocket 升级，因此实时通道选用 **SSE（Server-Sent Events）** ——
与聊天流 ``/api/chat/stream`` 同一套成熟模式：

* ``POST /api/pty/open``  创建/复用标签页，返回 tab 元信息；
* ``POST /api/pty/write`` 向标签页写入按键数据；
* ``POST /api/pty/resize`` 调整终端行列（xterm fit 后同步）；
* ``POST /api/pty/close`` 结束标签页；
* ``GET  /api/pty/stream?tab_id=...`` SSE 长连接，逐帧推送 ``pty_data`` /
  ``pty_exit`` 事件。

xterm.js 在浏览器侧用 ``fetch`` + ReadableStream 消费该 SSE（与 streamChat 同构）。
"""

from __future__ import annotations

import json as _json
import logging
import queue
from pathlib import Path
from typing import Any

from spark.pty import EXIT_MARKER, get_manager
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state

logger = logging.getLogger(__name__)

_SSE_DISCONNECT_ERRORS = (
    ConnectionError,
    BrokenPipeError,
    ConnectionAbortedError,
    OSError,
)


def _sid(handler: HandlerBase) -> str:
    return str(_route_state(handler).session_id)


def handle_pty_open(handler: HandlerBase, body: dict[str, Any]) -> None:
    tab_id = str(body.get("tab_id") or "main").strip()[:64] or "main"
    cwd_raw = body.get("cwd")
    state = _route_state(handler)
    cwd = Path(str(cwd_raw)) if cwd_raw else state.workdir
    try:
        rows = int(body.get("rows") or 24)
        cols = int(body.get("cols") or 80)
    except (TypeError, ValueError):
        handler._json(400, {"error": "rows/cols must be integers"})
        return
    try:
        sess = get_manager().get_or_create(_sid(handler), tab_id, cwd, rows=rows, cols=cols)
    except ImportError as exc:
        handler._json(501, {"error": str(exc)})
        return
    handler._json(
        200,
        {
            "tab_id": sess.tab_id,
            "cwd": str(sess.cwd),
            "alive": sess.alive,
            "rows": sess.size[0],
            "cols": sess.size[1],
        },
    )


def handle_pty_write(handler: HandlerBase, body: dict[str, Any]) -> None:
    tab_id = str(body.get("tab_id") or "main")
    data = str(body.get("data") or "")
    ok = get_manager().write(_sid(handler), tab_id, data)
    handler._json(200, {"ok": ok})


def handle_pty_resize(handler: HandlerBase, body: dict[str, Any]) -> None:
    tab_id = str(body.get("tab_id") or "main")
    try:
        rows = int(body.get("rows") or 24)
        cols = int(body.get("cols") or 80)
    except (TypeError, ValueError):
        handler._json(400, {"error": "rows/cols must be int"})
        return
    ok = get_manager().resize(_sid(handler), tab_id, rows, cols)
    handler._json(200, {"ok": ok})


def handle_pty_close(handler: HandlerBase, body: dict[str, Any]) -> None:
    tab_id = str(body.get("tab_id") or "main")
    ok = get_manager().close_tab(_sid(handler), tab_id)
    handler._json(200, {"ok": ok})


def handle_pty_stream(handler: HandlerBase) -> None:
    """SSE 长连接：把指定 tab 的 PTY 增量推给浏览器。"""
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(handler.path).query)
    tab_id = (query.get("tab_id", ["main"])[0] or "main").strip()
    mgr = get_manager()
    sess = mgr.get(_sid(handler), tab_id)
    if sess is None:
        handler._json(404, {"error": f"pty tab not found: {tab_id}"})
        return

    # SSE 响应头
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    def _frame(payload: dict[str, Any]) -> None:
        blob = b"data: " + _json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n"
        handler.wfile.write(blob)
        flush = getattr(handler.wfile, "flush", None)
        if flush is not None:
            flush()

    sub: queue.Queue[str] = sess.attach()
    try:
        _frame({"type": "pty_open", "tab_id": tab_id, "cwd": str(sess.cwd)})
        while sess.alive:
            try:
                text = sub.get(timeout=15.0)
            except queue.Empty:
                # 心跳，保持长连接
                try:
                    handler.wfile.write(b": ping\n\n")
                    flush = getattr(handler.wfile, "flush", None)
                    if flush is not None:
                        flush()
                except _SSE_DISCONNECT_ERRORS:
                    break
                continue
            if not text:
                continue
            # 退出标记可能单独来，也可能粘在尾部
            if EXIT_MARKER in text:
                before, _, _after = text.partition(EXIT_MARKER)
                if before:
                    _frame({"type": "pty_data", "data": before})
                _frame({"type": "pty_exit", "tab_id": tab_id})
                break
            _frame({"type": "pty_data", "data": text})
    except _SSE_DISCONNECT_ERRORS:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("pty stream error tab=%s", tab_id)
    finally:
        sess.detach(sub)


__all__ = [
    "handle_pty_close",
    "handle_pty_open",
    "handle_pty_resize",
    "handle_pty_stream",
    "handle_pty_write",
]
