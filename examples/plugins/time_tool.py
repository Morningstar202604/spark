"""示例插件 1：now 工具（列表写法）。"""
from spark2.tools.base import Tool


def _now(args, ctx):
    import datetime
    return "当前时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")


tools = [
    Tool(
        name="now",
        description="获取当前日期时间",
        parameters={"type": "object", "properties": {}},
        category="read",
        handler=_now,
    )
]
