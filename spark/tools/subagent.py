"""spawn_subagent 工具：主 Agent 派生子 Agent 的入口（实际执行由 loop 特判嵌入事件流）。

该工具的 handler 只作兜底（正常情况下 loop._execute_call 会先特判拦截），
保证即使绕过特判也不会真去"创建子 Agent"。
"""
from __future__ import annotations

from spark.tools.base import Tool


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


def build_explore_parallel_tool() -> Tool:
    """explore_parallel：一次并行派出多个只读子 Agent 探索不同区域并合并结论。

    适用：大仓库需要同时摸清多个模块/目录时，用 2-4 个并行 explore 替代串行，
    显著缩短探索时间。每个子 agent 都是只读调查员（read_file/list_dir/glob/search），
    不写文件、不执行命令、不派生子 agent；结果按区域汇总返回。
    """
    return Tool(
        name="explore_parallel",
        description=(
            "并行派出 2-4 个只读子 Agent，同时探索不同目录/模块/主题并汇总结论。"
            "当任务需要同时了解多个区域（如：入口在哪、数据层怎么组织、测试怎么覆盖）时使用，"
            "比逐个 spawn_subagent(explore) 更快。所有子 Agent 只读，不做任何修改。"
            "topics 数组：每个元素 {path: 探索区域（相对工作目录或绝对路径）, task: 该区域要查清的问题}。"
            "返回每个区域的调查结论汇总。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "探索区域（目录或文件，相对工作目录）"},
                            "task": {"type": "string", "description": "该区域要查清的问题（聚焦、明确）"},
                        },
                        "required": ["path", "task"],
                    },
                    "description": "2-4 个并行探索主题，每个都是 {path, task}",
                    "minItems": 2,
                    "maxItems": 4,
                }
            },
            "required": ["topics"],
        },
        category="system",
        handler=_not_directly_callable,
    )
