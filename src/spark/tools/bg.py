from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid

from spark.models import ToolResult
from spark.sandbox import WorkdirSandbox

MAX_BUFFER = 200_000
MAX_JOBS = 8

# registry-level job store: id -> job dict
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _clip(text: str) -> str:
    if len(text) <= MAX_BUFFER:
        return text
    return text[-MAX_BUFFER:]


def _start_background(sandbox: WorkdirSandbox, command: str, cwd: str | None) -> dict:
    cwd_path = sandbox.resolve(cwd or ".")
    job_id = uuid.uuid4().hex[:8]
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    job = {
        "id": job_id,
        "command": command,
        "process": proc,
        "output": "",
        "started_at": time.time(),
        "finished_at": None,
        "exit_code": None,
    }

    def reader() -> None:
        try:
            for line in proc.stdout:  # type: ignore[attr-defined]
                with JOBS_LOCK:
                    job["output"] = _clip(job["output"] + line)
        except Exception:
            pass
        proc.wait()
        job["exit_code"] = proc.returncode
        job["finished_at"] = time.time()

    threading.Thread(target=reader, daemon=True, name=f"bg-job-{job_id}").start()
    with JOBS_LOCK:
        JOBS[job_id] = job
        # keep at most MAX_JOBS finished jobs
        finished = [jid for jid, j in JOBS.items() if j["finished_at"] is not None]
        for jid in finished[:-MAX_JOBS]:
            JOBS.pop(jid, None)
    return job


def bg_start_tool(sandbox: WorkdirSandbox, args: dict) -> ToolResult:
    command = str(args.get("command") or "").strip()
    if not command:
        return ToolResult(ok=False, payload={"error": "command required"})
    reason = sandbox.check_shell(command)
    if reason:
        return ToolResult(ok=False, payload={"error": reason, "command": command})
    try:
        cwd_path = sandbox.resolve(str(args.get("cwd") or "."))
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    job = _start_background(sandbox, command, str(args.get("cwd") or "."))
    return ToolResult(
        ok=True,
        payload={
            "job_id": job["id"],
            "command": job["command"],
            "cwd": str(cwd_path),
            "hint": "use bg_output to poll results, bg_kill to terminate",
        },
    )


def bg_output_tool(args: dict) -> ToolResult:
    job_id = str(args.get("job_id") or "").strip()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return ToolResult(ok=False, payload={"error": f"unknown job: {job_id}"})
        running = job["finished_at"] is None
        tail_limit = int(args.get("tail") or 8000)
        output = job["output"][-tail_limit:]
        info = {
            "job_id": job_id,
            "command": job["command"],
            "running": running,
            "exit_code": job["exit_code"],
            "elapsed_sec": round((job["finished_at"] or time.time()) - job["started_at"], 1),
            "output": output,
            "truncated": len(job["output"]) > len(output),
        }
    return ToolResult(ok=True, payload=info)


def _kill_job(job: dict) -> None:
    """Kill the whole process group so children of `shell -c 'cmd'` die too."""
    proc = job["process"]
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass


def bg_kill_tool(args: dict) -> ToolResult:
    job_id = str(args.get("job_id") or "").strip()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return ToolResult(ok=False, payload={"error": f"unknown job: {job_id}"})
        finished = job["finished_at"] is not None
    if finished:
        return ToolResult(ok=True, payload={"job_id": job_id, "killed": False, "reason": "already finished"})
    try:
        _kill_job(job)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    return ToolResult(ok=True, payload={"job_id": job_id, "killed": True})


def bg_list_tool() -> ToolResult:
    with JOBS_LOCK:
        jobs = [
            {
                "job_id": jid,
                "command": j["command"],
                "running": j["finished_at"] is None,
                "exit_code": j["exit_code"],
                "elapsed_sec": round((j["finished_at"] or time.time()) - j["started_at"], 1),
            }
            for jid, j in JOBS.items()
        ]
    return ToolResult(ok=True, payload={"jobs": jobs})
