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

from pathlib import Path

_REDACTED = "[REDACTED]"

# 修复：改为 lazy 函数（避免容器环境下导入时固化旧值，HOME 变动/未设置时可跟到）
_HOME_CACHE: tuple[str, str] | None = None


def _get_home_prefixes() -> tuple[str, str]:
    """惰性计算 + 进程级缓存主目录前缀（unix 和反斜杠两种写法）。"""
    global _HOME_CACHE
    if _HOME_CACHE is None:
        raw = str(Path.home()).rstrip(r"\/")
        _HOME_CACHE = (raw, raw.replace("\\", "/"))
    return _HOME_CACHE


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
    # 修复：使用 lazy 函数存取主目录前缀（不再依赖导入时固化的 _HOME）
    home, home_fwd = _get_home_prefixes()
    if home and home not in ("/", "\\", ""):
        s = s.replace(home, "~")
        if home_fwd != home:
            s = s.replace(home_fwd, "~")
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


_MAX_SANITIZE_DEPTH = 3


def _sanitize_value(v, secrets: tuple[str, ...], depth: int):
    if depth > _MAX_SANITIZE_DEPTH:
        return v
    if isinstance(v, str):
        return sanitize(v, secrets)
    if isinstance(v, dict):
        return {k: _sanitize_value(x, secrets, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [_sanitize_value(x, secrets, depth + 1) for x in v]
    return v


def sanitize_sse_payload(payload: dict, secrets: tuple[str, ...] = ()) -> dict:
    """对 SSE 事件 payload 做浅递归脱敏（src 树事件协议：text/result/approval/tool/data）。

    只替换字符串值（密钥/主目录/堆栈），结构与非字符串不动；深度限 3 层。

    注意：与 secrets 是否为空无关——主目录路径与 Python 堆栈属于无条件
    脱敏项，必须走完整流程（线上本机令牌/API Key 缺失时同样生效）。
    """
    return _sanitize_value(payload, secrets, 0)
