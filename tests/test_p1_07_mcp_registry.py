"""P1-7 MCP registry 聚合代理单测。

全部用 ``httpx.MockTransport`` mock 网络，不碰真实外网。
覆盖：搜索解析 / 版本去重 / 命令建议 / 国内判定规则 / 默认过滤 / 降级路径 / Glama。
"""

from __future__ import annotations

import httpx
import pytest

from spark.mcp_registry import (
    assess_china_availability,
    fetch_glama_rating,
    parse_glama_search_html,
    parse_registry_servers,
    search_servers,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _registry_payload() -> dict:
    """伪造的 registry v0 响应：含多版本去重 / stdio / remote / 境外 OAuth / 被墙域名。"""

    def entry(name: str, version: str, is_latest: bool, **over) -> dict:
        server = {"name": name, "title": name, "description": "desc-" + name, "version": version}
        server.update(over)
        return {
            "server": server,
            "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": is_latest}},
        }

    return {
        "servers": [
            # 同一 server 两个版本，旧版 isLatest=False
            entry(
                "com.example/local-fs",
                "0.1.0",
                False,
                packages=[
                    {
                        "registryType": "npm",
                        "identifier": "old-pkg",
                        "runtimeHint": "npx",
                        "transport": {"type": "stdio"},
                        "runtimeArguments": [{"value": "-y"}],
                    }
                ],
            ),
            entry(
                "com.example/local-fs",
                "0.2.0",
                True,
                packages=[
                    {
                        "registryType": "npm",
                        "identifier": "local-fs-mcp",
                        "transport": {"type": "stdio"},
                        "runtimeArguments": [{"value": "-y"}],
                    }
                ],
            ),
            # 需要 Google 凭证 → risk
            entry(
                "com.example/gcs",
                "1.0.0",
                True,
                packages=[
                    {
                        "registryType": "npm",
                        "identifier": "gcs-mcp",
                        "runtimeHint": "npx",
                        "transport": {"type": "stdio"},
                        "environmentVariables": [
                            {"name": "GCS_BUCKET", "isRequired": True},
                            {"name": "GCS_PRIVATE_KEY", "isSecret": True},
                        ],
                    }
                ],
            ),
            # 境外 OAuth remote → unreachable
            entry(
                "com.example/oauth-remote",
                "1.0.0",
                False,
                remotes=[{"type": "streamable-http", "url": "https://api.example.com/mcp"}],
            ),
            entry(
                "com.example/oauth-remote",
                "1.0.1",
                True,
                remotes=[{"type": "streamable-http", "url": "https://accounts.google.com/mcp"}],
            ),
            # 被墙域名 remote → unreachable
            entry(
                "com.example/blocked",
                "2.0.0",
                True,
                remotes=[{"type": "streamable-http", "url": "https://api.googleapis.com/mcp"}],
            ),
        ]
    }


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def test_parse_dedupes_versions_and_picks_latest() -> None:
    items = parse_registry_servers(_registry_payload())
    by_name = {it["name"]: it for it in items}
    assert set(by_name) == {
        "com.example/local-fs",
        "com.example/gcs",
        "com.example/oauth-remote",
        "com.example/blocked",
    }
    fs = by_name["com.example/local-fs"]
    assert fs["version"] == "0.2.0"  # 取 isLatest
    assert fs["suggested_command"] == "npx"
    assert fs["suggested_args"] == ["-y", "local-fs-mcp"]


def test_parse_stdio_without_runtimeHint_defaults_to_npx() -> None:
    payload = {
        "servers": [
            {
                "server": {
                    "name": "com.example/no-hint",
                    "version": "1.0.0",
                    "packages": [
                        {
                            "registryType": "npm",
                            "identifier": "@scope/pkg",
                            "transport": {"type": "stdio"},
                        }
                    ],
                },
                "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}},
            }
        ]
    }
    items = parse_registry_servers(payload)
    assert items[0]["suggested_command"] == "npx"
    assert items[0]["suggested_args"] == ["-y", "@scope/pkg"]


def test_parse_remote_without_packages() -> None:
    payload = {
        "servers": [
            {
                "server": {
                    "name": "com.example/remote",
                    "version": "1.0.0",
                    "remotes": [{"type": "streamable-http", "url": "https://example.com/mcp"}],
                },
                "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}},
            }
        ]
    }
    items = parse_registry_servers(payload)
    assert items[0]["transport"] == "remote"
    assert items[0]["suggested_command"] is None


# ---------------------------------------------------------------------------
# 国内可用性判定
# ---------------------------------------------------------------------------


def test_assess_available_stdio() -> None:
    v = assess_china_availability({"remotes": [], "required_env": []})
    assert v["verdict"] == "available"


def test_assess_risk_on_overseas_cred_env() -> None:
    v = assess_china_availability({"remotes": [], "required_env": [{"name": "GCS_BUCKET"}]})
    assert v["verdict"] == "risk"
    assert any("GCS_BUCKET" in r for r in v["reasons"])


def test_assess_unreachable_on_overseas_oauth_host() -> None:
    v = assess_china_availability(
        {"remotes": [{"url": "https://accounts.google.com/mcp"}], "required_env": []}
    )
    assert v["verdict"] == "unreachable"
    assert "requires_overseas_oauth" in v["reasons"]


def test_assess_unreachable_on_blocked_domain() -> None:
    v = assess_china_availability(
        {"remotes": [{"url": "https://api.googleapis.com/mcp"}], "required_env": []}
    )
    assert v["verdict"] == "unreachable"
    assert any(r.startswith("blocked_domain:") for r in v["reasons"])


# ---------------------------------------------------------------------------
# search_servers：live 路径 + 过滤
# ---------------------------------------------------------------------------


def test_search_live_parses_and_filters_unreachable_by_default() -> None:
    body = _registry_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("search") == "fs"
        assert request.url.params.get("version") == "latest"
        return httpx.Response(200, json=body)

    result = search_servers("fs", limit=10, client=_mock_client(handler))
    assert result["source"] == "live"
    assert result["error"] is None
    verdicts = {it["name"]: it["china"]["verdict"] for it in result["items"]}
    # unreachable 默认被过滤
    assert "com.example/oauth-remote" not in verdicts
    assert "com.example/blocked" not in verdicts
    assert result["filtered_out"] == 2
    assert verdicts["com.example/local-fs"] == "available"
    assert verdicts["com.example/gcs"] == "risk"


def test_search_live_include_unavailable_keeps_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_registry_payload())

    result = search_servers("fs", client=_mock_client(handler), include_unavailable=True)
    names = {it["name"] for it in result["items"]}
    assert "com.example/oauth-remote" in names
    assert "com.example/blocked" in names
    assert result["filtered_out"] == 0


# ---------------------------------------------------------------------------
# 降级路径
# ---------------------------------------------------------------------------


def test_search_falls_back_to_offline_dataset_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    result = search_servers("", client=_mock_client(handler))
    assert result["source"] == "offline"
    assert "boom" in result["error"]
    # 4 条离线样本里有 2 条 unreachable，默认过滤后剩 2 条
    assert len(result["items"]) == 2
    assert all(it["china"]["verdict"] != "unreachable" for it in result["items"])


def test_search_offline_dataset_query_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    result = search_servers("gcs", client=_mock_client(handler))
    names = [it["name"] for it in result["items"]]
    assert any("gcs" in n for n in names)


def test_search_no_fallback_returns_empty_with_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "server error"})

    result = search_servers("x", client=_mock_client(handler), offline_fallback=False)
    assert result["items"] == []
    assert result["source"] == "offline"
    assert result["error"]


def test_search_http_error_status_triggers_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    result = search_servers("", client=_mock_client(handler))
    assert result["source"] == "offline"
    assert result["items"]  # 离线样本兜底


# ---------------------------------------------------------------------------
# Glama
# ---------------------------------------------------------------------------


def test_parse_glama_html_extracts_rating_value() -> None:
    html = '<html><script>{"ratingValue":4.2,"worstRating":1}</script></html>'
    assert parse_glama_search_html(html) == 4.2
    assert parse_glama_search_html("<html>no rating here</html>") is None


def test_fetch_glama_rating_without_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLAMA_API_KEY", raising=False)
    assert fetch_glama_rating("com.example/x", _mock_client(lambda r: httpx.Response(200))) is None


def test_fetch_glama_rating_with_key_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLAMA_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"rating": 4.5, "ratingCount": 12})

    rating = fetch_glama_rating("com.example/my-srv", _mock_client(handler))
    assert rating == {"score": 4.5, "count": 12, "source": "api"}


def test_fetch_glama_rating_401_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLAMA_API_KEY", "bad")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    assert fetch_glama_rating("com.example/x", _mock_client(handler)) is None
