import json
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.models import ToolResult
from spark.sandbox import WorkdirSandbox
from spark.tools import search, web


@pytest.fixture()
def sandbox(tmp_path: Path) -> WorkdirSandbox:
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    print('hello needle')\n")
    (tmp_path / "src" / "util.ts").write_text("export const needle = 1;\n")
    (tmp_path / "node_modules" / "lib.js").write_text("needle inside deps\n")
    (tmp_path / "README.md").write_text("# project\n")
    return WorkdirSandbox(tmp_path)


def test_grep_finds_matches_with_skip(sandbox: WorkdirSandbox) -> None:
    result = search.grep_tool(sandbox, search.GrepArgs(pattern="needle"))
    assert result.ok
    results = result.payload["results"]
    assert any("src/app.py" in r for r in results)
    assert any("src/util.ts" in r for r in results)
    assert not any("node_modules" in r for r in results)


def test_grep_glob_filter(sandbox: WorkdirSandbox) -> None:
    result = search.grep_tool(sandbox, search.GrepArgs(pattern="needle", glob="*.py"))
    results = result.payload["results"]
    assert all(r.startswith("src/app.py") for r in results)


def test_grep_invalid_regex(sandbox: WorkdirSandbox) -> None:
    result = search.grep_tool(sandbox, search.GrepArgs(pattern="([unclosed"))
    assert not result.ok
    assert "invalid regex" in result.payload["error"]


def test_glob_pattern(sandbox: WorkdirSandbox) -> None:
    result = search.glob_tool(sandbox, search.GlobArgs(pattern="src/**/*.ts"))
    assert result.ok
    assert result.payload["files"] == ["src/util.ts"]


def test_glob_no_match(sandbox: WorkdirSandbox) -> None:
    result = search.glob_tool(sandbox, search.GlobArgs(pattern="**/*.rs"))
    assert result.ok
    assert result.payload["count"] == 0


def test_grep_escape_outside_workdir(sandbox: WorkdirSandbox) -> None:
    result = search.grep_tool(sandbox, search.GrepArgs(pattern="x", path="../.."))
    assert not result.ok


def test_fetch_page_parses_html() -> None:
    html = "<html><head><title>T</title></head><body><script>bad()</script><p>hello world</p><p>second</p></body></html>"
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=html, headers={"content-type": "text/html"})

    import httpx._transports.mock

    original = httpx.Client
    try:
        httpx.Client = lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
        page = web.fetch_page("https://example.com/x", 5000)
    finally:
        httpx.Client = original
    assert page["title"] == "T"
    assert "hello world" in page["content"]
    assert "bad()" not in page["content"]


def test_fetch_rejects_non_http() -> None:
    with pytest.raises(ValueError):
        web.fetch_page("file:///etc/passwd", 1000)


def test_registry_schemas_include_new_tools() -> None:
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    registry = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(Path(".")), config=cfg))
    names = {s["function"]["name"] for s in registry.schemas()}
    assert {"grep", "glob", "web_search", "web_fetch", "update_plan"} <= names
