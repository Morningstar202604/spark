# AGENTS.md — 项目级指令（可编辑）

> 此文件由 Spark 自动加载（三级：`~/.agents/` → 项目根 → 子目录），用于告诉 AI 助手本项目的约定和偏好。
> 你可以自由修改此文件，Spark 每次启动都会重新读取。

## 项目概述

[在此填写项目是做什么的、技术栈、核心目标]

## 编码规范

- 语言：[Python / TypeScript / ...]
- 风格：[PEP 8 / ESLint / ...]
- 缩进：[4 空格 / 2 空格]
- 行宽：[88 / 100 / 120]
- 命名：[snake_case / camelCase / ...]

## 提交规范

使用 Conventional Commits：
```
<type>(<scope>): <subject>
```
type: feat / fix / docs / style / refactor / perf / test / chore / ci / build

## 测试要求

- 新功能必须附带测试
- 修复 bug 必须先写复现测试
- 提交前跑全量测试：`pytest tests/`（或项目对应命令）

## 安全约定

- 不硬编码密钥、密码、token
- 密钥从环境变量读取
- 用户输入必须校验
- 不提交 `.env`、密钥文件

## AI 助手偏好

- 优先用项目已有的工具和模式，不要重复造轮子
- 改代码前先读相关文件理解上下文
- 小步提交，每个提交只做一件事
- 不确定时先问，不要猜
- [在此添加你的其他偏好]

## 常用命令

```bash
# 安装依赖
uv sync

# 跑测试
python3 -m pytest tests/

# 启动开发服务
spark web --port 8000

# 构建前端
cd web && npm run build
```
