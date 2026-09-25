"""示例插件 3：count_loc 工具（只读统计代码行数）。"""
from spark2.tools.base import Tool
from pathlib import Path

_SKIP = {".git", "node_modules", ".venv", "venv", "dist", "build", "target", "__pycache__"}
_CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc"}


def _count_loc(args, ctx):
    root = Path(ctx.workdir)
    total = 0
    by_ext: dict[str, int] = {}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in _CODE_EXT and not any(part in _SKIP for part in p.parts):
            n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            total += n
            by_ext[p.suffix.lower()] = by_ext.get(p.suffix.lower(), 0) + n
    detail = "、".join(f"{ext.lstrip('.')} {n} 行" for ext, n in sorted(by_ext.items()))
    return f"代码共 {total} 行（{detail or '无可统计源码'}）"


def register(reg):
    reg.append(Tool(
        name="count_loc",
        description="统计工作目录内代码总行数（按语言分布，只读）",
        parameters={"type": "object", "properties": {}},
        category="read",
        handler=_count_loc,
    ))
