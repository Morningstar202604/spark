"""联网工具：web_search 网页搜索 + read_url 网页读取。

只读类别（无需审批），让 Agent 获取最新信息：文档、报错、API 变更、新闻、技术方案。
实现轻量：httpx + 正则提取，无额外依赖。
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote_plus

import httpx

from spark2.tools.base import Tool, ToolContext

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TIMEOUT = 15.0
_MAX_TEXT = 8000


def _strip(html_src: str) -> str:
    html_src = re.sub(
        r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<!--[\s\S]*?-->", " ", html_src
    )
    html_src = re.sub(r"<[^>]+>", " ", html_src)
    html_src = re.sub(r"\s+", " ", html_src)
    return html.unescape(html_src.strip())


def _extract_links(html_src: str, n: int) -> list[tuple[str, str]]:
    """Bing 新版 HTML：h2 内嵌 a 或直接 a[href]，尽量提取 (标题, URL)。"""
    out: list[tuple[str, str]] = []
    # 优先 h2>a 形态
    for m in re.finditer(r'<h2[^>]*>[\s\S]*?<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', html_src):
        if len(out) >= n:
            break
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append((t, m.group(1)))
    if out:
        return out
    # 兜底：任意 a[href]（过滤导航/脚本链接）
    for m in re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>([\s\S]*?)</a>', html_src):
        if len(out) >= n:
            break
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        t = re.sub(r"\s+", " ", t).strip()
        if t and len(t) > 1:
            out.append((t, m.group(1)))
    return out


async def _web_search(args: dict, ctx: ToolContext) -> str:
    q = str(args.get("query", "")).strip()
    n = min(int(args.get("max_results", 5)), 10)
    if not q:
        return "错误：缺少 query 参数"
    url = "https://www.bing.com/search?q=" + quote_plus(q) + "&setlang=zh-CN&cc=CN"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_TIMEOUT, headers={"User-Agent": _UA}
        ) as client:
            r = await client.get(url)
            html = r.text
    except Exception as e:  # noqa: BLE001
        return f"搜索失败：{e}"

    blocks = re.findall(r'<li class="b_algo"[\s\S]*?</li>', html)[:n]
    if not blocks:
        pairs = _extract_links(html, n)
        if not pairs:
            return f"未找到结果（搜索：{q}）"
        out = [f"- {t}\n  {h}" for t, h in pairs]
        return "\n\n".join(out)

    out: list[str] = []
    for b in blocks[:n]:
        pairs = _extract_links(b, 1)
        title, href = pairs[0] if pairs else ("?", "")
        sn = re.search(r"<p[^>]*>(.*?)</p>", b, re.S)
        snippet = _strip(sn.group(1)) if sn else ""
        out.append(f"### {title}\nURL: {href}\n{snippet}")
    return "\n\n".join(out)


async def _read_url(args: dict, ctx: ToolContext) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return "错误：缺少 url 参数"
    if not url.startswith(("http://", "https://")):
        return "错误：仅支持 http/https 链接"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_TIMEOUT, headers={"User-Agent": _UA}
        ) as client:
            r = await client.get(url)
            text = _strip(r.text)
    except Exception as e:  # noqa: BLE001
        return f"读取失败：{e}"
    if len(text) > _MAX_TEXT:
        text = text[: _MAX_TEXT] + "\n…（已截断，如需完整内容可让模型分块或指定其他链接）"
    return f"## {url}\n\n{text}"


def build_web_tools() -> list[Tool]:
    return [
        Tool(
            name="web_search",
            description=(
                "联网搜索网页，返回标题/链接/摘要列表。用于获取模型知识截止日期之后的信息："
                "最新文档、报错方案、API 变更、新闻、技术文章。query 为搜索词，max_results 默认 5 最多 10。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词（中文/英文均可）"},
                    "max_results": {"type": "integer", "description": "返回条数，默认 5，最大 10", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
            category="read",
            handler=_web_search,
        ),
        Tool(
            name="read_url",
            description=(
                "读取指定网页的正文（自动去标签转纯文本，最多 8000 字符），返回可读文本。"
                "用于精读搜索结果或用户给的链接。url 必须为 http(s) 链接。"
            ),
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "要读取的网页链接"}},
                "required": ["url"],
            },
            category="read",
            handler=_read_url,
        ),
    ]
