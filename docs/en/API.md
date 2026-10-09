# Spark HTTP API Reference

> 中文：[docs/API.md](../API.md)

## Authentication

The server listens on `0.0.0.0` by default (for external proxy / preview access; pass `--host 127.0.0.1` for local-only).

- Auth logic lives in `spark/web/api_common.py::check_token`; the token comes from the `token` config field.
- **No `token` configured (or empty) = no login required** — every request is allowed through (the default state).
- **Once `token` is set**: aside from the `GET /` static assets, all `/api/*` endpoints require the token, otherwise `401`. Provide it via either:
  - header `X-Spark-Token: <token>`, or
  - query parameter `?token=<token>`
  > Note: the implementation uses the custom `X-Spark-Token` header (or `?token=`). It does **not** use `Authorization: Bearer`.
- The WebSocket `/ws/pty` cannot set custom headers, so the token must go through the query parameter `?token=<token>` only; a wrong token closes the socket with code `4401`.
- The streaming endpoint `/api/chat/stream` uses SSE (`text/event-stream`).

## Root / static

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Serves the SPA shell (`spark/web/dist/index.html`, falling back to `spark/web/index.html`); static assets are served through the fallback mount |

## Sessions

| Method | Path | Auth | Request body / query | Response |
| --- | --- | --- | --- | --- |
| `POST` | `/api/sessions` | yes | `{"workdir": "..."}` (optional `"model"`); falls back to global config | session meta `{id, title, workdir, model, ...}`; no working dir → `400` |
| `GET` | `/api/sessions` | yes | `?q=keywords` (search when present, else list) | `[{...session meta, "running": bool}]` |
| `GET` | `/api/sessions/{sid}` | yes | — | `{meta, messages}`; not found → `404` |
| `PATCH` | `/api/sessions/{sid}` | yes | `{"title": "..."}` (clamped to 60 chars, non-empty) | `{ok: true, title}`; empty title → `400`, session not found → `404` |
| `DELETE` | `/api/sessions/{sid}` | yes | — | `{ok: true}`; running → `409`, not found → `404` |
| `POST` | `/api/sessions/{sid}/fork` | yes | no body (fork ignores the body) | new session meta; source not found → `404` |
| `POST` | `/api/sessions/{sid}/truncate` | yes | `{"message_id": "..."}` (empty clears the whole session) | `{ok: true, messages}`; running → `409`, message not found → `404` |
| `DELETE` | `/api/sessions/{sid}/messages/{message_id}` | yes | — | `{ok: true, messages}`; message not found → `404` |
| `GET` | `/api/sessions/{sid}/context` | yes | — | `{used, max, compact}` (estimated tokens / cap / over-window flag) |
| `GET` | `/api/sessions/{sid}/export` | yes | `?format=markdown\|json` (default `markdown`) | direct file download: Markdown (`text/markdown`, filename `session_<ts>_<title>.md`) or JSON (`{meta, messages}`, filename `session_<ts>_<sid>.json`); session not found → `404` |

## Chat

| Method | Path | Auth | Request body | Response |
| --- | --- | --- | --- | --- |
| `POST` | `/api/chat/stream` | yes | `{session_id, prompt, approval_mode?, workdir?, model?, images?}`; `images` is `[{data: base64, mime}]`, ≤3 images, each base64 ≤2.8MB | SSE stream (see below); missing `session_id`/`prompt` → `400`, session already running → `409`, session not found → `404`, no working dir → `400` |
| `POST` | `/api/approval` | yes | `{request_id, action: "allow"\|"deny"\|"always", tool?, files?}`; `files` is a list of strings (per-file allow for apply_patch) | `{ok: true}`; `files` not a string list → `400`, invalid `action` → `400`, approval request missing/expired → `404` |
| `POST` | `/api/cancel` | yes | `{session_id}` | `{ok: true}`; no running session → `404` |

SSE event `type`s: `hello` (with `session_id`, `user_msg_id`) / `status` / `plan` / `reasoning` / `text` / `tool_start` / `approval` / `tool_result` / `usage` / `done` (with `assistant_msg_id`) / `error` / `close`. Every event is redacted on egress (`sanitize.py`).

## Config

| Method | Path | Auth | Request body / query | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/config` | yes | — | config snapshot (presets, current fields, approval modes, MCP, dirs, version; `api_key`/`embed_api_key` are masked, `token` is exposed only as `token_set: bool`) |
| `POST` | `/api/config` | yes | full/incremental write-back (provider/base_url/model/... advanced fields validated before persisting; masked `api_key`/`embed_api_key` never overwrite real values; `mcp_servers` cleaned per entry; empty `token` clears it) | the same config snapshot after save |
| `POST` | `/api/test-connection` | yes | `{base_url?, model?, proxy?, api_key?}` (an `api_key` of all `*` is treated as not provided) | `{ok: bool, message: str}` |
| `GET` | `/api/slash-commands` | yes | — | `{commands: [...]}` (for the input-box suggestions) |

## Usage / plugins / recent dirs

| Method | Path | Auth | Request body / query | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/usage` | yes | `?session_id=xxx` (single session) or omitted (global summary) | the corresponding summary object |
| `GET` | `/api/plugins` | yes | — | `{dir, plugins: [{name, tools, error}], tool_count}` |
| `GET` | `/api/recent-dirs` | yes | — | `{dirs: [...]}` (up to 8) |

## Memory

Implementation in `spark/web/api_data.py` (memory is isolated per working directory via `MemoryStore`).

| Method | Path | Auth | Request body / query | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/memory` | yes | `?workdir=...` (falls back to global config) | `{workdir, count, items: [...]}`; no working dir → `400` |
| `POST` | `/api/memory` | yes | `{workdir, key, value, level?}`; `key`/`value` required and ≤200/≤5000 chars respectively; `level` optional ∈ `semantic` (default) / `situational` / `episodic` / `procedural`, invalid values fall back to `semantic` | `{ok: true, count}`; no working dir / missing key or value / over length → `400` |
| `DELETE` | `/api/memory/{memory_id}` | yes | path param `memory_id` (integer) | `{ok: true}`; not found → `404` |

## Files / git / terminal

Working dir for files and git: taken from the `?sid=`/body `sid` session's workdir first, then global config.

| Method | Path | Auth | Request body / query | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/fs` | yes | `?sid=...`, `?path=<relative subpath>` | `{workdir, path, entries: [{name, dir}]}` (read-only listing, skips node_modules/dist, etc.); out-of-bounds / missing dir → `400`/`404`, no working dir → `400` |
| `GET` | `/api/git` | yes | `?sid=...` | `{repo, branch, changes, checkpoints:[{hash,time,message}]}` or `{repo: false, reason}`; no working dir → `400` |
| `POST` | `/api/git/checkpoint` | yes | `{sid?, message?}` (message clamped to 120 chars, auto-filled when absent) | `{ok: true, message}`; git failure → `400`, no working dir → `400` |
| `POST` | `/api/git/reset` | yes | `{sid?, confirm: "yes"}` (destructive; `confirm=yes` required) | `{ok: true, message}`; missing confirmation → `400`, git failure → `400` |
| `POST` | `/api/pty/close` | yes | `{sid, tab}` | `{ok: true}` (terminates that tab's shell child process) |
| `WS` | `/ws/pty` | via `?token=` | query `?sid=&tab=&token=`; frames `{type:"in"\|"resize"...}` in / `{type:"out"\|"exit"\|"err"...}` out | persistent PTY stream; wrong token → close `4401`, no working dir / backend unavailable → close `4400` |
