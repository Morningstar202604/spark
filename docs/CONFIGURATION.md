# Spark Agent — 配置参考

本文档详细说明 Spark 的所有配置项。

## 配置文件位置

| 文件 | 优先级 | 说明 |
|------|--------|------|
| `~/.spark/config.toml` | 全局默认 | 用户级配置 |
| `<workdir>/.spark.toml` | 项目级 | 覆盖全局配置 |
| CLI 参数 | 最高 | 如 `--sandbox-mode` |
| 环境变量 | 次高 | 如 `SPARK_API_KEY` |

## 完整配置示例

```toml
# 当前激活的模型档案 ID
active_profile_id = ""

# ─── 模型提供者 ───────────────────────────────────────────────
[provider]
name = "openai_compat"        # openai_compat / ollama / mock
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "SPARK_API_KEY"
# api_key = "sk-..."          # 可选：直接写入密钥（不推荐，用环境变量更安全）

# ─── Agent 行为 ───────────────────────────────────────────────
[agent]
approval = "suggest"          # suggest / auto-edit / full-auto
workdir_only = true
sandbox_mode = "workspace"    # sandbox-only / workspace / full-access / unrestricted
protected_paths = []          # 额外保护路径（相对于 home 或 absolute）
shell_timeout_sec = 60
max_tool_rounds = 30
max_output_chars = 8000

# 展示开关（前端控制显示/隐藏）
show_thinking = true
show_tools = true
show_plan = true
show_context = true
show_keywords = true
show_notices = true

# ─── 上下文管理 ───────────────────────────────────────────────
[context]
agents_md = "AGENTS.md"                 # 注入的项目说明文件
max_fragment_chars = 8000               # 单文件最大读取字符数
history_budget_chars = 96000            # 历史消息总预算
max_context_tokens = 32768              # 上下文窗口上限
compact_threshold = 0.85                # 触发压缩的 token 比例（85%）
keep_recent_messages = 8                # 压缩后保留最近几条消息

# ─── 长期记忆 ─────────────────────────────────────────────────
[memory]
enabled = true
top_k = 6                    # 检索返回的最近记忆条数
capacity = 500               # 记忆库最大条数
embedding_model = "text-embedding-3-small"
auto_extract = true            # 每轮结束后自动抽取记忆

# ─── MCP 服务器 ───────────────────────────────────────────────
# [[mcp.servers]]
# name = "filesystem"
# command = "npx"
# args = ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"]
# readonly_tools = ["ReadFile", "ReadDir", "Glob"]

# ─── 模型档案（多模型切换） ───────────────────────────────────
# [[model_profiles]]
# id = "deepseek-chat"
# name = "DeepSeek Chat"
# provider = "openai_compat"
# base_url = "https://api.deepseek.com/v1"
# model = "deepseek-chat"
# api_key = ""  # 留空则读取 api_key_env 指定的环境变量
#
# [[model_profiles]]
# id = "local"
# name = "Ollama Local"
# provider = "ollama"
# base_url = "http://localhost:11434/v1"
# model = "deepseek-r1:7b"
# api_key = ""
```

## 环境石量

| 变量 | 说明 |
|------|------|
| `SPARK_API_KEY` | API 密钥（通用备选，优先级低于 config） |
| `SPARK_BASE_URL` | 覆盖 provider.base_url |
| `SPARK_MODEL` | 覆盖 provider.model |
| `SPARK_CONFIG_PATH` | 自定义配置文件路径 |

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

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 获取当前状态、配置摘要 |
| POST | `/api/chat` | 同步聊天（非流式） |
| POST | `/api/chat/stream` | 流式 SSE 聊天 |
| POST | `/api/cancel` | 停止当前生成 |
| POST | `/api/approval` | 回复审批决策 |
| GET | `/api/sessions` | 列出会话 |
| POST | `/api/sessions/new` | 创建新会话 |
| POST | `/api/sessions/switch` | 切换会话 |
| DELETE | `/api/sessions/:id` | 删除会话 |
| GET | `/api/checkpoints` | 列出检查点 |
| POST | `/api/checkpoints/rollback` | 回滚到检查点 |
| GET | `/api/config` | 获取当前配置 |
| PUT | `/api/settings` | 保存设置 |
| GET | `/api/models` | 列出模型档案 |
| POST | `/api/models/save` | 保存模型档案 |
| DELETE | `/api/models/:id` | 删除模型档案 |
| POST | `/api/models/activate` | 激活模型档案 |
| GET | `/api/memory` | 查看记忆库 |
| PUT | `/api/memory` | 保存记忆 |
| GET | `/api/agents_md` | 获取 AGENTS.md |
| PUT | `/api/agents_md` | 更新 AGENTS.md |
| POST | `/api/test` | 测试模型连通性 |

## 本地数据文件

| 路径 | 说明 |
|------|------|
| `~/.spark/config.toml` | 配置文件（含模型档案与密钥） |
| `~/.spark/sessions.db` | SQLite：会话、消息、工具事件、检查点、后台任务记录 |
| `~/.spark/checkpoints/*.tar.gz` | 工作区快照（回滚用） |
| `~/.spark/memory.db` | 长期记忆库 |
| `~/.spark/spark.log` | 运行日志 |

后台任务（`bg_start`）记录持久化在 `sessions.db` 的 `bg_jobs` 表：服务器重启后 `bg_list` 仍可列出历史任务（标记为 lost），`bg_output` 返回最后一次持久化的输出，进程本体已随重启终止。

## CLI 命令

```bash
# 启动 TUI（终端界面）
spark --workdir /path --approval suggest

# 无头执行（非交互）
spark exec --model deepseek-chat "写一个测试脚本"

# 启动 Web UI
spark web --port 8000 --approval full-auto

# 会话管理
spark sessions                    # 列出所有会话
spark resume <session_id>        # 恢复指定会话

# 模型探测（测试连通性）
spark test --provider openai_compat --model deepseek-chat
```

## 常见问题

**Q: 如何切换模型？**
A: 在 Web UI 的设置面板中，或修改 `~/.spark/config.toml` 的 `[provider]` 段。

**Q: 检查点默认保存在哪里？**
A: `~/.spark/checkpoints/<session_id>/<id>.tar.gz`。

**Q: 如何备份所有数据？**
A: 复制 `~/.spark/` 目录即可，包含配置、记忆、会话和检查点。

**Q: 如何重置为默认配置？**
A: 删除 `~/.spark/config.toml`，下次启动会自动重新生成模板。
