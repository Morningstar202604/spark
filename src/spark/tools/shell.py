from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from pydantic import BaseModel

from spark.errors import PathEscapeError
from spark.models import ToolResult
from spark.sandbox import SandboxPolicyError, WorkdirSandbox


MAX_SHELL_OUTPUT_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 8192


class RunShellArgs(BaseModel):
    command: str
    cwd: str | None = None


def run_shell(
    sandbox: WorkdirSandbox, args: RunShellArgs, timeout_sec: int, max_output_chars: int
) -> ToolResult:
    if not sandbox.shell_allowed:
        return ToolResult(
            ok=False,
            payload={"error": "run_shell is disabled: sandbox-only access mode"},
        )
    reason = sandbox.check_shell(args.command)
    if reason:
        return ToolResult(ok=False, payload={"error": reason, "command": args.command})
    try:
        cwd = sandbox.resolve(args.cwd or ".")
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not cwd.exists() or not cwd.is_dir():
        return ToolResult(ok=False, payload={"error": f"cwd not found: {args.cwd}"})
    output_limit = max(0, int(max_output_chars))
    byte_limit = min(max(0, int(MAX_SHELL_OUTPUT_BYTES)), output_limit * 4)
    process_kwargs: dict = {
        "shell": True,
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_kwargs["start_new_session"] = True
    process = subprocess.Popen(args.command, **process_kwargs)
    stdout_result: list[tuple[bytes, bool]] = []
    stderr_result: list[tuple[bytes, bool]] = []

    def read_stdout() -> None:
        try:
            stdout_result.append(_read_bounded(process.stdout, byte_limit))
        except (OSError, ValueError):
            stdout_result.append((b"", False))

    def read_stderr() -> None:
        try:
            stderr_result.append(_read_bounded(process.stderr, byte_limit))
        except (OSError, ValueError):
            stderr_result.append((b"", False))

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
    for thread in (stdout_thread, stderr_thread):
        thread.join(timeout=1)
    if timed_out or stdout_thread.is_alive() or stderr_thread.is_alive():
        _close_process_streams(process)
    if timed_out:
        return ToolResult(
            ok=False, payload={"error": "timeout", "command": args.command}
        )
    stdout_bytes, stdout_was_truncated = (
        stdout_result[0] if stdout_result else (b"", False)
    )
    stderr_bytes, stderr_was_truncated = (
        stderr_result[0] if stderr_result else (b"", False)
    )
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    stdout_was_clipped = len(stdout_text) > output_limit
    stderr_was_clipped = len(stderr_text) > output_limit
    stdout = _clip(stdout_text, output_limit)
    stderr = _clip(stderr_text, output_limit)
    stdout_truncated = stdout_was_truncated or stdout_was_clipped
    stderr_truncated = stderr_was_truncated or stderr_was_clipped
    return ToolResult(
        ok=process.returncode == 0,
        payload={
            "command": args.command,
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": stdout_truncated or stderr_truncated,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        },
    )


def _read_bounded(stream, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    remaining = max(0, limit)
    truncated = False
    while True:
        chunk = stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        if len(chunk) > remaining:
            truncated = True
        if remaining:
            kept = chunk[:remaining]
            chunks.append(kept)
            remaining -= len(kept)
    return b"".join(chunks), truncated


def _close_process_streams(process: subprocess.Popen) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _kill_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            process_group = os.getpgid(process.pid)
        except OSError:
            process_group = None
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except OSError:
                pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


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
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5, cwd=workdir
            )
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
