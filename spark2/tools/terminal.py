"""交互式终端工具：pty_run —— 启动持久 PTY 会话，可多轮 send_input。

对标 Claude Code 内置终端 / Cursor terminal：
- pty_run：创建交互式 shell 会话（非阻塞），返回 pty_id
- pty_send：向已有 PTY 发送输入
- pty_read：读取 PTY 输出（带缓冲）
- pty_kill：终止 PTY 会话

与 run_shell 的分工：
- run_shell：一次性命令（等结果就返回）
- pty_*：交互式进程（REPL、调试器、多步命令序列）
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid

from spark2.tools.base import Tool, ToolContext

_IS_WINDOWS = sys.platform == "win32"

# PTY 后端（与 pty.py 一致）
PtyProcess = None
if _IS_WINDOWS:
    try:
        from winpty import PtyProcess  # type: ignore
    except ImportError:
        pass
else:
    try:
        from ptyprocess import PtyProcess  # type: ignore
    except ImportError:
        pass

MAX_READ = 8_000


class _PtyEntry:
    __slots__ = ("pty", "buf", "lock")

    def __init__(self, pty: object, buf: list[str], lock: asyncio.Lock):
        self.pty = pty
        self.buf = buf
        self.lock = lock


# 全局 PTY 会话表（跨工具调用共享）
_ptys: dict[str, _PtyEntry] = {}


def _get_pty(pty_id: str) -> _PtyEntry | None:
    return _ptys.get(pty_id)


def _spawn_pty(cwd: str, cols: int = 110, rows: int = 30) -> tuple[str, _PtyEntry]:
    if PtyProcess is None:
        raise RuntimeError(
            "PTY 不可用：需要 ptyprocess（POSIX）或 pywinpty（Windows）"
        )
    pty_id = uuid.uuid4().hex[:10]
    shell_cmd = [os.environ.get("COMSPEC") or "cmd.exe"] if _IS_WINDOWS else ["bash", "-i"]
    proc = PtyProcess.spawn(
        shell_cmd,
        cwd=cwd or ".",
        dimensions=(rows, cols),
        env=dict(os.environ),
    )
    entry = _PtyEntry(proc, [], asyncio.Lock())
    _ptys[pty_id] = entry
    return pty_id, entry


def _read_buf(entry: _PtyEntry, timeout: float = 1.0) -> str:
    """非阻塞读 PTY 当前缓冲（带短超时等待新输出）。"""
    chunk: list[str] = []
    try:
        raw = entry.pty.read(4096)
        if raw:
            chunk.append(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
    except (OSError, EOFError, ValueError):
        pass
    # 合并之前未读出的缓冲
    prev = list(entry.buf)
    entry.buf.clear()
    entry.buf.extend(prev)
    entry.buf.extend(chunk)
    out = "".join(entry.buf)
    entry.buf.clear()
    if len(out) > MAX_READ:
        out = out[-MAX_READ:] + "\n…（截断，仅显示最后部分）"
    return out


async def pty_run(args: dict, ctx: ToolContext) -> str:
    cwd = str(args.get("workdir") or ctx.workdir)
    cols = int(args.get("cols") or 110)
    rows = int(args.get("rows") or 30)
    try:
        pty_id, _ = _spawn_pty(cwd, cols, rows)
    except RuntimeError as e:
        return f"错误：{e}"
    await asyncio.sleep(0.3)  # 等 shell 初始化
    return f"PTY 会话 {pty_id} 已启动（cwd={cwd}，{cols}x{rows}）。用 pty_send / pty_read / pty_kill 操作。"


async def pty_send(args: dict, ctx: ToolContext) -> str:
    pty_id = str(args.get("pty_id") or "")
    text = str(args.get("text") or "")
    entry = _get_pty(pty_id)
    if entry is None:
        return f"错误：PTY 会话 {pty_id} 不存在"
    if not text:
        return "错误：缺少 text"
    try:
        if _IS_WINDOWS:
            entry.pty.write(text)
        else:
            entry.pty.write(text.encode("utf-8"))
    except (OSError, ValueError, EOFError):
        return f"PTY 会话 {pty_id} 已结束"
    await asyncio.sleep(float(args.get("wait") or 0.5))
    out = _read_buf(entry)
    return f"已发送：{text!r}\n--- 输出 ---\n{out or '（无输出）'}"


async def pty_read(args: dict, ctx: ToolContext) -> str:
    pty_id = str(args.get("pty_id") or "")
    entry = _get_pty(pty_id)
    if entry is None:
        return f"错误：PTY 会话 {pty_id} 不存在"
    alive = True
    try:
        alive = entry.pty.isalive()
    except (OSError, ValueError):
        alive = False
    if not alive:
        return f"PTY 会话 {pty_id} 已结束（进程退出）"
    out = _read_buf(entry)
    return out or "（暂无新输出，可稍后再读）"


async def pty_kill(args: dict, ctx: ToolContext) -> str:
    pty_id = str(args.get("pty_id") or "")
    entry = _ptys.pop(pty_id, None)
    if entry is None:
        return f"PTY 会话 {pty_id} 不存在"
    try:
        entry.pty.close(force=True)
    except (OSError, ValueError, EOFError):
        try:
            if _IS_WINDOWS:
                entry.pty.terminate()
            else:
                entry.pty.kill()
        except (OSError, ValueError, EOFError):
            pass
    return f"PTY 会话 {pty_id} 已终止"


def build_terminal_tools() -> list[Tool]:
    return [
        Tool(
            name="pty_run",
            description=(
                "启动交互式终端会话（bash/REPL），返回 pty_id。"
                "用于需要多轮输入的场景（调试器、交互式脚本、REPL）。"
                "一次性命令请用 run_shell。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "workdir": {"type": "string", "description": "工作目录，默认用会话目录"},
                    "cols": {"type": "integer", "description": "终端宽度，默认 110"},
                    "rows": {"type": "integer", "description": "终端高度，默认 30"},
                },
            },
            category="shell",
            handler=pty_run,
            preview=lambda a, c: (f"启动终端", f"bash -i（cwd={a.get('workdir') or c.workdir}）"),
        ),
        Tool(
            name="pty_send",
            description="向 PTY 会话发送输入。pty_id：pty_run 返回的 id；text：要发送的内容；wait：等待输出的秒数（默认 0.5）。",
            parameters={
                "type": "object",
                "properties": {
                    "pty_id": {"type": "string"},
                    "text": {"type": "string"},
                    "wait": {"type": "number", "description": "等待输出秒数，默认 0.5"},
                },
                "required": ["pty_id", "text"],
            },
            category="shell",
            handler=pty_send,
            preview=lambda a, c: (f"终端输入：{a.get('text', '')[:80]}", f"pty {a.get('pty_id', '')}"),
        ),
        Tool(
            name="pty_read",
            description="读取 PTY 会话的输出。pty_id：pty_run 返回的 id。",
            parameters={
                "type": "object",
                "properties": {"pty_id": {"type": "string"}},
                "required": ["pty_id"],
            },
            category="read",
            handler=pty_read,
        ),
        Tool(
            name="pty_kill",
            description="终止 PTY 会话。pty_id：要终止的会话 id。",
            parameters={
                "type": "object",
                "properties": {"pty_id": {"type": "string"}},
                "required": ["pty_id"],
            },
            category="shell",
            handler=pty_kill,
            preview=lambda a, c: (f"终止终端 {a.get('pty_id', '')}", ""),
        ),
    ]
