"""文件工具：读、写（带新旧 diff）、列目录、glob、rg 搜索。

写文件前由审批门展示统一 diff（unified_diff），不再整文件倾倒。
搜索调用 ripgrep（未安装时降级为 Python 遍历）。
"""
from __future__ import annotations

import asyncio
import difflib
import shutil
from pathlib import Path

from spark2.tools.base import Tool, ToolContext, is_within, resolve_path

MAX_READ_LINES = 2000
MAX_LIST = 200
MAX_SEARCH_LINES = 200
MAX_OUT = 60_000


async def read_file(args: dict, ctx: ToolContext) -> str:
    raw = str(args.get("path", ""))
    if not raw:
        return "错误：缺少 path"
    p = resolve_path(raw, ctx.workdir)
    if not p.exists():
        return f"错误：文件不存在 {p}"
    if p.is_dir():
        return f"错误：{p} 是目录，请用 list_dir"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return f"错误：读取失败 {e}"
    total = len(lines)
    limit = int(args.get("limit", MAX_READ_LINES))
    limit = min(limit, MAX_READ_LINES)
    body = lines[:limit]
    out = "\n".join(body)
    note = f"\n（已截断：共 {total} 行，仅显示前 {limit} 行）" if total > limit else ""
    return out + note


async def write_file(args: dict, ctx: ToolContext) -> str:
    raw = str(args.get("path", ""))
    content = str(args.get("content", ""))
    if not raw:
        return "错误：缺少 path"
    p = resolve_path(raw, ctx.workdir)
    if p.is_dir():
        return f"错误：{p} 是目录，不能写入"
    old = ""
    if p.exists():
        try:
            old = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old = ""
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"错误：写入失败 {e}"
    added = sum(1 for ln in content.splitlines() if ln.strip())
    return f"已写入 {p}（{added} 行）"


async def list_dir(args: dict, ctx: ToolContext) -> str:
    raw = str(args.get("path", "."))
    p = resolve_path(raw, ctx.workdir)
    if not p.exists():
        return f"错误：路径不存在 {p}"
    if not p.is_dir():
        return f"错误：{p} 不是目录"
    try:
        entries = sorted(p.iterdir(), key=lambda e: (e.name.lower(),))
    except OSError as e:
        return f"错误：读取目录失败 {e}"
    rows = []
    for e in entries[:MAX_LIST]:
        kind = "dir" if e.is_dir() else "file"
        size = ""
        if e.is_file():
            try:
                size = f" {e.stat().st_size}B"
            except OSError:
                pass
        rows.append(f"{'[d]' if kind == 'dir' else '   '} {e.name}{size}")
    if len(entries) > MAX_LIST:
        rows.append(f"（已截断：共 {len(entries)} 项）")
    return "\n".join(rows) if rows else "（空目录）"


async def glob_tool(args: dict, ctx: ToolContext) -> str:
    pattern = str(args.get("pattern", ""))
    raw = str(args.get("path", "."))
    if not pattern:
        return "错误：缺少 pattern"
    p = resolve_path(raw, ctx.workdir)
    if not p.is_dir():
        return f"错误：{p} 不是目录"
    try:
        hits = list(p.glob(pattern))[:MAX_SEARCH_LINES]
    except OSError as e:
        return f"错误：{e}"
    return "\n".join(str(h.relative_to(ctx.workdir) if is_within(h, ctx.workdir) else h) for h in hits) or "（无匹配）"


async def search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", ""))
    raw = str(args.get("path", "."))
    if not query:
        return "错误：缺少 query"
    p = resolve_path(raw, ctx.workdir)
    if not p.exists():
        return f"错误：路径不存在 {p}"
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "-n", "--no-heading", "-i", "--max-columns", "300", query, str(p)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(ctx.workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except (asyncio.TimeoutError, OSError) as e:
            return f"错误：搜索失败 {e}"
        text = out.decode("utf-8", "replace")
        if proc.returncode not in (0, 1):
            return f"错误：rg 退出码 {proc.returncode}: {err.decode('utf-8','replace')[:300]}"
        lines = text.splitlines()[:MAX_SEARCH_LINES]
        joined = "\n".join(lines)
        if len(text.splitlines()) > MAX_SEARCH_LINES:
            joined += f"\n（已截断：结果超过 {MAX_SEARCH_LINES} 行）"
        return joined or "（无匹配）"
    # 降级：纯 Python 遍历（仅当 rg 不可用）
    hits = []
    try:
        for f in p.rglob("*"):
            if f.is_file() and query.lower() in f.read_text(errors="ignore").lower():
                rel = f.relative_to(ctx.workdir) if is_within(f, ctx.workdir) else f
                hits.append(str(rel))
                if len(hits) >= MAX_SEARCH_LINES:
                    break
    except OSError:
        pass
    return "\n".join(hits) or "（无匹配）"


def _preview_write(args: dict, ctx: ToolContext) -> tuple[str, str]:
    raw = str(args.get("path", ""))
    p = resolve_path(raw, ctx.workdir)
    old = ""
    if p.exists():
        try:
            old = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old = ""
    new = str(args.get("content", ""))
    diff = "\n".join(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(), fromfile=str(p), tofile=str(p), lineterm=""
        )
    )
    if not diff:
        diff = "（内容无变化）"
    n = sum(1 for ln in new.splitlines() if ln.strip())
    summary = f"写入 {p}（{n} 行）"
    return summary, diff[:MAX_OUT]


def build_file_tools() -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description="读取文件内容。path：文件路径（相对工作目录或绝对路径）；limit：最多行数。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "limit": {"type": "integer", "description": "最多读取行数，默认 2000"},
                },
                "required": ["path"],
            },
            category="read",
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description="写入文件（覆盖写）。会先展示新旧 diff 供用户确认。path：文件路径；content：完整新内容。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            category="write",
            handler=write_file,
            preview=_preview_write,
        ),
        Tool(
            name="list_dir",
            description="列出目录内容（不递归）。path：目录路径，默认当前工作目录。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
            category="read",
            handler=list_dir,
        ),
        Tool(
            name="glob",
            description="按通配符查找文件，如 pattern='**/*.py'。path：起始目录。",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["pattern"],
            },
            category="read",
            handler=glob_tool,
        ),
        Tool(
            name="search",
            description="在代码中全文搜索（使用 ripgrep，大小写不敏感）。query：关键词；path：搜索起点。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["query"],
            },
            category="read",
            handler=search,
        ),
    ]
