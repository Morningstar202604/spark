# Spark HTTP API Reference

> 中文：[docs/API.md](../API.md)
> Implementation source: `src/spark/web/routes.py` (GET routes + POST dispatch table) and `src/spark/web/_handlers.py` (POST handlers).

## Authentication

The server listens on `127.0.0.1` only by default (`spark web --host 0.0.0.0` opts into LAN exposure at your own risk).

- Auth lives in `routes.py` (`_guard` / `_authorized`); non-loopback callers must present the control token.
- The token is generated at startup (`SparkWebState.control_token`) and is only available on the console object; it is never logged.
- Loopback requests (127.0.0.1 / ::1 / localhost) need no token; everything else gets `401` unless the `X-Spark-Token` header matches.
- Extra guards: non-local Host header → `403`; a cross-origin Origin header → `403` (CSRF); POST bodies must be `application/json` or `415`.

## Static files

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Serves the SPA shell (bundled `src/spark/web/dist/index.html` first, built-in fallback page otherwise) |
| `GET` | `/<static file>` | Serves assets from the UI directory (path-escape checked); `/assets/*` is cached immutable for one year |

## Read-only GET endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/status` | Session, sandbox mode, approval mode, context usage, plan, environment info |
| `GET` | `/api/config` | Full config (keys masked): provider, agent, context, mcp_servers, hooks |
| `GET` | `/api/models` | Model profiles (api_key masked, `active` flag) |
| `GET` | `/api/agents_md` | Reads the AGENTS.md inside the workdir |
| `GET` | `/api/memory` | Long-term memory: `items`, `search?q=` hits with score, `stats` |
| `GET` | `/api/sessions` | Sessions for the current workdir (latest 50) + `current` id |
| `GET` | `/api/checkpoints` | Checkpoint list for the current session |
| `GET` | `/api/history` | Message history of the current session (images as base64 data URIs) |

## POST endpoints

| Method | Path | Body | Description |
| --- | --- | --- | --- |
| `POST` | `/api/chat/stream` | `{prompt, images?}` | Streaming chat over SSE. Events: `text_delta` / `tool` (call + result) / `approval` (with diff) / `turn_end` / `turn_error` / `done`; heartbeat `: ping` |
| `POST` | `/api/chat` | `{prompt}` | Non-streaming chat; returns the full event array (debugging) |
| `POST` | `/api/cancel` | — | Cancel the running turn and pending approval |
| `POST` | `/api/approval` | `{decision}` | `allow` / `allow_always` / `deny` |
| `POST` | `/api/models/save` | `{name, base_url, model, provider, api_key, id?}` | Create or update a model profile |
| `POST` | `/api/models/delete` | `{id}` | Delete a profile; clearing the active one resets `active_profile_id` |
| `POST` | `/api/models/activate` | `{id}` | Activate a profile and rebuild the AgentLoop; `409` while busy |
| `POST` | `/api/agents_md` | `{content}` | Write the AGENTS.md inside the workdir (filename validated) |
| `POST` | `/api/memory` | `{action}` | `add` (content/type/importance) / `delete` (id) / `optimize` |
| `POST` | `/api/sessions/new` | — | Start a new session |
| `POST` | `/api/sessions/delete` | `{session_id}` | Delete a session; auto-switches when deleting the current one |
| `POST` | `/api/sessions/switch` | `{session_id}` | Switch sessions and return the message history |
| `POST` | `/api/checkpoints/rollback` | `{checkpoint_id}` | Roll back to a checkpoint (restores workdir files + history) |
| `POST` | `/api/settings` | provider/agent/mcp_servers/hooks sections | Save settings and rebuild the AgentLoop; `400` on invalid fields, `409` while busy |
| `POST` | `/api/test` | `{base_url, model, api_key?}` | Probe the model endpoint (refuses to send the stored credential to a different host) |

Unknown paths return `404`; bodies above 32 MiB return `413`; malformed JSON returns `400`.

## SSE event format

`/api/chat/stream` responds with `text/event-stream`, one event per `data: {JSON}\n\n`:

```jsonc
{"type": "text_delta", "text": "…"}                       // incremental text (sanitized)
{"type": "tool", "tool": {…}, "ok": true, "result": {…}}  // tool call and result
{"type": "approval", "approval": {"summary": "…", "diff": "…"}}  // awaiting approval
{"type": "turn_end", "text": "…"}                         // turn finished
{"type": "turn_error", "text": "…"}                       // turn failed
{"type": "done"}                                          // stream closed
```

A dropped connection (write failure or peer close) cancels the running turn; a `: ping` heartbeat is sent every 15 seconds of silence.

> AI-generated
