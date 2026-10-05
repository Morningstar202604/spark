"""联网工具：web_search 网页搜索 + read_url 网页读取。

重构注记：
- 必应抓取 HTML 仍保留（无 API key 依赖），但文章提取改用 trafilatura（已在
  deps，pip install 即装）。trafilatura 比正则 <! 清洗更稳，对多语言/复杂排版/
  懒加载网页都能提取正文。
- 搜索接入 DuckDuckGo HTML（可选增强）：优先 duckduckgo_search，未装则回退必应抓取。
- 公共接口（_extract_links / _strip）保留，以兼容现有测试。
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote_plus

import httpx

from spark.tools.base import Tool, ToolContext

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TIMEOUT = 15.0
_MAX_TEXT = 8000


def _strip(html_src: str) -> str:
    """提取网页正文：优先 trafilatura，回退正则清洗（兼容无 trafilatura 环境）。"""
    if not html_src:
        return ""
    try:
        import trafilatura  # type: ignore

        extracted = trafilatura.extract(
            html_src,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        )
        if extracted:
            return extracted.strip()
    except Exception:  # noqa: BLE001
        pass
    # 正则兜底（必应结果页等无 article 语义的页面）
    html_src = re.sub(
        r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<!--[\s\S]*?-->",
        " ",
        html_src,
    )
    html_src = re.sub(r"<[^>]+>", " ", html_src)
    html_src = re.sub(r"\s+", " ", html_src)
    return html.unescape(html_src.strip())


def _extract_links(html_src: str, n: int) -> list[tuple[str, str]]:
    """从必应结果页提取 (标题, URL)。保留旧实现以兼容测试。"""
    out: list[tuple[str, str]] = []
    for m in re.finditer(
        r'<h2[^>]*>[\s\S]*?<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>',
        html_src,
    ):
        if len(out) >= n:
            break
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append((t, m.group(1)))
    if out:
        return out
    for m in re.finditer(
        r'<a[^>]*href="(https?://[^"]+)"[^>]*>([\s\S]*?)</a>',
        html_src,
    ):
        if len(out) >= n:
            break
        t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        t = re.sub(r"\s+", " ", t).strip()
        if t and len(t) > 1:
            out.append((t, m.group(1)))
    return out


async def _web_search(args: dict | None, ctx: ToolContext) -> str:
    args = args or {}
    q = str(args.get("query", "")).strip()
    n = min(int(args.get("max_results", 5)), 10)
    if not q:
        return "错误：缺少 query 参数"

    # 优先用 duckduckgo_search（更结构化）
    try:
        from duckduckgo_search import DDGS  # type: ignore

        with DDGS() as ddgs:
            results = list(ddgs.text(q, max_results=n))
        if results:
            out = [
                f'- {r.get("title", "")}\n  {r.get("href", "")}'
                for r in results
            ]
            return "\n\n".join(out)
    except Exception:  # noqa: BLE001
        pass

    # 兜底：必应抓取（旧实现）
    url = "https://www.bing.com/search?q=" + quote_plus(q) + "&setlang=zh-CN&cc=CN"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_TIMEOUT, headers={"User-Agent": _UA}
        ) as client:
            r = await client.get(url)
            h = r.text
    except Exception as e:  # noqa: BLE001
        return f"搜索失败：{e}"

    blocks = re.findall(r'<li class="b_algo"[\s\S]*?</li>', h)[:n]
    if not blocks:
        pairs = _extract_links(h, n)
        if not pairs:
            return f"未找到结果（搜索：{q}）"
        return "\n\n".join(f"- {t}\n  {h}" for t, h in pairs)

    out: list[str] = []
    for b in blocks[:n]:
        pairs = _extract_links(b, 1)
        title, href = pairs[0] if pairs else ("?", "")
        sn = re.search(r"<p[^>]*>(.*?)</p>", b, re.S)
        snippet = _strip(sn.group(1)) if sn else ""
        out.append(f"### {title}\nURL: {href}\n{snippet}")
    return "\n\n".join(out)


async def _read_url(args: dict | None, ctx: ToolContext) -> str:
    args = args or {}
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
        text = (
            text[:_MAX_TEXT]
            + "\n…（已截断，如需完整内容可让模型分块或指定其他链接）"
        )
    return f"## {url}\n\n{text}"


def build_web_tools() -> list[Tool]:
    return [
        Tool(
            name="web_search",
            description=(
                "联网搜索网页，返回标题/链接/摘要列表。用于获取模型知识截止日期之后的信息："
                "最新文档、报错方案、API 变更、新闻、技术方案。query 为搜索词，max_results 默认 5 最多 10。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词（中文/英文均可）",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回结果数，默认 5 最多 10",
                    },
                },
                "required": ["query"],
            },
            category="read",
            handler=_web_search,
        ),
        Tool(
            name="read_url",
            description=(
                "读取指定 URL 的网页文本内容（HTML 提取为纯文本）；"
                "用于阅读文档、API 参考、技术文章、报错讨论等。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "网页地址（http/https）",
                    }
                },
                "required": ["url"],
            },
            category="read",
            handler=_read_url,
        ),
    ]
