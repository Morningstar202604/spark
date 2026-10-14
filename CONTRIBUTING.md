# Spark Agent — 贡献指南

感谢你对 Spark 的贡献！

## 开发流程

1. Fork 本仓库并克隆你的副本
2. 创建分支：`feat/xxx` 或 `fix/xxx`（命名：`YYMMDD-类型-简述`）
3. 提交前保证：

   ```bash
   python3 -m pytest                     # 全量测试通过
   ruff check src/spark tests            # 静态检查零错误
   mypy src/spark                        # 类型检查零错误
   cd web && npx tsc --noEmit            # 前端类型零错误
   ```

4. 推送分支并发起 Pull Request / Merge Request（按你使用的平台），描述写清楚：
   动机、改动点、验证方式

## 代码约定

- 后端：Python 3.11+；核心依赖见 `pyproject.toml`（LiteLLM 统一模型调用、LangGraph、
  LlamaIndex 压缩、ChromaDB 向量记忆、MCP SDK、Textual TUI、Typer CLI）；
  Web 服务使用 stdlib `http.server`，**不引入 Web 框架**
- 前端：React + Vite + TypeScript + Tailwind；组件放在 `web/src/components/`，
  类型集中在 `web/src/types.ts`；界面文案必须走 `web/src/i18n.ts`（中英双语）
- 新工具实现参考 `src/spark/tools/registry.py` 现有 schema 模式，并在 `policy.py` 中归类
  （只读 / 写入 / 执行 / shell）
- 新增 API 端点在 `src/spark/web/routes.py` 注册（handler 放 `_handlers.py`）；
  SSE 事件协议见 `docs/API.md`
- 提交信息使用 conventional commits（`feat:` / `fix:` / `docs:` / `test:`）

## 安全要求

- 不要在代码或文档中硬编码任何 API Key
- 涉及沙箱与审批的改动必须补对应 `tests/` 用例
- 提交前跑 `pytest` 与 `ruff`：CI（`.github/workflows/ci.yml`）会以相同命令校验

