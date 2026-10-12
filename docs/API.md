# Spark HTTP API 参考

> English: [docs/en/API.md](en/API.md)
> 实现源：`src/spark/web/routes.py`（GET 路由 + POST dispatch 表）、`src/spark/web/_handlers.py`（POST 业务逻辑）。

## 认证

服务默认只监听本机 `127.0.0.1`（`spark web --host 0.0.0.0` 可显式开放到局域网，风险自负）。

- 鉴权实现位于 `routes.py` 的 `_guard` / `_authorized`；非回环来源必须带控制令牌。
- 令牌在服务启动时随机生成（`SparkWebState.control_token`），只在控制台对象上提供，不会写进日志。
- 本机（127.0.0.1 / ::1 / localhost）请求免令牌；其他来源一律 `401`，需带 `X-Spark-Token` 请求头。
- 额外防护：Host 头非本机 → `403`；带 Origin 头且非本机来源 → `403`（防 CSRF）；POST 的 Content-Type 必须是 `application/json`，否则 `415`。

## 静态资源

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 返回前端单页（优先 `src/spark/web/dist/index.html`，缺失时回落内置 `index.html`） |
| `GET` | `/<静态文件>` | 伺服 UI 目录内的资源（带路径逃逸检查）；`/assets/*` 带一年 immutable 缓存 |

## 只读 GET 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/status` | 会话 / 沙箱模式 / 审批模式 / 上下文用量 / 计划列表 / 环境信息 |
| `GET` | `/api/config` | 完整配置（密钥打码）：provider、agent、context、mcp_servers、hooks |
| `GET` | `/api/models` | 模型 profile 列表（api_key 打码，含 `active` 标记） |
| `GET` | `/api/agents_md` | 读取工作目录内 AGENTS.md 的内容与状态 |
| `GET` | `/api/memory` | 长期记忆：`items` 全量、`search?q=关键词` 检索（带 score）、`stats` 统计 |
| `GET` | `/api/sessions` | 当前工作目录的会话列表（最近 50 条）+ `current` 会话 id |
| `GET` | `/api/checkpoints` | 当前会话的检查点列表 |
| `GET` | `/api/history` | 当前会话消息历史（含图片的 base64 data URI） |

## POST 接口

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/chat/stream` | `{prompt, images?}` | SSE 流式对话。事件：`text_delta` / `tool`（含 tool_call 与结果）/ `approval`（含 diff）/ `turn_end` / `turn_error` / `done`；心跳 `: ping` |
| `POST` | `/api/chat` | `{prompt}` | 非流式对话，返回完整事件数组（调试用） |
| `POST` | `/api/cancel` | — | 取消当前回合与待审批 |
| `POST` | `/api/approval` | `{decision}` | 审批决定：`allow` / `allow_always` / `deny` |
| `POST` | `/api/models/save` | `{name, base_url, model, provider, api_key, id?}` | 新建或更新模型 profile |
| `POST` | `/api/models/delete` | `{id}` | 删除 profile；若删除的是活跃 profile 则清空 `active_profile_id` |
| `POST` | `/api/models/activate` | `{id}` | 切换活跃 profile 并重建 AgentLoop；agent 忙时 `409` |
| `POST` | `/api/agents_md` | `{content}` | 写工作目录内的 AGENTS.md（文件名受白名单校验） |
| `POST` | `/api/memory` | `{action}` | `add`（content/type/importance）/ `delete`（id）/ `optimize` |
| `POST` | `/api/sessions/new` | — | 新建会话 |
| `POST` | `/api/sessions/delete` | `{session_id}` | 删除会话；删当前会话时自动切换 |
| `POST` | `/api/sessions/switch` | `{session_id}` | 切换会话并返回消息历史 |
| `POST` | `/api/checkpoints/rollback` | `{checkpoint_id}` | 回滚到检查点（恢复工作区文件 + 对话历史） |
| `POST` | `/api/settings` | provider/agent/mcp_servers/hooks 各节 | 保存设置并重建 AgentLoop；字段校验失败 `400`，agent 忙 `409` |
| `POST` | `/api/test` | `{base_url, model, api_key?}` | 探活模型接口（拒绝向与已配置不同的地址发送存储的密钥） |

未知路径一律 `404`；请求体超过 32 MiB 返回 `413`；JSON 解析失败 `400`。

## SSE 事件格式

`/api/chat/stream` 返回 `text/event-stream`，每条事件为 `data: {JSON}\n\n`：

```jsonc
{"type": "text_delta", "text": "…"}                       // 增量文本（已脱敏）
{"type": "tool", "tool": {…}, "ok": true, "result": {…}}  // 工具调用与结果
{"type": "approval", "approval": {"summary": "…", "diff": "…"}}  // 等待审批
{"type": "turn_end", "text": "…"}                         // 回合结束
{"type": "turn_error", "text": "…"}                       // 回合出错
{"type": "done"}                                          // 流结束
```

连接断开（写失败或对端关闭）会自动取消当前回合；15 秒无数据发送 `: ping` 心跳。

> AI生成
