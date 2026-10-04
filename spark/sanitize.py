"""输出门禁（P2 安全）：对外展示/外发前统一脱敏，防止内部信息泄露。

触发时机：SSE 事件出口（面向前端）与对外 API 错误文案。工具内部回填
（模型上下文）仍保留原始信息——模型需要真实路径才能操作文件，因此门禁
只作用于"面向用户/外部"的出口，不碰模型侧消息。

脱敏项（按优先级）：
1. 密钥 / token（调用方传入已知的 secret 值）
2. 用户主目录绝对路径（C:\\Users\\xxx → ~，隐藏盘符与用户名）
3. Python 堆栈（Traceback ... 块折叠为 [REDACTED] + 末行异常）
设计原则：只做"缩小/折叠"，绝不误改正常业务文本；无法 100% 覆盖时宁可保守。
"""

from __future__ import annotations

import re
from pathlib import Path

_REDACTED = "[REDACTED]"

# 缓存一次（进程内主目录不变）
_HOME = str(Path.home()).rstrip("\\/")
_HOME_FWD = _HOME.replace("\\", "/")


def _strip_traceback(text: str) -> str:
    """把 Python 堆栈块折叠为 '[REDACTED] <末行异常>'；无堆栈则原样返回。"""
    if "Traceback" not in text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if "Traceback (most recent call last):" in lines[i]:
            # 跳过紧随其后的栈帧行：File "..." 行 + 其后的缩进源码行
            j = i + 1
            while j < n:
                ln = lines[j]
                if ln.startswith("  File ") or (
                    ln.startswith((" ", "\t")) and not ln.lstrip().startswith(("File ", "line "))
                ):
                    j += 1
                    continue
                break
            if j < n and lines[j].strip():
                out.append(f"{_REDACTED} {lines[j].strip()}")
                i = j + 1
            else:
                out.append(_REDACTED)
                i = j if j < n else n
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def sanitize(text: str | None, secrets: tuple[str, ...] = ()) -> str:
    """对外文本脱敏：密钥 → 主目录 → 堆栈。输入为空返回空串。"""
    if not text:
        return ""
    s = text
    for sec in secrets:
        if sec and isinstance(sec, str) and len(sec) >= 8:
            s = s.replace(sec, _REDACTED)
    if _HOME and _HOME not in ("/", "\\", ""):
        s = s.replace(_HOME, "~")
        if _HOME_FWD != _HOME:
            s = s.replace(_HOME_FWD, "~")
    return _strip_traceback(s)


def sanitize_event(ev: dict, secrets: tuple[str, ...] = ()) -> dict:
    """对 SSE 事件中"展示给用户"的字段脱敏（只读，返回脱敏后的副本）。"""
    t = ev.get("type")
    if t == "text" or t == "reasoning":
        d = ev.get("delta")
        if d:
            return {**ev, "delta": sanitize(d, secrets)}
        return ev
    if t == "error":
        m = ev.get("message")
        if m:
            return {**ev, "message": sanitize(m, secrets)}
        return ev
    if t == "tool_result":
        o = ev.get("output", "")
        so = sanitize(o, secrets)
        return {**ev, "output": so} if so != o else ev
    if t == "tool_start":
        a = ev.get("args_summary")
        if a:
            return {**ev, "args_summary": sanitize(a, secrets)}
        return ev
    return ev