# 接口定义

## CLI

入口命令：`spark`

```text
spark [OPTIONS] [PROMPT]
spark exec [OPTIONS] PROMPT
spark sessions
spark resume SESSION_ID
```

### 全局选项

| 选项 | 说明 |
|------|------|
| `--workdir PATH` | 工作区根，默认当前目录 |
| `--approval MODE` | `suggest` / `auto-edit` / `full-auto`，覆盖配置默认值 |
| `--model NAME` | 覆盖配置中的模型名 |
| `--provider NAME` | `openai_compat` 或 `ollama` |
| `--config PATH` | 指定配置文件 |

### 子命令

| 命令 | 行为 |
|------|------|
| `spark` | 打开 TUI；若带 PROMPT，进入后自动作为第一轮用户消息 |
| `spark exec PROMPT` | 非交互跑完一轮任务，审批模式必须能自动完成（`auto-edit` 或 `full-auto`），结束后把最终回复打到 stdout |
| `spark sessions` | 列出本地会话 id、工作区、更新时间、首条摘要 |
| `spark resume ID` | 用已有会话打开 TUI |

退出码：`0` 成功；`1` 运行错误；`2` 配置/参数错误；`3` 用户拒绝关键审批导致任务中止。

## 配置文件

路径优先级：`--config` > `./.spark.toml` > `~/.spark/config.toml`

```toml
[provider]
name = "openai_compat"
base_url = "https://api.example.com/v1"
model = "deepseek-chat"
api_key_env = "SPARK_API_KEY"

[agent]
approval = "suggest"
workdir_only = true
shell_timeout_sec = 60
max_tool_rounds = 30

[context]
agents_md = "AGENTS.md"
max_fragment_chars = 8000
history_budget_chars = 96000

[[mcp.servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

密钥只从 `api_key_env` 指出的环境变量读取。配置文件写占位变量名，不写真实 Key。

## TUI 交互

| 区域 | 内容 |
|------|------|
| 主栏 | 用户消息、模型流式文本、工具调用卡片（名称、参数摘要、结果截断） |
| 底栏 | 多行输入、发送、当前模型、审批模式、token/轮次粗计数 |
| 审批层 | 工具名、命令或 diff、`Allow` / `Deny` / `Allow always this tool in session` |
| 快捷键 | `Enter` 发送，`Ctrl+C` 中止本轮，`Ctrl+D` 退出，`Ctrl+L` 清屏显示（会话仍在） |

## 内置工具（模型可见 schema）

工具名使用 snake_case。参数 JSON Schema 由 pydantic 模型生成。

### read_file

```json
{
  "name": "read_file",
  "parameters": {
    "path": "string, relative to workdir",
    "offset": "optional int, 1-based start line",
    "limit": "optional int, max lines, default 200"
  }
}
```

成功返回 `{ "path": "...", "content": "..." }`。路径越界或文件不存在返回 `{ "error": "..." }`。

### list_dir

```json
{
  "name": "list_dir",
  "parameters": {
    "path": "string, relative, default .",
    "max_entries": "optional int, default 200"
  }
}
```

返回文件/目录名、类型、大小（文件）。

### write_file

```json
{
  "name": "write_file",
  "parameters": {
    "path": "string",
    "content": "string"
  }
}
```

整文件写入。Suggest 模式先展示将写入的内容摘要。

### apply_patch

```json
{
  "name": "apply_patch",
  "parameters": {
    "path": "string",
    "old_text": "string",
    "new_text": "string"
  }
}
```

在文件中精确替换一处 `old_text`。匹配 0 次或多次时失败并返回原因，避免静默写错。

### run_shell

```json
{
  "name": "run_shell",
  "parameters": {
    "command": "string",
    "cwd": "optional relative directory"
  }
}
```

在工作区内执行。捕获 stdout/stderr/exit_code，输出截断到上限。超时按配置中止进程。

## Provider 协议

```python
class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_calls: list[ToolCall] | None
    tool_call_id: str | None

class ChatDelta(TypedDict):
    type: Literal["text", "tool_call", "end"]
    text: str | None
    tool_call: ToolCall | None
```

`Provider.stream(messages, tools) -> AsyncIterator[ChatDelta]`

OpenAI 兼容实现：`POST {base_url}/chat/completions`，`stream=true`，`tools` 为 function 列表。

Ollama 实现：同一套 Chat Completions 形状，默认 `base_url=http://127.0.0.1:11434/v1`，`api_key` 可空。

## 会话存储

SQLite 表（逻辑字段）：

- `sessions(id, workdir, model, created_at, updated_at, title)`
- `messages(id, session_id, role, content, created_at, payload_json)`
- `tool_events(id, session_id, message_id, name, arguments_json, result_json, approval, created_at)`

`spark resume` 用 `sessions.id` 加载 `messages` 重建 Context。

## MCP 约定

- 传输：stdio
- 启动：进程生命周期绑定 Spark 进程
- 发现：初始化后 `tools/list`
- 调用：模型发起 `mcp__{server}__{tool}` 形式的函数名，Bridge 拆分后 `tools/call`
- 失败：服务器退出或协议错误时，该 server 的工具从本轮 registry 移除，并在 TUI 提示

## AGENTS.md

工作区根（或 `--workdir`）下的 `AGENTS.md` 在每次组装 Context 时读取。文件缺失则跳过。内容作为一条名为 `project_instructions` 的用户侧片段注入，受 `max_fragment_chars` 截断。
