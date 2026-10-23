"""纯文本工具（零依赖）。

单独成模块的原因：``spark.memory.store`` 的分词器被记忆与知识库共用，但从
memory 导入会连带拉起 chromadb —— 每轮对话都付一次 1.5s 的导入成本，ACP /
CLI 这种短生命周期进程尤其明显。这里只放纯函数，谁都能用。
"""

from __future__ import annotations

import re


def tokenize(text: str) -> list[str]:
    """英文按词、中文按 bigram 切分。

    中文没有空格，按字切会让 "鉴权" 与 "鉴" 同权重、按词切又需要词典；
    bigram 是零依赖下检索效果与成本最平衡的方案。
    """
    tokens: list[str] = []
    for raw in re.findall(r"[a-zA-Z0-9_]+|[一-鿿]+", text.lower()):
        if raw.isascii():
            if len(raw) > 1:
                tokens.append(raw)
        else:
            if len(raw) == 1:
                tokens.append(raw)
            else:
                tokens.extend(raw[i : i + 2] for i in range(len(raw) - 1))
    return tokens
