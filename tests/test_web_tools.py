"""web_search / read_url 联网工具的解析与边界测试（不真实联网）。"""

from __future__ import annotations

from spark2.tools.web import _extract_links, _strip, build_web_tools


def test_tools_registered() -> None:
    tools = {t.name: t for t in build_web_tools()}
    assert set(tools) == {"web_search", "read_url"}
    assert tools["web_search"].category == "read"  # 只读 → 审批自动放行
    assert tools["read_url"].category == "read"


def test_extract_links_h2_form() -> None:
    html = """
    <li class="b_algo"><h2><a href="https://a.example/x">标题 <b>甲</b></a></h2>
    <p>摘要文本 &amp; 更多</p></li>
    <li class="b_algo"><h2><a href="https://b.example/y">标题乙</a></h2></li>
    """
    pairs = _extract_links(html, 5)
    assert pairs[0] == ("标题 甲", "https://a.example/x")
    assert pairs[1] == ("标题乙", "https://b.example/y")


def test_extract_links_fallback_any_a() -> None:
    html = '<div><a href="https://c.example/z">链接丙</a></div><a href="https://d.example/w">丁</a>'
    pairs = _extract_links(html, 5)
    assert pairs[0] == ("链接丙", "https://c.example/z")


def test_extract_links_drops_noise() -> None:
    html = '<a href="https://x.example/1">ok</a><a href="#top">锚点</a><a href="/rel">相对</a>'
    pairs = _extract_links(html, 5)
    assert len(pairs) == 1


def test_strip_unescape_and_tags() -> None:
    raw = "<script>var x=1;</script><style>a{}</style><p>你好 &amp; 世界</p>"
    assert _strip(raw) == "你好 & 世界"
