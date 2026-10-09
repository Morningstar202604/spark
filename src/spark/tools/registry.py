"""Built-in tool schema registry and dispatcher."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from spark.config import SparkConfig
from spark.models import ToolCall, ToolResult
from spark.sandbox import WorkdirSandbox
from spark.tools import bg, fs, gitops, notebook, search, shell, web

MCP_PREFIX = "mcp__"


@dataclass
class ToolContext:
    sandbox: WorkdirSandbox
    config: SparkConfig
    mcp_call: Callable[[str, dict[str, Any]], Awaitable[ToolResult]] | None = None
    task_runner: Any = None
    task_runner_parallel: Any = None


ToolHandler = Callable[[ToolContext, dict[str, Any]], ToolResult]


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


class ToolRegistry:
    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self._extra_schemas: list[dict[str, Any]] = []
        self._schema_revision = 0
        self.readonly_mcp: set[str] = set()
        self.plan: list[dict[str, Any]] = []
        self._registered_scopes: dict[str, set[str]] = {}

    @property
    def schema_revision(self) -> int:
        return self._schema_revision

    def register_scope(self, scope_id: str, schemas: list[dict[str, Any]]) -> None:
        """Register multiple schemas under *scope_id*; replaces previous registration."""
        if scope_id in self._registered_scopes:
            self.unregister_scope(scope_id)
        names: set[str] = set()
        for schema in schemas:
            name = schema.get("function", {}).get("name", "")
            if not name:
                continue
            self._extra_schemas.append(schema)
            names.add(name)
        self._registered_scopes[scope_id] = names
        self._schema_revision += 1

    def unregister_scope(self, scope_id: str) -> None:
        """Remove every schema/name registered under *scope_id*."""
        names = self._registered_scopes.pop(scope_id, None)
        if names is None:
            return
        self._extra_schemas = [
            s for s in self._extra_schemas
            if s.get("function", {}).get("name", "") not in names
        ]
        self.readonly_mcp -= names
        self._schema_revision += 1

    def stale_tools(self, tool_names: list[str]) -> list[str]:
        """Return subset of *tool_names* no longer in the schema registry (MCP-only)."""
        available: set[str] = set()
        for schema in self._extra_schemas:
            n = schema.get("function", {}).get("name", "")
            if n:
                available.add(n)
        return [n for n in tool_names if n.startswith(MCP_PREFIX) and n not in available]

    def _mcp_name_available(self, name: str) -> bool:
        """True if *name* matches a schema in `_extra_schemas`."""
        return any(
            s.get("function", {}).get("name", "") == name for s in self._extra_schemas
        )

    def schemas(self) -> list[dict[str, Any]]:
        builtin = [
            _schema("read_file", "Read a UTF-8 file inside workdir.",
                {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
                ["path"]),
            _schema("list_dir", "List a directory inside workdir.",
                {"path": {"type": "string"}, "max_entries": {"type": "integer"}}, []),
            _schema("write_file", "Write a complete UTF-8 file inside workdir.",
                {"path": {"type": "string"}, "content": {"type": "string"}},
                ["path", "content"]),
            _schema("apply_patch", "Replace exactly one occurrence of old_text in a file.",
                {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                ["path", "old_text", "new_text"]),
            _schema("run_shell", "Run a shell command inside workdir.",
                {"command": {"type": "string"}, "cwd": {"type": "string"}},
                ["command"]),
            _schema("grep",
                "Search file contents inside the workdir with a regular expression. "
                "Returns path:line:text matches. Automatically skips .git, node_modules, venvs and build dirs.",
                {"pattern": {"type": "string", "description": "regular expression"},
                 "path": {"type": "string", "description": "subdirectory to search, default '.'"},
                 "glob": {"type": "string", "description": "optional filename filter like '*.py'"},
                 "max_results": {"type": "integer", "description": "default 50"}},
                ["pattern"]),
            _schema("glob",
                "Find files inside the workdir by filename pattern, e.g. 'src/**/*.ts' or '*.md'.",
                {"pattern": {"type": "string"},
                 "path": {"type": "string", "description": "subdirectory to search, default '.'"},
                 "max_results": {"type": "integer", "description": "default 200"}},
                ["pattern"]),
            _schema("web_search",
                "Search the public web (Bing) for current information: docs, error messages, library versions, news. "
                "Returns title/url/snippet results. Follow up with web_fetch to read a promising page.",
                {"query": {"type": "string"}, "max_results": {"type": "integer", "description": "default 8"}},
                ["query"]),
            _schema("web_fetch",
                "Fetch a web page by URL and return its readable text content (HTML converted to text). "
                "Use after web_search, or to read documentation/gists/raw files.",
                {"url": {"type": "string"}, "max_chars": {"type": "integer", "description": "default 12000"}},
                ["url"]),
            _schema("task",
                "Spawn one or more sub-agents, each with its own context window, to handle self-contained "
                "research or exploration work. Pass 'prompt' for a single sub-agent, or 'tasks' (array of "
                "{prompt}) to run up to 4 sub-agents in parallel - e.g. explore different modules at once. "
                "Sub-agents have all tools except task; you only receive their final summaries.",
                {"prompt": {"type": "string", "description": "single sub-agent mode: complete instructions"},
                 "tasks": {"type": "array",
                           "description": 'parallel mode: up to 4 items, each {"prompt": "..."}',
                           "items": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}},
                []),
            _schema("read_notebook",
                "Read a Jupyter notebook (.ipynb): list cells with index, type, source and output previews.",
                {"path": {"type": "string"}, "max_cells": {"type": "integer"}},
                ["path"]),
            _schema("notebook_edit",
                "Replace the source of one cell in a Jupyter notebook (.ipynb) by cell index. Clears that cell's outputs.",
                {"path": {"type": "string"}, "cell_index": {"type": "integer"}, "new_source": {"type": "string"},
                 "cell_type": {"type": "string", "description": "optional: change cell to 'code' or 'markdown'"}},
                ["path", "cell_index", "new_source"]),
            _schema("update_plan",
                "Maintain a visible task plan for complex multi-step work. "
                "Replace the whole plan each time: list every step with its status "
                "(pending / in_progress / completed). Call this whenever the plan changes, "
                "including marking steps done as you finish them.",
                {"steps": {"type": "array",
                           "items": {"type": "object",
                                      "properties": {"title": {"type": "string"},
                                                     "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
                                      "required": ["title", "status"]}}},
                ["steps"]),
            _schema("bg_start",
                "Run a shell command in the background (dev servers, watchers, long builds) and return "
                "immediately with a job_id. Poll progress with bg_output; stop with bg_kill. "
                "The command still passes sandbox policy checks.",
                {"command": {"type": "string"}, "cwd": {"type": "string"}},
                ["command"]),
            _schema("bg_output",
                "Read the accumulated output of a background job started with bg_start. "
                "Returns running state, exit code when finished, and the last N chars of output.",
                {"job_id": {"type": "string"},
                 "tail": {"type": "integer", "description": "chars of output tail, default 8000"}},
                ["job_id"]),
            _schema("bg_kill", "Terminate a running background job started with bg_start.",
                {"job_id": {"type": "string"}}, ["job_id"]),
            _schema("bg_list", "List all background jobs from this server run with their states.",
                {}, []),
            _schema("git_status",
                "Show git working tree status (branch + changed files) for a repo inside workdir.",
                {"path": {"type": "string", "description": "repo directory, default '.'"}},
                []),
            _schema("git_diff",
                "Show git diff for a repo inside workdir. Use staged=true for staged changes, or ref like 'HEAD~1' to diff against it.",
                {"path": {"type": "string"},
                 "staged": {"type": "boolean", "description": "show staged changes only"},
                 "ref": {"type": "string", "description": "optional commit-ish to diff against"}},
                []),
            _schema("git_log",
                "Show recent commit history: short hash, author, relative date, subject.",
                {"path": {"type": "string"},
                 "max_count": {"type": "integer", "description": "default 20, max 100"}},
                []),
            _schema("git_branch", "Show the current branch name of a repo inside workdir.",
                {"path": {"type": "string"}}, []),
            _schema("git_add",
                "Stage files for commit in a repo inside workdir. Paths are sandbox-checked.",
                {"paths": {"type": "array", "items": {"type": "string"}, "description": "files or directories to stage"},
                 "path": {"type": "string", "description": "repo directory, default '.'"}},
                ["paths"]),
            _schema("git_commit",
                "Commit staged changes with a message. Set add_all=true to stage every change first. "
                "Push/pull/reset stay out of scope - tell the user to run them manually.",
                {"message": {"type": "string"}, "path": {"type": "string"},
                 "add_all": {"type": "boolean", "description": "stage all changes before committing"}},
                ["message"]),
            _schema("run_tests",
                "Run the repository's test suite and return pass/fail plus captured output. "
                "Detects pytest / npm test / make test automatically; pass 'command' to override. "
                "Use this to close the loop after a code change instead of guessing whether the change works.",
                {"path": {"type": "string", "description": "subdirectory, default '.'"},
                 "command": {"type": "string", "description": "optional explicit command override"},
                 "timeout_sec": {"type": "integer", "description": "default 600"}},
                []),
            _schema("worktree_list", "List git worktrees attached to this repository.",
                {"path": {"type": "string"}}, []),
            _schema("worktree_create",
                "Create an isolated git worktree on a new branch, so concurrent work does not "
                "disturb the current working tree. Returns the worktree path to operate in.",
                {"branch": {"type": "string", "description": "new branch name, e.g. feature/login"},
                 "path": {"type": "string", "description": "repo subdirectory, default '.'"},
                 "base": {"type": "string", "description": "optional base ref"}},
                ["branch"]),
            _schema("worktree_remove",
                "Remove a git worktree previously created by worktree_create.",
                {"worktree": {"type": "string", "description": "worktree path returned by worktree_create"},
                 "force": {"type": "boolean", "description": "remove even when dirty"}},
                ["worktree"]),
            _schema("load_skill",
                "Load a skill's full content by name. Use this when the system prompt "
                "shows <available_skills> and you need the full instructions for a specific skill. "
                "Returns the skill content to follow.",
                {"name": {"type": "string", "description": "skill name from available_skills"}},
                ["name"]),
        ]
        return builtin + self._extra_schemas

    def add_mcp_schema(self, schema: dict[str, Any]) -> None:
        self._extra_schemas.append(schema)
        self._schema_revision += 1

    def approval_summary(self, call: ToolCall) -> tuple[str, str | None]:
        if call.name == "write_file":
            path = str(call.arguments.get("path", ""))
            content = str(call.arguments.get("content", ""))
            return fs.write_summary(path, content), fs.write_summary(path, content)
        if call.name == "apply_patch":
            diff = fs.patch_diff(
                str(call.arguments.get("path", "")),
                str(call.arguments.get("old_text", "")),
                str(call.arguments.get("new_text", "")),
            )
            return diff, diff
        if call.name == "run_shell":
            cmd = str(call.arguments.get("command", ""))[:200]
            cwd = str(call.arguments.get("cwd") or ".")
            text = f"run_shell cwd={cwd}\n{cmd}"
            return text, None
        if call.name == "bg_start":
            cmd = str(call.arguments.get("command", ""))[:200]
            cwd = str(call.arguments.get("cwd") or ".")
            text = f"bg_start cwd={cwd}\n{cmd}"
            return text, None
        return f"{call.name} {call.arguments}", None

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            if call.name.startswith(MCP_PREFIX) and not self._mcp_name_available(call.name):
                return ToolResult(
                    ok=False,
                    payload={"error": f"Stale tool call: {call.name} is no longer available"},
                )
            if call.name == "read_file":
                return fs.read_file(
                    self.ctx.sandbox, fs.ReadFileArgs.model_validate(call.arguments))
            if call.name == "list_dir":
                return fs.list_dir(
                    self.ctx.sandbox, fs.ListDirArgs.model_validate(call.arguments))
            if call.name == "write_file":
                return fs.write_file(
                    self.ctx.sandbox, fs.WriteFileArgs.model_validate(call.arguments))
            if call.name == "apply_patch":
                return fs.apply_patch(
                    self.ctx.sandbox, fs.ApplyPatchArgs.model_validate(call.arguments))
            if call.name == "run_shell":
                return shell.run_shell(
                    self.ctx.sandbox,
                    shell.RunShellArgs.model_validate(call.arguments),
                    timeout_sec=self.ctx.config.agent.shell_timeout_sec,
                    max_output_chars=self.ctx.config.agent.max_output_chars,
                )
            if call.name == "bg_start":
                return bg.bg_start_tool(self.ctx.sandbox, call.arguments)
            if call.name == "bg_output":
                return bg.bg_output_tool(call.arguments)
            if call.name == "bg_kill":
                return bg.bg_kill_tool(call.arguments)
            if call.name == "bg_list":
                return bg.bg_list_tool()
            if call.name == "git_status":
                return gitops.git_status(
                    self.ctx.sandbox, gitops.GitStatusArgs.model_validate(call.arguments))
            if call.name == "git_diff":
                return gitops.git_diff(
                    self.ctx.sandbox, gitops.GitDiffArgs.model_validate(call.arguments))
            if call.name == "git_log":
                return gitops.git_log(
                    self.ctx.sandbox, gitops.GitLogArgs.model_validate(call.arguments))
            if call.name == "git_branch":
                return gitops.git_branch(
                    self.ctx.sandbox, gitops.GitBranchArgs.model_validate(call.arguments))
            if call.name == "git_add":
                return gitops.git_add(
                    self.ctx.sandbox, gitops.GitAddArgs.model_validate(call.arguments))
            if call.name == "git_commit":
                return gitops.git_commit(
                    self.ctx.sandbox, gitops.GitCommitArgs.model_validate(call.arguments))
            if call.name == "run_tests":
                return gitops.run_tests(
                    self.ctx.sandbox, gitops.RunTestsArgs.model_validate(call.arguments))
            if call.name == "worktree_list":
                return gitops.worktree_list(
                    self.ctx.sandbox, gitops.WorktreeListArgs.model_validate(call.arguments))
            if call.name == "worktree_create":
                return gitops.worktree_create(
                    self.ctx.sandbox, gitops.WorktreeCreateArgs.model_validate(call.arguments))
            if call.name == "worktree_remove":
                return gitops.worktree_remove(
                    self.ctx.sandbox, gitops.WorktreeRemoveArgs.model_validate(call.arguments))
            if call.name == "grep":
                return search.grep_tool(
                    self.ctx.sandbox, search.GrepArgs.model_validate(call.arguments))
            if call.name == "glob":
                return search.glob_tool(
                    self.ctx.sandbox, search.GlobArgs.model_validate(call.arguments))
            if call.name == "web_search":
                return web.web_search_tool(web.WebSearchArgs.model_validate(call.arguments))
            if call.name == "web_fetch":
                return web.web_fetch_tool(web.WebFetchArgs.model_validate(call.arguments))
            if call.name == "read_notebook":
                return notebook.read_notebook(
                    self.ctx.sandbox, notebook.ReadNotebookArgs.model_validate(call.arguments))
            if call.name == "notebook_edit":
                return notebook.notebook_edit(
                    self.ctx.sandbox, notebook.NotebookEditArgs.model_validate(call.arguments))
            if call.name == "load_skill":
                from pathlib import Path as _Path
                from spark.skills import discover_skills, format_skill_invocation
                name = call.arguments.get("name", "")
                workdir = self.ctx.sandbox.root
                skills = discover_skills(_Path(workdir))
                for s in skills:
                    if s.name == name:
                        return ToolResult(ok=True, payload={"content": format_skill_invocation(s)})
                return ToolResult(ok=False, payload={"error": f"skill not found: {name}"})
            if call.name == "update_plan":
                steps = call.arguments.get("steps")
                if not isinstance(steps, list):
                    return ToolResult(ok=False, payload={"error": "steps must be a list"})
                clean: list[dict[str, Any]] = []
                for step in steps:
                    if not isinstance(step, dict) or not str(step.get("title", "")).strip():
                        continue
                    status = step.get("status", "pending")
                    clean.append(
                        {"title": str(step["title"]).strip(),
                         "status": status if status in {"pending", "in_progress", "completed"} else "pending"})
                self.plan = clean
                return ToolResult(ok=True, payload={"steps": clean})
            if call.name.startswith("mcp__") and self.ctx.mcp_call is not None:
                return await self.ctx.mcp_call(call.name, call.arguments)
        except Exception as exc:
            return ToolResult(ok=False, payload={"error": str(exc)})
        return ToolResult(ok=False, payload={"error": f"Unknown tool: {call.name}"})
