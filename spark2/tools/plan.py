"""计划工具：update_plan —— 让 Agent 动手前先给计划，交互上对应前端"计划卡"。

属于 system 类：不触发审批，由循环直接拦截为 plan 事件。
"""
from __future__ import annotations

from spark2.tools.base import Tool, ToolContext


async def update_plan(args: dict, ctx: ToolContext) -> str:
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        return "错误：steps 需为非空数组"
    return f"计划已更新：{len(steps)} 步"


def build_plan_tool() -> list[Tool]:
    return [
        Tool(
            name="update_plan",
            description="动手前先调用：给用户展示接下来的执行计划。steps：字符串步骤数组，每步一句话。",
            parameters={
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "执行计划步骤（2~5 步为宜）",
                    }
                },
                "required": ["steps"],
            },
            category="system",
            handler=update_plan,
        ),
    ]
