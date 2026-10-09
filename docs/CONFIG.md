# Spark 配置文档（config.toml）

> English: [docs/en/CONFIG.md](en/CONFIG.md)

配置文件位于 `~/.spark/config.toml`（可用 `SPARK_HOME` 环境变量覆盖目录）。
绝大多数字段可在网页「设置」面板中修改并保存，本文件列出全部字段与默认值。

## 核心字段

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `provider` | `mock` | 模型服务预设：`deepseek` / `deepseek-flash` / `qwen` / `glm` / `kimi` / `doubao` / `unisound` / `ollama` / `custom` / `mock` |
| `base_url` | `""` | OpenAI 兼容接口地址（`custom` 预设必须填） |
| `model` | `mock` | 主模型名 |
| `api_key` | `""` | API 密钥（网页回显打码，写入时不受影响） |
| `workdir` | 启动目录 | 默认工作目录 |
| `approval_mode` | `suggest` | 审批模式：`suggest` 建议（写类询问）/ `auto-edit` 自动改文件 / `full-auto` 全自动 / `plan` 只读规划 |
| `max_context_tokens` | `32000` | 上下文窗口上限，超限自动压缩旧消息 |
| `token` | `""` | 访问令牌；空 = 免登录（默认监听 0.0.0.0，公网可达时务必开启） |

## 模型路由与容灾

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `model_fast` | `""` | 快速模型名；简单任务自动走它、复杂任务走主模型。留空 = 不启用路由 |
| `route_enabled` | `true` | 多模型路由开关（`model_fast` 非空时才生效） |
| `route_keywords` | `""` | 自定义「强任务」关键词（逗号/空格/换行分隔）；空 = 内置词表 |
| `fallback_model` | `""` | 备用模型：主模型不可用（限流/故障/配置错误）自动切换。留空 = 不启用 |

## 记忆

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `memory_embedding` | `off` | `off` 仅关键词检索（零依赖）/ `api` 火山方舟 doubao-embedding / `local` 本地模型 |
| `memory_embed_model` | `""` | 嵌入模型名（`api`/`local` 模式必填） |

## MCP 服务器

```toml
[[mcp_servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
transport = "stdio"   # stdio | http
env = {}              # 环境变量（JSON 对象）
# transport = "http" 时：
# url = "http://127.0.0.1:8000/mcp"
# headers = {}        # HTTP 头（JSON 对象）
```

## 高级可调项

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `system_prompt` | `""` | 自定义系统提示词；空 = 内置默认。支持 `{workdir}` `{protected}` 占位符 |
| `protected_paths` | `[]` | 额外保护路径（TOML 数组）：这些路径下永远拒绝写入 |
| `max_turns` | `25` | 单次对话最大工具轮次 |
| `tool_timeout` | `180` | 单个工具执行超时（秒） |
| `auto_verify` | `true` | `apply_patch` 成功后自动跑 pytest 验证（可关） |
| `temperature` | `""` | 采样温度；空 = 不传（用服务端默认） |
| `max_tokens` | `""` | 单次回复最大 tokens；空 = 不传 |
| `usage_pricing` | `{}` | 成本单价覆盖：`{模型名: {input: 元/百万, output: 元/百万}}` |
| `proxy` | `""` | HTTP(S) 代理（如 `http://127.0.0.1:7890`）；空 = 尊重环境变量 |

## 环境变量

- `SPARK_HOME`：覆盖配置/数据目录（默认 `~/.spark`）
- `<PROVIDER>_API_KEY`：provider 对应的密钥环境变量（`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `ZHIPU_API_KEY` / `MOONSHOT_API_KEY` / `ARK_API_KEY` / `UNISOUND_API_KEY` / `OLLAMA_API_KEY`）
- `HTTPS_PROXY` / `HTTP_PROXY`：未设 `proxy` 字段时由 httpx 自动尊重

## 示例（云知声 u2-flash）

```toml
provider = "unisound"
base_url = "https://maas-api.unisound.com/v1"
model = "u2-flash"
api_key = "sk-xxx"          # 或设环境变量 UNISOUND_API_KEY
workdir = "/path/to/proj"
approval_mode = "suggest"
fallback_model = "u2-pro"   # 可选：主模型故障自动切换
```

> AI生成