# Spark HTTP API Reference

The server listens on `0.0.0.0` by default (for external proxy / preview access; pass `--host 127.0.0.1` for local-only). If an access `token` is configured, every request
must carry the header `X-Spark-Token: <token>`, otherwise `401` is returned.
Streaming endpoints use SSE (`text/event-stream`).

## Sessions

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/sessions` | Create session (`{"workdir": "..."}`) → `{id, title, ...}` |
| `GET` | `/api/sessions` | List sessions; `?q=keywords` full-text search (title/workdir/message hits with `match` snippets) |
| `GET` | `/api/sessions/{sid}` | Session detail (all messages) |
| `PATCH` | `/api/sessions/{sid}` | Rename (`{"title": "..."}`) |
| `DELETE` | `/api/sessions/{sid}` | Delete session |
| `POST` | `/api/sessions/{sid}/fork` | Fork into a new session (`{"workdir": "..."}`) |
| `POST` | `/api/sessions/{sid}/truncate` | Truncate at a message (`{"message_id": "..."}`) — removes it and everything after |
| `DELETE` | `/api/sessions/{sid}/messages/{message_id}` | Delete a single message (persistent) |
| `GET` | `/api/sessions/{sid}/context` | Context watermark → `{used, max, compact}` |

## Chat

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/chat/stream` | SSE streaming chat (`{session_id, prompt, approval_mode}`; `images` supported for multimodal) |
| `POST` | `/api/approval` | Approval response (`{request_id, approve}` while a write tool waits) |
| `POST` | `/api/cancel` | Cancel the running session |

SSE events: `hello` (with `user_msg_id`) / `reasoning` / `text` / `tool_start` /
`approval` / `tool_result` / `usage` / `done` (with `assistant_msg_id`) / `error` / `close`.

## Config

| Method | Path | Description |
| --- | --- | --- |
| `GET` / `POST` | `/api/config` | Read/save config (POST writes the full payload; keys are masked on echo and never overwritten) |
| `POST` | `/api/test-connection` | Test the current model connection (body = subset of POST /api/config) |

## Memory

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/memory?q=...` | Search memory |
| `POST` | `/api/memory` | Write memory (`{key, value, scope}`) |
| `DELETE` | `/api/memory/{memory_id}` | Delete memory |

## Files / terminal / integrations

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/fs?path=...` | Browse files in the working directory |
| `GET` | `/api/git` | Git status; `POST /api/git/checkpoint` checkpoint; `POST /api/git/reset` rollback |
| `GET` | `/api/plugins` | Plugin list & status |
| `GET` | `/api/usage` | Usage & cost stats |
| `GET` | `/api/recent-dirs` | Recent directories |
| `GET` | `/api/pty`、`POST /api/pty/close`、`WS /ws/pty` | Web terminal (PTY, grouped by session) |
