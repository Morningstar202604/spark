"""数据浏览 API 路由：记忆 / 文件浏览 / git 检查点。

从 server.py 拆分（原 create_app 内"记忆""文件浏览""git 检查点"分组端点，行为不变）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from .api_common import AppState, check_token, get_app_state, json_body, session_workdir

router = APIRouter()


# ---------- 记忆 ----------
@router.get("/api/memory")
async def list_memory(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    workdir = str(
        request.query_params.get("workdir") or state.cfg.get("workdir") or ""
    )
    if not workdir:
        raise HTTPException(status_code=400, detail="未设置工作目录")
    return {
        "workdir": workdir,
        "count": state.memory.count(workdir),
        "items": state.memory.list(workdir, limit=200),
    }


@router.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: int, request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    if not state.memory.delete_by_id(memory_id):
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}


@router.post("/api/memory")
async def add_memory(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    """手动添加/更新一条长期记忆（Web 设置侧入口，与 remember 工具同语义）。"""
    check_token(request, state)
    body = await json_body(request)
    workdir = str(body.get("workdir") or state.cfg.get("workdir") or "").strip()
    key = str(body.get("key") or "").strip()
    value = str(body.get("value") or "").strip()
    if not workdir:
        raise HTTPException(status_code=400, detail="未设置工作目录")
    if not key or not value:
        raise HTTPException(status_code=400, detail="key 与 value 均为必填")
    if len(key) > 200 or len(value) > 5000:
        raise HTTPException(
            status_code=400, detail="key/value 超长（200/5000 字符）"
        )
    # 记忆分层（可选）：semantic=通用事实/偏好 / situational=当前任务 / episodic=事件 / procedural=流程做法
    level = str(body.get("level") or "semantic").strip().lower()
    if level not in ("semantic", "situational", "episodic", "procedural"):
        level = "semantic"
    # 写入走线程池：remember 内部可能触发同步语义嵌入（HTTP/本地模型），
    # 直接执行会阻塞单事件循环。
    await asyncio.to_thread(state.memory.remember, workdir, key, value, level)
    return {"ok": True, "count": state.memory.count(workdir)}


# ---------- 文件浏览（@ 引用 / 工作目录导航，只读列表） ----------
@router.get("/api/fs")
async def fs_list(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    sid = (request.query_params.get("sid") or "").strip()
    rel = (request.query_params.get("path") or "").strip()
    workdir = session_workdir(state, sid)
    if not workdir:
        raise HTTPException(status_code=400, detail="未设置工作目录")
    base = Path(workdir).resolve()
    try:
        target = (base / rel).resolve() if rel else base
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"路径无效：{exc}") from exc
    if target != base and base not in target.parents:
        raise HTTPException(status_code=400, detail="工作目录之外的路径")
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")
    skip = {"node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    entries: list[dict] = []
    try:
        for e in sorted(
            target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        ):
            if e.name in skip:
                continue
            if e.name.startswith(".") and rel == "":
                continue  # 顶层隐藏项（.git 等）不列，子目录允许
            entries.append({"name": e.name, "dir": e.is_dir()})
            if len(entries) >= 300:
                break
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"目录读取失败：{exc}") from exc
    rel_out = str(target.relative_to(base)) if target != base else ""
    return {"workdir": str(base), "path": rel_out, "entries": entries}


# ---------- git 检查点（UI 入口：状态 / 手动存档 / 回滚） ----------
@router.get("/api/git")
async def git_info(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    sid = (request.query_params.get("sid") or "").strip()
    workdir = session_workdir(state, sid)
    if not workdir:
        raise HTTPException(status_code=400, detail="未设置工作目录")
    from spark.tools.git import _run_git, git_available, is_git_repo

    wd = Path(workdir)
    if not git_available():
        return {"repo": False, "reason": "git 未安装"}
    if not is_git_repo(wd):
        return {"repo": False, "reason": "工作目录不是 git 仓库"}
    _, status = await _run_git(wd, "status", "--porcelain")
    _, branch = await _run_git(wd, "rev-parse", "--abbrev-ref", "HEAD")
    _, log = await _run_git(
        wd,
        "log",
        "--pretty=format:%h|%ad|%s",
        "--date=format:%Y-%m-%d %H:%M",
        "-15",
    )
    checkpoints = []
    for line in log.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            checkpoints.append(
                {"hash": parts[0], "time": parts[1], "message": parts[2]}
            )
    changes = [ln for ln in status.splitlines() if ln.strip()]
    return {
        "repo": True,
        "branch": branch.strip(),
        "changes": len(changes),
        "checkpoints": checkpoints,
    }


@router.post("/api/git/checkpoint")
async def git_checkpoint_ep(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    body = await json_body(request)
    sid = str(body.get("sid") or "")
    message = str(body.get("message") or "").strip()[:120]
    message = message or "spark 手动存档（Web）"
    workdir = session_workdir(state, sid)
    if not workdir:
        raise HTTPException(status_code=400, detail="未设置工作目录")
    from spark.tools.git import git_commit

    ok, text = await git_commit(Path(workdir), message)
    if not ok:
        raise HTTPException(status_code=400, detail=text)
    return {"ok": True, "message": text}


@router.post("/api/git/reset")
async def git_reset_ep(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    """回滚到最近检查点（破坏性操作，必须 confirm=yes）。"""
    check_token(request, state)
    body = await json_body(request)
    if str(body.get("confirm") or "") != "yes":
        raise HTTPException(
            status_code=400, detail="破坏性操作：需 confirm=yes 确认"
        )
    sid = str(body.get("sid") or "")
    workdir = session_workdir(state, sid)
    if not workdir:
        raise HTTPException(status_code=400, detail="未设置工作目录")
    from spark.tools.git import git_reset

    ok, text = await git_reset(Path(workdir))
    if not ok:
        raise HTTPException(status_code=400, detail=text)
    return {"ok": True, "message": text}
