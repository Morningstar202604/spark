"""工具注册表：把所有工具汇总为 name -> Tool。"""
from __future__ import annotations

from spark2.tools.base import Tool
from spark2.tools.codeindex_tools import build_codeindex_tools
from spark2.tools.fs import build_file_tools
from spark2.tools.git import build_checkpoint_tools
from spark2.tools.memory_tools import build_memory_search_tool, build_memory_tools
from spark2.tools.patch import build_patch_tool
from spark2.tools.plan import build_plan_tool
from spark2.tools.shell import build_shell_tool
from spark2.tools.subagent import build_subagent_tool

# 只读工具（explore 子 Agent 专用）：只能读取/搜索/记忆检索，不含任何写与命令
_READONLY_NAMES = {"read_file", "list_dir", "glob", "search", "memory_search", "index_project", "search_symbol", "lint_file"}


def build_registry(with_subagent: bool = True, plugin_tools: list[Tool] | None = None) -> dict[str, Tool]:
    reg: dict[str, Tool] = {}
    for tool in (
        build_file_tools()
        + build_shell_tool()
        + build_plan_tool()
        + build_memory_tools()
        + build_checkpoint_tools()
        + build_patch_tool()
        + build_codeindex_tools()
        + (plugin_tools or [])
    ):
        reg[tool.name] = tool
    if with_subagent:
        reg["spawn_subagent"] = build_subagent_tool()
    return reg


def build_readonly_registry() -> dict[str, Tool]:
    """explore 子 Agent 的工具集：只有只读工具（无 shell / 无写 / 无子 Agent）。"""
    reg: dict[str, Tool] = {}
    for tool in build_file_tools() + build_plan_tool() + build_codeindex_tools():
        if tool.name in _READONLY_NAMES:
            reg[tool.name] = tool
    ms = build_memory_search_tool()
    if ms:
        reg[ms.name] = ms
    return reg


def tool_schemas(reg: dict[str, Tool]) -> list[dict]:
    return [t.schema() for t in reg.values()]
