"""Tool-call approval policy and wildcard permission rules."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from spark.config import ApprovalMode, PermissionAction, PermissionRule
from spark.models import ToolCall

DecisionKind = Literal["allow", "prompt", "deny"]

READONLY_TOOLS = {
    "read_file",
    "list_dir",
    "update_plan",
    "grep",
    "glob",
    "web_search",
    "web_fetch",
    "read_notebook",
    "task",
    "bg_output",
    "bg_list",
    "git_status",
    "git_diff",
    "git_log",
    "git_branch",
    "run_tests",
    "worktree_list",
}
WRITE_TOOLS = {"write_file", "apply_patch", "notebook_edit", "git_add", "git_commit"}
SHELL_TOOLS = {"run_shell", "bg_start", "bg_kill"}
# Shell and worktree-mutating tools can have unbounded blast radius, so they may
# never be granted a permanent "always allow".
NEVER_PERSIST_ALLOW = SHELL_TOOLS | {"worktree_create", "worktree_remove"}


# ---------------------------------------------------------------------------
# Wildcard matching
# ---------------------------------------------------------------------------


def _wildcard_match(pattern: str, value: str) -> bool:
    """Match value against a glob-style pattern for permission rules.

    Supports: '*' (any chars except '/'), '**' (any chars including '/'),
    '?' (single char).  Normalises path separators so backslashes and
    forward slashes are equivalent.
    """
    normalized_pattern = pattern.replace("\\", "/")
    normalized_value = value.replace("\\", "/")
    return _glob_match(normalized_pattern, normalized_value)


def _glob_match(pattern: str, value: str) -> bool:
    """Glob matcher: '*' stays within a single path segment, '**' crosses '/'."""
    p_len, v_len = len(pattern), len(value)

    def _m(pi: int, vi: int) -> bool:  # noqa: E501
        while pi < p_len:
            c = pattern[pi]
            if c == "*" and pi + 1 < p_len and pattern[pi + 1] == "*":  # '**'
                pn = pi + 2
                if pn < p_len and pattern[pn] == "/":
                    pn += 1
                for vn in range(vi, v_len + 1):
                    if vn not in (vi, v_len) and value[vn - 1] != "/":
                        continue
                    if _m(pn, vn):
                        return True
                return False
            if c == "*":  # single-segment '*', stops at '/'
                pn = pi + 1
                for vn in range(vi, v_len):
                    if value[vn] == "/":
                        break
                    if _m(pn, vn + 1):
                        return True
                return _m(pn, vi)
            if c == "?":
                if vi >= v_len or value[vi] == "/":
                    return False
                pi, vi = pi + 1, vi + 1
                continue
            if vi >= v_len or value[vi] != c:
                return False
            pi, vi = pi + 1, vi + 1
        return vi == v_len

    return _m(0, 0)


# ---------------------------------------------------------------------------
# Permission rules
# ---------------------------------------------------------------------------


def evaluate_permission(
    rules: list[PermissionRule], action: str, resource: str
) -> PermissionAction:
    """Return the decision from the last matching rule, or ``"prompt"`` default.

    Rules are evaluated in order; the *last* rule whose ``resource`` glob matches
    ``resource`` wins.  If no rule matches, ``"prompt"`` is returned.
    """
    matched: PermissionAction = "prompt"
    for rule in rules:
        if _wildcard_match(rule.resource, resource):
            matched = rule.action
    return matched


# ---------------------------------------------------------------------------
# Helpers (unchanged)
# ---------------------------------------------------------------------------


def call_signature(tool: ToolCall) -> str:
    """Stable signature of (tool name, arguments) used for scoped approvals."""
    payload = json.dumps(
        {"name": tool.name, "arguments": tool.arguments},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return f"{tool.name}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def is_mcp_tool(name: str) -> bool:
    return name.startswith("mcp__")


def mcp_parts(name: str) -> tuple[str, str] | None:
    if not is_mcp_tool(name):
        return None
    parts = name.split("__", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


# ---------------------------------------------------------------------------
# Resource extraction
# ---------------------------------------------------------------------------


def _extract_resource(tool: ToolCall) -> str:
    """Derive the resource string from a tool call's arguments.

    Used by :func:`decide` when evaluating wildcard permission rules.
    """
    args = tool.arguments
    if tool.name in {"write_file", "apply_patch", "notebook_edit"}:
        return args.get("path", "")
    if tool.name in {"run_shell", "bg_start"}:
        return args.get("command", "")
    if tool.name in {"git_add", "git_commit"}:
        paths = args.get("paths", [])
        if isinstance(paths, list) and paths:
            return paths[0]
        return ""
    return ""


# ---------------------------------------------------------------------------
# Decision entry point
# ---------------------------------------------------------------------------


def decide(
    mode: ApprovalMode,
    tool: ToolCall,
    *,
    allow_always: set[str],
    readonly_mcp: set[str],
    rules: list[PermissionRule] | None = None,
) -> DecisionKind:
    # Approvals are bound to the exact (tool, arguments) pair, so approving one
    # command can never silently authorize a different one later.
    if call_signature(tool) in allow_always:
        return "allow"

    # Wildcard rule evaluation
    if rules:
        resource = _extract_resource(tool)
        rule_decision = evaluate_permission(rules, tool.name, resource)
        if rule_decision == "deny":
            return "deny"
        if rule_decision == "allow":
            return "allow"
        # rule_decision == "prompt" falls through to the original logic below

    if tool.name in READONLY_TOOLS:
        return "allow"
    if tool.name in readonly_mcp:
        return "allow"
    if tool.name in WRITE_TOOLS:
        if mode == "suggest":
            return "prompt"
        return "allow"
    if tool.name in SHELL_TOOLS or is_mcp_tool(tool.name):
        if mode == "full-auto":
            return "allow"
        return "prompt"
    return "prompt"
