"""Built-in tool schema registry and dispatcher."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from spark.config import SparkConfig
from spark.edit.tool import ApplyEditArgs, apply_edit, apply_edit_diff
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
            _schema("read_file",
                "Read a UTF-8 file inside workdir. Returns at most 2000 lines from offset "
                "(default first 200) plus total_lines and truncated, so you know whether to "
                "continue with a larger offset.",
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
            _schema("apply_edit",
                "Rewrite a file using aider-style SEARCH/REPLACE blocks, OR whole-file content. "
                "For targeted edits send one or more blocks like:\n"
                "<<<<<<< SEARCH\n<exact current text, must be UNIQUE in the file>\n=======\n<new text>\n>>>>>>> REPLACE\n"
                "Each SEARCH must match exactly once (fuzzy whitespace-tolerant matching is tried); "
                "ambiguous/missing matches are refused without touching the file. "
                "If content has no SEARCH block markers it is treated as a whole-file rewrite. "
                "Edits to .py/.ts/.tsx/.js are gated by tree-sitter: a syntax error is refused.",
                {"path": {"type": "string"},
                 "content": {"type": "string",
                             "description": "either SEARCH/REPLACE block(s) or the complete new file content"}},
                ["path", "content"]),
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
            return fs.write_summary(path, content), fs.diff_for_write(
                self.ctx.sandbox, path, content
            )
        if call.name == "apply_patch":
            diff = fs.patch_diff(
                self.ctx.sandbox,
                str(call.arguments.get("path", "")),
                str(call.arguments.get("old_text", "")),
                str(call.arguments.get("new_text", "")),
            )
            return diff, diff
        if call.name == "apply_edit":
            diff = apply_edit_diff(
                self.ctx.sandbox,
                str(call.arguments.get("path", "")),
                str(call.arguments.get("content", "")),
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

    def _build_dispatch_table(self) -> dict[str, Callable]:
        """Return name→handler map.

        Handlers are plain callables: sync functions return ToolResult directly,
        async functions return a coroutine. ``execute`` detects which is which.
        """
        ctx = self.ctx

        out: dict[str, Callable] = {
            # --- fs ---
            "read_file": lambda a: fs.read_file(ctx.sandbox, fs.ReadFileArgs.model_validate(a)),
            "list_dir": lambda a: fs.list_dir(ctx.sandbox, fs.ListDirArgs.model_validate(a)),
            "write_file": lambda a: fs.write_file(ctx.sandbox, fs.WriteFileArgs.model_validate(a)),
            "apply_patch": lambda a: fs.apply_patch(ctx.sandbox, fs.ApplyPatchArgs.model_validate(a)),
            "apply_edit": lambda a: apply_edit(ctx.sandbox, ApplyEditArgs.model_validate(a)),
            # --- shell ---
            "run_shell": lambda a: shell.run_shell(
                ctx.sandbox, shell.RunShellArgs.model_validate(a),
                timeout_sec=ctx.config.agent.shell_timeout_sec,
                max_output_chars=ctx.config.agent.max_output_chars,
            ),
            # --- bg ---
            "bg_start": lambda a: bg.bg_start_tool(ctx.sandbox, a),
            "bg_output": lambda a: bg.bg_output_tool(a),
            "bg_kill": lambda a: bg.bg_kill_tool(a),
            "bg_list": lambda a: bg.bg_list_tool(),
            # --- git ---
            "git_status": lambda a: gitops.git_status(ctx.sandbox, gitops.GitStatusArgs.model_validate(a)),
            "git_diff": lambda a: gitops.git_diff(ctx.sandbox, gitops.GitDiffArgs.model_validate(a)),
            "git_log": lambda a: gitops.git_log(ctx.sandbox, gitops.GitLogArgs.model_validate(a)),
            "git_branch": lambda a: gitops.git_branch(ctx.sandbox, gitops.GitBranchArgs.model_validate(a)),
            "git_add": lambda a: gitops.git_add(ctx.sandbox, gitops.GitAddArgs.model_validate(a)),
            "git_commit": lambda a: gitops.git_commit(ctx.sandbox, gitops.GitCommitArgs.model_validate(a)),
            "run_tests": lambda a: gitops.run_tests(ctx.sandbox, gitops.RunTestsArgs.model_validate(a)),
            "worktree_list": lambda a: gitops.worktree_list(ctx.sandbox, gitops.WorktreeListArgs.model_validate(a)),
            "worktree_create": lambda a: gitops.worktree_create(ctx.sandbox, gitops.WorktreeCreateArgs.model_validate(a)),
            "worktree_remove": lambda a: gitops.worktree_remove(ctx.sandbox, gitops.WorktreeRemoveArgs.model_validate(a)),
            # --- search ---
            "grep": lambda a: search.grep_tool(ctx.sandbox, search.GrepArgs.model_validate(a)),
            "glob": lambda a: search.glob_tool(ctx.sandbox, search.GlobArgs.model_validate(a)),
            # --- web ---
            "web_search": lambda a: web.web_search_tool(web.WebSearchArgs.model_validate(a)),
            "web_fetch": lambda a: web.web_fetch_tool(web.WebFetchArgs.model_validate(a)),
            # --- notebook ---
            "read_notebook": lambda a: notebook.read_notebook(ctx.sandbox, notebook.ReadNotebookArgs.model_validate(a)),
            "notebook_edit": lambda a: notebook.notebook_edit(ctx.sandbox, notebook.NotebookEditArgs.model_validate(a)),
        }
        return out

    async def execute(self, call: ToolCall) -> ToolResult:
        name = call.name
        try:
            if name.startswith(MCP_PREFIX) and not self._mcp_name_available(name):
                return ToolResult(
                    ok=False,
                    payload={"error": f"Stale tool call: {name} is no longer available"},
                )

            if name == "update_plan":
                return self._handle_update_plan(call.arguments)

            if name == "load_skill":
                return self._handle_load_skill(call.arguments)

            if name == "task":
                return await self._handle_task(call)

            if name.startswith("mcp__") and self.ctx.mcp_call is not None:
                result = await self.ctx.mcp_call(name, call.arguments)
                # 第三方 MCP 输出不可信：包裹 untrusted 围栏（防注入）
                if result.ok and result.payload is not None:
                    from spark.tools.untrusted import payload_text, wrap

                    result.payload = {"content": wrap(payload_text(result.payload))}
                return result

            # Built-in tools dispatched via table
            table = self._build_dispatch_table()
            handler = table.get(name)
            if handler is not None:
                result = handler(call.arguments)
                if hasattr(result, "__await__"):
                    result = await result
                return result

        except Exception as exc:
            return ToolResult(ok=False, payload={"error": str(exc)})
        return ToolResult(ok=False, payload={"error": f"Unknown tool: {name}"})

    def _handle_update_plan(self, arguments: dict[str, Any]) -> ToolResult:
        steps = arguments.get("steps")
        if not isinstance(steps, list):
            return ToolResult(ok=False, payload={"error": "steps must be a list"})
        clean: list[dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict) or not str(step.get("title", "")).strip():
                continue
            status = step.get("status", "pending")
            clean.append({
                "title": str(step["title"]).strip(),
                "status": status if status in {"pending", "in_progress", "completed"} else "pending",
            })
        self.plan = clean
        return ToolResult(ok=True, payload={"steps": clean})

    def _handle_load_skill(self, arguments: dict[str, Any]) -> ToolResult:
        from pathlib import Path as _Path

        from spark.skills import discover_skills, format_skill_invocation

        name = arguments.get("name", "")
        skills = discover_skills(_Path(self.ctx.sandbox.root))
        for s in skills:
            if s.name == name:
                return ToolResult(ok=True, payload={"content": format_skill_invocation(s)})
        return ToolResult(ok=False, payload={"error": f"skill not found: {name}"})

    async def _handle_task(self, call: ToolCall) -> ToolResult:
        """Placeholder: task tool is handled specially by AgentLoop before reach here."""
        return ToolResult(ok=False, payload={"error": "task should be intercepted by AgentLoop"})
