# 国产 agentic 模型路由矩阵（P1-10）

> 依据调研报告第 1/10 章。本文件是「文档 + 决策函数」：代码实现见
> `src/spark/providers/routing.py`，决策函数 `decide_tier` /
> `apply_routed_provider` / `route_and_build`。
>
> **诚实底线**：本矩阵**不接任何海外直连模型平台**。所有国产模型都复用 spark
> 现有两条通道：在线模型走 `openai_compat`（LiteLLM OpenAI 兼容协议），
> 本地模型走 `ollama`。下文各模型的 `base_url` / `model` 名 / thinking
> 参数为 2025 下半年可核实配置，**具体以各厂商最新文档为准**。

---

## 1. 三档矩阵

| 档位 | 定位 | 代表模型 | 通道 | 典型场景 |
|---|---|---|---|---|
| **能力档 `capability`** | 质量优先，多步编程/复杂调试 | DeepSeek-V3.2(thinking)、Qwen3-Coder、Kimi K2、GLM-4.5 | `openai_compat` | 跨文件重构、难 bug 定位、长链路 agent 任务 |
| **成本档 `cost`** | 省钱，日常轻量任务 | DeepSeek-V3.2(非思考 `deepseek-chat`)、豆包(火山方舟) | `openai_compat` | 问答、补全、单文件小改、批量低风险调用 |
| **离线档 `offline`** | 无网络/隐私/断网 | Ollama 本地 Qwen3-Coder GGUF（native tool calling） | `ollama` | 内网、离线机房、敏感代码不出本机 |

### 档位划分依据

* **能力档**：选自调研报告中「agentic coding 榜单密集登顶」的国产模型，均支持
  OpenAI 风格 `tools` 协议与（部分）thinking/reasoning 流。spark 已有
  effort（low/medium/high）链路，会把 effort 映射为 provider 原生的思考预算
  （DeepSeek `thinking.budget_tokens`、其余 `reasoning_effort`）。
* **成本档**：DeepSeek 非思考档命中磁盘 prompt 缓存后单价极低；豆包（火山方舟）
  作为第二家便宜在线后端，用于在线档互备。
* **离线档**：Ollama 单二进制、GGUF、MIT 体量轻；Qwen3-Coder 30B-A3B 版适配
  单机/Apple Silicon；其 native tool calling 让离线环境也能跑工具循环。
  代价是本地小模型工具调用准确率低于云端。

---

## 2. 每模型接入方式

下表字段：`base_url` / `model` / `thinking` 参数 / API key 环境变量。
配置方式为在 `~/.spark/config.toml` 的 `[provider]` 段填写，或用
`apply_routed_provider(cfg, decision)` 由路由层自动写回。

### 2.1 能力档

#### DeepSeek-V3.2（thinking / `deepseek-reasoner`）
- `base_url`: `https://api.deepseek.com/v1`
- `model`: `deepseek-reasoner`（思考档；非思考档用 `deepseek-chat`，见成本档）
- thinking: spark 已在 `openai_compat.py` 内按 effort 注入
  `extra_body.thinking.budget_tokens`（low=2048 / medium=4096 / high=8192）
- API key: 环境变量 `DEEPSEEK_API_KEY`（或 `SPARK_API_KEY`）
- 注意：V3.2 把 thinking 直接融入工具调用，流协议字段以官方 changelog 为准。

#### Qwen3-Coder（阿里云百炼）
- `base_url`: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- `model`: `qwen3-coder-plus`（具体模型 ID 以百炼文档为准）
- thinking: `enable_thinking` 开关映射到 spark 的 effort 档
- API key: 环境变量 `DASHSCOPE_API_KEY`

#### Kimi K2（月之暗面开放平台）
- `base_url`: `https://api.moonshot.cn/v1`
- `model`: `kimi-k2-0905-preview`（模型名以 Moonshot 文档为准）
- thinking: 非 thinking 档（长上下文 + 工具调用为强项）
- API key: 环境变量 `MOONSHOT_API_KEY`

#### GLM-4.5（智谱 bigmodel.cn）
- `base_url`: `https://open.bigmodel.cn/api/paas/v4`
- `model`: `glm-4.5`（以智谱文档为准）
- thinking: 支持 thinking/非 thinking 双模态，接入 effort 与 `reasoning_content` 解析
- API key: 环境变量 `ZHIPUAI_API_KEY`

### 2.2 成本档

#### DeepSeek-V3.2（非思考 / `deepseek-chat`）
- `base_url`: `https://api.deepseek.com/v1`
- `model`: `deepseek-chat`
- thinking: 关闭；磁盘 prompt 缓存自动命中（保持 system + 工具定义在前缀稳定）
- API key: 环境变量 `DEEPSEEK_API_KEY`

#### 豆包 / Doubao-Seed-Code（火山引擎方舟）
- `base_url`: `https://ark.cn-beijing.volces.com/api/v3`
- `model`: 方舟通常填接入点 ID（`ep-xxxxxxxx`）或 `doubao-seed-code-*` 模型名，
  **以火山引擎方舟控制台与文档为准**
- thinking: 视所选接入点而定
- API key: 环境变量 `ARK_API_KEY`

### 2.3 离线档

#### Ollama 本地 Qwen3-Coder GGUF
- `provider`: `ollama`
- `base_url`: `http://127.0.0.1:11434/v1`（Ollama OpenAI 兼容端点）
- `model`: `qwen3-coder:latest`（先 `ollama pull qwen3-coder`）
- tool calling: Ollama **native tool calling**，直接输出 OpenAI 风格 tools JSON
- API key: 本地无需 key（`require_api_key` 对 ollama/mock 返回占位值）
- 模型获取：国内可经 ModelScope 镜像拉取 GGUF；30B-A3B 版适配单机。

---

## 3. 何时用哪档（路由决策规则）

`decide_tier(task_type, budget, offline)` 的决策逻辑（实现见 routing.py）：

| 输入 | 目标档位类 |
|---|---|
| `offline=True`（或 `task_type="offline"`） | `offline` 离线档 |
| `task_type="code"` / `"debug"` | `capability` 能力档（质量优先） |
| `task_type="chat"` 且 `budget="low"` | `cost` 成本档（省钱） |
| `task_type="chat"` 且 `budget="high"` | `capability` 能力档 |
| `task_type="chat"` 且 `budget="medium"` | `cost` 成本档（默认平衡） |

同类内按偏好顺序选第一个「可用」的模型（可用 = 对应 `*_API_KEY` 已设置，
离线档恒可用）。**降级链**：`capability → cost → offline`，任何环境都能选出一档。

快速示例：

```bash
# 能力档（复杂重构）
export DASHSCOPE_API_KEY=...
# 代码内：decide_tier("code", "high")  -> qwen_coder / deepseek_thinking

# 成本档（日常问答，省钱）
export DEEPSEEK_API_KEY=...
# decide_tier("chat", "low") -> deepseek_chat

# 离线档（断网/隐私）
ollama pull qwen3-coder
# decide_tier("code", offline=True) -> ollama_local
```

---

## 4. 与现有 create_provider 的关系

* 路由层**只改写** `cfg.provider` 的 `name/base_url/model/api_key_env` 字段，
  不触碰 `agent` / `context` / `memory` 等其它配置段。
* 改写后调用**原有** `create_provider(cfg)` 即可：
  - `name="openai_compat"` → `OpenAICompatProvider`（DeepSeek/Qwen/Kimi/GLM/豆包）
  - `name="ollama"` → `OllamaProvider`（本地 Qwen3-Coder GGUF）
  - `name="mock"` → `MockProvider`（测试）
* `create_provider` 的签名与三条既有路径行为**完全不变**，mock/openai_compat/ollama
  的存量测试不受影响。

---

## 5. 配置切换（config / 环境变量）

路由层默认从环境变量探测各模型 key。若要手动锁定某一档：

```python
from spark.config import SparkConfig
from spark.providers.routing import decide_tier, apply_routed_provider

cfg = SparkConfig()
decision = decide_tier("debug", "high", preferred="glm_45")  # 显式首选
apply_routed_provider(cfg, decision)
# 或一步到位：from spark.providers.routing import route_and_build
# provider, decision = route_and_build(cfg, task_type="debug", budget="high")
```

> 注：路由矩阵当前为「决策函数 + 文档」，未做自动健康检查轮询。
> 可用性判断基于「API key 是否配置」；真实连通性由 provider 层的重试/报错承担。
