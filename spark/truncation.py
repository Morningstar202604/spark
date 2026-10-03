"""输出截断判定：识别"模型回复被长度上限截断"并触发分批重试（原 loop 拆分）。

纯函数模块。网关输出长度上限会截断流式响应，且部分网关把截断报成 stop 而非 length。
判定"本轮被截断"：显式 length，或纯文本结尾没有句读收尾（仅限动过手的中途轮次）。
"""

from __future__ import annotations

# 句尾收尾字符集：尽量覆盖代码/链接/JSON 等常见收尾（反引号、}、>、*、~、%、/、\、中文冒号），
# 减少"动过手后正常回答被误判成截断"的多余重试。
_SENTENCE_END = "。．.！？!?…」』】）)]\"'`：}*~%/>\\"

_TRUNCATION_NUDGE = (
    "上一条回复在输出长度上限处被截断，且没有真正执行任何动作。"
    "请立刻调用工具继续执行任务：一次只处理一个文件，单个文件内容不要过长，"
    "长文件先写骨架再分次补充；不要用文字描述将要做什么。"
)


def _looks_truncated(
    text: str, finish_reason: str | None, mid_task: bool = False
) -> bool:
    """本轮输出是否疑似被长度上限截断。

    mid_task=True（本次请求里已经执行过工具）时，才启用"句尾不完整"的启发式；
    否则只看网关明确给的 length 信号。
    """
    if finish_reason == "length":
        return True
    if not mid_task:
        return False
    stripped = (text or "").rstrip()
    if not stripped:
        return False
    return stripped[-1] not in _SENTENCE_END
