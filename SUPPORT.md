# 支持

- **快速开始**：`pip install -e ".[dev]"` 后运行 `spark web`（详见 [README](README.md)）
- **文档**：`docs/` — [安装](docs/INSTALL.md) / [配置](docs/CONFIG.md) / [API](docs/API.md) / [架构](docs/ARCHITECTURE.md)
- **问题反馈**：使用本仓库的 Issues 页（附 bug_report 模板）
- **功能建议**：使用 feature_request 模板

## 常见问题

- **模型连接失败** → 在设置面板点「测试连接」；检查 `base_url` / `model` 是否与提供商一致，
  自定义模型名不需要加 `openai/` 前缀（Spark 已自动按 OpenAI 兼容处理）
- **需要走代理** → 设置 `HTTPS_PROXY` 环境变量（Spark 未内置 `proxy` 配置项）
- **没填 API Key** → 会自动进入演示模式（mock）；在 Web 设置中填入密钥后立即切换真实模型，无需重启
- **端口被占用** → 省略 `--port`，Spark 会自动从 8000 起选择可用端口
- **记忆"只在当前目录生效"** → 记忆按工作目录隔离，属设计如此
- **想换模型** → 设置面板「模型档案」可保存多个档案并一键切换；`spark test` 可做连通性探活
- **沙箱拦住了我要做的事** → 设置 → 权限 → 访问级别。`完全访问` 允许操作工作目录外的路径；
  `无限制` 会关闭全部拦截（含 Spark 自身代码保护），请谨慎

## 诊断

```bash
spark doctor                  # 配置与环境体检
spark version                 # 版本
spark config show             # 当前生效配置（密钥已打码）
```
