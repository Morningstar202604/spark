# 竞品功能对比：spark vs Qoder / ZCode / opencode / Codex CLI

> 调研日期：2026-10-14。来源：各产品官方文档站、官方仓库、变更日志（逐条核查，见文末来源）。
> 目的：给出 spark 与主流 AI 编程 agent 的功能差距矩阵，服务于后续路线规划。
> 标注：✅ 已有 ｜ ⚠️ 部分/不完整 ｜ ❌ 缺失。

## 0. 对象与形态总览

| 产品 | 开发商 | 形态 | 开源 | 模型策略 |
|---|---|---|---|---|
| **spark** | badhope（本仓库） | TUI + Web + Desktop(EXE) + CLI + `mcp-serve` | MIT | BYO（OpenAI 兼容 / Ollama / mock） |
| **Qoder** | 阿里 | VS Code 系 IDE + 独立桌面 App + CLI + Web + Mobile + JetBrains 插件 + Cloud Agents | 闭源 | 订阅 Credits；内置 Qwen/DeepSeek/GLM/Kimi 等 + BYOK |
| **ZCode** | 智谱 Z.ai | Desktop(Electron) + Web + CLI/TUI + 手机遥控 + 飞书/微信 Bot | Apache-2.0 | GLM 系为主 + 第三方 provider |
| **opencode** | sst | TUI + Web + 桌面 App + ACP(编辑器协议) | 开源 | models.dev 目录，任意 provider |
| **Codex CLI** | OpenAI | CLI/TUI + IDE 扩展 + 桌面 App + Cloud | 开源（客户端） | ChatGPT 订阅 / API key / Bedrock / Ollama |

## 1. 安装与分发

| 功能点 | spark | Qoder | ZCode | opencode | codex |
|---|---|---|---|---|---|
| 一键安装脚本 | ❌（clone+venv 或 EXE） | 安装器 | dmg/exe/deb 包 | `curl … \| bash` | `curl … \| sh`（Win: irm \| iex） |
| 包管理器 | ❌ | 安装器 | ❌ | brew/npm/choco/scoop/pacman… | npm/brew/Releases |
| 自动更新 | ❌ | 内置 | 内置 | ✅ auto/notify 三态 | ✅ check_for_update_on_startup |
| 卸载命令 | ❌ | 安装器 | — | ✅ `opencode uninstall` | — |

**spark 缺口**：发布渠道与自动更新。建议 P1（EXE 用户体感关键）。

## 2. 认证 / 模型配置

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 登录方式 | 配置文件 + 环境变量 + `spark init` 向导 | Qoder/ZCode：账号登录（OAuth）；codex：Sign in with ChatGPT |
| 多 provider 预设 | ✅ 8 家（DeepSeek/通义/智谱/Kimi/豆包/Agnes/Ollama/自定义） | Qoder 预设 9+；opencode models.dev 全目录 |
| 多模型档案切换 | ✅ `[[model_profiles]]` + Web 面板 | codex `--profile`；opencode agent 级 model |
| 热键循环最近模型 | ❌ | opencode `F2/Shift+F2`；codex `/model` 弹窗 |
| 推理档（effort/variant） | ✅ effort 低/中/高（Web + 配置） | opencode `ctrl+t` variant 循环；codex `/model` 带 reasoning 档 |
| 连通性自检 | ✅ `spark test` + `doctor --probe` | 都有对应（`/status`、doctor） |
| 小模型分工（标题/压缩） | ❌ | opencode `small_model`；codex `review_model` |

## 3. 交互模式

| 功能点 | spark | 竞品代表 |
|---|---|---|
| TUI 快捷指令 | ⚠️ 仅 `/test`（其余透传模型） | codex 60+ slash；opencode 全量 slash（`/init /undo /share /compact /export /models /sessions /themes …`） |
| 自定义斜杠命令 | ❌ | opencode markdown 命令（`$ARGUMENTS`/`!shell` 模板）；codex 经 hooks/skills |
| 内联 shell（`!cmd`） | ❌ | opencode ✅ |
| `/editor` 外部编辑器写 prompt | ❌ | opencode / codex ✅ |
| 消息回溯编辑 | ❌ | codex **Esc Esc** 回溯改 prompt 分支重跑；opencode timeline 回填 |
| Vim 模式 | ❌ | codex `/vim` |
| 非交互 exec | ✅ `spark exec`（stdout 纯文本） | codex `exec --json/--output-schema/--ephemeral`；opencode `run --format` |
| 代码审查命令 | ❌（`/test` 只跑测试） | codex `codex review --uncommitted/--base/--commit` |
| 计划模式 | ✅ `plan-only` / `approve-plan` 两档 | codex `/plan`；opencode plan agent；Qoder Plan-driven；ZCode `/plan` |
| Goal 长程模式 | ❌ | ZCode `/goal`（证据门槛 + 迭代验证）；Qoder `/goal` |

## 4. 会话管理

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 新建/恢复/切换 | ✅ TUI ctrl+r、Web 侧栏、`spark resume` | 全都有 |
| Fork | ⚠️ 存储层有 `store.fork()`，无 UI | codex `/fork`；opencode `--fork`；ZCode Edit History 分支 |
| 分享链接 | ❌ | opencode `/share` → `opncd.ai/s/<id>` |
| 导出/导入 | ❌ | opencode `/export`（md/JSON）；codex `/export`；codex `/import` 从 Claude Code 迁移 |
| 归档 | ✅ Web 删除 | codex `/archive`；ZCode 自动归档候选（窗口可配） |
| 重命名 | ✅ 自动标题 | codex `/rename`；opencode ctrl+r |
| 会话内 token 用量 | ✅ ContextMeter | codex `/status`；ZCode Usage Stats 面板 |
| 消息级撤销/回滚 | ⚠️ 存储层支持，UI 未接 | ZCode Undo/Reapply（全有或全无）；Qoder `/rewind`（会话/文件分别恢复）；codex `thread/revert` |
| 侧聊/旁支 | ❌ | codex `/side`(`/btw`)；ZCode Side Conversation；Qoder Side task |

## 5. 上下文 / 记忆 / 知识

| 功能点 | spark | 竞品代表 |
|---|---|---|
| AGENTS.md | ✅ 项目级（可配文件名） | opencode 多级向上 + `~/.config` 全局；codex 逐级收集；ZCode 两源（全局+workspace） |
| 全局规则文件 | ❌ | opencode `~/.config/opencode/AGENTS.md`；ZCode `~/.zcode/AGENTS.md` |
| Claude Code 兼容（CLAUDE.md/skills） | ⚠️ 声明式 subagent 读 `.claude/agents/*.md` | opencode `CLAUDE.md` 回退 + `.claude/skills`；ZCode 一键导入 + 预装 CC marketplace |
| `@` 文件引用补全 | ❌ | opencode `@` 模糊；codex `@` file-search；ZCode `@` 文件/文件夹 |
| 外部目录/仓库引用 | ❌ | opencode `references`（别名+description）；codex `--add-dir` |
| 图片输入 | ✅ Web 粘贴/拖拽 | opencode 自动缩放/5MB 上限；codex 剪贴板截图 + `view_image` |
| 自动压缩 | ✅ 85% 阈值 + 保留 8 条 | opencode 三态（auto/prune/reserved）；codex 双阈值 + pre/post_compact hooks |
| 压缩可编程干预 | ❌ | opencode 插件可换压缩提示词；codex hooks |
| 仓库地图/索引 | ✅ tree-sitter 符号地图（6000 字符） | opencode LSP 诊断；Qoder 云端向量索引（10 万文件）；ZCode Repo Wiki |
| Repo Wiki（生成式知识文档） | ❌ | Qoder（含 Citation 开关，50 credits/仓）；ZCode（产物本地、带 file:line 溯源、Mermaid 图） |
| 长期记忆 | ✅ 独立记忆库（embedding + auto_extract + Web 管理） | codex `/memories`（后台管线 + 合并）；ZCode Project Memory（默认关）；Qoder Auto-Memory |
| 记忆可视化管理 UI | ✅ Web 记忆面板 | Qoder `/memory manage`；ZCode 无管理页（手改文件） |

## 6. 工具集

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 读写/edit | ✅ 三种（整写/单处 SEARCH-REPLACE/aider 多块 + tree-sitter 语法门禁） | opencode edit/write/apply_patch；codex apply_patch（专用 crate + diff 审批） |
| shell | ✅ 沙箱策略 + 超时/输出上限 | codex unified exec（可后台）；opencode bash |
| 后台任务 | ✅ bg_start/output/kill/list（跨重启持久化） | codex `/ps` `/stop`；Qoder `/tasks` 面板 |
| grep/glob | ✅ | opencode ripgrep 尊重 .gitignore + `.ignore`；codex file-search |
| **LSP 集成** | ❌ | opencode 内建 40+ 语言 LSP（definition/references/hover/…） |
| 代码格式化 | ❌ | opencode 30+ formatter（写后自动，默认关） |
| web 搜索/抓取 | ✅ Bing 搜索 + HTML→text | codex 原生 web_search + browser_use/computer_use 配置 |
| 内置浏览器（agent 可驱动） | ❌ | ZCode built-in browser（点击/填表/截图/视口模拟/Chrome 数据导入）；Qoder Browser Agent |
| Notebook | ✅ read + edit（cell 级） | — |
| Git 工具 | ✅ status/diff/log/branch/add/commit/worktree + run_tests（测试回灌闭环） | 多数靠 bash；codex `/diff` + `codex apply`；`--worktree` |
| 任务即分支（绿测提交门控） | ✅ 已注册 `task_begin/task_step_commit/task_finish` | 无同类（独特能力） |
| 用户交互（提问工具） | ❌（仅审批弹窗） | opencode `question`；codex `request_user_input` |
| 技能（Skills） | ✅ `.spark/skills/**` + `load_skill` | ZCode 全生态（导入 CC/Codex 等 + 插件分发）；opencode 6 目录 search；codex bundled + 动态选择器 |
| 插件系统 | ⚠️ 仅 MCP 注册 | opencode npm 插件（可注册覆盖内建工具）；codex plugin marketplace；ZCode Public/Personal marketplace |
| 自定义斜杠命令 | ❌ | opencode markdown 命令；ZCode `~/.zcode/commands/*.md` |

## 7. 权限 / 沙箱 / 安全

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 审批档位 | ✅ 6 档（含 plan-only/approve-plan） | codex `-a on-request/never` + sandbox 双轴；opencode allow/ask/deny + `--auto`；Qoder 3 档；ZCode 4 档（Shift+Tab 循环） |
| 审批弹窗粒度 | ✅ 允许/始终允许/拒绝（相同签名复用） | codex：命令前缀 / 文件集合两种粒度；ZCode：Allow/Always/Reject/Always Reject + 5 分钟倒计时（权限问题不受限） |
| 规则引擎 | ✅ `[[permissions]]` 通配 + 最后匹配生效 | opencode 13 键 pattern→动作；codex execpolicy `.rules` + `--ignore-rules` |
| OS 级沙箱 | ⚠️ bwrap（Linux 可选）+ 纯策略 | codex 三平台（Seatbelt/Landlock/Windows 沙箱）+ 默认断网；Qoder/ZCode 为策略级 |
| 网络限制开关 | ❌ | codex `sandbox_workspace_write.network_access` |
| AI 审 AI（自动审查） | ❌ | codex Guardian（自动审查 + `/approve` 放行 + OTLP 审计） |
| 输出脱敏 | ✅ 三层（密钥/主目录/堆栈） | — |
| 第三方输出防注入 | ✅ untrusted 围栏 | 无同等显式机制（ZCode 浏览器"页面文本不作指令"） |
| Web 控制面保护 | ✅ loopback 默认 + X-Spark-Token（非本机启动时打印） | opencode `OPENCODE_SERVER_PASSWORD`；ZCode `--token` |

## 8. 多 Agent / 协作

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 子代理 | ✅ `task`（最多 4 并行）+ 声明式 `.claude/agents` | codex spawn_agent（并发/深度限制/消息板）；opencode general/explore/scout；ZCode subagents（frontmatter 超集）+ workflows |
| 子代理独立模型 | ❌ | ZCode subagent `model`/`thoughtLevel`；codex `default_subagent_model` |
| 专业团队编排 | ❌ | Qoder Experts Mode（Lead + 6 专家 + Team Canvas）；ZCode Dynamic Workflows（JS 编排） |
| 主 agent 切换 | ❌（单 agent） | opencode Tab 切 build/plan；codex `/agents` 指挥中心 |
| 子会话导航 | ❌ | opencode 子会话树（leader+方向键）；codex `/subagents` |

## 9. 回滚 / 检查点

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 快照粒度 | ✅ 每轮（涉文件/命令后）tar.gz + baseline 增量 | opencode 基于 git snapshot；codex rollout + git apply |
| 回滚范围 | ✅ 文件 + 对话截断 | opencode `/undo` `/redo`（可多次）；ZCode 全有或全无 + Reapply；Qoder 会话/文件分别 |
| 非 git 目录回滚 | ✅ | — |

## 10. 自动化 / 通知 / 更新 / 其它

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 定时任务 | ❌ | ZCode Automations（模板/聊天创建 + 四种频率 + skipped 语义）；Qoder `/crontab` |
| 空闲算力队列 | ❌ | ZCode Idle-time task（免费跑、超时回队列续跑）—— 独特 |
| 桌面通知/声音 | ❌（仅页内 Toast） | opencode attention 声音包；codex notifications（osc9/bel）+ `/warnings` |
| 主题 | ⚠️ Web light/dark/system；TUI 固定 | opencode 10+ 主题 + 自定义 JSON；codex `/theme` + `$CODEX_HOME/themes` |
| 快捷键重绑 | ❌ | codex `/keymap` 交互重绑；ZCode 自定义键位 |
| 状态栏定制 | ⚠️ TUI 单行 | codex `tui.status_line` 有序项；ZCode 命令中心 |
| 通知/远程 | ❌ | ZCode 手机 Remote Control + 飞书/微信 Bot；Qoder Mobile |
| IDE 集成 | ❌ | opencode 自动装 VS Code 扩展 + ACP（Zed/JetBrains/neovim）；codex `/ide`；Qoder 本尊是 IDE |
| Git PR 工作流 | ❌ | opencode `pr <num>` + GitHub Action（评论触发）；codex Cloud PR review |
| 遥测 | ⚠️ otel.py（仅 provider span） | codex 全量 OTLP（log/trace/metrics + 审计导出） |
| 多语言 UI | ⚠️ i18n 词典齐全但仅少量组件接入（见待办） | ZCode/Qoder 多语言完整 |

## 11. spark 独有 / 领先项

1. **四档沙箱 × 六档审批正交组合**——`plan-only`/`approve-plan` 作为独立审批档位是差异化设计。
2. **tar.gz 检查点**：不依赖 git、含 manifest 校验、非 git 目录可回滚。
3. **任务即分支**（`task_begin/step_commit/finish`）：绿测门控自动提交、失败自动废弃——竞品无同类一等功能。
4. **MCP 注册表搜索**：内置中国可用性评估 + Glama 评分 + 离线数据集兜底。
5. **`spark mcp-serve`**：把自己反向暴露为 MCP server。
6. **`/test` 失败回灌闭环**：traceback 自动作为 user 消息让 agent 自修。
7. **完全本地数据面**：无任何云依赖可全功能运行（含离线 mock）。
8. **tree-sitter 语法门禁**：写文件前 AST 检查（语法错误拒写 + 符号删除警告），竞品未见同款内建。

## 12. 差距清单（建议优先级）

**P0（低成本高价值，已有基础设施只差接线/UI）**
- Fork 的 UI/API 暴露（`store.fork()` 已有）
- 消息级 Undo（`truncate_messages/delete_message` 已有）+ 按轮"文件+消息"一并还原
- `/export` 会话导出（markdown，`sessions.db` 全字段已有）
- `@` 文件引用补全（前端 Composer + `/api/fs/tree` 已有）
- effort 全链路（本轮已修 ✅）
- system_prompt 全链路（本轮已修 ✅）

**P1（中成本，对齐业界预期）**
- 自定义斜杠命令（markdown 目录扫描 + `$ARGUMENTS` 模板）
- 桌面通知（web Notification API / TUI 成功失败提示音可关）
- codex 风格 `exec --json`（当前 stdout 纯文本，CI 集成弱）
- LSP 只读工具（definition/references——接 pylsp/typescript-language-server 子进程）
- 会话分享（本地导出为静态 HTML 单文件即可，无需服务端）
- 快捷键可配置 + 主题扩展

**P2（高成本 / 需要产品决策）**
- Repo Wiki（可复用现有 tree-sitter + LLM 摘要，产物落本地）
- 定时 Automations（复用 bg_jobs 持久化机制 + 轻量 scheduler）
- IDE/ACP 集成（opencode 的 ACP 协议有开源实现可参考）
- 插件市场（先做"本地目录 + git URL 安装"最小版）
- 多 agent 专业编排（Experts/Workflows 级）

**不建议做**：云端沙箱/账号体系/移动 App（与本地自托管定位冲突）；Tab 补全/NES（那是 IDE 形态的功能，spark 无编辑器本体）。

## 13. 来源

- opencode：opencode.ai/docs（config/tui/keybinds/permissions/plugins/skills/mcp/acp/…），github.com/sst/opencode
- Codex CLI：github.com/openai/codex（README、cli/src、config 源码、release notes 158–162）
- Qoder：docs.qoder.com（qoder/*、cli/*、extensions/*、account/*、release-notes/*）
- ZCode：zcode.z.ai/en/docs/*、zcode.z.ai/en/changelog、github.com/zai-org/ZCode（README、apps/zcode-cli、docs/ 设计文档）
- spark：本仓库源码与 docs/
