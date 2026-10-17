> 🌐 [English](README.md) | [中文](README_zh.md)

# Spark Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen.svg)](https://www.python.org/)
[![Node 18+](https://img.shields.io/badge/node-18%2B-339933.svg)](https://nodejs.org/)

Spark 是一个**本地自托管**的 AI 编程 Agent，代码在你手，密钥在你手。

**一句话介绍：** 把 Claude Code 的能力装进你的本地环境，零隐私泄露，完全可控。

## 核心价值

🔒 **隐私至上** — 所有代码、对话、记忆全部存本地，不经过任何第三方服务器
⚡ **即开即用** — 四种形态：Web UI、原生桌面窗口、终端 TUI、脚本化 CLI
🧠 **长程记忆** — 向量记忆库，记住你的项目偏好、命名规范、工作流习惯
🔬 **四档沙箱** — 从只读分析到完全开放，按风险灵活调整
⏮️ **检查点回滚** — 工作区快照 + 对话恢复，像 Git 一样回到任意时刻

## 界面预览

### 对话界面 · 真实模型驱动

> SSE 流式渲染、Markdown 代码块、工具调用卡片（可展开）、上下文用量计量。
> 图为真实使用：模型读取 `pyproject.toml` 后总结项目——工具调用 `read_file` 显示 ✓ 完成。

![对话界面](assets/screenshots/01-chat.png)

### 英文界面 · 一键切换

> 内置中英文双语（设置 → 展示 → 语言）。图为英文界面下模型连续调用 `read_file` + `glob`
> 两个工具完成仓库调研——两次工具调用均显示 Completed。

![英文界面](assets/screenshots/00-chat-en.png)

### 设置 · 模型与多档案

> 8 家内置预设（DeepSeek / 通义 / 智谱 / Kimi / 豆包 / Agnes / Ollama / 自定义），
> 支持多模型档案保存与一键切换，`测试连接` 探活，推理强度（effort）三档可调。

![设置 · 模型](assets/screenshots/02-settings-model.png)

### 设置 · 权限与沙箱

> 四档访问级别：仅沙箱内访问 / 局部访问（推荐）/ 完全访问 / 无限制，
> 每档都有明确的行为说明；受保护路径清单可自定义，默认保护 `/etc`、`/root/.spark` 与 Spark 自身代码。

![设置 · 权限](assets/screenshots/03-settings-permissions.png)

### 设置 · 展示与定制

> 亮/暗/跟随系统三态主题；6 个前端展示开关（思考过程/工具调用/计划卡片/上下文用量/会话关键词/系统提示）；
> **系统提示词可完全自定义**（留空用内置默认）。

![设置 · 展示](assets/screenshots/04-settings-display.png)

### 文件查看器 · 语法高亮

> 工作目录内文件树浏览 + 带行号的语法高亮预览（highlight.js，含跨行 token 正确着色）、
> 文件大小显示、二进制与超长文件自动降级提示。

![文件查看器](assets/screenshots/05-file-viewer.png)

### Windows EXE · 免安装运行

> PyInstaller 单文件打包（`dist/spark.exe`），双击即用、无需 Python 环境。
> 图为 EXE 实例执行的完整链路：模型真实调用 `run_shell` 命令、取回输出 `42` 并正确汇报退出码。

![EXE 运行](assets/screenshots/06-exe-tool-call.png)

## 快速开始

```bash
git clone https://gitcode.com/badhope/spark        # 或 GitHub: https://github.com/badhope/spark
cd spark && python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
spark web --workdir /your/project
```

浏览器打开打印出来的地址即可开始。**没有 API Key 时自动进入演示模式（mock），
在 Web 设置里填入密钥后立即切换真实模型，无需重启。**

Windows 用户也可以直接构建免安装 EXE（Python 3.11+）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
dist\spark\spark.exe web                    # 或双击 dist\spark.exe
```

## 四种形态

| 形态 | 命令 | 适用场景 |
|------|------|----------|
| Web UI | `spark web` | 日常对话、文件查看、终端、执行图（默认自动选可用端口） |
| 桌面窗口 | `spark desktop` | 原生窗口（pywebview），固定本机绑定 |
| 终端 TUI | `spark` | 纯终端环境、SSH 远程 |
| CLI / 脚本 | `spark exec "任务"` | 自动化、CI、管道输出 |
| MCP Server | `spark mcp-serve` | 把自己的全部工具反向暴露给其他 MCP 客户端 |

## 特性一览

| 类别 | 功能 |
|------|------|
| 对话 | SSE 流式渲染、Markdown 代码块、图片附件上传、思考流展示 |
| 工具 | 读写文件、apply_edit（AST 语法门禁）、grep/glob 搜索、Shell、后台任务、Jupyter、Web 检索、Git 全套、worktree |
| 规划 | 多步骤任务计划（PlanCard 可视化）、子 Agent 并行委派（最多 4 个）、声明式子代理（`.claude/agents/*.md`） |
| 审批 | 六档审批模式（含 plan-only / approve-plan）、`[[permissions]]` 通配规则引擎、审批弹窗三键决策 |
| 权限 | 四档沙箱、受保护路径、凭据路径保护、命令 token 级检查、MCP 最小权限白名单 |
| 记忆 | AGENTS.md 注入、向量长期记忆（embedding + 可选 reranker）、自动抽取、Web 可视化管理 |
| 持久化 | 会话管理、检查点快照回滚（含非 git 目录）、配置热加载、token 用量统计 |
| 模型 | 任意 OpenAI 兼容端点、Ollama 本地模型、多档案切换、`spark test` 探活 |
| 扩展 | MCP 客户端（stdio/SSE/HTTP + OAuth）、MCP 注册表搜索（含国内可用性）、项目级技能 `.spark/skills/`、生命周期 hooks |
| 界面 | 中英双语、深浅主题、命令面板（Ctrl+K）、文件查看器、内置终端、网页阅读、执行图、检查点回滚面板 |

## 架构

![架构概览](docs/assets/architecture.svg)

详细架构说明 → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## 支持的平台

- Python 3.11+
- Node.js 18+（仅前端开发需要；发行包已内嵌构建好的前端）
- 任意 OpenAI 兼容 API（DeepSeek、OpenAI、Ollama、Azure、Agnes、云知声、商汤等）
- Windows / macOS / Linux（Windows 提供一键 EXE 构建）

## 文档

| 文档 | 说明 |
|------|------|
| [docs/INSTALL.md](docs/INSTALL.md) | 安装与打包（含 Windows EXE） |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 配置参考（config.toml / 环境变量 / API 一览） |
| [docs/API.md](docs/API.md) | HTTP API 与 SSE 事件协议 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构说明 |
| [docs/competitive-comparison.md](docs/competitive-comparison.md) | 与 Qoder / ZCode / opencode / Codex CLI 的功能对比 |
| [docs/diagrams-ops.md](docs/diagrams-ops.md) | 架构图 / 发布矩阵 / 路线图 / 密钥与流水线运维手册 |

## 社区

- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- 问题反馈：使用本仓库的 **Issues** 页

---

**License:** MIT · **作者:** badhope · **Built with ❤️ in Shenzhen**
