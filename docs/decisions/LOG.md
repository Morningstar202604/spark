# Decision Log

| date | actor | decision | rationale | basis | supersedes |
|---|---|---|---|---|---|
2026-09-23 | agent | 新增 BLE001/S110 防御性吞错保持现状不改 logging | 与仓库既有同风格，改动扩大 diff 且无统一 logger 约定 | commit:b3d61f2 | -
2026-09-23 | agent | 本批不 commit，保留约33文件工作区改动给用户审阅 | 用户未明确要求 commit，按纪律不主动提交 | commit:b3d61f2 | -
2026-09-23 | agent | 亮色主题完整化：新增 spark-side/code 变量、侧栏与代码块去硬编码、Logo/hljs/徽标主题化 | 用户反馈左侧栏仍黑且要求主题灵活，全量替换硬编码色后 build+pytest 验证 | worktree:theme-full | -
2026-09-23 | agent | 删除 spark 深度审计链（临时产物+AQG phase-state），后续不再追该审计 | 用户明确表示不需要该审计链条 | user-2026-09-23 | -
2026-09-23 | agent | 工作区改动仅本地 commit，不 push origin | 用户要求提交到本地、不进远程仓库 | user-2026-09-23 | commit:b3d61f2
2026-09-23 | agent | 新增长任务可靠性护栏：重复调用熔断 + 单轮 token 预算 + 子代理取消传播 | 对标 Claude Code/Codex/OpenHands 的长任务止损做法，防止烧钱与失控 | worktree:reliability | -
2026-09-23 | agent | 新增 run_tests 工具做测试闭环、worktree_create/list/remove 做分支隔离、hooks 生命周期扩展点 | 对标 Aider auto-test、Cline worktree、Claude hooks | worktree:reliability | -
2026-09-24 | agent | 护栏参数与 hooks 暴露到 Web 设置：Agent 页加熔断/预算输入，Security 页加 hooks 编辑器；/api/config 与 /api/settings 双向支持 | 用户要求在 UI 直接调节，无需手改 toml | worktree:settings-ui | -
2026-09-24 | agent | 设置 API 测试改为 monkeypatch save_config，禁止测试写用户真实 ~/.spark/config.toml | 端到端测试曾把真实配置覆盖为 mock provider 并清空密钥 | tests/test_settings_api.py:70 | -
