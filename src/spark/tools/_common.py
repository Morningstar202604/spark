"""tools/ 内多处复用的共享纯函数（内部模块，不对外导出）。"""

from __future__ import annotations


def clip_text(text: str, limit: int, *, keep_tail: bool = False) -> str:
    """裁剪超长文本。

    ``keep_tail=True`` 保留尾部（如后台任务日志，最新输出最重要）；
    否则保留头部并追加截断标记（如命令输出，开头的信息最重要）。
    """
    if len(text) <= limit:
        return text
    if keep_tail:
        return text[-limit:]
    return text[:limit] + "\n...truncated..."
