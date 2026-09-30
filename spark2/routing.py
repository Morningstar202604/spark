"""模型路由：根据用户任务复杂度在快/强模型间选择（原 loop.route_model 拆分）。

纯函数模块，不依赖 AgentLoop；命中强任务关键词 → 走主模型，否则切快速模型。
可在设置里关闭（route_enabled=False）或自定义关键词（route_keywords）。
"""

from __future__ import annotations

import re
from typing import Any


# 多模型路由：命中关键词 → 复杂任务走主模型；否则可切快速模型
STRONG_TASK_KEYWORDS = (
    "写",
    "改",
    "修",
    "重构",
    "实现",
    "创建",
    "删除",
    "迁移",
    "优化",
    "修复",
    "报错",
    "异常",
    "部署",
    "编译",
    "运行",
    "测试",
    "接口",
    "登录",
    "配置",
    "bug",
    "fix",
    "test",
    "deploy",
    "refactor",
    "compile",
)


def _route_keywords(cfg: dict) -> tuple[str, ...]:
    """自定义强任务关键词（逗号/空格/换行分隔）；未配置 → 内置词表。"""
    raw = str(cfg.get("route_keywords") or "").strip()
    if not raw:
        return STRONG_TASK_KEYWORDS
    parts = [p.lower() for p in re.split(r"[,\uff0c\s]+", raw) if p]
    return tuple(parts) if parts else STRONG_TASK_KEYWORDS


def route_model(cfg: dict[str, Any], user_text: str) -> str | None:
    """返回本轮应使用的模型名；None = 用主模型（不路由）。

    规则（符合人的直觉）：简单问答/闲聊走快模型省时省钱；
    涉及改动、排错、搭建等动手任务一律走主模型，保证质量。
    """
    if cfg.get("route_enabled") is False:
        return None
    fast = (cfg.get("model_fast") or "").strip()
    main = cfg.get("model") or ""
    if not fast or fast == main or main == "mock":
        return None
    t = (user_text or "").lower()
    for kw in _route_keywords(cfg):
        if kw in t:
            return None
    return fast
