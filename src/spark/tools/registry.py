from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from spark.config import SparkConfig
from spark.models import ToolCall, ToolResult
from spark.sandbox import WorkdirSandbox
from spark.tools import bg, fs, notebook, search, shell, web


@dataclass
class ToolContext:
    sandbox: WorkdirSandbox
    config: SparkConfig
    mcp_call: Callable[[str, dict[str, Any]], Awaitable[ToolResult]] | None = None
    task_runner: Any = None  # async (prompt) -> AsyncIterator[TurnEvent]; set by AgentLoop for sub-agents
    task_runner_parallel: Any = None  # async (prompts) -> AsyncIterator[TurnEvent]; parallel sub-agents


ToolHandler = Callable[[ToolContext, dict[str, Any]], ToolResult]


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


class ToolRegistry:
    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self._extra_schemas: list[dict[str, Any]] = []
        self.readonly_mcp: set[str] = set()
        self.plan: list[dict[str, Any]] = []

    def schemas(self) -> list[dict[str, Any]]:
        builtin = [
            _schema(
                "read_file",
                "Read a UTF-8 file inside workdir.",
                {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                ["path"],
            ),
            _schema(
                "list_dir",
                "List a directory inside workdir.",
                {"path": {"type": "string"}, "max_entries": {"type": "integer"}},
                [],
            ),
            _schema(
                "write_file",
                "Write a complete UTF-8 file inside workdir.",
                {"path": {"type": "string"}, "content": {"type": "string"}},
                ["path", "content"],
            ),
            _schema(
                "apply_patch",
                "Replace exactly one occurrence of old_text in a file.",
                {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                ["path", "old_text", "new_text"],
            ),
            _schema(
                "run_shell",
                "Run a shell command inside workdir.",
                {"command": {"type": "string"}, "cwd": {"type": "string"}},
                ["command"],
            ),
            _schema(
                "grep",
                "Search file contents inside the workdir with a regular expression. "
                "Returns path:line:text matches. Automatically skips .git, node_modules, venvs and build dirs.",
                {
                    "pattern": {"type": "string", "description": "regular expression"},
                    "path": {"type": "string", "description": "subdirectory to search, default '.'"},
                    "glob": {"type": "string", "description": "optional filename filter like '*.py'"},
                    "max_results": {"type": "integer", "description": "default 50"},
                },
                ["pattern"],
            ),
            _schema(
                "glob",
                "Find files inside the workdir by filename pattern, e.g. 'src/**/*.ts' or '*.md'.",
                {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "subdirectory to search, default '.'"},
                    "max_results": {"type": "integer", "description": "default 200"},
                },
                ["pattern"],
            ),
            _schema(
                "web_search",
                "Search the public web (Bing) for current information: docs, error messages, library versions, news. "
                "Returns title/url/snippet results. Follow up with web_fetch to read a promising page.",
                {"query": {"type": "string"}, "max_results": {"type": "integer", "description": "default 8"}},
                ["query"],
            ),
            _schema(
                "web_fetch",
                "Fetch a web page by URL and return its readable text content (HTML converted to text). "
                "Use after web_search, or to read documentation/gists/raw files.",
                {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "description": "default 12000"},
                },
                ["url"],
            ),
            _schema(
                "task",
                "Spawn one or more sub-agents, each with its own context window, to handle self-contained "
                "research or exploration work. Pass 'prompt' for a single sub-agent, or 'tasks' (array of "
                "{prompt}) to run up to 4 sub-agents in parallel - e.g. explore different modules at once. "
                "Sub-agents have all tools except task; you only receive their final summaries.",
                {
                    "prompt": {"type": "string", "description": "single sub-agent mode: complete instructions"},
                    "tasks": {
                        "type": "array",
                        "description": "parallel mode: up to 4 items, each {\"prompt\": \"...\"}",
                        "items": {
                            "type": "object",
                            "properties": {"prompt": {"type": "string"}},
                            "required": ["prompt"],
                        },
                    },
                },
                [],
            ),
            _schema(
                "read_notebook",
                "Read a Jupyter notebook (.ipynb): list cells with index, type, source and output previews.",
                {"path": {"type": "string"}, "max_cells": {"type": "integer"}},
                ["path"],
            ),
            _schema(
                "notebook_edit",
                "Replace the source of one cell in a Jupyter notebook (.ipynb) by cell index. Clears that cell's outputs.",
                {
                    "path": {"type": "string"},
                    "cell_index": {"type": "integer"},
                    "new_source": {"type": "string"},
                    "cell_type": {"type": "string", "description": "optional: change cell to 'code' or 'markdown'"},
                },
                ["path", "cell_index", "new_source"],
            ),
            _schema(
                "update_plan",
                "Maintain a visible task plan for complex multi-step work. "
                "Replace the whole plan each time: list every step with its status "
                "(pending / in_progress / completed). Call this whenever the plan changes, "
                "including marking steps done as you finish them.",
                {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["title", "status"],
                        },
                    }
                },
                ["steps"],
            ),
            _schema(
                "bg_start",
                "Run a shell command in the background (dev servers, watchers, long builds) and return "
                "immediately with a job_id. Poll progress with bg_output; stop with bg_kill. "
                "The command still passes sandbox policy checks.",
                {"command": {"type": "string"}, "cwd": {"type": "string"}},
                ["command"],
            ),
            _schema(
                "bg_output",
                "Read the accumulated output of a background job started with bg_start. "
                "Returns running state, exit code when finished, and the last N chars of output.",
                {"job_id": {"type": "string"}, "tail": {"type": "integer", "description": "chars of output tail, default 8000"}},
                ["job_id"],
            ),
            _schema(
                "bg_kill",
                "Terminate a running background job started with bg_start.",
                {"job_id": {"type": "string"}},
                ["job_id"],
            ),
            _schema(
                "bg_list",
                "List all background jobs from this server run with their states.",
                {},
                [],
            ),
        ]
        return builtin + self._extra_schemas

    def add_mcp_schema(self, schema: dict[str, Any]) -> None:
        self._extra_schemas.append(schema)

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
            cmd = str(call.arguments.get("command", ""))
            cwd = str(call.arguments.get("cwd") or ".")
            text = f"run_shell cwd={cwd}\n{cmd}"
            return text, None
        if call.name == "bg_start":
            cmd = str(call.arguments.get("command", ""))
            cwd = str(call.arguments.get("cwd") or ".")
            text = f"bg_start (后台任务) cwd={cwd}\n{cmd}"
            return text, None
        return f"{call.name} {call.arguments}", None

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            if call.name == "read_file":
                return fs.read_file(self.ctx.sandbox, fs.ReadFileArgs.model_validate(call.arguments))
            if call.name == "list_dir":
                return fs.list_dir(self.ctx.sandbox, fs.ListDirArgs.model_validate(call.arguments))
            if call.name == "write_file":
                return fs.write_file(self.ctx.sandbox, fs.WriteFileArgs.model_validate(call.arguments))
            if call.name == "apply_patch":
                return fs.apply_patch(self.ctx.sandbox, fs.ApplyPatchArgs.model_validate(call.arguments))
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
            if call.name == "grep":
                return search.grep_tool(self.ctx.sandbox, search.GrepArgs.model_validate(call.arguments))
            if call.name == "glob":
                return search.glob_tool(self.ctx.sandbox, search.GlobArgs.model_validate(call.arguments))
            if call.name == "web_search":
                return web.web_search_tool(web.WebSearchArgs.model_validate(call.arguments))
            if call.name == "web_fetch":
                return web.web_fetch_tool(web.WebFetchArgs.model_validate(call.arguments))
            if call.name == "read_notebook":
                return notebook.read_notebook(self.ctx.sandbox, notebook.ReadNotebookArgs.model_validate(call.arguments))
            if call.name == "notebook_edit":
                return notebook.notebook_edit(self.ctx.sandbox, notebook.NotebookEditArgs.model_validate(call.arguments))
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
                        {"title": str(step["title"]).strip(), "status": status if status in {"pending", "in_progress", "completed"} else "pending"}
                    )
                self.plan = clean
                return ToolResult(ok=True, payload={"steps": clean})
            if call.name.startswith("mcp__") and self.ctx.mcp_call is not None:
                return await self.ctx.mcp_call(call.name, call.arguments)
        except Exception as exc:
            return ToolResult(ok=False, payload={"error": str(exc)})
        return ToolResult(ok=False, payload={"error": f"Unknown tool: {call.name}"})
