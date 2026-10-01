# Spark HTTP API 参考

> English: [docs/en/API.md](en/API.md)

服务启动后监听 `127.0.0.1`。设置过访问令牌（`token`）后，所有请求需带请求头
`X-Spark-Token: <token>`，否则返回 401。流式接口走 SSE（`text/event-stream`）。

## 会话

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/sessions` | 创建会话（body: `{"workdir": "..."}`）→ `{id, title, ...}` |
| `GET` | `/api/sessions` | 会话列表；`?q=关键词` 全文搜索（标题/目录/消息正文命中，附 `match` 片段） |
| `GET` | `/api/sessions/{sid}` | 会话详情（含全部消息） |
| `PATCH` | `/api/sessions/{sid}` | 重命名（body: `{"title": "..."}`） |
| `DELETE` | `/api/sessions/{sid}` | 删除会话 |
| `POST` | `/api/sessions/{sid}/fork` | 分叉新会话（body: `{"workdir": "..."}`） |
| `POST` | `/api/sessions/{sid}/truncate` | 截断到某消息（body: `{"message_id": "..."}`）→ 该消息及之后全删 |
| `DELETE` | `/api/sessions/{sid}/messages/{message_id}` | 删除单条消息（持久生效） |
| `GET` | `/api/sessions/{sid}/context` | 上下文水位 → `{used, max, compact}` |

## 对话

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/chat/stream` | SSE 流式对话（body: `{session_id, prompt, approval_mode}`；支持 `images` 多模态） |
| `POST` | `/api/approval` | 审批响应（写类工具等待时：`{request_id, approve}`） |
| `POST` | `/api/cancel` | 取消当前会话运行 |

SSE 事件类型：`hello`（含 `user_msg_id`）/ `reasoning` / `text` / `tool_start` / `approval` /
`tool_result` / `usage` / `done`（含 `assistant_msg_id`）/ `error` / `close`。

## 配置

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` / `POST` | `/api/config` | 读取/保存配置（POST 全量回写，密钥回显打码且不会覆盖真值） |
| `POST` | `/api/test-connection` | 测试当前模型连接（body 同 POST /api/config 子集） |

## 记忆

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/memory?q=...` | 检索记忆 |
| `POST` | `/api/memory` | 写入记忆（body: `{key, value, scope}`） |
| `DELETE` | `/api/memory/{memory_id}` | 删除记忆 |

## 文件 / 终端 / 集成

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/fs?path=...` | 工作目录文件浏览 |
| `GET` | `/api/git` | Git 状态；`POST /api/git/checkpoint` 检查点；`POST /api/git/reset` 回滚 |
| `GET` | `/api/plugins` | 插件列表与状态 |
| `GET` | `/api/usage` | 用量/成本统计 |
| `GET` | `/api/recent-dirs` | 最近目录 |
| `GET` | `/api/pty`、`POST` /api/pty/close、`WS /ws/pty` | Web 终端（PTY，按会话分组） |
