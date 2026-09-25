"""工具基座：统一的工具描述（schema）+ 执行上下文。

每个工具 = 名称 + 描述 + JSON Schema 参数 + 类别 + 处理器 + 审批预览。
类别只用于审批策略：read / write / shell / system。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class ToolContext:
    workdir: Path
    protected: list[Path] = field(default_factory=list)
    processes: dict[int, Any] = field(default_factory=dict)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    memory: Any = None  # MemoryStore（注入式，None 时记忆工具返回"未启用"）
    index: Any = None  # 代码索引状态：{workdir: {index, loaded_at}}（惰性构建）

    def check_cancelled(self) -> bool:
        return self.cancel_event.is_set()


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    category: str  # read | write | shell | system
    handler: Callable[[dict, ToolContext], Awaitable[str]]
    preview: Callable[[dict, ToolContext], tuple[str, str]] | None = None

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def resolve_path(raw: str, workdir: Path) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = workdir / p
    return p.resolve()


def is_within(p: Path, base: Path) -> bool:
    try:
        p.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False
