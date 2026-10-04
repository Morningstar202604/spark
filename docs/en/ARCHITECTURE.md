# Spark Architecture

> 中文：[docs/ARCHITECTURE.md](../ARCHITECTURE.md)

## Top-level layout

```
spark/
├── config.py          # Config layer: tomlkit I/O, Chinese-provider presets (PRESETS), key masking (mask_key / is_masked_key), random access token, keys saved with chmod 600
├── loop.py            # AgentLoop main loop: one asyncio event stream, plan → tools → approval → execute → summarize; drives turns and yields SSE events
├── execution.py       # Tool execution layer (ToolExecutor): approval verdict, git checkpoint before writes, per-tool timeout/kill, cancellation, subagent event embedding (split out of loop._execute_call)
├── routing.py         # Model routing (pure fn): strong-task keywords → main model, else fast model; toggleable (route_enabled) / custom keywords (route_keywords)
├── compaction.py      # Context management (pure fn): folds overflow history into a summary + strips orphan tool messages (strip_orphans)
├── prompt.py          # System prompt & memory-context template constants (SYSTEM_PROMPT_TEMPLATE, ...)
├── truncation.py      # Output-truncation detection (pure fn): spots replies cut off by the length cap and triggers batched retry
├── provider.py        # OpenAI-compatible client: streaming /chat/completions (httpx, zero extra deps) + mock + token estimation
├── approval.py        # ApprovalGate: pass/ask/deny by mode (suggest / auto-edit / full-auto / plan); in-session "always allow" tracked per tool name
├── store.py           # Session store: one JSONL per session + a meta JSON; search / truncate / fork / delete
├── memory.py          # Cross-session long-term memory: SQLite + FTS5, isolated per working dir; optional semantic embedding (off/api/local), explicit remember/forget
├── usage.py           # Usage & cost accounting: JSONL append, built-in pricing (overridable)
├── slash.py           # Slash commands: expand "/..." input into a preset prompt (list_commands / expand_slash)
├── sanitize.py        # Output gate: redacts SSE egress & external error text (keys, home paths, tracebacks); model-side messages untouched
├── plugins.py         # Lightweight plugin point: importlib dynamic load of ~/.spark/plugins/*.py; plugin tools still pass the approval gate
├── subagent.py        # Subagents: explore (read-only research) / general (full tools); recursion forbidden, shared approval gate & cancel signal
├── codeindex.py       # Lightweight code index + syntax diagnostics (Python via ast, other langs via line regex; .py via py_compile)
├── patch_apply.py     # Unified-diff parse/validate/apply (apply_patch backend; multi-file; rejects out-of-workdir / protected paths wholesale)
├── pty.py             # Built-in persistent terminal: one shell child process per tab, dual backend (POSIX ptyprocess / Windows pywinpty)
├── recent_dirs.py     # Recent working-dir list (up to 8, stored in ~/.spark/recent_dirs.json)
├── tui.py             # Terminal TUI (optional, single module)
├── cli.py / __main__.py  # CLI entry points
├── tools/             # Tool registry
│   ├── base.py        #   Tool base (name/description/JSON Schema/category/handler/preview) + ToolContext; category = read/write/shell/system
│   ├── __init__.py    #   build_registry: aggregates all tools into name→Tool and lists the read-only set for the explore subagent (_READONLY_NAMES)
│   ├── fs.py          #   read_file / write_file / list_dir / glob / search (rg)
│   ├── patch.py       #   apply_patch: one multi-file unified diff (per-file approval preview)
│   ├── shell.py       #   run_shell: async subprocess, process-group governance, timeout kill, output truncation
│   ├── terminal.py    #   pty_run: interactive persistent terminal (multi-turn send_input)
│   ├── web.py         #   web_search / read_url (read-only networking)
│   ├── git.py         #   checkpoint / reset (uses system git)
│   ├── plan.py        #   update_plan (system class, intercepted as a plan event)
│   ├── memory_tools.py#   remember / forget / memory_search
│   ├── knowledge.py   #   kb_add / kb_search (zero-dependency local knowledge base)
│   ├── codeindex_tools.py # index_project / search_symbol / lint_file
│   ├── office.py      #   docx / xlsx / pdf read-write (optional deps; clear message when missing)
│   ├── report.py      #   gen_report: self-contained HTML report (inline SVG)
│   ├── session.py     #   session export to Markdown / JSON
│   ├── subagent.py    #   spawn_subagent entry (loop special-cases & embeds events)
│   ├── mcp.py         #   official MCP SDK, stdio / Streamable HTTP transports, dynamically registered as ordinary tools
│   └── injection.py   #   Prompt-injection defense: marks tool/file return content as untrusted data
└── web/               # Web layer: FastAPI backend + React frontend
    ├── server.py      #   create_app assembly, SSE chat stream, approval/cancel, WS terminal, GET / SPA + static fallback mount
    ├── api_common.py  #   AppState, auth check_token, config_payload, sse/json_body helpers
    ├── api_config.py  #   config / test-connection / recent-dirs / usage / plugins / slash-commands
    ├── api_sessions.py#   session CRUD / fork / context watermark / truncate / delete message / export
    ├── api_data.py    #   memory / file browse / git checkpoints
    ├── src/           #   React frontend source (main.tsx / App.tsx / components / lib / state.tsx / types.ts / styles)
    └── dist/          #   Vite build output (committed to git); backend serves static assets from here first
```

## Data flow (one conversation)

```
user input → server.py opens an SSE stream → loop.py (AgentLoop.stream)
  ├─ plan: parallel read-only explore subagents → update_plan produces the plan (plan event)
  ├─ execute: model call (stream_chat) → tool call → approval gate (approval.py) → execution.py runs it → result fed back
  ├─ context: compaction.py folds overflow into a summary; truncation.py handles cut-off replies
  └─ verify: after apply_patch, auto-runs affected tests when enabled (auto_verify, toggleable)
Events pushed to the frontend over SSE: hello → status/plan/reasoning/text/tool_start/approval/tool_result/usage → done (with assistant_msg_id) → close
```

## Security model

- **Approval gate** (`approval.py`) — writes and commands are graded by `approval_mode` (suggest = ask all / auto-edit = in-workdir writes pass, commands ask / full-auto = nothing asks / plan = read-only)
- **Per-file approval** — `apply_patch` filters by file in `execution.py` (the frontend sends a `files` list on approval)
- **Prompt-injection defense** (`tools/injection.py`) — file contents and search results are treated as untrusted data
- **Key protection** — `mask_key` masks on echo; `is_masked_key` blocks masked values from being written back
- **Output gate** (`sanitize.py`) — SSE egress & external error text are uniformly redacted (keys, home paths, tracebacks); the model still sees the originals
- **Protected paths** — writes under `protected_paths` and outside the working dir are always refused
- **Local-first** — memory, sessions and config stay in local directories; no hidden cloud calls (semantic embedding must be explicitly enabled)

## Frontend (React + Vite, served after build)

- Stack: React 18 + TypeScript + Vite 5 + Tailwind CSS + Radix UI (dialog / dropdown / popover / tabs / tooltip ...) + lucide-react icons
- Source: `spark/web/src/**.tsx`
  - `main.tsx` entry / `App.tsx` root / `state.tsx` global state
  - `components/`: ChatView, MessageList, Sidebar, SettingsDrawer, WelcomeView, ui (primitives)
  - `lib/`: api (HTTP/SSE client), markdown, utils
  - `types.ts` types, `styles/global.css` global styles (Tailwind + design tokens)
- Build: `npm run build` produces `spark/web/dist/` (an index.html plus hashed assets); this directory is **committed to git**. `STATIC_DIR` points at dist first and falls back to `spark/web/` (the legacy single-file `index.html`) when dist is absent.
- Capabilities: Ctrl+K command palette, message edit & resend, voice input (Web Speech),
  context watermark, full-text search, one-click MCP market, terminal (PTY/WS), multimodal image input

> AI生成
