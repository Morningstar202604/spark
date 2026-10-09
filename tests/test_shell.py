"""run_shell 跨平台回归：基本执行、超时强杀、取消清树在当前平台必须真实可用。

背景：create_subprocess_shell(start_new_session=True) 与 os.killpg/SIGKILL 均为
POSIX-only，Windows 上分别抛 ValueError / AttributeError——现有套件从未真实执行
过 run_shell，此盲区由本文件锁定。取消清树的断言不依赖 killpg：改为观察「孙进程
心跳文件是否停更」，因此在 POSIX 与 Windows 上都是真实有效的进程树回归。
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


# 孙进程：每 0.2s 往心跳文件追加一行，用来证明"它是否还活着"
_CHILD_SRC = """
import pathlib, sys, time
log = pathlib.Path(sys.argv[1])
while True:
    with log.open("a", encoding="utf-8") as f:
        f.write(repr(time.time()) + chr(10))
    time.sleep(0.2)
"""

# 父进程：拉起孙进程后自身挂起，制造"shell → 父 → 孙"三层进程树
_PARENT_SRC = """
import pathlib, subprocess, sys, time
here = pathlib.Path(__file__).parent
child = pathlib.Path(sys.argv[2])
proc = subprocess.Popen([sys.executable, str(child), sys.argv[1]])
print("started", proc.pid, flush=True)
while True:
    time.sleep(0.5)
"""


async def test_run_shell_cancel_kills_whole_process_tree(tmp_path: Path) -> None:
    """用户点「停止」（cancel_event）后，整棵进程树都必须终止——不留继续写文件的孤儿孙进程。

    跨平台同一套断言：shell → python(父) → python(孙，每 0.2s 落一行心跳)。
    POSIX 靠 killpg（start_new_session 让整树同组），Windows 靠 taskkill /F /T。
    任一层漏杀，取消后心跳文件仍会继续增长。
    """
    log = tmp_path / "heartbeat.log"
    parent = tmp_path / "parent.py"
    child = tmp_path / "child.py"
    parent.write_text(_PARENT_SRC, encoding="utf-8")
    child.write_text(_CHILD_SRC, encoding="utf-8")
    ctx = ToolContext(
        workdir=tmp_path,
        protected=[],
        processes={},
        cancel_event=asyncio.Event(),
        memory=None,
        index={},
    )
    command = f'"{sys.executable}" "{parent}" "{log}" "{child}"'
    task = asyncio.create_task(run_shell({"command": command, "timeout": 120}, ctx))

    # 等心跳出现，确认孙进程确实活着
    for _ in range(40):
        if log.exists() and log.stat().st_size > 0:
            break
        await asyncio.sleep(0.2)
    assert log.exists() and log.stat().st_size > 0, "孙进程心跳未启动，测试前置条件失败"

    proc = next(iter(ctx.processes.values()))
    ctx.cancel_event.set()  # 模拟用户点「停止」
    out = await asyncio.wait_for(task, timeout=10)
    assert "取消" in out
    assert not ctx.processes, "取消后 ctx.processes 仍残留进程句柄"

    # 取消后再观察 1.2s：树被真杀干净的话心跳必然停更
    size_before = log.stat().st_size
    await asyncio.sleep(1.2)
    assert log.stat().st_size == size_before, "取消后孙进程仍在写心跳——进程树未清干净（孤儿残留）"
    if os.name != "nt":
        try:
            os.killpg(os.getpgid(proc.pid), 0)
        except ProcessLookupError:
            pass  # 已不存在 = 通过
        else:
            raise AssertionError("取消后进程组仍存在（子进程残留）")
