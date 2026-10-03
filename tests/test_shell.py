"""run_shell 跨平台回归：基本执行、超时强杀（进程树治理）在当前平台必须可用。

背景：create_subprocess_shell(start_new_session=True) 与 os.killpg/SIGKILL 均为
POSIX-only，Windows 上分别抛 ValueError / AttributeError——现有套件从未真实执行
过 run_shell，此盲区由本文件锁定。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from spark.tools.base import ToolContext
from spark.tools.shell import run_shell


async def test_run_shell_echo(tmp_path: Path) -> None:
    ctx = ToolContext(workdir=tmp_path)
    out = await run_shell({"command": "echo spark-shell-ok"}, ctx)
    assert "spark-shell-ok" in out


async def test_run_shell_cwd_is_workdir(tmp_path: Path) -> None:
    script = tmp_path / "where.py"
    script.write_text("import os; print(os.getcwd())", encoding="utf-8")
    ctx = ToolContext(workdir=tmp_path)
    out = await run_shell({"command": f'"{sys.executable}" "{script}"'}, ctx)
    assert str(tmp_path).lower() in out.lower()


async def test_run_shell_timeout_kills(tmp_path: Path) -> None:
    script = tmp_path / "sleep30.py"
    script.write_text("import time; time.sleep(30)", encoding="utf-8")
    ctx = ToolContext(workdir=tmp_path)
    out = await run_shell(
        {"command": f'"{sys.executable}" "{script}"', "timeout": 1}, ctx
    )
    assert "强制终止" in out


async def test_run_shell_empty_command(tmp_path: Path) -> None:
    ctx = ToolContext(workdir=tmp_path)
    out = await run_shell({"command": "  "}, ctx)
    assert "缺少 command" in out


async def test_run_shell_cancel_stops_command_and_clears_group(tmp_path: Path) -> None:
    """用户点「停止」（cancel_event）时应及时终止命令并整组清掉子进程，不留残留。"""
    ctx = ToolContext(
        workdir=tmp_path,
        protected=[],
        processes={},
        cancel_event=asyncio.Event(),
        memory=None,
        index={},
    )
    # 模拟长任务 + 派生子进程：sleep 后台 + wait（进程组里应有 shell 和 sleep 两个进程）
    task = asyncio.create_task(
        run_shell({"command": "sleep 60 & echo started; wait", "timeout": 120}, ctx)
    )
    await asyncio.sleep(0.8)  # 让命令真正跑起来
    proc = next(iter(ctx.processes.values()))
    ctx.cancel_event.set()  # 模拟用户点「停止」
    out = await asyncio.wait_for(task, timeout=8)
    assert "取消" in out
    # 进程组应已整组清除
    try:
        os.killpg(os.getpgid(proc.pid), 0)
    except ProcessLookupError:
        pass  # 已不存在 = 通过
    else:
        raise AssertionError("取消后进程组仍存在（子进程残留）")
