# 支持

- **快速开始**：`pip install -e ".[dev]" && spark web`
- **文档**：`docs/`（CONFIG / API / ARCHITECTURE）
- **问题**：GitHub / GitCode / Gitee 的 Issues（用模板提交）
- **功能建议**：feature_request 模板

## 常见问题

- 模型连接失败 → 检查 `api_key`、`base_url`，设置面板「测试连接」
- 需要代理 → 设置 `proxy` 字段（或 HTTPS_PROXY 环境变量）
- 想切备用模型 → 设置面板填 `fallback_model`
- 记忆只在当前目录生效 → 记忆按工作目录隔离，是设计如此

> AI生成
