"""会话 API 路由：列表 / 创建 / 读取 / 重命名 / 删除 / 分叉。

从 server.py 拆分（原 create_app 内"会话"分组端点，行为不变）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from fastapi import Depends

from .api_common import AppState, check_token, get_app_state

router = APIRouter()


@router.get("/api/sessions")
async def list_sessions(request: Request, state: AppState = Depends(get_app_state)) -> list[dict]:
    check_token(request, state)
    rows = state.store.list()
    for r in rows:
        r["running"] = r.get("id") in state.running
    return rows


@router.post("/api/sessions/{sid}/fork")
async def fork_session(sid: str, request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    meta = state.store.fork(sid)
    if not meta:
        raise HTTPException(status_code=404, detail="会话不存在")
    return meta


@router.get("/api/sessions/{sid}")
async def get_session(sid: str, request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    meta = state.store.meta(sid)
    if not meta:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"meta": meta, "messages": state.store.messages(sid)}


@router.post("/api/sessions")
async def create_session(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    body = await request.json()
    workdir = str(body.get("workdir") or state.cfg.get("workdir") or "").strip()
    if not workdir:
        raise HTTPException(
            status_code=400, detail="请先在工作目录设置里指定项目路径"
        )
    meta = state.store.create(
        workdir, str(body.get("model") or state.cfg.get("model") or "")
    )
    return meta


@router.delete("/api/sessions/{sid}")
async def delete_session(sid: str, request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    if sid in state.running:
        raise HTTPException(status_code=409, detail="该会话正在运行，先停止再删除")
    if not state.store.delete(sid):
        raise HTTPException(status_code=404, detail="会话不存在")
    state.gates.pop(sid, None)
    return {"ok": True}


@router.patch("/api/sessions/{sid}")
async def rename_session(sid: str, request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    body = await request.json()
    title = str(body.get("title") or "").strip()[:60]
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    if not state.store.rename(sid, title):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "title": title}
