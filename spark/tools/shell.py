"""Shell 工具：异步子进程、进程组治理、超时强杀、输出截断。

不做 deny-list 假沙箱——安全性由审批门 + 保护路径 + 进程组治理承担。
取消（cancel）时对进程组发送 SIGKILL，不留孤儿进程。
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from typing import Any

from spark.shell_env import build_clean_env
from spark.tools.base import Tool, ToolContext

MAX_OUT = 60_000
_IS_WINDOWS = sys.platform == "win32"


def _kill_group(proc: Any) -> None:
    """跨平台终止整棵进程树，不留孤儿进程。

    POSIX：向进程组发 SIGKILL；Windows：taskkill /T 递归杀子进程。
    两条路径都以 proc.kill() 兜底，任一失败都吞掉（进程可能已退出）。
    """
    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


async def _reap(proc: Any) -> None:
    """等待已被杀的子进程退出并回收，关闭管道 transport，避免 ResourceWarning。"""
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (TimeoutError, ProcessLookupError, OSError):
        pass


def _popen_group_kwargs() -> dict:
    """把子进程放进独立进程组，便于整体终止。

    start_new_session 仅 POSIX 有效；Windows 用 CREATE_NEW_PROCESS_GROUP。
    """
    if _IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


async def run_shell(args: dict, ctx: ToolContext) -> str:
    cmd = str(args.get("command", "")).strip()
    if not cmd:
        return "错误：缺少 command"
    try:
        timeout = min(int(args.get("timeout", 120)), 600)
    except (TypeError, ValueError):
        timeout = 120  # 模型传了非数字超时 → 用默认值，不中断任务
    if timeout <= 0:
        timeout = 120
    env = build_clean_env(extra={"LANG": os.environ.get("LANG", "C.UTF-8")})
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=str(ctx.workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        **_popen_group_kwargs(),  # 独立进程组，便于整体终止
    )
    ctx.processes[proc.pid] = proc
    # 同时等待：进程退出 / 用户停止(cancel_event) / 超时，任一先到即处理。
    # 修复：此前只等 communicate，用户点"停止"无法及时终止正在运行的命令；
    # 现在 cancel_event 触发即整组强杀，不留孤儿子进程。
    wait_task = asyncio.create_task(proc.wait())
    cancel_task = asyncio.create_task(ctx.cancel_event.wait())
    code = -1
    try:
        try:
            await asyncio.wait_for(
                asyncio.wait(
                    {wait_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                ),
                timeout=timeout,
            )
        except TimeoutError:
            _kill_group(proc)
            text = f"命令超过 {timeout}s，已强制终止（进程组）。"
            await _reap(proc)
        else:
            if ctx.cancel_event.is_set():
                _kill_group(proc)
                text = "命令已取消（用户停止）。"
                await _reap(proc)
            else:
                # 进程已自然退出：读干净 stdout/stderr 缓冲
                out, err = await proc.communicate()
                text = out.decode("utf-8", "replace")
                err_text = err.decode("utf-8", "replace")
                if err_text.strip():
                    text += "\n[stderr]\n" + err_text
                code = proc.returncode if proc.returncode is not None else -1
    except asyncio.CancelledError:
        _kill_group(proc)
        await _reap(proc)
        raise
    finally:
        for t in (wait_task, cancel_task):
            if not t.done():
                t.cancel()
        ctx.processes.pop(proc.pid, None)
    if code not in (0, None):
        text = f"[退出码 {code}]\n" + text
    if len(text) > MAX_OUT:
        text = text[:MAX_OUT] + f"\n（输出过长，已截断前 {MAX_OUT} 字符）"
    return text or "（无输出）"


def build_shell_tool() -> list[Tool]:
    return [
        Tool(
            name="run_shell",
            description="在工作目录执行 shell 命令（bash）。用于运行测试、构建、安装依赖等。"
            "执行前需用户确认。不可交互（无 PTY）；常驻命令请配合 timeout 使用。",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 120，最大 600",
                    },
                },
                "required": ["command"],
            },
            category="shell",
            handler=run_shell,
            preview=lambda args, ctx: (
                f"执行命令：{args.get('command', '')}",
                f"$ {args.get('command', '')}\n（工作目录：{ctx.workdir}）",
            ),
        ),
    ]
