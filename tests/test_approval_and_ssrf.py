from __future__ import annotations

import pytest

from spark.config import PermissionRule
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
    for name in ("read_file", "grep", "glob", "git_status", "list_dir"):
        target = call(name)
        assert (
            decide("suggest", target, allow_always=set(), readonly_mcp=set()) == "allow"
        )


def test_tools_that_execute_or_reach_out_always_prompt() -> None:
    """``run_tests`` runs the repo's own declared test command, ``task`` spawns
    sub-agents and ``web_fetch`` can be aimed at internal endpoints — none of
    them is read-only, so ``suggest`` must not auto-approve them."""
    for name in ("run_tests", "task", "web_fetch"):
        target = call(name, command="npm test", prompt="x", url="http://example.com")
        assert (
            decide("suggest", target, allow_always=set(), readonly_mcp=set()) == "prompt"
        )
        assert (
            decide("full-auto", target, allow_always=set(), readonly_mcp=set()) == "allow"
        )


def test_wildcard_rule_cannot_approve_a_tool_without_a_resource() -> None:
    """A blanket ``allow **`` rule must not silently disable approvals for tools
    whose resource cannot be derived from their arguments."""
    rules = [PermissionRule(action="allow", resource="**")]
    for name in ("task", "web_fetch"):
        target = call(name, prompt="x", url="http://example.com")
        assert (
            decide(
                "suggest",
                target,
                allow_always=set(),
                readonly_mcp=set(),
                rules=rules,
            )
            == "prompt"
        )


def test_permission_rule_tool_axis_scopes_the_grant() -> None:
    rules = [PermissionRule(action="allow", resource="git *", tool="run_shell")]
    assert (
        decide(
            "suggest",
            call("run_shell", command="git status"),
            allow_always=set(),
            readonly_mcp=set(),
            rules=rules,
        )
        == "allow"
    )
    # Same string, different tool: the grant must not leak across tools.
    assert (
        decide(
            "suggest",
            call("write_file", path="git status"),
            allow_always=set(),
            readonly_mcp=set(),
            rules=rules,
        )
        == "prompt"
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
    from urllib.parse import urlparse

    from spark.tools.web import _assert_fetchable, _is_public_host

    # The guard returns None on success; assert both the return value and the
    # underlying classification, so a regression to "allow everything" fails the
    # block-list test while a regression to "block everything" fails this one.
    assert _assert_fetchable(url) is None
    assert _is_public_host(urlparse(url).hostname or "")


def test_web_fetch_tool_reports_block_without_raising() -> None:
    from spark.tools.web import WebFetchArgs, web_fetch_tool

    result = web_fetch_tool(WebFetchArgs(url="http://127.0.0.1:9/secret"))
    assert not result.ok
    assert "non-public" in str(result.payload.get("error", "")).lower()


def test_web_fetch_has_size_cap() -> None:
    from spark.tools import web

    assert 0 < web.MAX_FETCH_BYTES <= 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# Audit F-001: the old predicate enumerated bad ranges and kept missing
# metadata endpoints that are neither "private" nor "reserved".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://100.100.100.200/latest/meta-data/",  # Aliyun / Tencent
        "http://100.64.0.1/",                        # CGNAT space
        "http://169.254.169.254/latest/meta-data/",  # AWS / GCP / Azure
        "http://169.254.170.2/v2/credentials",       # AWS ECS
        "http://2130706433/",                        # decimal 127.0.0.1
        "http://[fd00::1]/",                         # IPv6 ULA
        "http://[fe80::1]/",                         # IPv6 link-local
        "http://[::ffff:127.0.0.1]/",                # IPv4-mapped loopback
    ],
)
def test_web_fetch_blocks_metadata_and_encoded_targets(url: str) -> None:
    from spark.tools.web import _assert_fetchable

    with pytest.raises(ValueError):
        _assert_fetchable(url)


def test_public_targets_still_allowed() -> None:
    """The guard must not become "block everything" (the other failure mode)."""
    from spark.tools.web import _assert_fetchable

    for url in ("https://example.com/docs", "http://1.1.1.1/", "https://api.github.com/"):
        assert _assert_fetchable(url) is None
