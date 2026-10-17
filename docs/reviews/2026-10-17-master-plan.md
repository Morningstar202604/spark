# Spark 总体建设计划（Master Plan）

> 版本：v1.0 · 日期：2026-10-17 · 依据：`2026-10-17-audit-and-direction.md`（审查+两轮方向拍板）
> 范围：**内核、CLI、TUI、Web 前端、Web 服务端、Desktop EXE、文档体系、CI/CD、五平台分发** —— 全端覆盖，不止后端。
> 原则（已拍板）：生态优先不造轮子；好组件直接换；LangGraph 迁入；主仓 badhope、四仓纯镜像；pip 先行 + EXE 软件轨并行。

---

## 0. 阅读指南

| 你想看什么 | 去哪节 |
|---|---|
| 为什么这么拆模块 | §2 目标架构 / §3 拆包 |
| Web 端重复造轮子怎么解 | §4.3 前端分册 |
| 每一端（CLI/TUI/Web/EXE）各自的问题与方案 | §4 全端分册 |
| 协议怎么吃干净 | §5 |
| 十条差异化怎么落地 | §6 |
| 懒加载等优化策略 | §7 |
| CI/CD 与五平台镜像自动化 | §8 |
| 会出什么事、怎么兜底 | §9 风险登记册 |
| 什么时候做什么、做完算数 | §10 里程碑（DoD） |

---

## 1. 总原则（贯穿全部章节）

1. **生态优先**：凡有维护良好的标准/框架/组件，直接采用；自研只保留"座舱逻辑"（审批、检查点、记忆、模式）。
2. **座舱化（harness 化）**：Spark 统一审批/检查点/记忆/技能层，大脑（agent）与外壳（IDE/前端）均可插拔。
3. **可验证的本地优先**：隐私不喊口号，做成上下文合同（§6.1）与出网黑匣子（§6.2）。
4. **分数卡发布**：每个 release 附评测分数（§6.8），透明即营销。
5. **一条纪律**：新能力先问"协议/标准有没有"；再问"前端三端要不要同时有"；最后问"能不能懒加载"。

---

## 2. 目标架构

```
                       ┌────────── 分发层 ──────────┐
                       │ PyPI/uvx · EXE · 镜像安装脚本 │
                       └──────────────┬─────────────┘
┌──────────── 表面层（多端） ─────────────┐    │
│ CLI(typer+rich)  TUI(textual)  Web(React) │    │
│ Desktop(pywebview)  ACP server  IM 网关   │    │
└──────────────┬───────────────────────┘    │
        ┌──────┴─────── 服务层 ───────────────┐
        │ FastAPI + SSE/WebSocket（OpenAPI 自动化）│
        └──────┬───────────────────────────┘
┌──────────────┴─────────── 座舱层（自研，唯一保留的核心） ──────────────┐
│ AgentLoop(LangGraph) · 审批策略 · 检查点 · 压缩 · 记忆/技能 · 模式档案    │
└──────────────┬───────────────────────────────────────────────────┘
┌──────────────┴─────────── 协议层（全生态） ───────────────────────────┐
│ litellm(provider/fallback/凭据池) · MCP 全能力 · ACP · OTel · 模型目录  │
└────────────────────────────────────────────────────────────────────┘
```

依赖方向强制单向：分发 → 表面 → 服务 → 座舱 → 协议。**座舱层禁止 import 上层**（CI 用 import-linter 守护）。

---

## 3. 模块化拆包方案

### 3.1 包结构（monorepo + namespace packages，单仓多包）

```
packages/
  spark-core/        # loop(LangGraph)、context、compact、checkpoint、tokens、policy
  spark-providers/   # litellm 封装、fallback、凭据池、模型目录、usage 回填
  spark-tools/       # 33 内置工具 + registry + sandbox 后端矩阵
  spark-mcp/         # MCP 全能力 client/server + registry
  spark-server/      # FastAPI、SSE/WS、ACP server、IDE attach
  spark-interfaces/  # cli、tui、web（前端构建产物内嵌）、desktop 入口
plugins/             # 官方插件（audit-proxy、im-gateway、skill-market…）
```

### 3.2 迁移步骤（保持每步可发布）

1. 先按"导入边界"划 package（仍是单发布物），CI 加 import-linter 规则 → 2. 拆 pyproject（uv workspace）→ 3. 元包 `spark-agent` 依赖全部子包，老入口不变 → 4. 插件 API（entry-points，`PROTOCOL_VERSION` 常量，废弃周期一个 minor）。

### 3.3 插件体系（学 dsh，用 Python 生态实现，不引 Cordis）

- 插件 = 普通 Python 包 + entry-points 声明（`spark.tools` / `spark.skills` / `spark.interfaces` 三类钩子）
- **Creator mode**：对话内生成 skill/hook/插件 → 本地目录热加载 → `spark plugin publish` 走 GitHub topic + 内置目录页双轨
- 懒加载：插件元数据启动时读，代码首次调用才 import

---

## 4. 全端分册（每端：现状问题 → 方案 → 造轮子替换清单）

### 4.0 问题×端 复查矩阵（把审查发现映射到每一端）

| 问题 | 内核 | CLI | TUI | Web 前端 | Web 服务端 | EXE |
|---|---|---|---|---|---|---|
| 重复造轮子 | graph.py 双真相源 | rich/completion 缺 | 差异化渲染 | Markdown/Diff/虚拟列表/终端手搓 | HTTP 框架手搓 | 更新器无 |
| 性能 | 32k 上下文偏小 | 冷启动重依赖 | — | 首屏/长列表 | 线程模型 | 体积/启动 |
| 安全 | shell 黑名单 | — | — | CSP/XSS | _guard 简陋 | 签名无 |
| 可维护 | 40 文件平铺 | — | — | 组件无分层 | 路由散 | spec×2 |
| 文档 | ADR 缺 | help 文案 | — | — | API 无文档 | 安装文档 |

### 4.1 CLI 分册

- **问题**：无补全、无进度可视化、重依赖拖慢冷启动、缺 agent/IDE 切换入口。
- **方案**：typer 保留 + `rich`（状态面板/进度/表格）+ typer 内置 completion；`spark agent use <profile>`、`spark ide attach <target>`、`spark status`（对标 codex /status：模型/上下文/成本/沙箱状态）。
- **懒加载**（见 §7）：`uvx spark-agent --version` 冷启动预算 <400ms；chromadb/llama_index/langgraph/litellm 全部命令内延迟导入。
- **验收 DoD**：补全（bash/zsh/fish/pwsh）生成；--version <400ms；exec JSON 输出带 schema 版本号（脚本化消费者可依赖）。

### 4.2 TUI 分册

- **问题**：textual 已是生态件（保留）；缺主题化、移动审批、性能长会话。
- **方案**：textual 升级锁版 + 主题跟随 Web 主题令牌（同一 CSS 变量源生成）；审批推送走 IM 网关（§6.9）兜底"人不在电脑前"；大历史窗口按需加载（store 分页读取）。
- **验收 DoD**：1000+ 消息会话滚动不掉帧；主题与 Web 一致；审批可从手机完成（网关路径）。

### 4.3 Web 前端分册（重复造轮子重灾区 → 替换清单）

| 手搓实现 | 替换为（生态件） | 理由 |
|---|---|---|
| Markdown.tsx 自研渲染 | **streamdown**（Vercel，流式 Markdown 专用）+ rehype-sanitize | 流式渲染即插即用；防 XSS 注入 |
| 代码高亮 | **shiki**（懒加载 grammar，按需语言包） | 与 GitHub 同渲染质量；按需加载不打首屏 |
| DiffView.tsx | 保留壳，内核换 **diff** 库 + 词级高亮（或 react-diff-view） | 自研对齐 diff 算法是纯轮子 |
| VirtualMessageList | **@tanstack/react-virtual** | 成熟虚拟化，双向滚动锚定 |
| Terminal.tsx | **xterm.js** + WebSocket（后端 py-pty 桥） | 终端仿真是深坑，不自制 |
| api.ts 手写 fetch | **orval / openapi-typescript + openapi-fetch**（从 FastAPI OpenAPI 生成） | 类型安全端到端，接口变更即编译错 |
| 手写 Modal/Toast/Popover | **Radix UI** 原语（headless）+ 现主题令牌 | 可访问性(focus trap/aria)免费获得 |
| i18n.ts 自研 | 评估 **i18next**（若键管理已失控则换） | 命名空间/懒加载语言包 |
| CommandPalette | 保留（cmdk 库可选） | cmdk 即标准实现 |
| 状态管理 | 如未用 zustand 则迁（store/ 目录已有雏形） | 中间件/持久化/DevTools |
| 图表/仪表 | **recharts**（ContextMeter 用量图） | 按需 |
| E2E 测试 | **Playwright**（审批流/检查点回滚/E2E 三条主链路） | 回归保障 |

分层：`pages / features / components(ui 原语) / lib(api 生成物) / store`；Vite `manualChunks`（vendor/react/marked/shiki 分包）+ 路由级懒加载（若加多页面）。
**验收 DoD**：首屏 JS <300KB gzip；Lighthouse 性能/无障碍 ≥90；类型覆盖 api 层 100%（生成物）。

### 4.4 Web 服务端分册

- **问题**：ThreadingHTTPServer + 手写路由 + 线程 SSE（routes/_handlers/_httpbase ≈1800 行）难扩展、无 API 文档、_guard 简陋。
- **方案**：**FastAPI + uvicorn(+uvloop) + sse-starlette + websockets**（pty 终端）；SSE 事件模型不变（前端无感迁移）；OpenAPI schema 自动产出 → 驱动前端 codegen；鉴权保留本机 token，加 CSP/安全响应头中间件；多会话并发用 task-per-session（LangGraph checkpointer 保证可恢复）。
- **验收 DoD**：路由代码量 −70%；/docs 可用；并发 4 会话互不阻塞；pty WS 断线重连。

### 4.5 Desktop EXE 分册

- **问题**：双 spec 无自动更新、无签名、体积未治理。
- **方案**：pywebview 壳保留；**更新器**：启动时查 release 元数据（GitHub→Gitee 镜像双源）→ 下载差量/全量包 → 校验哈希（cosign/sha256）→ 替换；Windows 签名（Authenticode，可后置到 v0.13）；onedir 替代 onefile 治理启动时间；安装器 Inno Setup（含"加入 PATH/开始菜单/开机自启"选项）。
- **懒加载**：EXE 内 web 资产按需解压；重依赖 dist 单独目录。
- **验收 DoD**：安装→启动 <8s（机械盘基准）；更新失败可回滚；无杀软误报白名单清单。

### 4.6 文档与配置体系（"写得好才有人看，不烂大街"）

- 框架：**Diátaxis 四分法**（tutorials / how-to / reference / explanation）重组 docs/；**mkdocs-material** + mkdocstrings：**配置参考从 pydantic schema 自动生成**（配置文档永不腐烂——这是对"配置文档十分重要"的根治方案）；API 参考从 OpenAPI 生成；决策记录 ADR 编号化。
- 多语言：中英双语（mkdocs i18n），中文为主英文跟进；版本化文档（mike，跟随 tag）。
- 门面内容：首页放 60 秒 GIF + 一行安装命令 + 分数卡；"对比页"（诚实矩阵，链接竞品文档——引用我们已有的 competitive-comparison 数据）。
- **验收 DoD**：docs 链接零死链（CI 检查）；每个配置项有 schema 生成的说明+默认值+示例；新人 15 分钟跑通 tutorial。

---

## 5. 协议与生态接入计划

| 协议/框架 | 范围 | 阶段 |
|---|---|---|
| **MCP** | tools → **全能力**：resources+templates、prompts、sampling、elicitation、roots、notifications、OAuth 2.1、streamable HTTP（client+server 双侧）；registry 可达性标注扩展 | P2 |
| **ACP** | spark-server 内置 ACP server → Zed 及一切 ACP 客户端；反向：Hermes 式接 ACP agent 当大脑（agent profiles） | P2 |
| **LangGraph** | interrupt=审批、SqliteSaver=turn 级恢复、子图=子代理、plan/verify 节点；68 个既有测试做行为等价安全网；graph.py 删除 | P1 |
| **litellm** | fallback 链、凭据池、`prompt caching` 编排（ProviderTransform 思路）、usage 全回填 | P1 |
| **模型目录** | models.dev/Catwalk 数据源替换 routing.py 硬编码；TTL 缓存 + 离线兜底 | P1 |
| **OTel** | GenAI 语义约定：trace=turn、span=tool、metric=token/成本/延迟；默认关，doctor 一键开 | P2 |
| **LSP** | pygls 客户端池：edit→didChange→diagnostics→context 回流（SWE-agent/opencode 范式）；首批 python/ts | P1 |
| **A2A** | 观望，接口留位（agent profile 未来可指远程 A2A agent） | P4 |

---

## 6. 差异化十条 → 工程化落点

| # | 差异化 | 落点 | 阶段 |
|---|---|---|---|
| 1 | 上下文合同 | replay.jsonl（schema 公开）+ Web 回放视图 + `spark replay <turn>` | P2 |
| 2 | 出网黑匣子 | plugins/audit-proxy（本地正向代理，策略=allow/deny/ask，报告导出） | P3 |
| 3 | 双粒度时间机器 | 检查点(有)+消息级 undo(接 store 已有能力)+任务级 git_memory(有)→统一时间轴 UI | P2 |
| 4 | 自进化技能库 | 任务后产 SKILL.md + 使用中自改进 + nudge 调度器 + FTS 跨会话回忆（学 hermes） | P3 |
| 5 | 中国网络原生 | 安装脚本双镜像 + 模型路由含可达性维度 + 插件目录可达性标注 | P1 |
| 6 | Harness 可换内核 | agent profiles：spark-native/claude-code(ACP)/codex-exec 子进程驱动 | P2 |
| 7 | Creator mode | 对话生成 skill/hook/插件 + 热加载 + 双轨目录 | P3 |
| 8 | 分数卡 | evals.yml 产出 JSON → 渲染进 README/docs/release | P1 |
| 9 | IM 即 IDE | plugins/im-gateway：飞书优先→微信→Telegram；审批/派活/diff 卡片 | P3 |
| 10 | 工程秀 | CI 全绿徽章 + scorecard + 分数卡 + 五平台同步机器人 | P0 |

---

## 7. 性能与优化策略（懒加载为主轴）

**后端 import 级懒加载**（`python -X importtime` 入 CI 预算测试）：
- 首屏命令路径零重依赖：`chromadb`/`llama_index`/`langgraph`/`litellm`/`tree_sitter` 各语言 grammar/MCP SDK —— 全部延迟到首次使用
- 工具 schema 注册表惰性构建（顺带修 registry.execute 重建问题）
- embedding 引擎懒初始化（顺带修 P0：默认关闭+独立 base_url）

**服务端**：uvloop/httptools、orjson 序列化、SQLite mmap+WAL（已有 WAL）、快照 zstd 可选（比 gzip 快 3-5×）、prompt caching 编排（省真金白银）

**前端**：路由/组件级 lazy()、Vite manualChunks、shiki 按语言懒加载、虚拟列表、Web Worker 跑 Markdown/Diff 解析（长消息不卡主线程）、图片懒加载、i18n 语言包按需

**预算进 CI**（test_perf_bounds 扩展）：CLI --version <400ms；import spark <600ms；首屏 <300KB gzip；p95 turn 首 token <1.2s（不含模型）；快照 1 万文件 <5s

---

## 8. CI/CD 与五平台自动化（主 badhope，四镜像）

```
main ──► ci.yml（3.11/3.12/3.13 矩阵：ruff+mypy+pytest+bandit+evals+perf 预算+import-linter）
      ──► release-please（conventional commits → 自动 CHANGELOG/版本 PR）
tag  ──► release.yml：wheel+sdist（PyPI trusted publishing）+ EXE×win + macOS/Linux 产物
      ──► mirror.yml：五平台推送（token 注入式 push，主仓发 tag → 四镜像同步同 tag）
      ──► docs.yml：mkdocs 构建部署（GitHub Pages 主 + 镜像站可后置）
      ──► scorecard/codeql/dependabot 常开
镜像兜底：Gitee/GitCode 强制同步 API（防 push 漂移）；同步状态徽章进 README
```

- 提交规范：conventional commits（feat/fix/docs/chore），release-please 全自动
- 本地开发：`bash spark_push.sh`（已就绪）仅作应急；常态以 CI mirror 为准
- 密钥管理：GitHub Environments 分层 secret；镜像 token 只读推送权限

---

## 9. 风险登记册

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| LangGraph API 破坏性变更 | 中 | 高 | 锁版 + 适配层（spark-core 内薄封装，不散落调用） |
| litellm 行为漂移（provider 侧） | 中 | 高 | 契约测试（每 provider 一条金样本流式用例） |
| MCP 规范演进 | 高 | 中 | spec 版本协商 + SDK 锁版跟进窗口 |
| FastAPI 迁移引入回归 | 中 | 中 | SSE 事件模型不变 + e2e_server 全量保留 + 双跑一周（开关切换） |
| 前端依赖体积失控 | 中 | 中 | 首屏预算进 CI + manualChunks + 依赖评审 |
| EXE 杀软误报 | 中 | 中 | 签名 + 提交误报白名单清单文档 |
| 镜像同步冲突/落后 | 中 | 中 | tag 触发强制同步 API + 状态徽章 + 告警 |
| 上下文合同引入性能损耗 | 低 | 中 | 异步落盘 + 采样开关 |
| 自进化技能产生坏技能 | 中 | 中 | 技能变更留 diff 审计 + 用户确认门 + 回滚 |
| 出网黑匣子被绕过（进程直连） | 中 | 高 | 文档明示威胁模型（覆盖主流工具路径，非完美沙箱）+ 后续 landlock 加固 |
| 采纳组件许可证污染 | 低 | 高 | 采纳清单表 + CI license-check（MIT/Apache/BSD 白名单） |
| 单人维护 bus factor | 高 | 高 | ADR + Diátaxis 文档 + 插件 API 让外部贡献有落点 |

---

## 10. 里程碑路线图（DoD 制）

| 阶段 | 版本 | 内容（依赖序） | DoD |
|---|---|---|---|
| **P0 卫生周** | v0.9.3 | 3 个 P0 修复 + P1 快修（usage 回填/registry 缓存/ Compactor 走 provider）+ 五平台镜像 CI + conventional commits 启用 | 全绿 CI；镜像同步 <60s；P0 关闭 |
| **P1 模块化+分发** | v0.10 | 拆包+import-linter；FastAPI/OpenAPI+前端 codegen；litellm fallback/凭据池/usage 全回填；模型目录；LSP 诊断闭环（py/ts）；repo map PageRank；rich CLI+补全+agent/ide 命令；文档站 v1（Diátaxis+自动配置参考）；uvx 发布 | 冷启动/首屏预算达标；pip 一行可用；docs 死链 0 |
| **P2 座舱+协议** | v0.11 | LangGraph 迁入；MCP 全能力；ACP server；agent profiles（IDE/Agent 一键切换）；上下文合同回放；双粒度时间轴 UI；Web 前端造轮子替换完成 | 行为等价测试全过；Zed 可用；回放 100% 覆盖 |
| **P3 进化+信任** | v0.12–0.13 | 自进化技能库；Creator mode+插件目录；出网黑匣子；IM 网关（飞书先行）；EXE 更新器+安装器 | 分数卡三连版可对比；黑匣子报告样例发布 |
| **P4 生态** | v0.14 | A2A 留位/远程 agent；插件市场治理（审收流程）；企业版预留（审计导出/SSO 留接口） | 外部贡献者走通插件 PR |

里程碑原则：**每阶段结束必是可发布态**；任何阶段可被中断而不烂尾（风险登记册联动）。

---

## 11. 度量（分数卡指标定义）

- 能力：evals 场景通过率（分场景）、SWE-bench-lite 定期抽样
- 性能：§7 四项预算
- 成本：每任务真实 token/成本（P1 后为实测值）
- 工程化：CI 时长、镜像同步延迟、docs 死链数、覆盖率（现状基线先测）
- 生态：插件数、外部 PR 数、五平台 star 合计

---

*本文档由 2026-10-17 全量源码走查与两轮方向讨论产出；执行中每阶段结束回写"实际偏差"到 §10。*
