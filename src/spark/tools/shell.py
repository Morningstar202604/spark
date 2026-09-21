from __future__ import annotations

import shutil
import subprocess
from pydantic import BaseModel

from spark.errors import PathEscapeError
from spark.models import ToolResult
from spark.sandbox import SandboxPolicyError, WorkdirSandbox


class RunShellArgs(BaseModel):
    command: str
    cwd: str | None = None


def run_shell(sandbox: WorkdirSandbox, args: RunShellArgs, timeout_sec: int, max_output_chars: int) -> ToolResult:
    if not sandbox.shell_allowed:
        return ToolResult(ok=False, payload={"error": "run_shell is disabled: sandbox-only access mode"})
    reason = sandbox.check_shell(args.command)
    if reason:
        return ToolResult(ok=False, payload={"error": reason, "command": args.command})
    try:
        cwd = sandbox.resolve(args.cwd or ".")
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not cwd.exists() or not cwd.is_dir():
        return ToolResult(ok=False, payload={"error": f"cwd not found: {args.cwd}"})
    try:
        completed = subprocess.run(
            args.command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, payload={"error": "timeout", "command": args.command})
    stdout = _clip(completed.stdout or "", max_output_chars)
    stderr = _clip(completed.stderr or "", max_output_chars)
    return ToolResult(
        ok=completed.returncode == 0,
        payload={
            "command": args.command,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        },
    )


def detect_env(workdir) -> dict:
    """Probe the default runtime environment so it can be stated in the system prompt."""
    probes = {
        "python3": ["python3", "--version"],
        "python": ["python", "--version"],
        "pip": ["pip3", "--version"],
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
        "git": ["git", "--version"],
    }
    result: dict[str, str] = {}
    for name, cmd in probes.items():
        exe = shutil.which(cmd[0])
        if exe is None:
            continue
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=5, cwd=workdir)
            version = (completed.stdout or completed.stderr).strip().splitlines()
            result[name] = version[0][:60] if version else "installed"
        except Exception:
            result[name] = "installed"
    result["path"] = str(workdir)
    return result


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...truncated..."
