"""检查点：用 git 做自动快照与回滚（不造轮子，直接用系统 git）。

- 工作目录是 git 仓库时：写文件前自动提交一个检查点；reset 工具回滚到该提交。
- 不是仓库时：自动跳过（不擅自 git init 用户的目录），明确提示。
- 回滚只用 `git reset --hard HEAD`（不跑 clean），避免误删用户未跟踪的文件。
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from spark.tools.base import Tool, ToolContext


def git_available() -> bool:
    return shutil.which("git") is not None


def is_git_repo(workdir: Path) -> bool:
    if not git_available():
        return False
    return (workdir / ".git").exists()


# Spark 自身状态目录 + 工具运行必然产生的构建产物。
# 自动检查点用 `git add -A`，不排除就会把这些全提交进用户仓库；
# 且已跟踪的文件会让 `git reset --hard` 在 Windows 上因文件被占用而失败（回滚直接失效）。
SPARK_EXCLUDES = (
    ".spark-home/",  # Spark 自身状态（配置/会话/记忆/日志，SPARK_HOME 指到仓库内时会出现）
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "node_modules/",
    "*.pyc",
)


async def ensure_local_excludes(workdir: Path) -> tuple[list[str], list[str]]:
    """把上述条目写进仓库本地 .git/info/exclude，并解跟踪已提交的条目。

    只用 .git/info/exclude（本地生效、不改用户的 .gitignore、不产生提交）。
    解跟踪用 `git rm --cached`：只动索引，**不删除工作区文件**。
    返回 (新增忽略项, 已解跟踪项)。
    """
    if not is_git_repo(workdir):
        return [], []
    git_path = workdir / ".git"
    if not git_path.is_dir():  # worktree / 子模块里 .git 是文件，交给 git 自身处理
        return [], []
    exclude = git_path / "info" / "exclude"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            exclude.read_text(encoding="utf-8", errors="replace")
            if exclude.exists()
            else ""
        )
    except OSError:
        return [], []
    added = [p for p in SPARK_EXCLUDES if p not in existing]
    if added:
        block = (
            "\n# spark 自动添加：工具运行产物，不要提交进仓库\n"
            + "\n".join(added)
            + "\n"
        )
        try:
            with open(exclude, "a", encoding="utf-8") as fh:
                fh.write(block)
        except OSError:
            return [], []
    untracked: list[str] = []
    for pattern in added:
        rc, _ = await _run_git(workdir, "ls-files", "--error-unmatch", "--", pattern)
        if rc != 0:
            continue
        rc_rm, _ = await _run_git(
            workdir, "rm", "-r", "--cached", "-q", "-f", "--", pattern
        )
        if rc_rm == 0:
            untracked.append(pattern)
    return added, untracked


async def _run_git(workdir: Path, *args: str, timeout: float = 30.0) -> tuple[int | None, str]:
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
    except (TimeoutError, OSError) as e:
        return -1, f"git 执行失败：{e}"
    text = (out + err).decode("utf-8", "replace").strip()
    return proc.returncode, text


async def git_commit(workdir: Path, message: str) -> tuple[bool, str]:
    """提交当前全部改动为检查点。返回 (是否产生提交, 说明)。"""
    if not is_git_repo(workdir):
        return False, "当前目录不是 git 仓库（检查点未启用，可用 git init 开启）"
    ignored, untracked = await ensure_local_excludes(workdir)
    note = ""
    if ignored or untracked:
        note = f"（已排除运行产物：{'、'.join(ignored)}"
        if untracked:
            note += f"；已从版本控制移除：{'、'.join(untracked)}"
        note += "）"
    rc, text = await _run_git(workdir, "add", "-A")
    if rc != 0:
        return False, text
    commit_msg = message
    if untracked:
        # 本次提交的实质内容是"取消跟踪运行产物"，写进提交信息，避免 git log 误导
        commit_msg = (
            f"{message}\n\n"
            f"（spark 自动从版本控制移除运行产物：{'、'.join(untracked)}；文件仍保留在工作区）"
        )
    rc, text = await _run_git(workdir, "commit", "-m", commit_msg)
    if rc == 0:
        return True, f"检查点已创建：{message}{note}"
    # rc==1 且无改动是正常情况
    if "nothing to commit" in text or "no changes added" in text:
        return False, f"无改动，跳过检查点{note}"
    return False, text + note


async def git_reset(workdir: Path) -> tuple[bool, str]:
    """回滚到最近一次提交（保留未跟踪文件）。"""
    if not is_git_repo(workdir):
        return False, "当前目录不是 git 仓库"
    # 先确保运行产物已解跟踪：否则 reset --hard 会去覆盖被占用的 db/二进制而整体失败
    await ensure_local_excludes(workdir)
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
    msg = str(args.get("message", "")).strip() or "spark 检查点"
    ok, text = await git_commit(ctx.workdir, msg)
    return text


async def reset(args: dict, ctx: ToolContext) -> str:
    ok, text = await git_reset(ctx.workdir)
    return text
