# Spark Configuration (config.toml)

The config file lives at `~/.spark/config.toml` (override the directory with the
`SPARK_HOME` environment variable). Most fields are editable from the in-app
**Settings** panel; this document lists every field and its default.

## Core

| Field | Default | Description |
| --- | --- | --- |
| `provider` | `mock` | Model preset: `deepseek` / `deepseek-flash` / `qwen` / `glm` / `kimi` / `doubao` / `unisound` / `ollama` / `custom` / `mock` |
| `base_url` | `""` | OpenAI-compatible endpoint (required when `provider = "custom"`) |
| `model` | `mock` | Primary model name |
| `api_key` | `""` | API key (masked on echo; masked values are never written back) |
| `workdir` | startup dir | Default working directory |
| `approval_mode` | `suggest` | `suggest` (ask on writes) / `auto-edit` (auto-apply file edits) / `full-auto` (no prompts) / `plan` (read-only planning) |
| `max_context_tokens` | `32000` | Context window ceiling; older messages are compacted beyond this |
| `token` | `""` | Access token; empty = no-login (default listen 0.0.0.0 — enable a token when publicly reachable) |

## Routing & failover

| Field | Default | Description |
| --- | --- | --- |
| `model_fast` | `""` | Fast model name; simple tasks use it automatically, complex tasks use the main model. Empty = routing off |
| `route_enabled` | `true` | Multi-model routing switch (only active when `model_fast` is set) |
| `route_keywords` | `""` | Custom "heavy task" keywords (comma/space/newline separated); empty = built-in list |
| `fallback_model` | `""` | Backup model: automatically used when the primary fails (rate-limit / outage / misconfig). Empty = off |

## Memory

| Field | Default | Description |
| --- | --- | --- |
| `memory_embedding` | `off` | `off` = keyword search only (zero deps) / `api` = Volcano Ark doubao-embedding / `local` = local model |
| `memory_embed_model` | `""` | Embedding model name (required in `api` / `local` modes) |

## MCP servers

```toml
[[mcp_servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
transport = "stdio"   # stdio | http
env = {}              # environment variables (JSON object)
# with transport = "http":
# url = "http://127.0.0.1:8000/mcp"
# headers = {}        # HTTP headers (JSON object)
```

## Advanced

| Field | Default | Description |
| --- | --- | --- |
| `system_prompt` | `""` | Custom system prompt; empty = built-in. Supports `{workdir}` `{protected}` placeholders |
| `protected_paths` | `[]` | Extra protected paths (TOML array): writes are always refused under these |
| `max_turns` | `25` | Max tool turns per conversation |
| `tool_timeout` | `180` | Per-tool execution timeout (seconds) |
| `auto_verify` | `true` | Auto-run pytest after a successful `apply_patch` (toggleable) |
| `temperature` | `""` | Sampling temperature; empty = server default |
| `max_tokens` | `""` | Max reply tokens; empty = server default |
| `usage_pricing` | `{}` | Cost overrides: `{model: {input: CNY/M, output: CNY/M}}` |
| `proxy` | `""` | HTTP(S) proxy (e.g. `http://127.0.0.1:7890`); empty = respect env vars |

## Environment variables

- `SPARK_HOME` — override config/data dir (default `~/.spark`)
- `<PROVIDER>_API_KEY` — provider key env var (`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `ZHIPU_API_KEY` / `MOONSHOT_API_KEY` / `ARK_API_KEY` / `UNISOUND_API_KEY` / `OLLAMA_API_KEY`)
- `HTTPS_PROXY` / `HTTP_PROXY` — respected by httpx when `proxy` is unset

## Example (Unisound u2-flash)

```toml
provider = "unisound"
base_url = "https://maas-api.unisound.com/v1"
model = "u2-flash"
api_key = "sk-xxx"          # or set env var UNISOUND_API_KEY
workdir = "/path/to/proj"
approval_mode = "suggest"
fallback_model = "u2-pro"   # optional: auto-failover
```

> AI生成