"""内置终端：持久 PTY（每 tab 一个 shell 子进程），供 Web 端实时读写。

安全边界（设计意图，写清楚避免误解）：
- 终端里的命令是**用户自己敲的**，不受审批门约束——审批门只管 Agent 的工具调用
  （run_shell 等）。与"用户在自己电脑上开终端"语义一致。
- 每个 PTY 的 cwd 固定为对应会话的工作目录，不额外做沙箱
  （信任用户本机操作，与 run_shell 一致）。

实现：双后端——POSIX 用 ptyprocess（纯 Python、轻量），Windows 用 pywinpty
（ConPTY，Jupyter 同款成熟后端，pexpect 兼容接口；read 返回 str 而非 bytes）。
Reader 用后台线程读 PTY，通过 asyncio loop 的 call_soon_threadsafe 推入 queue，
由 WebSocket 循环消费。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable

_IS_WINDOWS = sys.platform == "win32"

# PTY 后端按平台选择；都装不上时保持模块可导入，Web 服务正常启动，
# 内置终端在连接时报明确错误（不拖垮整个应用）。
PtyProcess = None
PTY_AVAILABLE = False
if _IS_WINDOWS:
    try:
        from winpty import PtyProcess  # type: ignore  # pywinpty（ConPTY 后端）

        PTY_AVAILABLE = True
    except ImportError:
        pass
else:
    try:
        from ptyprocess import PtyProcess  # type: ignore  # ptyprocess 依赖 Unix fcntl

        PTY_AVAILABLE = True
    except ImportError:
        pass

DEFAULT_COLS = 110
DEFAULT_ROWS = 28
READ_CHUNK = 4096


def safe_tab_id(raw: str) -> str:
    """终端 tab id 消毒：只留 [A-Za-z0-9_-]（防 "../" 穿越 rc 文件名），空则自动生成。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", raw or "")[:64]
    return cleaned or uuid.uuid4().hex[:12]


class PtySession:
    """一个终端 tab：shell 子进程（POSIX bash / Windows cmd）+ 输出 reader 线程。"""

    def __init__(
        self,
        tab_id: str,
        cwd: Path,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
    ) -> None:
        if PtyProcess is None:
            raise RuntimeError(
                "内置终端不可用：缺少 PTY 后端（POSIX 需要 ptyprocess；Windows 需要 pip install pywinpty）"
            )
        self.tab_id = tab_id
        self.cwd = cwd.resolve()
        self.cwd.mkdir(parents=True, exist_ok=True)
        self._rc: Path | None = None
        if _IS_WINDOWS:
            # ConPTY：用系统 shell（ComSpec → cmd.exe），无 bash rcfile 定制
            shell_cmd = [os.environ.get("COMSPEC") or "cmd.exe"]
        else:
            # 品牌化 bash 提示符（"spark 路径$"），避免裸露出沙箱主机名；
            # rcfile 用 mkstemp 生成随机路径（0600），防多用户机器上的预创建覆盖；
            # 不污染工作目录（ls 里也看不到）
            rc = None
            try:
                tmp_dir = os.environ.get("TMPDIR") or "/tmp"
                fd, rc_path = tempfile.mkstemp(
                    prefix="spark_pty_", suffix=".sh", dir=tmp_dir
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(
                        "PS1='\\[\\e[32m\\]spark\\[\\e[0m\\] \\w\\$ '\n"
                        "unset PROMPT_COMMAND\n"
                        "clear\n"
                    )
                rc = Path(rc_path)
                shell_cmd = ["bash", "--rcfile", str(rc), "-i"]
            except OSError:
                shell_cmd = ["bash", "-i"]
            self._rc = rc
        self.proc = PtyProcess.spawn(
            shell_cmd,
            cwd=str(self.cwd),
            dimensions=(rows, cols),
            env=_shell_env(),
        )
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
            if _IS_WINDOWS:
                self.proc.write(data)  # pywinpty 收 str
            else:
                self.proc.write(data.encode("utf-8"))  # ptyprocess 收 bytes
        except (OSError, ValueError, EOFError):
            pass

    def resize(self, cols: int, rows: int) -> None:
        if not self.alive:
            return
        try:
            self.proc.setwinsize(rows, cols)
        except (OSError, ValueError, EOFError):
            pass

    def start_reader(
        self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]
    ) -> None:
        """启动后台 reader：读到的字节经 loop 推入 queue（线程安全）。"""
        self._loop = loop
        self._queue = queue
        self._thread = threading.Thread(
            target=self._read_loop, name=f"pty-{self.tab_id}", daemon=True
        )
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
                # pywinpty read 返回 str；ptyprocess 返回 bytes
                data = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
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
        except (OSError, ValueError, EOFError):
            try:
                if _IS_WINDOWS:
                    self.proc.terminate()
                else:
                    self.proc.kill()
            except (OSError, ValueError, EOFError):
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
