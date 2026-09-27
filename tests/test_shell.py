"""run_shell 跨平台回归：基本执行、超时强杀（进程树治理）在当前平台必须可用。

背景：create_subprocess_shell(start_new_session=True) 与 os.killpg/SIGKILL 均为
POSIX-only，Windows 上分别抛 ValueError / AttributeError——现有套件从未真实执行
过 run_shell，此盲区由本文件锁定。
"""

from __future__ import annotations

import sys
from pathlib import Path

from spark2.tools.base import ToolContext
from spark2.tools.shell import run_shell


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
