# Spark — AI Coding Agent

本地优先的 AI 编码助手，Python + TypeScript/Electron 双形态。

## 架构

- `src/spark/web/server.py` — HTTP 入口（路由拆分到 routes.py，状态在 state.py，SSE 在 sse.py）
- `src/spark/core/loop.py` — 经典 ReAct 循环（LangGraph 路径在 graph.py）
- `src/spark/tools/registry.py` — 22 个内建工具 + MCP 动态工具，dispatch table 模式
- `src/spark/tools/mcp_bridge.py` — 官方 MCP SDK (`mcp` 包)，支持 stdio/sse/streamable_http
- `src/spark/memory/store.py` — ChromaDB 向量持久化
- `src/spark/providers/openai_compat.py` — LiteLLM 统一 LLM 调用层，含指数退避重试
- `web/src/` — React + Zustand 前端

## 关键约定

1. **日志**：所有模块使用 `logging.getLogger(__name__)`，禁止 `print()`
2. **异常**：禁止 bare `except:` 或 `except Exception: pass`；关键路径至少 `logger.warning`
3. **类型**：使用 Pydantic v2 模型，消除 `type: ignore`（目前仅剩 4 处 Unix-only API）
4. **TOML**：配置序列化使用 `tomlkit`，不手搓字符串
5. **MCP**：使用官方 `mcp` SDK，不手写 JSON-RPC

## 验证命令

```bash
# Python
ruff check src/spark/                  # 零警告
pytest --deselect tests/test_p0_fixes.py::test_mcp_call_survives_loop_restart --ignore=tests/test_mcp.py  # 369 passed

# 前端
cd web && npx tsc --noEmit             # exit code 0
cd web && npx vite build               # 构建成功
```

## 工具开发

新增工具时：
1. 在 `tools/` 下实现工具函数（Pydantic Args 模型 + `ToolResult` 返回）
2. 在 `tools/registry.py` 的 `_build_dispatch_table()` 中注册
3. 添加对应的测试用例

## 安全

- 沙箱：`sandbox.py` 命令过滤 + `policy.py` 权限规则
- 速率限制：per-IP token bucket (`web/routes.py`)
- 审计日志：`core/loop.py` 中每个 tool call 记录 name+args+duration+ok
- 脱敏：`sanitize.py` 正则 + 密钥 tuple 双重过滤
