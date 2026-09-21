from __future__ import annotations

import subprocess

from pydantic import BaseModel

from spark.models import ToolResult
from spark.sandbox import WorkdirSandbox

MAX_OUTPUT = 12_000

# Read-only commands the model may invoke directly.
_ALLOWED = {"status", "diff", "log", "branch"}# Mutating commands exposed as explicit tools below.
_ALLOWED_MUTATING = {"add", "commit"}

# Commands the model must never run through these tools.
BLOCKED = {"push", "pull", "fetch", "rebase", "merge", "reset", "checkout", "clean", "cherry-pick", "revert", "tag", "stash"}


class GitStatusArgs(BaseModel):
    path: str = "."


class GitDiffArgs(BaseModel):
    path: str = "."
    staged: bool = False
    ref: str | None = None


class GitLogArgs(BaseModel):
    path: str = "."
    max_count: int = 20


class GitBranchArgs(BaseModel):
    path: str = "."


class GitAddArgs(BaseModel):
    paths: list[str]
    path: str = "."


class GitCommitArgs(BaseModel):
    message: str
    path: str = "."
    add_all: bool = False


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + "\n...truncated..."


def _run_git(sandbox: WorkdirSandbox, cwd_arg: str, args: list[str], timeout: int = 30) -> ToolResult:
    """Run git inside the sandbox-resolved repo dir and capture output."""
    try:
        cwd = sandbox.resolve(cwd_arg or ".")
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not cwd.is_dir():
        return ToolResult(ok=False, payload={"error": f"directory not found: {cwd_arg}"})
    if not (cwd / ".git").exists():
        return ToolResult(ok=False, payload={"error": f"not a git repository: {cwd_arg}"})
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, payload={"error": f"git {args[0]} timed out"})
    except FileNotFoundError:
        return ToolResult(ok=False, payload={"error": "git is not installed"})
    ok = completed.returncode == 0
    payload: dict = {
        "command": f"git {' '.join(args)}",
        "exit_code": completed.returncode,
    }
    if completed.stdout:
        payload["output"] = _clip(completed.stdout.strip())
    if completed.stderr:
        payload["stderr"] = _clip(completed.stderr.strip())
    return ToolResult(ok=ok, payload=payload)


def git_status(sandbox: WorkdirSandbox, args: GitStatusArgs) -> ToolResult:
    return _run_git(sandbox, args.path, ["status", "--short", "--branch"])


def git_diff(sandbox: WorkdirSandbox, args: GitDiffArgs) -> ToolResult:
    cmd = ["diff"]
    if args.ref:
        cmd.append(args.ref)
    if args.staged:
        cmd.append("--staged")
    return _run_git(sandbox, args.path, cmd)


def git_log(sandbox: WorkdirSandbox, args: GitLogArgs) -> ToolResult:
    count = max(1, min(100, args.max_count))
    return _run_git(
        sandbox,
        args.path,
        ["log", f"--max-count={count}", "--pretty=format:%h|%an|%ar|%s"],
    )


def git_branch(sandbox: WorkdirSandbox, args: GitBranchArgs) -> ToolResult:
    return _run_git(sandbox, args.path, ["branch", "--show-current"])


def git_add(sandbox: WorkdirSandbox, args: GitAddArgs) -> ToolResult:
    paths = [p.strip() for p in args.paths if p.strip()]
    if not paths:
        return ToolResult(ok=False, payload={"error": "paths required"})
    for p in paths:
        try:
            sandbox.resolve(p)
        except Exception as exc:
            return ToolResult(ok=False, payload={"error": f"path rejected: {p}: {exc}"})
    return _run_git(sandbox, args.path, ["add", "--", *paths])


def git_commit(sandbox: WorkdirSandbox, args: GitCommitArgs) -> ToolResult:
    message = args.message.strip()
    if not message:
        return ToolResult(ok=False, payload={"error": "message required"})
    if len(message) > 2000:
        return ToolResult(ok=False, payload={"error": "message too long (max 2000 chars)"})
    if args.add_all:
        pre = _run_git(sandbox, args.path, ["add", "-A"])
        if not pre.ok:
            return pre
    # refuse an empty commit attempt: nothing staged -> git fails on its own
    return _run_git(sandbox, args.path, ["commit", "-m", message])
