"""Shell 工具：异步子进程、进程组治理、超时强杀、输出截断。

不做 deny-list 假沙箱——安全性由审批门 + 保护路径 + 进程组治理承担。
取消（cancel）时对进程组发送 SIGKILL，不留孤儿进程。
"""
from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from spark2.tools.base import Tool, ToolContext

MAX_OUT = 60_000


def _kill_group(proc: Any) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


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
    env = dict(os.environ)
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=str(ctx.workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # 独立进程组，便于整体终止
        env=env,
    )
    ctx.processes[proc.pid] = proc
    partial = b""
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = out.decode("utf-8", "replace")
        err_text = err.decode("utf-8", "replace")
        if err_text.strip():
            text += "\n[stderr]\n" + err_text
    except asyncio.TimeoutError:
        _kill_group(proc)
        text = f"命令超过 {timeout}s，已强制终止（进程组）。"
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    except asyncio.CancelledError:
        _kill_group(proc)
        raise
    finally:
        ctx.processes.pop(proc.pid, None)
    code = proc.returncode if proc.returncode is not None else -1
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
                    "timeout": {"type": "integer", "description": "超时秒数，默认 120，最大 600"},
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
