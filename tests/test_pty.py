"""内置终端（PTY）测试：多 tab 管理、读写回显、目录绑定、退出标记。

注意：这些测试会真实 fork bash 子进程，仅在 Linux/macOS 下运行
（Windows 需要 winpty，P1 明确不支持，测试自动跳过）。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

ptyprocess = pytest.importorskip("ptyprocess")

from spark2.pty import PtyManager  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY 暂不支持 Windows")


def _collect(sess, loop, seconds=2.5):
    """起 reader，向 PTY 写入命令，返回期间所有输出文本。"""
    q: asyncio.Queue[str] = asyncio.Queue()
    sess.start_reader(loop, q)
    out: list[str] = []

    async def drain(sec):
        while True:
            try:
                out.append(await asyncio.wait_for(q.get(), timeout=sec))
            except asyncio.TimeoutError:
                break

    async def main():
        await asyncio.sleep(1.0)
        sess.write("echo SPARK_PTY_ECHO\n")
        await asyncio.sleep(1.0)
        sess.write("pwd\n")
        await asyncio.sleep(0.8)
        sess.write("exit\n")
        await asyncio.sleep(0.8)
        await drain(0.4)
        return "".join(out)

    return loop.run_until_complete(main())


def test_pty_read_write_and_cwd():
    tmp = Path(tempfile.mkdtemp())
    m = PtyManager()
    sess = m.get_or_create("tab_a", tmp)
    try:
        assert sess.alive
        loop = asyncio.new_event_loop()
        try:
            text = _collect(sess, loop)
            assert "SPARK_PTY_ECHO" in text          # 命令回显
            assert str(tmp) in text                   # pwd = 工作目录
            assert "\x00__SPARK_PTY_EXIT__\x00" in text  # 退出标记
        finally:
            loop.close()
    finally:
        m.close_all()


def test_pty_multi_tab_isolated():
    tmp1 = Path(tempfile.mkdtemp())
    tmp2 = Path(tempfile.mkdtemp())
    m = PtyManager()
    s1 = m.get_or_create("t1", tmp1)
    s2 = m.get_or_create("t2", tmp2)
    try:
        assert s1.tab_id == "t1" and s2.tab_id == "t2"
        assert s1.cwd == tmp1.resolve() and s2.cwd == tmp2.resolve()
        # 同 id 复用
        assert m.get_or_create("t1", tmp1) is s1
    finally:
        m.close_all()


def test_pty_write_to_closed_is_noop():
    m = PtyManager()
    sess = m.get_or_create("gone", Path(tempfile.mkdtemp()))
    m.close_all()
    assert m.write("gone", "ls\n") is False  # 已关闭 → no-op 而非抛错
