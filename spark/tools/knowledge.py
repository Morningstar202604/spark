"""轻量知识库工具：kb_add 收入本地文档，kb_search 关键词检索（零依赖 RAG 雏形）。

对标钉钉/通义/飞书「企业知识库」：把 office/pdf/md 文档吸收成可检索的知识，后续问答/引用直接命中。
文本存到配置目录 ~/.spark/knowledge/ 下，按文档名分片，检索用关键词加权打分。
"""
from __future__ import annotations

import re
from pathlib import Path

from spark.config import config_dir
from spark.tools.base import Tool, ToolContext, resolve_path, is_within

KB_DIR_NAME = "knowledge"
CHUNK = 900  # 每片字符数


def _kb_dir(ctx: ToolContext) -> Path:
    # 放到配置目录 ~/.spark/knowledge（SPARK_HOME 时走测试隔离目录）
    return config_dir() / KB_DIR_NAME


async def _kb_add(args: dict, ctx: ToolContext) -> str:
    src = resolve_path(str(args.get("path", "")), ctx.workdir)
    if not src.exists():
        return f"错误：文件不存在 {src}"
    kb = _kb_dir(ctx)
    kb.mkdir(parents=True, exist_ok=True)

    text = ""
    ext = src.suffix.lower()
    if ext == ".txt" or ext == ".md":
        text = src.read_text(encoding="utf-8", errors="ignore")
    elif ext == ".docx":
        if not __import__("importlib").util.find_spec("docx"):
            return "错误：未安装 python-docx，无法解析 docx"
        from docx import Document  # noqa: PLC0415

        text = "\n".join(p.text for p in Document(src).paragraphs)
    elif ext == ".pdf":
        if not __import__("importlib").util.find_spec("pypdf"):
            return "错误：未安装 pypdf，无法解析 pdf"
        from pypdf import PdfReader  # noqa: PLC0415

        text = "\n".join(p.extract_text() or "" for p in PdfReader(src).pages)
    else:
        text = src.read_text(encoding="utf-8", errors="ignore")

    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return f"（{src} 无可提取文本，未能入库）"

    # 分片写入：knowledge/<源文件名>.txt
    out_path = kb / (src.stem + ".txt")
    blocks = [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)]
    with out_path.open("w", encoding="utf-8") as f:
        for b in blocks:
            f.write(b + "\n\u0000BLOCK\u0000\n")
    return f"已入库：{out_path.name}（{len(text)} 字符，{len(blocks)} 片）。可随时用 kb_search 检索。"


def _search(kb: Path, query: str, limit: int) -> list[tuple[float, str, str]]:
    terms = [t for t in re.split(r"\s+", query.lower()) if t]
    hits: list[tuple[float, str, str]] = []
    if not kb.exists():
        return hits
    for f in kb.glob("*.txt"):
        content = f.read_text(encoding="utf-8", errors="ignore")
        for block in content.split("\u0000BLOCK\u0000"):
            b = block.strip()
            if not b:
                continue
            low = b.lower()
            score = sum(low.count(t) for t in terms)
            if score > 0:
                hits.append((score, f.name, b))
    hits.sort(key=lambda x: -x[0])
    return hits[:limit]


async def _kb_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    limit = max(1, min(int(args.get("limit", 5)), 20))
    if not query:
        return "错误：缺少 query 参数"
    hits = _search(_kb_dir(ctx), query, limit)
    if not hits:
        return f"知识库中未找到「{query}」相关内容。可先用 kb_add 收录文档。"
    out = [f"找到 {len(hits)} 处相关片段：\n"]
    for score, fname, block in hits:
        # 截取命中的上下文
        m = [t for t in re.split(r"\s+", query.lower()) if t]
        start = 0
        for t in m:
            idx = block.lower().find(t)
            if idx >= 0:
                start = max(0, idx - 120)
                break
        snippet = block[start : start + 500]
        out.append(f"### 来自 {fname}（匹配 {score}）\n{snippet}\n")
    return "\n".join(out)


def _kb_context(ctx: ToolContext) -> Path:
    return _kb_dir(ctx)


def build_knowledge_tools() -> list[Tool]:
    return [
        Tool(
            name="kb_add",
            description="把本地文档（docx/pdf/md/txt）收录进知识库，后续可用 kb_search 检索。用于把项目文档、规格、制度、参考资料变成可查询的知识。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "要入库的文档路径（docx/pdf/md/txt）"}},
                "required": ["path"],
            },
            category="write",
            handler=_kb_add,
        ),
        Tool(
            name="kb_search",
            description="在知识库中按关键词检索相关片段并返回。query 为检索词；先用 kb_add 收录文档才能检索。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词"},
                    "limit": {"type": "integer", "description": "返回片段数，默认 5，最大 20"},
                },
                "required": ["query"],
            },
            category="read",
            handler=_kb_search,
        ),
    ]