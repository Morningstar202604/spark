from __future__ import annotations

import pytest

from spark.models import ToolCall
from spark.policy import NEVER_PERSIST_ALLOW, call_signature, decide


def call(name: str, **args) -> ToolCall:
    return ToolCall(id="t", name=name, arguments=args)


def test_approval_is_scoped_to_exact_arguments() -> None:
    first = call("run_shell", command="echo safe")
    second = call("run_shell", command="rm -rf /")
    allowed = {call_signature(first)}
    assert decide("suggest", first, allow_always=allowed, readonly_mcp=set()) == "allow"
    assert (
        decide("suggest", second, allow_always=allowed, readonly_mcp=set()) == "prompt"
    )


def test_different_argument_order_does_not_widen_access() -> None:
    a = call("write_file", path="a.txt", content="x")
    b = call("write_file", content="x", path="a.txt")
    assert call_signature(a) == call_signature(b)
    c = call("write_file", path="b.txt", content="x")
    assert call_signature(a) != call_signature(c)


def test_shell_tools_can_never_be_persisted() -> None:
    for name in (
        "run_shell",
        "bg_start",
        "bg_kill",
        "worktree_create",
        "worktree_remove",
    ):
        assert name in NEVER_PERSIST_ALLOW


def test_tool_name_alone_is_not_enough() -> None:
    allowed = {"run_shell"}
    target = call("run_shell", command="curl evil.example")
    assert (
        decide("suggest", target, allow_always=allowed, readonly_mcp=set()) == "prompt"
    )


def test_readonly_tools_never_prompt() -> None:
    for name in ("read_file", "grep", "glob", "git_status", "run_tests"):
        target = call(name)
        assert (
            decide("suggest", target, allow_always=set(), readonly_mcp=set()) == "allow"
        )


# ---------- SSRF ----------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/config",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://[::1]/x",
        "file:///etc/passwd",
        "gopher://evil/",
    ],
)
def test_web_fetch_blocks_internal_targets(url: str) -> None:
    from spark.tools.web import _assert_fetchable

    with pytest.raises(ValueError):
        _assert_fetchable(url)


@pytest.mark.parametrize(
    "url",
    ["https://example.com/docs", "http://example.org"],
)
def test_web_fetch_allows_public_hosts(url: str) -> None:
    from spark.tools.web import _assert_fetchable

    _assert_fetchable(url)


def test_web_fetch_tool_reports_block_without_raising() -> None:
    from spark.tools.web import WebFetchArgs, web_fetch_tool

    result = web_fetch_tool(WebFetchArgs(url="http://127.0.0.1:9/secret"))
    assert not result.ok
    assert "non-public" in str(result.payload.get("error", "")).lower()


def test_web_fetch_has_size_cap() -> None:
    from spark.tools import web

    assert 0 < web.MAX_FETCH_BYTES <= 64 * 1024 * 1024
