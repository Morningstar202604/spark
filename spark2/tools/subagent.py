"""spawn_subagent 工具：主 Agent 派生子 Agent 的入口（实际执行由 loop 特判嵌入事件流）。

该工具的 handler 只作兜底（正常情况下 loop._execute_call 会先特判拦截），
保证即使绕过特判也不会真去"创建子 Agent"。
"""
from __future__ import annotations

from spark2.tools.base import Tool


def _not_directly_callable(args, ctx):
    return "错误：spawn_subagent 由主循环调度，不能直接调用"


def build_subagent_tool() -> Tool:
    return Tool(
        name="spawn_subagent",
        description=(
            "派生一个子 Agent 帮你处理子任务。"
            "agent_type：explore（只读调查员，只能读取/搜索，推荐先派它摸清代码结构）"
            "或 general（可读写执行，写操作需用户确认）；"
            "task：交给子 Agent 的任务描述（明确、聚焦）。子 Agent 的结果会汇报给你。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "enum": ["explore", "general"],
                    "description": "explore=只读调查；general=可读写执行",
                },
                "task": {"type": "string", "description": "子 Agent 要完成的任务"},
            },
            "required": ["agent_type", "task"],
        },
        category="system",
        handler=_not_directly_callable,
    )
