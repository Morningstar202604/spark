"""MCP Streamable HTTP 测试服务器：FastMCP + uvicorn，端口 8931。"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo-http", host="127.0.0.1", port=8931)


@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b


@mcp.tool(annotations=None)
def echo(text: str) -> str:
    """原样返回"""
    return text


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
