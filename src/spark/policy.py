from __future__ import annotations

import hashlib
import json
from typing import Literal

from spark.config import ApprovalMode
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


def decide(
    mode: ApprovalMode,
    tool: ToolCall,
    *,
    allow_always: set[str],
    readonly_mcp: set[str],
) -> DecisionKind:
    # Approvals are bound to the exact (tool, arguments) pair, so approving one
    # command can never silently authorize a different one later.
    if call_signature(tool) in allow_always:
        return "allow"
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
