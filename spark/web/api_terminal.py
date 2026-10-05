"""终端 WebSocket / 关闭 API 路由：pty / pty-close。

从 server.py 拆分（原 create_app 内"内置终端"端点：WS 双向 PTY、关闭终端 tab，行为不变）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from spark.pty import _EXIT_MARKER as _PTY_EXIT_MARKER
from spark.pty import safe_tab_id

from .api_common import AppState, get_app_state

router = APIRouter()


def _ws_state(websocket: WebSocket) -> AppState:
    """WS 场景下从 app.state 取 AppState（WS 不支持 Depends 注入 Request）。"""
    return websocket.app.state.spark


@router.websocket("/ws/pty")
async def ws_pty(websocket: WebSocket):
    await websocket.accept()
    state = _ws_state(websocket)
    # 令牌鉴权：WS 无法自定义 header，令牌走 query 参数
    expected = (state.cfg.get("token") or "").strip()
    if expected:
        from .api_common import _safe_compare

        if not _safe_compare(websocket.query_params.get("token"), expected):
            await websocket.send_text(
                json.dumps({"type": "err", "message": "未授权：访问令牌不正确"})
            )
            await websocket.close(code=4401)
            return
    # 跨域/重绑定防护：浏览器对 WS 不施同源策略，任何网页都能连上来。
    # 校验 Origin 与本服务 Host 同源；缺失 Origin 时（native/curl）放行。
    origin = websocket.headers.get("origin")
    if origin:
        host = websocket.headers.get("host", "")
        origin_host = origin.split("//", 1)[-1].split("/", 1)[0]
        if origin_host != host:
            await websocket.send_text(
                json.dumps({"type": "err", "message": "未授权：Origin 不允许"})
            )
            await websocket.close(code=4401)
            return

    tab_id = safe_tab_id(websocket.query_params.get("tab") or "")
    sid = websocket.query_params.get("sid") or ""
    # 工作目录：优先取该会话的 workdir，其次全局配置
    cwd = state.cfg.get("workdir") or ""
    if sid:
        meta = state.store.meta(sid)
        if meta and meta.get("workdir"):
            cwd = meta["workdir"]
    if not cwd:
        await websocket.send_text(
            json.dumps({"type": "err", "message": "未设置工作目录（先到设置里指定）"})
        )
        await websocket.close(code=4400)
        return
    try:
        sess = state.pty.get_or_create(sid, tab_id, Path(cwd))
    except (RuntimeError, OSError) as exc:
        await websocket.send_text(json.dumps({"type": "err", "message": str(exc)}))
        await websocket.close(code=4400)
        return
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2048)
    sess.start_reader(asyncio.get_running_loop(), queue)

    async def send_loop() -> None:
        while True:
            data = await queue.get()
            if data == _PTY_EXIT_MARKER:
                await websocket.send_text(json.dumps({"type": "exit"}))
                return
            await websocket.send_text(json.dumps({"type": "out", "data": data}))

    async def recv_loop() -> None:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "in":
                state.pty.write(sid, sess.tab_id, str(msg.get("data", "")))
            elif t == "resize":
                state.pty.resize(
                    sid,
                    sess.tab_id,
                    int(msg.get("cols", 110)),
                    int(msg.get("rows", 28)),
                )

    try:
        await asyncio.gather(send_loop(), recv_loop())
    except WebSocketDisconnect:
        pass
    except (RuntimeError, asyncio.CancelledError):
        pass
    finally:
        # 只断开推送，不杀进程：UI 折叠/刷新后重连，终端内容仍在（持久终端）
        pass


@router.post("/api/pty/close")
async def pty_close(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    """关闭一个终端 tab（真正终止其 shell 子进程）。"""
    from .api_common import check_token
    check_token(request, state)
    body = await request.json()
    sid = str(body.get("sid") or "")
    tab_id = safe_tab_id(str(body.get("tab") or ""))
    state.pty.close(sid, tab_id)
    return {"ok": True}
