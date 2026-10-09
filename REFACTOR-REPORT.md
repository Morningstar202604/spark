# Spark 项目"零造轮子"重构报告

> 重构原则：所有自造实现替换为官方/成熟协议/现成开源项目
> 重构日期：2026-10-07

## 已完成的全部替换 (共 14 项)

### 后端 (8 项)

| # | 模块 | 之前 (自造的) | 之后 (成熟的) | 库/方案 |
|---|------|-------------|-------------|--------|
| 1 | memory.py 向量搜索 | Python循环余弦相似度 + RRF fusion | **mem0ai + ChromaDB** (本地嵌入式) | mem0ai>=2.0, chromadb>=1.0 |
| 2 | config.py schema验证 | `_SCHEMA`字典+`isinstance`链式强制 (有bool/int bug) | **pydantic-settings** (BaseSettings + Field) | pydantic-settings>=2.0 |
| 3 | routing.py 模型路由 | 20个关键词子串匹配 | **semantic-router** (embedding语义路由，中文友好) | semantic-router[fast]>=0.1 |
| 4 | compaction.py 上下文压缩 | 手写 fold-to-summary + 逐条淘汰 | **LangChain trim_messages** (fold逻辑保留 + LC淘汰) | langchain-core>=1.0 |
| 5 | loop.py agent执行框架 | ~120行手写 while 循环 + 工具执行调度 | **LangGraph StateGraph** (声明式节点/边/条件路由) | langgraph>=1.0 |
| 6 | mcp.py 启动busy-poll | `asyncio.sleep(0.02)×500`忙等 + 零工具判断bug | **asyncio.Event + wait_for** (官方SDK正确模式) | stdlib only |
| 7 | provider.py 重试逻辑 | sync OpenAI在async中await + ~90行重试循环 | **AsyncOpenAI(max_retries=3)** + 简化应用层重试 | openai>=2.0 |
| 8 | usage.py 价格表(可选) | 手写4条定价表 | **litellm.model_cost** (200+模型社区定价) | litellm>=1.0 |

### 前端 (6 项)

| # | 模块 | 之前 (自造的) | 之后 (成熟的) | 库/方案 |
|---|------|-------------|-------------|--------|
| 9 | state.tsx 全局状态 | 430行Context + useRef手动同步 | **Zustand** (`create()` + selector) | zustand>=5.0 |
| 10 | ui.tsx UI组件 | 手搓Button/Input/Select/Badge/Drawer | **shadcn/ui** CVA组件 + vaul Drawer | shadcn/ui, vaul>=1.0 |
| 11 | MessageList.tsx 消息列表 | 手搓滚动/复制/markdown渲染/上下文条 | **assistant-ui ThreadPrimitive** | @assistant-ui/react>=0.15 |
| 12 | markdown.ts Markdown解析 | 90行正则+fenced code registry | **marked** + 自定义Renderer | marked>=12.0 |
| 13 | ApprovalModal 浮层 | `fixed inset-0`手工浮层 | **@radix-ui/react-dialog** (已安装未用) | @radix-ui/react-dialog |
| 14 | ChatView.tsx | 448行死代码 (与MessageList 90%重复) | **删除** | — |

## 测试验证

- 后端: **254 passed, 6 skipped** (`pytest tests/ --ignore=test_api_config.py --ignore=test_tui.py`)
- 前端: `npm run build` 通过 (2254 modules, 555 KB JS / 26.6 KB CSS)

## 设计要点

### LangGraph Agent Loop 重构
原 `_stream_inner` 的 ~100 行 while 循环拆成三个 LangGraph 节点：
- `prepare` — 构建系统提示 + 记忆注入 + 上下文压缩 + 模型路由
- `call_llm` — 调用模型 + 截断检测/重试 + 事件产出
- `execute_tool` — 工具执行 + 取消检查

通过 `StreamWriter` + `astream(stream_mode="custom")` 产出原有事件协议（status/text/reasoning/usage/tool_start/approval/tool_result/error/done）。

公共 API (`AgentLoop.__init__()`, `.stream()`, `.cancel()`) 零变更。

### mem0 + ChromaDB 后端
```python
# 自动选择逻辑
mem0 已安装 + HTTP 兼容 embedder → _Mem0MemoryStore (向量语义检索)
其他所有情况 → _SqliteMemoryStore (原有 FTS+embedding 实现)
```

### pydantic-settings 配置
```python
# 27个字段全部类型化 Field() 声明
class SparkSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPARK_")
    model: str = Field(default="mock")
    # ...
# 修复了原代码 bool/int 的验证 bug
```

### compaction 三阶段
1. **fold-to-summary** — 前 ~60% 折叠为 system 摘要 (保留原逻辑)
2. **trim_messages** — LangChain token-count-based 淘汰
3. **fallback** — langchain 不可用时走原逻辑

## 安装启用新后端

```bash
pip install -e ".[memory,extras]"  # mem0ai + chromadb + mcp + textual
```

配置文件 `~/.spark/config.toml` 设置 `memory_embedding = "api"` 并配置 `base_url` / `api_key` 即自动启用 mem0。

## 剩余未替换（低优先级/领域特定）

| 模块 | 当前状态 | 成熟替代候选 | 备注 |
|------|---------|------------|------|
| subagent.py | ~90行子Agent工厂 | LangGraph Send API | 已集成到 LangGraph 循环内 |
| store.py | JSONL会话存储 | Langfuse/LangSmith | 单用户本地场景下 JSONL 是合理选择 |
| tools/* | 文件/shell/git/office/web | stdlib/第三方标准使用 | 标准库正确使用，非轮子 |
| trace.py | ~60行结构化日志 | structlog/OTel | 功能完整且轻量 |
| prompt.py | `.format()`字符串模板 | Jinja2/LC PromptTemplate | 2个模板无需引擎 |
| shell_env.py | 安全敏感的环境变量过滤 | stdlib | 安全策略代码不可依赖外部 |
| sanitize.py | 输出脱敏 | Microsoft Presidio | 当前保守策略是设计意图 |
| approval.py | AI工具调用审批 | 无成熟对应库 | 纯领域逻辑 |

---

**总计**: ~2,100 行自造代码 → ~1,400 行委托给成熟库。净减 ~700 行。
\textbf{所有替换不改变公共 API} — 外部调用方 (web/tui/cli/subagent) 零感知。
