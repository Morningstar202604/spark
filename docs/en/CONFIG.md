# Spark Configuration (config.toml)

> 中文：[docs/CONFIG.md](../CONFIG.md)
> Single source of truth for every field: `src/spark/config.py` (`SparkConfig` and its nested models, including defaults and bounds).

The config file lives at `~/.spark/config.toml` (override the directory with the
`SPARK_HOME` environment variable); `spark init` writes a template. Most fields
are editable from the in-app **Settings** panel; this document lists every
field and its default, grouped by `[section]`.

## `[provider]` — model service

| Field | Default | Description |
| --- | --- | --- |
| `name` | `openai_compat` | `openai_compat` / `ollama` / `mock` |
| `base_url` | `https://api.deepseek.com/v1` | OpenAI-compatible endpoint (backed by LiteLLM, 100+ providers) |
| `model` | `deepseek-chat` | Primary model name |
| `api_key_env` | `SPARK_API_KEY` | Name of the environment variable holding the key |
| `api_key` | `null` | Inline key (masked on echo); the env var takes precedence |

## `[agent]` — behavior & safety

| Field | Default | Description |
| --- | --- | --- |
| `approval` | `suggest` | `suggest` (ask on writes) / `auto-edit` (auto-apply edits) / `full-auto` (no prompts) |
| `workdir_only` | `true` | Confine file access to the working directory |
| `sandbox_mode` | `workspace` | `sandbox-only` (read-only) / `workspace` / `full-access` (outside workdir, protected paths still blocked) / `unrestricted` |
| `protected_paths` | `[]` | Extra paths that are never written (Spark's own code and config are always protected) |
| `shell_timeout_sec` | `60` | Per-command timeout (1–3600) |
| `max_tool_rounds` | `30` | Max tool rounds per turn (1–1000) |
| `max_repeat_calls` | `4` | Circuit-breaker threshold for a repeated (tool, arguments) signature (0 = off) |
| `max_turn_tokens` | `0` | Per-turn token budget (0 = off) |
| `max_output_chars` | `8000` | Tool output truncation (200–1,000,000) |
| `show_thinking` / `show_tools` / `show_plan` / `show_context` / `show_keywords` / `show_notices` | `true` | Frontend display toggles |

## `[context]` — context window

| Field | Default | Description |
| --- | --- | --- |
| `agents_md` | `AGENTS.md` | Project-instructions filename (bare filename only, no path escapes) |
| `max_fragment_chars` | `8000` | AGENTS.md injection cap |
| `history_budget_chars` | `96000` | Char budget for recent history |
| `max_context_tokens` | `32768` | Context ceiling; compaction triggers past `compact_threshold` |
| `compact_threshold` | `0.85` | Usage ratio that triggers compaction (0.1–0.99) |
| `keep_recent_messages` | `8` | Recent messages kept during compaction |

## `[memory]` — long-term memory

| Field | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Master switch (ChromaDB vector persistence + keyword retrieval) |
| `top_k` | `6` | Retrieval count (1–50) |
| `capacity` | `500` | Memory capacity cap (1–100,000) |
| `embedding_model` | `text-embedding-3-small` | Embedding model |
| `auto_extract` | `true` | Auto-extract facts after each turn |

## MCP servers

```toml
[[mcp_servers]]
name = "filesystem"
command = "npx"                      # stdio transport
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
readonly_tools = []                  # tool names declared read-only (skip approval)

# Remote transports (SSE / streamable HTTP):
# [[mcp_servers]]
# name = "remote"
# transport = "sse"                  # stdio | sse | streamable_http
# [mcp_servers.http]
# url = "http://127.0.0.1:3000/sse"
# headers = {}
# timeout_sec = 30
```

## `[[hooks]]` — lifecycle hooks

```toml
[[hooks]]
event = "pre_tool"                   # see HookRegistry.EVENTS in spark/hooks.py
command = "python"
args = ["check.py"]
timeout_sec = 15
on_deny = "warn"                     # warn | block
```

## Model profiles (multi-model switching)

```toml
active_profile_id = "a1b2c3d4"

[[model_profiles]]
id = "a1b2c3d4"
name = "DeepSeek"
provider = "openai_compat"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
# the api_key field exists but stays empty here: keep keys in config/env, never in the repo
```

The Settings → Models panel reads and writes this section.

## Environment variables

- Every field can be overridden with the `SPARK_` prefix plus double-underscore nesting: `SPARK_AGENT__MAX_TOOL_ROUNDS=50`, `SPARK_PROVIDER__BASE_URL=…`
- `.env` is read **only** from `$SPARK_HOME/.env` (a project-local `.env` is ignored — a cloned repo cannot grant itself full-auto)
- `SPARK_HOME`: overrides the config/data directory (default `~/.spark`)
- `LITELLM_LOCAL_MODEL_COST_MAP`: Spark sets this to `True` by default (use LiteLLM's bundled model prices and skip the raw.githubusercontent.com fetch, which stalls ~9s on networks that cannot reach it)

## Project-level config (`.spark.toml`)

A `.spark.toml` in the working directory is merged with the global config, but
passes through a security sanitizer first: executable settings (provider, MCP,
hooks) may not be injected by project files (prompt-injection defense). See
`_sanitize_project_config` in `config.py`.

> AI-generated
