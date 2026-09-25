"""代码索引工具（P3 ⑤）：index_project / search_symbol / lint_file。"""
from __future__ import annotations

import json

from spark2.tools.base import Tool, ToolContext, resolve_path


def _get_index(ctx: ToolContext) -> dict:
    """惰性索引：ctx.index 是 {workdir: {"index": {...}, "loaded_at": ts}} 状态。"""
    if ctx.index is None:
        return {}
    state = ctx.index
    key = str(ctx.workdir)
    entry = state.get(key)
    if entry is None:
        import spark2.codeindex as ci
        idx = ci.index_project(str(ctx.workdir))
        state[key] = {"index": idx, "loaded_at": __import__("time").time()}
        entry = state[key]
    return entry["index"]


async def _index_project(args: dict, ctx: ToolContext) -> str:
    workdir = str(args.get("path") or ctx.workdir).strip()
    if not ctx.workdir.exists():
        return "错误：工作目录不存在"
    import spark2.codeindex as ci

    idx = ci.index_project(workdir)
    if ctx.index is not None:
        ctx.index[str(ctx.workdir)] = {"index": idx, "loaded_at": __import__("time").time()}
    symbols = idx.get("symbols", [])
    files = idx.get("files", {})
    lang = idx.get("language_count", {})
    kinds: dict[str, int] = {}
    for s in symbols:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    lines = [
        f"索引完成：{len(files)} 个文件，{len(symbols)} 个符号",
        "语言分布：" + "、".join(f"{k} {v} 个" for k, v in sorted(lang.items())) if lang else "（无可索引源码）",
        "符号构成：" + "、".join(f"{k} {v}" for k, v in sorted(kinds.items())) if kinds else "",
    ]
    samples = [f"{s['file']}:{s['line']} {s['kind']} {s['name']}" for s in symbols[:8]]
    if samples:
        lines.append("示例：\n" + "\n".join("  " + x for x in samples))
    return "\n".join(x for x in lines if x)


async def _search_symbol(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "错误：需要 query（符号名）"
    limit = int(args.get("limit") or 20)
    idx = _get_index(ctx)
    if not idx:
        return "索引为空，先调用 index_project"
    import spark2.codeindex as ci

    hits = ci.search_symbol(idx, query, limit=limit)
    if not hits:
        return f"未找到与「{query}」匹配的符号（可先 index_project 刷新索引）"
    lines = [f"「{query}」匹配 {len(hits)} 个符号："]
    for s in hits:
        args_s = f"({s.get('args', '')})" if s.get("args") else ""
        lines.append(f"  {s['file']}:{s['line']}  {s['kind']}  {s['name']}{args_s}")
    return "\n".join(lines)


async def _lint_file(args: dict, ctx: ToolContext) -> str:
    path = str(args.get("path") or "").strip()
    if not path:
        return "错误：需要 path"
    import spark2.codeindex as ci

    diags = ci.lint_file(str(ctx.workdir), path)
    if not diags:
        return f"语法检查通过：{path}"
    lines = [f"发现 {len(diags)} 个问题（{path}）："]
    for d in diags:
        lines.append(f"  [{d.get('severity')}] {d.get('message')}")
    return "\n".join(lines)


def build_codeindex_tools() -> list[Tool]:
    return [
        Tool(
            name="index_project",
            description="扫描工作目录生成代码索引（函数/类/变量符号，含语言分布），动手改代码前先摸清项目结构。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "可选，要索引的目录（默认工作目录）"},
                },
            },
            category="read",
            handler=_index_project,
        ),
        Tool(
            name="search_symbol",
            description="在代码索引里按名称搜索符号（函数/类等）的位置，快速定位定义。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "符号名（模糊匹配，如 add）"},
                    "limit": {"type": "integer", "description": "最多返回条数（默认 20）"},
                },
                "required": ["query"],
            },
            category="read",
            handler=_search_symbol,
        ),
        Tool(
            name="lint_file",
            description="对单个文件做语法诊断（.py 用 py_compile，.js 用 node --check），改完代码自查语法。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对工作目录或绝对路径）"},
                },
                "required": ["path"],
            },
            category="read",
            handler=_lint_file,
        ),
    ]
