# Spark 配置文档（config.toml）

> English: [docs/en/CONFIG.md](en/CONFIG.md)
> 字段的唯一事实来源：`src/spark/config.py`（`SparkConfig` 及嵌套模型，含全部默认值与边界）。

配置文件位于 `~/.spark/config.toml`（可用 `SPARK_HOME` 环境变量覆盖目录），由 `spark init` 生成模板。
绝大多数字段可在网页「设置」面板中修改并保存，本文件按 `[section]` 列出全部字段与默认值。

## `[provider]` — 模型服务

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `name` | `openai_compat` | `openai_compat` / `ollama` / `mock` |
| `base_url` | `https://api.deepseek.com/v1` | OpenAI 兼容接口地址（LiteLLM 承载，支持 100+ 后端） |
| `model` | `deepseek-chat` | 主模型名 |
| `api_key_env` | `SPARK_API_KEY` | 密钥所在环境变量名 |
| `api_key` | `null` | 直接写密钥（网页回显打码）；优先级低于环境变量 |

## `[agent]` — 行为与安全

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `approval` | `suggest` | 审批模式：`suggest` 写类逐项询问 / `auto-edit` 自动改文件 / `full-auto` 全自动 |
| `workdir_only` | `true` | 限制文件访问在工作目录内 |
| `sandbox_mode` | `workspace` | `sandbox-only` 只读 / `workspace` 工作目录内读写 / `full-access` 可越界（保护路径除外）/ `unrestricted` 无限制 |
| `protected_paths` | `[]` | 永远拒绝写入的额外路径（Spark 自身代码与配置始终受保护） |
| `shell_timeout_sec` | `60` | 单条命令超时（1–3600） |
| `max_tool_rounds` | `30` | 单回合最大工具轮次（1–1000） |
| `max_repeat_calls` | `4` | 同一 (工具， 参数) 签名的熔断阈值（0 = 关闭） |
| `max_turn_tokens` | `0` | 单回合 token 预算（0 = 关闭） |
| `max_output_chars` | `8000` | 工具输出截断长度（200–1,000,000） |
| `show_thinking` / `show_tools` / `show_plan` / `show_context` / `show_keywords` / `show_notices` | `true` | 前端显示开关 |

## `[context]` — 上下文窗口

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `agents_md` | `AGENTS.md` | 注入的项目说明文件名（仅裸文件名，防路径逃逸） |
| `max_fragment_chars` | `8000` | AGENTS.md 注入截断长度 |
| `history_budget_chars` | `96000` | 历史消息字符预算 |
| `max_context_tokens` | `32768` | 上下文窗口上限，超过 `compact_threshold` 自动压缩 |
| `compact_threshold` | `0.85` | 触发压缩的用量比例（0.1–0.99） |
| `keep_recent_messages` | `8` | 压缩时保留的最近消息数 |

## `[memory]` — 长期记忆

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关（ChromaDB 向量持久化 + 关键词检索） |
| `top_k` | `6` | 检索条数（1–50） |
| `capacity` | `500` | 记忆容量上限（1–100,000） |
| `embedding_model` | `text-embedding-3-small` | 嵌入模型 |
| `auto_extract` | `true` | 每回合后自动抽取事实入记忆 |

## MCP 服务器

```toml
[[mcp_servers]]
name = "filesystem"
command = "npx"                      # stdio 传输
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
readonly_tools = []                  # 声明为只读的工具名（免审批）

# 远程传输（SSE / streamable HTTP）：
# [[mcp_servers]]
# name = "remote"
# transport = "sse"                  # stdio | sse | streamable_http
# [mcp_servers.http]
# url = "http://127.0.0.1:3000/sse"
# headers = {}
# timeout_sec = 30
```

## `[[hooks]]` — 生命周期钩子

```toml
[[hooks]]
event = "pre_tool"                   # 见 spark/hooks.py 的 HookRegistry.EVENTS
command = "python"
args = ["check.py"]
timeout_sec = 15
on_deny = "warn"                     # warn | block
```

## 模型 profiles（多模型切换）

```toml
active_profile_id = "a1b2c3d4"

[[model_profiles]]
id = "a1b2c3d4"
name = "DeepSeek"
provider = "openai_compat"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
# api_key 字段存在但示例留空：密钥请写入配置或环境变量，勿提交到仓库
```

网页「设置 → 模型」的增删切换即读写这一节。

## 环境变量

- 所有字段都可用 `SPARK_` 前缀 + 双下划线嵌套覆盖：`SPARK_AGENT__MAX_TOOL_ROUNDS=50`、`SPARK_PROVIDER__BASE_URL=…`
- `.env` 只从 `$SPARK_HOME/.env` 读取（项目目录内的 `.env` 一律忽略——防止克隆来的仓库给自己开 full-auto）
- `SPARK_HOME`：覆盖配置/数据目录（默认 `~/.spark`）
- `LITELLM_LOCAL_MODEL_COST_MAP`：Spark 默认设为 `True`（使用 LiteLLM 内置模型价格表，跳过 raw.githubusercontent.com 拉取；国内网络拉不到会白等约 9 秒）

## 项目级配置（`.spark.toml`）

放在工作目录的 `.spark.toml` 会与全局配置合并，但会经过安全清洗：provider、MCP、hooks 等可执行配置项不允许由项目文件注入（防提示注入提权）。细节见 `config.py` 的 `_sanitize_project_config`。

> AI生成

## P1 新增：供应商韧性配置（v0.9.4+）

```toml
[provider]
model = "deepseek-chat"
# 凭据池：请求失败逐次轮换（单 key 行为不变）
api_keys = ["sk-backup-1", "sk-backup-2"]
# fallback 链：主模型重试耗尽后依次切换（同端点）
fallback_models = ["deepseek-v4-flash"]
# 小模型分工：压缩/摘要/合并等辅助调用用便宜模型（留空 = 主模型）
small_model = "deepseek-v4-flash"
```

- **usage 真实回填**：OpenAI 兼容端点会在流尾上报 token 用量（自动请求，
  不支持的端点自动降级估算）；`token_usage` 表 `source` 列为 `reported`。
- **模型目录**：`spark models [关键词]` 查询 models.dev 开放目录
  （232+ 供应商、价格、上下文长度；24h 本地缓存，离线回退内置快照）。
- **repo map git 热度**：代码地图按 git 提交频率排序（高频文件优先入图）。

## 沙箱模式详解

| 模式 | 可读 | 可写 | Shell | MCP | 用途 |
|------|------|------|-------|-----|------|
| `sandbox-only` | ✅ | ❌ | ❌ | ❌ | 只读分析、代码审查 |
| `workspace` | ✅ | ✅ | ✅ | 限制 | 默认模式，平衡安全与能力 |
| `full-access` | ✅ | ✅ | ✅ | ✅ | 高级用户、信任环境 |
| `unrestricted` | ✅ | ✅ | ✅ | ✅ | 开发调试、完全放开 |

## 审批模式详解

| 模式 | 说明 |
|------|------|
| `suggest` | 敏感操作（写文件、shell）弹出确认对话框 |
| `auto-edit` | 只读操作自动放行，写操作需确认 |
| `full-auto` | 全部自动放行，适合 CI/CD 或可信环境 |
| `plan-only` | 只读允许；写/执行一律不执行，仅输出计划（plan mode） |
| `approve-plan` | 先出计划等待批准，批准后按 auto-edit 语义自动执行 |
| `manual` | 写/执行逐条人工确认 |

## 本地数据文件

| 路径 | 说明 |
|------|------|
| `~/.spark/config.toml` | 配置文件（含模型档案与密钥） |
| `~/.spark/sessions.db` | SQLite：会话、消息、工具事件、检查点、后台任务记录 |
| `~/.spark/checkpoints/*.tar.gz` | 工作区快照（回滚用） |
| `~/.spark/memory.db` | 长期记忆库 |
| `~/.spark/spark.log` | 运行日志 |
| `~/.spark/backups/*.db` | `doctor --backup` 的在线快照（VACUUM INTO） |
| `~/.spark/update-check.json` | 最近一次版本检查缓存（24h） |

后台任务（`bg_start`）记录持久化在 `sessions.db` 的 `bg_jobs` 表：服务器重启后
`bg_list` 仍可列出历史任务（标记为 lost），`bg_output` 返回最后一次持久化的输出。

HTTP API 与 SSE 事件协议单独成篇：见 [API.md](API.md)。CLI 命令见
[INSTALL.md](INSTALL.md) 与 `spark --help`。
