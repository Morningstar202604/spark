"""检查点：用 git 做自动快照与回滚（不造轮子，直接用系统 git）。

- 工作目录是 git 仓库时：写文件前自动提交一个检查点；reset 工具回滚到该提交。
- 不是仓库时：自动跳过（不擅自 git init 用户的目录），明确提示。
- 回滚只用 `git reset --hard HEAD`（不跑 clean），避免误删用户未跟踪的文件。
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from spark2.tools.base import Tool, ToolContext


def git_available() -> bool:
    return shutil.which("git") is not None


def is_git_repo(workdir: Path) -> bool:
    if not git_available():
        return False
    return (workdir / ".git").exists()


async def _run_git(workdir: Path, *args: str, timeout: float = 30.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(workdir),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, OSError) as e:
        return -1, f"git 执行失败：{e}"
    text = (out + err).decode("utf-8", "replace").strip()
    return proc.returncode, text


async def git_commit(workdir: Path, message: str) -> tuple[bool, str]:
    """提交当前全部改动为检查点。返回 (是否产生提交, 说明)。"""
    if not is_git_repo(workdir):
        return False, "当前目录不是 git 仓库（检查点未启用，可用 git init 开启）"
    rc, text = await _run_git(workdir, "add", "-A")
    if rc != 0:
        return False, text
    rc, text = await _run_git(workdir, "commit", "-m", message)
    if rc == 0:
        return True, f"检查点已创建：{message}"
    # rc==1 且无改动是正常情况
    if "nothing to commit" in text or "no changes added" in text:
        return False, "无改动，跳过检查点"
    return False, text


async def git_reset(workdir: Path) -> tuple[bool, str]:
    """回滚到最近一次提交（保留未跟踪文件）。"""
    if not is_git_repo(workdir):
        return False, "当前目录不是 git 仓库"
    rc, text = await _run_git(workdir, "reset", "--hard", "HEAD")
    if rc != 0:
        return False, text
    return True, "已回滚到最近一次检查点（未跟踪文件保留）"


def build_checkpoint_tools() -> list[Tool]:
    return [
        Tool(
            name="checkpoint",
            description="把当前全部改动提交为一个检查点（git commit）。用于用户要求'先存个档'或重大改动前。",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string", "description": "检查点说明，默认自动生成"}},
            },
            category="system",
            handler=checkpoint,
        ),
        Tool(
            name="reset",
            description="回滚工作目录到最近一次检查点（git reset --hard HEAD）。会丢弃已跟踪文件的改动，未跟踪的新文件保留。需用户确认。",
            parameters={"type": "object", "properties": {}},
            category="shell",
            handler=reset,
            preview=lambda args, ctx: ("回滚到最近一次检查点", "git reset --hard HEAD（未跟踪文件保留）"),
        ),
    ]


async def checkpoint(args: dict, ctx: ToolContext) -> str:
    msg = str(args.get("message", "")).strip() or "spark2 检查点"
    ok, text = await git_commit(ctx.workdir, msg)
    return text


async def reset(args: dict, ctx: ToolContext) -> str:
    ok, text = await git_reset(ctx.workdir)
    return text
