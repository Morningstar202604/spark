from __future__ import annotations

import html as html_mod
import re
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse

import httpx
from pydantic import BaseModel

from spark.models import ToolResult

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class WebSearchArgs(BaseModel):
    query: str
    max_results: int = 8


class WebFetchArgs(BaseModel):
    url: str
    max_chars: int = 12000


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    return html_mod.unescape(text).strip()


def bing_search(query: str, max_results: int) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?q=" + quote_plus(query)
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()
    page = resp.text
    blocks = re.findall(r'<li class="b_algo".*?</li>', page, re.S)
    results: list[dict[str, str]] = []
    for block in blocks[: max_results * 2]:
        m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        link = m.group(1)
        title = _strip_tags(m.group(2))
        snip = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        snippet = _strip_tags(snip.group(1)) if snip else ""
        if link.startswith("http"):
            results.append({"title": title, "url": link, "snippet": snippet[:300]})
        if len(results) >= max_results:
            break
    return results


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "iframe", "nav", "footer", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in {"p", "br", "li", "tr", "div", "h1", "h2", "h3", "h4", "pre", "section", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data.strip() + " ")

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [line.strip() for line in raw.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line))


def fetch_page(url: str, max_chars: int) -> dict[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported scheme: {parsed.scheme or 'none'} (use http/https)")
    with httpx.Client(timeout=25.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()
    ctype = resp.headers.get("content-type", "")
    if "html" in ctype or "xml" in ctype or not ctype:
        parser = _TextExtractor()
        parser.feed(resp.text)
        text = parser.text()
        title_m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.S | re.I)
        title = _strip_tags(title_m.group(1)) if title_m else ""
    else:
        title = ""
        text = resp.text
    return {"title": title, "url": str(resp.url), "content": text[:max_chars]}


def web_search_tool(args: WebSearchArgs) -> ToolResult:
    query = args.query.strip()
    if not query:
        return ToolResult(ok=False, payload={"error": "query required"})
    max_results = max(1, min(10, args.max_results))
    try:
        results = bing_search(query, max_results)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": f"web search failed: {exc}"})
    if not results:
        return ToolResult(ok=False, payload={"error": "no results (search engine may be rate-limiting; try again later)"})
    return ToolResult(ok=True, payload={"query": query, "results": results, "count": len(results)})


def web_fetch_tool(args: WebFetchArgs) -> ToolResult:
    url = args.url.strip()
    max_chars = max(500, min(50000, args.max_chars))
    try:
        page = fetch_page(url, max_chars)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": f"fetch failed: {exc}"})
    content = page["content"]
    truncated = len(page["content"]) >= max_chars
    payload: dict = {
        "url": page["url"],
        "title": page["title"],
        "content": content,
        "chars": len(content),
    }
    if truncated:
        payload["note"] = f"content truncated at {max_chars} chars; fetch with larger max_chars or a more specific page if needed"
    return ToolResult(ok=True, payload=payload)
