"""Tests for wildcard permission rules in spark.policy."""

from __future__ import annotations

import pytest

from spark.models import ToolCall
from spark.policy import (
    PermissionRule,
    _wildcard_match,
    decide,
    evaluate_permission,
)


# ---------------------------------------------------------------------------
# _wildcard_match
# ---------------------------------------------------------------------------


class TestWildcardMatch:
    def test_star_matches_filename(self):
        assert _wildcard_match("*.py", "foo.py")

    def test_star_does_not_match_different_extension(self):
        assert not _wildcard_match("*.py", "foo.txt")

    def test_double_star_matches_nested_path(self):
        assert _wildcard_match("src/**/*", "src/foo/bar.py")

    def test_double_star_matches_direct_child(self):
        assert _wildcard_match("src/*", "src/foo.py")

    def test_double_star_does_not_match_sibling_dir(self):
        assert not _wildcard_match("src/*", "src/foo/bar.py")

    def test_question_mark_matches_single_char(self):
        assert _wildcard_match("file?.py", "file1.py")

    def test_question_mark_does_not_match_multiple_chars(self):
        assert not _wildcard_match("file?.py", "file12.py")

    def test_literal_match(self):
        assert _wildcard_match("exact/path.txt", "exact/path.txt")

    def test_backslash_normalized(self):
        assert _wildcard_match("src/*.py", "src\\foo.py")

    def test_no_match_different_prefix(self):
        assert not _wildcard_match("src/*", "test/foo.py")


# ---------------------------------------------------------------------------
# evaluate_permission
# ---------------------------------------------------------------------------


class TestEvaluatePermission:
    def test_last_match_wins(self):
        rules = [
            PermissionRule("allow", "*.py"),
            PermissionRule("deny", "secret.py"),
        ]
        assert evaluate_permission(rules, "edit", "secret.py") == "deny"

    def test_no_match_returns_prompt(self):
        rules = [PermissionRule("allow", "*.py")]
        assert evaluate_permission(rules, "edit", "readme.md") == "prompt"

    def test_middle_rule_overridden(self):
        rules = [
            PermissionRule("deny", "**/*.py"),
            PermissionRule("allow", "safe/*.py"),
            PermissionRule("deny", "safe/secret.py"),
        ]
        assert evaluate_permission(rules, "edit", "safe/secret.py") == "deny"
        assert evaluate_permission(rules, "edit", "safe/ok.py") == "allow"
        assert evaluate_permission(rules, "edit", "other/foo.py") == "deny"

    def test_double_star_rule(self):
        rules = [PermissionRule("allow", "**/*.py")]
        assert evaluate_permission(rules, "edit", "deep/nested/foo.py") == "allow"
        assert evaluate_permission(rules, "edit", "README.md") == "prompt"

    def test_empty_rules_returns_prompt(self):
        assert evaluate_permission([], "edit", "anything.py") == "prompt"


# ---------------------------------------------------------------------------
# decide with rules
# ---------------------------------------------------------------------------


def _tc(name: str, **kwargs) -> ToolCall:
    return ToolCall(id="t1", name=name, arguments=kwargs)


class TestDecideWithRules:
    def test_allow_rule_overrides_suggest_mode(self):
        """An allow rule should bypass suggest-mode prompt for write tools."""
        rules = [PermissionRule("allow", "docs/*.md")]
        tool = _tc("write_file", path="docs/readme.md")
        assert (
            decide("suggest", tool, allow_always=set(), readonly_mcp=set(), rules=rules)
            == "allow"
        )

    def test_deny_rule_blocks_write(self):
        """A deny rule intercepts before allow_always hash is checked."""
        rules = [PermissionRule("deny", "secret/*")]
        tool = _tc("write_file", path="secret/keys.txt")
        # Even with full-auto, deny must win
        assert (
            decide("full-auto", tool, allow_always=set(), readonly_mcp=set(), rules=rules)
            == "deny"
        )

    def test_deny_rule_blocks_allow_always(self):
        """deny rule fires before call_signature allow_always lookup."""
        rules = [PermissionRule("deny", "*.lock")]
        tool = _tc("write_file", path="poetry.lock")
        sig = (
            "write_file:"
            "e3b0c44298fc1c149afbf4c8996fb924"  # fake hash
        )
        result = decide(
            "suggest",
            tool,
            allow_always={sig},
            readonly_mcp=set(),
            rules=rules,
        )
        # Even though allow_always doesn't actually contain the real sig,
        # the deny rule should fire regardless.
        assert result == "deny"

    def test_rule_prompt_falls_through(self):
        """A prompt rule lets the original decide logic handle the tool."""
        rules = [PermissionRule("prompt", "src/*")]
        # In suggest mode, write tools default to prompt
        tool = _tc("write_file", path="src/main.py")
        assert (
            decide("suggest", tool, allow_always=set(), readonly_mcp=set(), rules=rules)
            == "prompt"
        )

    def test_no_rules_backward_compatible(self):
        """Without rules, decide behaves exactly as before."""
        tool = _tc("read_file", path="foo.txt")
        assert (
            decide("suggest", tool, allow_always=set(), readonly_mcp=set())
            == "allow"
        )

    def test_no_rules_keyword_backward_compatible(self):
        """Passing rules=None is equivalent to omitting rules."""
        tool = _tc("run_shell", command="ls")
        assert (
            decide("suggest", tool, allow_always=set(), readonly_mcp=set(), rules=None)
            == "prompt"
        )

    def test_shell_command_rule(self):
        """Shell tools use command string as resource."""
        rules = [PermissionRule("allow", "git *")]
        tool = _tc("run_shell", command="git status")
        assert (
            decide("suggest", tool, allow_always=set(), readonly_mcp=set(), rules=rules)
            == "allow"
        )

    def test_deny_shell_command(self):
        rules = [PermissionRule("deny", "rm -rf **")]
        tool = _tc("run_shell", command="rm -rf /")
        assert (
            decide("full-auto", tool, allow_always=set(), readonly_mcp=set(), rules=rules)
            == "deny"
        )

    def test_git_add_paths_resource(self):
        """git_add uses first path from list as resource."""
        rules = [PermissionRule("allow", "src/**/*.py")]
        tool = _tc("git_add", paths=["src/app/main.py"])
        assert (
            decide("suggest", tool, allow_always=set(), readonly_mcp=set(), rules=rules)
            == "allow"
        )

    def test_empty_rules_list_falls_through(self):
        """An empty rules list is the same as no rules."""
        tool = _tc("grep", pattern="test")
        assert (
            decide("suggest", tool, allow_always=set(), readonly_mcp=set(), rules=[])
            == "allow"  # grep is read-only
        )

    def test_rules_empty_defaults_to_prompt(self):
        """An empty rules list does not interfere with decide's built-in logic."""
        tool = _tc("write_file", path="src/main.py", content="x")
        assert (
            decide(
                "suggest", tool, allow_always=set(), readonly_mcp=set(), rules=[]
            )
            == "prompt"
        )

    def test_tool_without_resource_extraction(self):
        """Tools not in the extraction map get empty string resource."""
        rules = [PermissionRule("allow", "")]
        # read_file is read-only, always allowed
        tool = _tc("read_file", path="foo.txt")
        assert (
            decide("suggest", tool, allow_always=set(), readonly_mcp=set(), rules=rules)
            == "allow"
        )
