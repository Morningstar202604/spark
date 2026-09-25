"""内置终端：持久 PTY（每 tab 一个 bash 子进程），供 Web 端实时读写。

安全边界（设计意图，写清楚避免误解）：
- 终端里的命令是**用户自己敲的**，不受审批门约束——审批门只管 Agent 的工具调用
  （run_shell 等）。与"用户在自己电脑上开终端"语义一致。
- 每个 PTY 的 cwd 固定为对应会话的工作目录，不额外做沙箱
  （信任用户本机操作，与 run_shell 一致）。

实现：ptyprocess（纯 Python、轻量、无重依赖）。Reader 用后台线程读 PTY，
通过 asyncio loop 的 call_soon_threadsafe 推入 queue，由 WebSocket 循环消费。
"""
from __future__ import annotations

import asyncio
import os
import threading
import uuid
from pathlib import Path
from typing import Callable

from ptyprocess import PtyProcess

DEFAULT_COLS = 110
DEFAULT_ROWS = 28
READ_CHUNK = 4096


class PtySession:
    """一个终端 tab：bash -i 子进程 + 输出 reader 线程。"""

    def __init__(
        self,
        tab_id: str,
        cwd: Path,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
    ) -> None:
        self.tab_id = tab_id
        self.cwd = cwd.resolve()
        self.cwd.mkdir(parents=True, exist_ok=True)
        # 品牌化 bash 提示符（"spark 路径$"），避免裸露出沙箱主机名；
        # rcfile 放系统临时目录，不污染工作目录（ls 里也看不到）
        rc = Path(os.environ.get("TMPDIR", "/tmp")) / f"spark_pty_rc_{tab_id}.sh"
        try:
            rc.write_text(
                "PS1='\\[\\e[32m\\]spark\\[\\e[0m\\] \\w\\$ '\n"
                "unset PROMPT_COMMAND\n"
                "clear\n",
                encoding="utf-8",
            )
            shell_cmd = ["bash", "--rcfile", str(rc), "-i"]
        except OSError:
            shell_cmd = ["bash", "-i"]
        self.proc = PtyProcess.spawn(
            shell_cmd,
            cwd=str(self.cwd),
            dimensions=(rows, cols),
            env=_shell_env(),
        )
        self._rc = rc
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[str] | None = None
        self._thread: threading.Thread | None = None
        self._closed = False

    @property
    def alive(self) -> bool:
        try:
            return self.proc.isalive()
        except (OSError, ValueError):
            return False

    def write(self, data: str) -> None:
        if not self.alive:
            return
        try:
            self.proc.write(data.encode("utf-8"))
        except (OSError, ValueError):
            pass

    def resize(self, cols: int, rows: int) -> None:
        if not self.alive:
            return
        try:
            self.proc.setwinsize(rows, cols)
        except (OSError, ValueError):
            pass

    def start_reader(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]) -> None:
        """启动后台 reader：读到的字节经 loop 推入 queue（线程安全）。"""
        self._loop = loop
        self._queue = queue
        self._thread = threading.Thread(target=self._read_loop, name=f"pty-{self.tab_id}", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        loop = self._loop
        queue = self._queue
        while loop is not None and queue is not None and self.alive:
            try:
                raw = self.proc.read(READ_CHUNK)
                if not raw:
                    if not self.alive:
                        break
                    continue
                data = raw.decode("utf-8", errors="replace")
            except (OSError, EOFError, ValueError):
                break
            try:
                loop.call_soon_threadsafe(queue.put_nowait, data)
            except (RuntimeError, asyncio.QueueFull):
                break
        # 进程结束：推送退出通知
        if queue is not None and loop is not None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, _EXIT_MARKER)
            except (RuntimeError, asyncio.QueueFull):
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.proc.close(force=True)
        except (OSError, ValueError):
            try:
                self.proc.kill()
            except (OSError, ValueError):
                pass
        rc = getattr(self, "_rc", None)
        if rc is not None:
            try:
                rc.unlink(missing_ok=True)
            except OSError:
                pass


_EXIT_MARKER = "\x00__SPARK_PTY_EXIT__\x00"


class PtyManager:
    """tab 级 PTY 管理：tab_id → PtySession。UI 折叠不杀进程（持久终端）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, tab_id: str, cwd: Path) -> PtySession:
        with self._lock:
            sess = self._sessions.get(tab_id)
            if sess is not None and sess.alive:
                return sess
            sess = PtySession(tab_id or uuid.uuid4().hex, cwd)
            self._sessions[sess.tab_id] = sess
            return sess

    def write(self, tab_id: str, data: str) -> bool:
        with self._lock:
            sess = self._sessions.get(tab_id)
        if sess is None or not sess.alive:
            return False
        sess.write(data)
        return True

    def resize(self, tab_id: str, cols: int, rows: int) -> None:
        with self._lock:
            sess = self._sessions.get(tab_id)
        if sess is not None:
            sess.resize(cols, rows)

    def close(self, tab_id: str) -> None:
        with self._lock:
            sess = self._sessions.pop(tab_id, None)
        if sess is not None:
            sess.close()

    def close_all(self) -> None:
        with self._lock:
            items = list(self._sessions.items())
            self._sessions.clear()
        for _, sess in items:
            sess.close()


def _shell_env() -> dict:
    env = dict(os.environ)
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("COLORTERM", "truecolor")
    env.setdefault("LANG", "C.UTF-8")
    return env
