# 开发指南

## 环境要求

- Python 3.11 或更高
- 可写的 `~/.spark/`（会话库与默认配置）
- 至少一个模型后端：OpenAI 兼容 HTTP 接口，或本机 Ollama
- 可选：Node.js（仅当使用 `npx` 拉起 MCP 服务器）

对照阅读官方循环时，可看 `当前工作区/codex`，修改 Spark 源码时不要改这个目录。

## 快速开始

```bash
# 创建虚拟环境并安装
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 配置密钥（用户自己的 Key，不要使用平台环境里的大模型 Key）
export SPARK_API_KEY=your-api-key-here

# 复制示例配置到用户目录
spark  # 首次启动会生成 ~/.spark/config.toml 模板
```

`.env.example` 仅包含占位符：

```env
SPARK_API_KEY=your-api-key-here
SPARK_BASE_URL=https://api.deepseek.com/v1
SPARK_MODEL=deepseek-chat
```

## 项目结构说明

实现代码在 `当前工作区/src/spark/`。规划文档在 `当前工作区/.monkeycode/docs/`。功能规格在 `当前工作区/.monkeycode/specs/2026-09-21-spark-coding-agent/`。

模块边界：

- `core/loop.py` 只编排，不直接打 HTTP、不直接跑 subprocess
- `providers/` 只负责模型 IO
- `tools/` 只负责副作用
- `policy/` 是 Loop 调用工具前的唯一闸门
- `tui/` 只消费 Loop 事件，不内嵌业务规则

## 开发规范

- 命名：模块与函数 snake_case；TUI 组件 PascalCase
- 类型：公开函数标注类型；配置与工具参数用 pydantic 模型
- 异步：Loop、Provider、TUI 使用 asyncio；同步工具在 `asyncio.to_thread` 中执行
- 日志：`logging` 写 `~/.spark/spark.log`；TUI 不刷 stdout
- 密钥：代码读取 `SPARK_API_KEY` 等用户侧变量；禁止扫描或写入平台 `MCAI_LLM_*`
- 提交信息：`feat:` / `fix:` / `docs:` / `test:` / `chore:` 前缀
- 注释：默认不写；只在非显然的「为什么」处用英文短句

## 常见任务

跑单测：

```bash
pytest
```

只跑循环相关：

```bash
pytest tests/test_loop.py
```

本地接 mock 模型（无真实 Key）：

```bash
spark --provider mock --approval full-auto "list files in this repo"
```

第一版提供 `mock` Provider：按脚本返回固定 tool_calls，用于演示循环与 TUI，不发起网络请求。

## 构建与发布

第一版以本地可编辑安装为主：

```bash
pip install -e .
```

打包：

```bash
python -m build
```

发布渠道待产品稳定后再定（PyPI 或单文件 zipapp）。当前目标是仓库内 `spark` 命令可在工作区启动 TUI。

## 测试策略

- Loop：用 mock Provider 断言「tool → 结果回填 → 最终消息」的轮次与终止条件
- Policy：三种模式下对 read/write/shell 的放行与拦截表
- Tools：临时目录夹具，断言越界路径失败、patch 多匹配失败、shell 超时
- Store：创建会话、追加、resume 后消息顺序一致
- TUI：对审批组件做 Textual 的 `pilot` 交互测试（有限覆盖）

## 实现分期

1. Mock Provider + Loop + 内置工具 + Suggest 审批 + 最小 TUI（循环可演示）
2. OpenAI 兼容与 Ollama Provider、SQLite 会话、`AGENTS.md`
3. MCP stdio 客户端与 `spark exec`
4. 打磨截断、日志、配置模板、README
