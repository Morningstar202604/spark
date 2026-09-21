# Spark Agent — 贡献指南

感谢你对 Spark 的贡献！

## 开发流程

1. Fork 本仓库并克隆你的副本
2. 创建分支：`feat/xxx` 或 `fix/xxx`（命名：`YYMMDD-类型-简述`）
3. 提交前保证：

   ```bash
   python3 -m pytest          # 全量测试通过
   cd web && npx tsc -b       # 前端类型零错误
   ```

4. 推送分支并发起 Merge Request（GitCode），MR 描述写清楚：动机、改动点、验证方式

## 代码约定

- 后端：Python 3.11+，仅依赖 httpx / pydantic / textual / typer；Web 服务使用 stdlib `http.server`，不引入 Web 框架
- 前端：React 19 + Vite + TypeScript + Tailwind 4；组件放在 `web/src/components/`，类型集中在 `web/src/types.ts`
- 新工具实现参考 `src/spark/tools/registry.py` 现有 schema 模式，并在 `policy.py` 中归类（只读 / shell 类）
- 新增 API 端点在 `src/spark/web/server.py` 注册；SSE 事件协议见 `docs/ARCHITECTURE.md`
- 提交信息使用 conventional commits（`feat:` / `fix:` / `docs:` / `test:`）

## 安全要求

- 不要在代码或文档中硬编码任何 API Key
- 涉及沙箱与审批的改动必须补对应 `tests/` 用例
