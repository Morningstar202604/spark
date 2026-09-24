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
2026-09-24 | agent | 从 opencode.json 的 agnes-api provider 恢复密钥与 base_url 到 spark 配置 | 测试覆盖真实配置导致密钥丢失，opencode 配置是同一密钥来源 | user-2026-09-24 | -
2026-09-24 | agent | 新增 spark init/doctor/config/version 四个面向新手的命令，错误提示改为可操作指引 | 用户要求"面对普通人友好"；原本缺密钥只报环境变量名，新人无从下手 | worktree:onboarding | -
2026-09-24 | agent | TUI 增加欢迎面板、空状态任务示例、? 帮助浮层、密钥状态指示、中文审批与错误提示 | TUI 原本只有一个输入框和 Test model 按钮，无引导 | worktree:onboarding | -
2026-09-24 | agent | 品牌升级为 "Spark · Ember"：暖炭黑 + 余烬橙 + 骨白，标志改为裂口方块内火花核（弃通用闪电） | 用户要求打造独特品牌并全面使用；原青绿渐变与市面 AI 产品高度同质 | user-2026-09-24 | -
2026-09-24 | agent | 安全加固：Web 默认仅 127.0.0.1 + 非本机需令牌、run_tests 去 shell=True、hooks 去 cmd.exe 包装、项目配置禁注入 MCP/hooks/provider、agents_md 限裸文件名、/api/test 禁跨域发密钥、checkpoint 改 UUID 且回滚删新增文件、熔断窗口随阈值增长、配置加数值边界 | 全队对抗性压测发现 3 个 Critical + 多个 High | worktree:hardening | -
2026-09-24 | agent | 第二批：search/glob 加文件/字节/时间预算、SQLite 开 WAL+组合索引+合并事务、记忆聚类改倒排索引去 O(M²)、审批按 (工具+参数) 签名授权且 shell 禁止永久放行、web_fetch 拦截内网与重定向、read_file/web_fetch 加体积上限、TUI 关 markup 加行数上限与审批 Esc、Web 加焦点环/侧栏 inert/审批播报 | 性能与无障碍审查发现的高优先项 | worktree:perf-a11y | -
2026-09-24 | agent | 第三批（五路并行）：SSE 文本合并+15s 心跳+队列上限+断连即取消+静态资源 immutable 缓存；_usage() 增量缓存（1M 字符 97.9ms→0.25ms）；checkpoint 基线+delta 增量（10k 文件 15.3s→0.33s）；记忆 CAS 版本校验+单事务；Web 分包（504KB→首屏 464KB，零 >500KB 警告）；TUI 窄终端不溢出；shell 有界输出+进程树清理 | 收尾上一批遗留项 | worktree:final-hardening | -
