"""记忆工具：remember / forget（显式、零隐性 LLM 调用）。

通过 ctx.memory（MemoryStore）读写。记忆按工作目录隔离，
用户说"记住 XX 是 YY"或"忘了 XX"时模型才调用；不自动猜测写记忆。
"""
from __future__ import annotations

from spark2.tools.base import Tool, ToolContext


async def remember(args: dict, ctx: ToolContext) -> str:
    key = str(args.get("key", "")).strip()
    value = str(args.get("value", "")).strip()
    if not key or not value:
        return "错误：需要 key 和 value（如 remember(key='部署方式', value='用 systemd 服务')）"
    if ctx.memory is None:
        return "错误：记忆库未启用"
    ctx.memory.remember(str(ctx.workdir), key, value)
    return f"已记住：{key}"


async def forget(args: dict, ctx: ToolContext) -> str:
    key = str(args.get("key", "")).strip()
    if not key:
        return "错误：需要 key（与 remember 时的 key 一致）"
    if ctx.memory is None:
        return "错误：记忆库未启用"
    n = ctx.memory.forget(str(ctx.workdir), key)
    return f"已忘记：{key}（删除 {n} 条）" if n else f"没有找到记忆：{key}"


async def memory_search(args: dict, ctx: ToolContext) -> str:
    """只读检索记忆（explore 子 Agent 与主 Agent 共用）。"""
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit") or 5)
    if not query:
        return "错误：需要 query"
    if ctx.memory is None:
        return "错误：记忆库未启用"
    try:
        hits = ctx.memory.search(str(ctx.workdir), query, limit=limit)
    except Exception as e:  # noqa: BLE001 —— 检索失败不阻断
        return f"检索失败：{e}"
    if not hits:
        return "没有相关记忆"
    return "\n".join(f"- [{h.get('key','')}] {h.get('value','')}" for h in hits)


def build_memory_search_tool() -> Tool | None:
    return Tool(
        name="memory_search",
        description="检索本地长期记忆（按当前工作目录隔离，只读）。query：关键词。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        category="read",
        handler=memory_search,
    )


def build_memory_tools() -> list[Tool]:
    return [
        Tool(
            name="remember",
            description="记住一条长期记忆（按当前工作目录隔离，同 key 会覆盖更新）。key：简短关键词；value：要点内容。仅当用户明确要求记住时才调用。",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "记忆关键词，简短"},
                    "value": {"type": "string", "description": "记忆内容要点"},
                },
                "required": ["key", "value"],
            },
            category="system",
            handler=remember,
        ),
        Tool(
            name="forget",
            description="忘记一条长期记忆（需与 remember 时的 key 一致）。仅当用户明确要求忘记时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "要忘记的记忆关键词"},
                },
                "required": ["key"],
            },
            category="system",
            handler=forget,
        ),
    ]
