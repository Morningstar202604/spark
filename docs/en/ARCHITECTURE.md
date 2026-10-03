# Spark Architecture

## Top-level layout

```
spark/
├── config.py          # Config layer: TOML I/O, Chinese-provider presets, key masking (mask_key / is_masked_key)
├── loop.py            # Main loop: plan → tools → approval → execute → summarize; routing + fallback
├── compaction.py      # Context management: folds old messages into a summary (not a hard delete)
├── provider.py        # OpenAI-compatible client: streaming /chat/completions (httpx, zero extra deps)
├── approval.py        # Approval gate: writes/commands gated by mode (suggest / auto-edit / full-auto / plan)
├── executor.py        # Tool executor: per-tool timeout + cancellation
├── store.py           # Session store: JSONL messages, titles, full-text search, truncate/delete
├── memory.py          # Cross-session memory: keyword / API / local embedding retrieval
├── usage.py           # Usage & cost accounting
├── tools/             # Tool registry: fs (read/write/edit/search), web, indexing, MCP, subagents
├── web/               # Web layer: FastAPI + static frontend
│   ├── server.py      #   Router assembly, SSE stream, PTY terminal
│   ├── api_*.py       #   Grouped routes: config / sessions / data / approval ...
│   ├── index.html     #   Single-page frontend (native Web Components + ES Modules, 16 modules)
│   └── js|css/        #   Frontend modules: core / render / sse / sessions / settings* / components / mic ...
└── tui/               # Terminal TUI (optional)
```

## Data flow (one conversation)

```
user input → server.py opens SSE stream → loop.py
  ├─ plan: parallel multi-agent explore (read-only research) → plan
  ├─ execute: model call (stream_chat) → tool call → approval gate → executor → feed result back
  ├─ context: compact_messages folds overflow into a summary
  └─ verify: auto-runs tests after apply_patch (toggleable)
Events pushed to the frontend over SSE: hello → reasoning/text/tool_start/approval/tool_result → done
```

## Security model

- **Approval gate** — writes and commands are graded by `approval_mode` (read-only auto-passes; writes ask / auto / full-auto)
- **Prompt-injection defense** — file contents and search results are treated as untrusted content
- **Key protection** — `mask_key` masks on echo; `is_masked_key` blocks masked values from being written back
- **Protected paths** — writes are always refused under `protected_paths`
- **Local-first** — memory, sessions and config stay in local directories; no hidden cloud calls

## Frontend (native, no build step)

- ES Modules: `main.js` single entry, 16 modules with explicit dependencies
- Web Components: `components.js` templates (message / session card / MCP row ...)
- Design tokens: CSS variables (`--accent` / `--bg-*` / `--sp-*`), light/dark theme follows the system
- Capabilities: Ctrl+K command palette, message edit & resend, voice input (Web Speech),
  context watermark, full-text search, one-click MCP market, terminal (PTY/WS), multimodal image input

> AI生成
