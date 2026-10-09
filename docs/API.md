# Spark HTTP API 参考

> English: [docs/en/API.md](en/API.md)

## 认证

服务默认监听 `0.0.0.0`（便于外部代理/预览访问；仅本机使用可 `--host 127.0.0.1`）。

- 鉴权逻辑见 `spark/web/api_common.py::check_token`，令牌取自配置项 `token`。
- **未配置 `token`（或为空）= 免登录**，所有请求直接放行（默认状态）。
- **配置了 `token` 后**：除 `GET /` 静态资源外，所有 `/api/*` 接口都必须带令牌，否则返回 `401`。令牌可用两种方式之一提供：
  - 请求头 `X-Spark-Token: <token>`，或
  - 查询参数 `?token=<token>`
  > 注意：实现使用自定义头 `X-Spark-Token`（或 `?token=`），**不使用** `Authorization: Bearer`。
- WebSocket `/ws/pty` 无法自定义请求头，令牌**只能**走查询参数 `?token=<token>`；不正确时以关闭码 `4401` 断开。
- 流式接口 `/api/chat/stream` 走 SSE（`text/event-stream`）。

## 根 / 静态资源

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 返回前端单页（`spark/web/dist/index.html`，缺失时回落 `spark/web/index.html`）；静态资源经兜底挂载伺服 |

## 会话

| 方法 | 路径 | 鉴权 | 请求体 / 查询 | 响应 |
| --- | --- | --- | --- | --- |
| `POST` | `/api/sessions` | 是 | `{"workdir": "..."}`（可选 `"model"`）；缺省回落全局配置 | 会话元信息 `{id, title, workdir, model, ...}`；无工作目录 → `400` |
| `GET` | `/api/sessions` | 是 | `?q=关键词`（有则全文搜索，否则列表） | `[{...会话元信息, "running": bool}]` |
| `GET` | `/api/sessions/{sid}` | 是 | — | `{meta, messages}`；不存在 → `404` |
| `PATCH` | `/api/sessions/{sid}` | 是 | `{"title": "..."}`（截断 60 字，非空） | `{ok: true, title}`；标题空 → `400`，会话不存在 → `404` |
| `DELETE` | `/api/sessions/{sid}` | 是 | — | `{ok: true}`；运行中 → `409`，不存在 → `404` |
| `POST` | `/api/sessions/{sid}/fork` | 是 | 无请求体（分叉参数不读 body） | 新会话元信息；源不存在 → `404` |
| `POST` | `/api/sessions/{sid}/truncate` | 是 | `{"message_id": "..."}`（为空则清空整个会话） | `{ok: true, messages}`；运行中 → `409`，消息不存在 → `404` |
| `DELETE` | `/api/sessions/{sid}/messages/{message_id}` | 是 | — | `{ok: true, messages}`；消息不存在 → `404` |
| `GET` | `/api/sessions/{sid}/context` | 是 | — | `{used, max, compact}`（估算 token / 上限 / 是否超窗） |
| `GET` | `/api/sessions/{sid}/export` | 是 | `?format=markdown\|json`（默认 `markdown`） | 直接下载文件：Markdown（`text/markdown`，附件名 `session_<ts>_<title>.md`）或 JSON（`{meta, messages}`，附件名 `session_<ts>_<sid>.json`）；会话不存在 → `404` |

## 对话

| 方法 | 路径 | 鉴权 | 请求体 | 响应 |
| --- | --- | --- | --- | --- |
| `POST` | `/api/chat/stream` | 是 | `{session_id, prompt, approval_mode?, workdir?, model?, images?}`；`images` 为 `[{data: base64, mime}]`，≤3 张、单张 base64 ≤2.8MB | SSE 流（见下）；缺 `session_id`/`prompt` → `400`，会话运行中 → `409`，会话不存在 → `404`，无工作目录 → `400` |
| `POST` | `/api/approval` | 是 | `{request_id, action: "allow"\|"deny"\|"always", tool?, files?}`；`files` 为字符串列表（逐文件放行 apply_patch） | `{ok: true}`；`files` 非字符串列表 → `400`，`action` 非法 → `400`，审批请求不存在/过期 → `404` |
| `POST` | `/api/cancel` | 是 | `{session_id}` | `{ok: true}`；无运行中的会话 → `404` |

SSE 事件 `type`：`hello`（含 `session_id`、`user_msg_id`）/ `status` / `plan` / `reasoning` / `text` / `tool_start` / `approval` / `tool_result` / `usage` / `done`（含 `assistant_msg_id`）/ `error` / `close`。所有事件出口统一脱敏（`sanitize.py`）。

## 配置

| 方法 | 路径 | 鉴权 | 请求体 / 查询 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/api/config` | 是 | — | 配置快照（预设、current 字段、审批档位、MCP、目录、版本等；`api_key`/`embed_api_key` 回显打码，`token` 仅以 `token_set: bool` 表示） |
| `POST` | `/api/config` | 是 | 全量/增量回写（provider/base_url/model/... 高级项经类型与范围校验后落盘；打码的 `api_key`/`embed_api_key` 不覆盖真值；`mcp_servers` 逐项清洗；`token` 空串=清除） | 保存后的同一份配置快照 |
| `POST` | `/api/test-connection` | 是 | `{base_url?, model?, proxy?, api_key?}`（`api_key` 全为 `*` 视为未提供） | `{ok: bool, message: str}` |
| `GET` | `/api/slash-commands` | 是 | — | `{commands: [...]}`（前端输入框提示用） |

## 用量 / 插件 / 最近目录

| 方法 | 路径 | 鉴权 | 请求体 / 查询 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/api/usage` | 是 | `?session_id=xxx`（单会话）或省略（全局总览） | 对应汇总对象 |
| `GET` | `/api/plugins` | 是 | — | `{dir, plugins: [{name, tools, error}], tool_count}` |
| `GET` | `/api/recent-dirs` | 是 | — | `{dirs: [...]}`（最多 8 条） |

## 记忆

实现见 `spark/web/api_data.py`（记忆按工作目录隔离，`MemoryStore`）。

| 方法 | 路径 | 鉴权 | 请求体 / 查询 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/api/memory` | 是 | `?workdir=...`（缺省回落全局配置） | `{workdir, count, items: [...]}`；无工作目录 → `400` |
| `POST` | `/api/memory` | 是 | `{workdir, key, value, level?}`；`key`/`value` 必填且分别 ≤200/≤5000 字符；`level` 可选 ∈ `semantic`（默认）/`situational`/`episodic`/`procedural`，非法值回落 `semantic` | `{ok: true, count}`；无工作目录 / key 或 value 缺 / 超长 → `400` |
| `DELETE` | `/api/memory/{memory_id}` | 是 | 路径参数 `memory_id`（整数） | `{ok: true}`；不存在 → `404` |

## 文件 / git / 终端

文件与 git 工作目录来源：优先 `?sid=`/body `sid` 对应会话的 workdir，其次全局配置。

| 方法 | 路径 | 鉴权 | 请求体 / 查询 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/api/fs` | 是 | `?sid=...`、`?path=相对子路径` | `{workdir, path, entries: [{name, dir}]}`（只读列表，跳过 node_modules/dist 等）；越界/目录不存在 → `400`/`404`，无工作目录 → `400` |
| `GET` | `/api/git` | 是 | `?sid=...` | `{repo, branch, changes, checkpoints:[{hash,time,message}]}` 或 `{repo: false, reason}`；无工作目录 → `400` |
| `POST` | `/api/git/checkpoint` | 是 | `{sid?, message?}`（message 截断 120 字，缺省自动填） | `{ok: true, message}`；git 失败 → `400`，无工作目录 → `400` |
| `POST` | `/api/git/reset` | 是 | `{sid?, confirm: "yes"}`（破坏性操作，必须 `confirm=yes`） | `{ok: true, message}`；缺确认 → `400`，git 失败 → `400` |
| `POST` | `/api/pty/close` | 是 | `{sid, tab}` | `{ok: true}`（终止该 tab 的 shell 子进程） |
| `WS` | `/ws/pty` | 令牌走 `?token=` | 查询 `?sid=&tab=&token=`；消息 `{type:"in"|"resize", ...}`，服务端回 `{type:"out"|"exit"|"err"}` | 持久 PTY 双向流；令牌错误 → `4401`，无工作目录/后端不可用 → `4400` |
