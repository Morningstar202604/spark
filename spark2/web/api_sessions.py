"""会话 API 路由：列表 / 创建 / 读取 / 重命名 / 删除 / 分叉。

从 server.py 拆分（原 create_app 内"会话"分组端点，行为不变）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .api_common import AppState, check_token, get_app_state

router = APIRouter()


@router.get("/api/sessions")
async def list_sessions(request: Request, state: AppState = Depends(get_app_state)) -> list[dict]:
    check_token(request, state)
    q = (request.query_params.get("q") or "").strip()
    rows = state.store.search(q) if q else state.store.list()
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


@router.get("/api/sessions/{sid}/context")
async def session_context(sid: str, request: Request, state: AppState = Depends(get_app_state)) -> dict:
    """会话上下文占用：估算全部消息 tokens + 上限 + 是否需压缩（对标主流 agent 的上下文水位）。"""
    check_token(request, state)
    from spark2.compaction import json_dumps
    from spark2.provider import estimate_tokens

    msgs = state.store.messages(sid)
    used = sum(estimate_tokens(json_dumps(m)) for m in msgs) if msgs else 0
    max_t = int(state.cfg.get("max_context_tokens") or 32000)
    return {"used": used, "max": max_t, "compact": used > max_t}


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


@router.post("/api/sessions/{sid}/truncate")
async def truncate_session(sid: str, request: Request, state: AppState = Depends(get_app_state)) -> dict:
    """编辑重发：截断到 message_id 之前（删除该消息及之后），返回保留消息。message_id 为空时清空整个会话。"""
    check_token(request, state)
    body = await request.json()
    mid = str(body.get("message_id") or "")
    if sid in state.running:
        raise HTTPException(status_code=409, detail="该会话正在运行，先停止再编辑")
    if sid in state.running:
        raise HTTPException(status_code=409, detail="该会话正在运行，先停止再编辑")
    if not mid:
        # 清空会话：不保留任何历史消息
        keep = state.store.clear(sid)
        if keep is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"ok": True, "messages": keep}
    keep = state.store.truncate(sid, mid)
    if keep is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"ok": True, "messages": keep}


@router.delete("/api/sessions/{sid}/messages/{message_id}")
async def delete_message(sid: str, message_id: str, request: Request, state: AppState = Depends(get_app_state)) -> dict:
    """删除单条消息（其余保持顺序）。"""
    check_token(request, state)
    rest = state.store.delete_message(sid, message_id)
    if rest is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"ok": True, "messages": rest}


@router.get("/api/sessions/{sid}/export")
async def export_session(
    sid: str, request: Request, state: AppState = Depends(get_app_state)
):
    """导出会话为 Markdown 或 JSON 文件（直接下载，不经模型）。"""
    check_token(request, state)
    fmt = (request.query_params.get("format") or "markdown").lower()
    from datetime import datetime

    meta = state.store.meta(sid)
    if not meta:
        raise HTTPException(status_code=404, detail="会话不存在")

    safe_title = "".join(
        c for c in str(meta.get("title", "session"))[:30] if c.isalnum() or c in (" ", "_", "-")
    ).replace(" ", "_") or "session"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "json":
        import json
        from fastapi.responses import JSONResponse

        msgs = state.store.messages(sid)
        out = {"meta": meta, "messages": msgs}
        return JSONResponse(
            content=out,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="session_{ts}_{sid}.json"'
                )
            },
        )

    from fastapi.responses import Response

    msgs = state.store.messages(sid)
    lines = [f"# {meta.get('title', '会话')}", ""]
    lines.append(f"- 工作目录：{meta.get('workdir', '')}")
    lines.append(f"- 模型：{meta.get('model', '')}")
    lines.append(f"- 创建：{meta.get('created', '')}")
    lines.append(f"- 导出：{datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 消息数：{len(msgs)}")
    lines.append("")
    for m in msgs:
        role = m.get("role", "system")
        content = m.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        content = str(content).strip()
        if not content:
            continue
        label = {"user": "你", "assistant": "Spark", "tool": "工具"}.get(role, role)
        lines.append(f"## {label}")
        lines.append("")
        lines.append(content)
        lines.append("")
    body = "\n".join(lines)
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="session_{ts}_{safe_title}.md"'
            )
        },
    )
