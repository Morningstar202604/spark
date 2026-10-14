"""P1-5 补充单测：tab 生命周期、目录绑定、resize、异常路径。

与 tests/test_pty.py 互补：后者覆盖契约主路径（多 tab 隔离、读写回显、
退出标记），本文件覆盖生命周期边界与 manager 方法。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import pytest

ptyprocess = pytest.importorskip("ptyprocess")
PtyManager = pytest.importorskip("spark.pty").PtyManager
from spark.pty import EXIT_MARKER, PtySession  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY 暂不支持 Windows")


def _collect_until_exit(sess: PtySession, loop: asyncio.AbstractEventLoop) -> str:
    q: asyncio.Queue[str] = asyncio.Queue()
    sess.start_reader(loop, q)
    out: list[str] = []

    async def main() -> str:
        await asyncio.sleep(1.0)
        sess.write("echo P105_LIFECYCLE_OK\n")
        await asyncio.sleep(1.0)
        sess.write("exit\n")
        # 等退出标记到来（最多 5s）
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=0.5)
            except TimeoutError:
                break
            out.append(chunk)
            if EXIT_MARKER in chunk:
                break
        return "".join(out)

    return loop.run_until_complete(main())


def test_tab_lifecycle_exit_then_recreate():
    """shell exit 后 alive=False；再次 get_or_create 同一 key 应重建新 shell。"""
    tmp = Path(tempfile.mkdtemp())
    m = PtyManager()
    loop = asyncio.new_event_loop()
    try:
        s1 = m.get_or_create("lc", "t1", tmp)
        assert s1.alive
        text = _collect_until_exit(s1, loop)
        assert "P105_LIFECYCLE_OK" in text
        assert EXIT_MARKER in text
        # 等读线程收尾，alive 应翻为 False
        deadline = time.monotonic() + 3.0
        while s1.alive and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not s1.alive
        # 同 key 重建：得到新对象（旧 shell 已退出）
        s2 = m.get_or_create("lc", "t1", tmp)
        assert s2 is not s1
        assert s2.alive
    finally:
        loop.close()
        m.close_all()


def test_resize_updates_winsize():
    tmp = Path(tempfile.mkdtemp())
    m = PtyManager()
    s = m.get_or_create("rs", "t1", tmp)
    try:
        assert s.alive
        assert s.resize(40, 120) is True
        assert s.size == (40, 120)
    finally:
        m.close_all()


def test_close_tab_removes_and_noop():
    tmp = Path(tempfile.mkdtemp())
    m = PtyManager()
    m.get_or_create("ct", "t1", tmp)
    assert m.close_tab("ct", "t1") is True
    # 已移除：write 是 False；重复 close 也是 False（幂等不抛错）
    assert m.write("ct", "t1", "ls\n") is False
    assert m.close_tab("ct", "t1") is False
    assert m.resize("ct", "t1", 20, 80) is False


def test_directory_binding_resolved():
    tmp = Path(tempfile.mkdtemp())
    m = PtyManager()
    # 带冗余 ./ 的相对形式，resolve 后应等于 tmp 绝对路径
    s = m.get_or_create("dir", "t1", tmp / ".")
    try:
        # cwd 必须是 resolve 后的绝对路径
        assert s.cwd == tmp.resolve()
        assert s.cwd.is_absolute()
    finally:
        m.close_all()


def test_two_tabs_concurrent_writes_isolated():
    """两个 tab 各自独立写；manager 路由不错位。"""
    tmp1 = Path(tempfile.mkdtemp())
    tmp2 = Path(tempfile.mkdtemp())
    m = PtyManager()
    s1 = m.get_or_create("iso", "a", tmp1)
    s2 = m.get_or_create("iso", "b", tmp2)
    loop = asyncio.new_event_loop()
    q1: asyncio.Queue[str] = asyncio.Queue()
    q2: asyncio.Queue[str] = asyncio.Queue()
    s1.start_reader(loop, q1)
    s2.start_reader(loop, q2)
    out1: list[str] = []
    out2: list[str] = []

    async def main() -> None:
        await asyncio.sleep(1.0)
        s1.write("echo FROM_TAB_A\n")
        s2.write("echo FROM_TAB_B\n")
        await asyncio.sleep(1.2)
        s1.write("exit\n")
        s2.write("exit\n")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                out1.append(await asyncio.wait_for(q1.get(), timeout=0.3))
            except TimeoutError:
                pass
            try:
                out2.append(await asyncio.wait_for(q2.get(), timeout=0.3))
            except TimeoutError:
                pass
            text1, text2 = "".join(out1), "".join(out2)
            if "FROM_TAB_A" in text1 and "FROM_TAB_B" in text2:
                break

    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
        m.close_all()
    text1, text2 = "".join(out1), "".join(out2)
    assert "FROM_TAB_A" in text1
    assert "FROM_TAB_B" in text2
    # 隔离：B 的输出不出现在 A
    assert "FROM_TAB_B" not in text1
