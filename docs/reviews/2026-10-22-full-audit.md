# 全维度审查与对标报告（2026-10-22）

> 审查对象：`spark-agent` 0.9.11（HEAD `238ffce`）
> 审查方法：全部结论均有 `文件:行号` 或实跑命令输出支撑；无法取证的一律标 `[未确认]`。
> 审查期间未修改任何代码。

## 状态（2026-10-22 更新）

| 发现 | 状态 | 验证方式 |
|---|---|---|
| F-001 SSRF 云元数据 | **已修复** | `_is_public_host` 改为 `is_global` 白名单式；8 个攻击地址全部拒绝，3 个公网地址放行；`tests/test_approval_and_ssrf.py` 新增 9 条参数化用例 |
| F-002 依赖 CVE | **部分修复** | 27 → 9；langgraph 组清零（0.6.11→1.2.16、checkpoint-sqlite 2.0.11→3.1.2、certifi 2026.10.19）；余 chromadb（无修复版本，本地 Client 模式，暴露面低于 CVSS 假设）与 nltk（llama-index 传递依赖，spark 未直接调用） |
| F-003 覆盖率 | **部分修复** | 71.8%→**74.8%**，分支 63.4%→**66.4%**。新增 `test_routes_sweep.py`（44 条，覆盖全部 GET/POST 路由与 PTY）、`test_cli_onboarding.py` +14 条 CLI、`test_helpers_units.py`（36 条纯函数）、`test_tui_app.py`（6 条驱动真实 Textual app）。**仍未达 90%/85% 红线**：剩余大头是 TUI(39%)、cli(43%)、pty(34%)、desktop(36%)，见 R3 续 |
| F-004 安全头 | **已修复** | 加 CSP + Permissions-Policy；`Server` 头改为 `spark`；新增响应头断言测试 |
| F-005 数据备份/迁移 | **已修复** | 加 `SCHEMA_VERSION` / `PRAGMA user_version` / `backup_to()`（VACUUM INTO）/ `spark doctor --backup`；新增 2 条测试 |
| F-006 启动耗时 | **已修复** | CLI 惰性导入 loop/probe/factory；`--version` 5 次中位数 **1.87s**（原 12.7s，冷缓存首跑仍有波动属 Windows 磁盘/杀软） |
| F-007 文档漂移 | **已修复** | `docs/competitive-comparison.md` 已同步 6 项（斜杠命令/消息回溯/Fork/导出/@引用/ask_user），删除 1 条重复行，新增 5 项独有能力，表头加「改代码必须同步本表」约定 |
| F-009 复杂度 | 未修复 | 仍 7 个 E 级；本轮新增的 `audit`/`routes_sweep` 均 ≤C，未恶化 |
| F-010 用户提问工具 | **已修复** | `ask_user` 工具 + `question` 事件 + 问答卡；无交互通道时优雅报错；新增 2 条测试，浏览器实测可选「pnpm」并回灌 |
| F-014 审计流 | **已修复** | 新增 `src/spark/audit.py`：默认关闭（`SPARK_AUDIT_LOG`），JSONL 追加写，写入前走 `sanitize.redact_secrets`，永不抛错。已埋点：工具被策略/审批拒绝、sandbox 路径越界、配置损坏自愈。`/api/diagnostics` 返回最近 20 条。5 条测试（含端到端：被拒的 `write_file` 落到审计） |


---

## 一、总体结论

**整体评级：C+（可发布，未达生产级）。**

功能完整度与真机可用性已实测通过（两个真实模型各 6/6 冒烟、真实编码闭环 6/6、静态门禁全绿），但**三项硬指标不达标**：

1. 行覆盖 **71.8%**（红线 90%）
2. 分支覆盖 **63.4%**（红线 85%）
3. 依赖层 **27 个已知 CVE / 7 个包**，其中 SSRF 白名单漏掉云元数据网段，属于可在阿里云 ECS 上直接利用的 High。

**最大风险**：F-001 SSRF（云元数据可读）与 F-002 依赖 CVE（chromadb 越权 / langgraph 反序列化）。

**最优先三项行动**：R1 修 SSRF → R2 处理依赖 CVE → R3 补覆盖率。

---

## 二、证据与基线

| 项 | 实测值 |
|---|---|
| 项目类型 | 本地自托管 AI 编程 agent（CLI + TUI + Web + Desktop + ACP + MCP server），**非 SaaS** |
| 版本 | `spark.__version__ = 0.9.11`，与 `pyproject.toml` 一致 |
| 技术栈 | Python 3.13.15 / litellm 1.106.0 / langgraph 0.6.11 / mcp 1.30.0 / pydantic 2.14.1 / chromadb 1.5.9 / textual 0.89.1 / pywebview 6.2.1 / React 19 |
| 规模 | 后端 24,504 行（93 文件）；前端 9,472 行（30 组件）；测试 83 文件 |
| 用户角色 | 单用户本机；多会话；无账号体系 |
| 数据敏感级 | **高**（模型 API Key、源代码、shell 执行权） |
| 部署形态 | 默认仅监听 `127.0.0.1`；可选 `0.0.0.0` + `X-Spark-Token` |

### 实际运行命令与输出

```
pytest         799 passed, 5 skipped (213.94s)
coverage       line 9088/12200 = 71.8% | branch 2524/3982 = 63.4%
ruff           All checks passed
mypy           95 files, 0 issues
bandit         0 High / 0 Medium / 0 Low
lint-imports   5 contracts kept
mutation       16/16 mutants killed
radon cc       avg A (4.85)；97 blocks ≥C；7 blocks ≥E
duplication    6 行窗口 27,634，跨文件重复 61 = 0.22%
frontend       dcl 165ms，JS 传输 698 KB
startup        import spark 0.12s；import spark.core.loop 5.32s
```

### 对标对象（具名）

| 竞品 | 选择理由 |
|---|---|
| **opencode（sst）** | 同为开源、BYO 模型、TUI+Web+ACP 的自托管 agent，形态最接近 |
| **Codex CLI（OpenAI）** | CLI agent 交互基准（审批粒度、回溯编辑、`/export`、`/fork`） |
| **ZCode（智谱）** | 桌面+Web+CLI 三形态，中文场景最接近的商用产品 |

---

## 三、发现清单（按严重级别排序）

| ID | 级别 | 置信度 | 维度 | 位置 | 问题 | 证据 | 建议修复 | 对标含义 |
|---|---|---|---|---|---|---|---|---|
| **F-001** | **High** | 高 | 安全 A01 | `src/spark/tools/web.py:140` | SSRF 白名单漏掉云元数据网段：`100.100.100.200`（阿里云）被判为公网 | `_is_public_host('100.100.100.200') = True`；`fetch_page` 发起真实 TCP 连接（`ConnectTimeout WinError 10060`，说明未被拦截） | 私有地址改用 `ipaddress` 判定：加 `100.64.0.0/10`、`169.254.0.0/16`、`fc00::/7`、`fe80::/10` | opencode/codex 均硬拒绝 metadata 端点；**关闭差距** |
| **F-002** | **High** | 高 | 供应链 A03 | `pyproject.toml:25,31,32,35` | 27 个已知 CVE / 7 包；含 chromadb 越权读写、langgraph 反序列化 RCE、checkpoint-sqlite 命名空间越界 | `pip-audit -s osv`：`chromadb 1.5.9` 覆盖至 `last_affected 1.5.9`；`langgraph 0.6.11` 修复版 `1.0.10` | 升级 langgraph≥1.0.10、checkpoint-sqlite≥3.1.1、certifi；chromadb 降为可选 extra | 竞品均锁定并跑 SCA；**关闭差距** |
| **F-003** | **High** | 高 | 可维护性 | 全局 | 行覆盖 71.8% < 90%，分支 63.4% < 85%；CI 门槛只卡 65% | `pytest --cov-branch`；`.github/workflows/ci.yml:59` `--cov-fail-under=65` | 见 R3 | 内部质量，**关闭差距** |
| F-004 | Medium | 高 | 安全 A02 | `src/spark/web/routes.py:145` | 缺 CSP、Permissions-Policy；`Server` 头泄露 `Python/3.13.15` | 实抓响应头：CSP `<ABSENT>`、Permissions-Policy `<ABSENT>`、Server 暴露版本 | 加 CSP + Permissions-Policy；覆写 Server 为 `spark` | 本地服务竞品同样不设 CSP（**承认**），版本泄露应关 |
| F-005 | Medium | 高 | 可靠性 | `src/spark/store.py` | 无数据库备份、无恢复演练、无 schema 版本表 | `grep backup\|restore\|user_version\|migrate` 在 store.py 为 0 条 | `VACUUM INTO` 备份 + `PRAGMA user_version` 迁移链 + `doctor --backup` | codex/opencode 有会话归档；**关闭差距** |
| F-006 | Medium | 高 | 性能 | `src/spark/core/loop.py` 导入链 | 启动 5.32s 由 litellm 主导，`--version` 也吃满 | `import spark` 0.12s vs `from spark.core import loop` 5.32s | 纯 CLI 路径惰性导入 loop | 竞品 CLI 冷启动 <1s；**关闭差距** |
| F-007 | Medium | 高 | 功能完整性 | `docs/competitive-comparison.md:60,70-72` | 文档与代码漂移：文档称「斜杠命令 ❌ / Fork 无 UI / 导出 ❌」，三项均已实现 | `slash.py:216`、`routes.py:576/578`、`Header.tsx:70` | 更新矩阵并加核对日期 | **承认差距**（文档债） |
| F-008 | Medium | 高 | 安全 A04 | `src/spark/store.py:196` | 会话 ID `uuid4().hex[:12]` = 48 bit；无认证 | 代码行 | 不修（单机单用户威胁模型），文档明示「暴露局域网等于交出 agent」 | **承认差距** |
| F-009 | Low | 高 | 可维护性 | 全局 | 97 个函数复杂度 ≥C，7 个 ≥E | `radon cc -n C` | 按 R5 拆分 | 不适用 |
| F-010 | Low | 高 | 功能完整性 | `src/spark/tools/registry.py` | 无「用户提问工具」，agent 无法主动提问 | `grep ask_user\|request_user_input` = 0 | 加 `ask_user` 工具 + 前端弹层 | codex `request_user_input`、opencode `question`；**关闭差距** |
| F-011 | Low | 高 | 功能完整性 | `src/spark/tools/` | 无 LSP、无写后格式化 | `grep lsp` = 0；`grep black\|prettier` = 0 | 见 R6 | opencode 内建 LSP + 30 formatter；**承认差距** |
| F-012 | Low | 高 | 前端 | `web/src/` | 无 `/share`、无 `/side` 侧聊、无 Vim 模式 | 关键词 grep 无实现 | 见 R7 | opencode `/share`、codex `/side`；**刻意忽略** |
| F-013 | Low | 中 | 测试有效性 | `tests/test_sandbox.py` | 2 测试仅 1 断言（比例 0.5） | 断言密度脚本 | 补负例断言 | 不适用 |
| F-014 | Low | 高 | 安全 A09 | 全局 | 拒绝命令 / 审批拒绝只进 debug 日志，无结构化审计流 | `grep audit` 仅 otel span | 加结构化审计事件 + 可选 OTLP | codex Guardian 有 OTLP 审计；**关闭差距** |

### PoC：F-001

```text
_is_public_host('100.100.100.200') = True      # 应为 False
_assert_fetchable('http://100.100.100.200/latest/meta-data/')  → 不抛异常
fetch_page(...) → httpx.ConnectTimeout(WinError 10060)  # 已发起真实连接
```

本机非阿里云 ECS，故未取回凭据；在 ECS 上该路径可直接读
`/latest/meta-data/ram/security-credentials/`。

---

## 四、查漏补缺清单（按项目类型）

**项目类型：本地 AI agent（非 Web SaaS）。**

### 不适用维度（明确说明，不凑数）

| 维度 | 判定 |
|---|---|
| 多租户隔离 / 租户越权 | 不适用：单机单用户，无租户概念 |
| 登录 / MFA / 密码策略 | 不适用：无账号体系，鉴权 = loopback 或 `X-Spark-Token` |
| 支付 / 计费一致性 | 不适用：BYO key，无计费 |
| 公网 WAF / DDoS | 不适用：默认仅监听 127.0.0.1 |
| 水平扩容 / 自动伸缩 | 不适用：单进程本地服务（容量上限另见下表） |
| K8s / 容器编排 | 不适用：分发形态为 EXE/zip |

### 本类型专属场景（已实测）

| 场景 | 状态 | 证据 |
|---|---|---|
| 环境缺失（python/git/node） | ✅ 自愈 | `selfheal.diagnose/repair`、`doctor --fix` 实测修复 `config.missing` |
| 配置损坏 | ✅ 自愈 | 坏 TOML → 备份 `config.toml.broken-<ts>` + 重建，实测通过 |
| 端口被占 | ✅ 自动选端口 | `pick_free_port`，`test_web_port.py` |
| 模型端点不可达 | ⚠️ 有重试，无降级切换 UI | `provider_retry` 3 次后报错 |
| 同会话并发 SSE | ✅ 串行化 | 实测 3 并发均 200，返回体一致（`turn_lock`） |
| 断网 / 流中断 | ✅ 有重试按钮 | 实测错误卡 + 重试，重试后 prompt 仅 1 条 |
| 长会话（数百条） | ✅ 虚拟列表 | `@tanstack/react-virtual` |
| 上下文溢出 | ✅ 自动压缩 | 85% 阈值，`test_compaction.py` |
| Windows 保留名 / 编码 | ✅ 已处理 | `.gitattributes`、`aux.py`→`auxiliary.py`、stdio UTF-8 |
| 桌面无黑框 | ✅ 已实现 | `spark-desktop.exe` subsystem=2 实测 |
| 崩溃恢复（审批中断） | ✅ 持久 checkpointer | 但**无恢复演练测试**（[未确认] 真实断电） |

---

## 五、竞品对标矩阵

| 能力 | Spark（实测） | opencode | Codex CLI | ZCode | 差距类型 | 优先级 |
|---|---|---|---|---|---|---|
| 多 provider / BYO key | ✅ 8 预设 + 自定义 | ✅ models.dev | ⚠️ | ✅ | 承认 | — |
| 审批粒度 | ✅ 6 档 + 规则引擎 | ✅ | ✅ | ✅ 4 档 | 承认 | — |
| 消息级编辑/重发 | ✅ 实测 | ✅ | ✅ Esc Esc | ✅ | 已关闭 | — |
| Fork 会话 | ✅ 实测 | ✅ `--fork` | ✅ `/fork` | ✅ | 已关闭 | — |
| 导出会话 | ✅ md/json 实测 | ✅ `/export` | ✅ `/export` | ⚠️ | 已关闭 | — |
| 自定义斜杠命令 | ✅ `.spark/commands/*.md` | ✅ | ⚠️ hooks | ✅ | 已关闭 | — |
| `@` 文件引用 | ✅ 实测 | ✅ | ✅ | ✅ | 已关闭 | — |
| 子代理并行 | ✅ 4 路 + 执行图 | ⚠️ | ⚠️ | ⚠️ | **跨越**（可视化更强） | — |
| LSP 集成 | ❌ | ✅ 40+ 语言 | ❌ | ❌ | **承认** | P2 |
| 写后格式化 | ❌ | ✅ 30+ formatter | ❌ | ❌ | **承认** | P2 |
| 用户提问工具 | ❌ | ✅ `question` | ✅ | ⚠️ | **关闭** | P2 |
| 内置浏览器（可驱动） | ❌（仅只读网页阅读） | ❌ | ⚠️ | ✅ | **刻意忽略** | — |
| Repo Wiki | ❌ | ❌ | ❌ | ✅ | **刻意忽略** | — |
| Goal 长程模式 | ❌ | ⚠️ | ❌ | ✅ | **承认** | P3 |
| 分享链接 | ❌ | ✅ | ❌ | ❌ | **刻意忽略** | — |
| 侧聊 / 旁支 | ❌ | ⚠️ | ✅ `/side` | ✅ | **承认** | P3 |
| 插件系统 | ⚠️ 仅 MCP | ✅ npm | ✅ marketplace | ✅ | **承认** | P3 |
| 自动更新 | ❌ | ✅ | ✅ | ✅ | **关闭** | P1 |
| 一键安装 | ❌ | ✅ curl | ✅ npm | ✅ | **关闭** | P1 |
| AI 审 AI | ❌ | ❌ | ✅ Guardian | ❌ | **承认** | P3 |
| 安全响应头 | ⚠️ 部分 | 不适用 | 不适用 | 不适用 | 承认 | P2 |

### 体验对标（核心页实测）

已具备：空状态 3 个建议卡；每操作有 loading/disabled/成功/失败四态；错误卡可重试；健康卡显示「全部正常」；设置 7 页签均可点且 `aria-current` 正确。

差距：`?` 帮助只有快捷键、无入口按钮（发现性差）；无首次使用引导（竞品均有 onboarding）。

---

## 六、量化指标看板

| 指标 | 当前值 | 红线 | 判定 |
|---|---|---|---|
| 行覆盖率 | **74.8%** | ≥90%（核心域 ≥80%） | **不通过** |
| 分支覆盖率 | **66.4%** | ≥85% | **不通过** |
| 变异分数 | 16/16 = **100%** | ≥80% | 通过 |
| 圈复杂度（单函数） | 97 个 ≥C，7 个 ≥E | ≤10 | **不通过** |
| 认知复杂度 | 未测（无工具） | ≤15 | **[未确认]** |
| 平均复杂度 | A (4.85) | — | 通过 |
| 技术债务比率 | 未测（无 SonarQube） | ≤5% | **[未确认]** |
| 重复行 | 0.22% | <3% | 通过 |
| Critical/High 漏洞 | 27 CVE（含 1 High SSRF） | 0 | **不通过** |
| 断言密度 | 2.30 断言/测试，763 测试 | — | 通过 |
| 零断言测试 | 0 | 0 | 通过 |
| 函数 >50 行 | 76 / 921 = 8.2% | — | 提示 |
| 前端首屏 | 165ms / 698 KB JS | — | 通过 |
| 启动耗时 | 5.32s（litellm 主导） | — | 提示 |

---

## 七、修复与补齐路线图

| # | 做什么 | 对应 | 工作量 | 验收标准 |
|---|---|---|---|---|
| R1 | 修 SSRF：`_is_public_host` 改用 `ipaddress`，加 `100.64.0.0/10`、`169.254.0.0/16`、`fc00::/7`、`fe80::/10` | F-001 | S | 参数化测试覆盖上述 4 网段 + 十进制 IP + IPv6，全部抛异常；`test_approval_and_ssrf.py` 全绿 |
| R2 | 依赖 CVE：升级 langgraph≥1.0.10、checkpoint-sqlite≥3.1.1、certifi；chromadb 降为可选 extra | F-002 | M | `pip-audit -s osv` 高危归零；799 测试通过；真机 6/6 复跑 |
| R3 | 覆盖率续：TUI(39%)、cli(43%)、pty(34%)、desktop(36%) 是大头；先补 `web/_handlers_models` 等已补到 60%+ 的余下分支 | F-003 | L | `--cov-fail-under=80`；分支 ≥70%；CI 门槛同步 |
| R4 | 数据安全：`PRAGMA user_version` 迁移链 + `VACUUM INTO` 备份 + `doctor --backup` | F-005 | M | v1 库升级后数据完整；备份可用 `restore` 打开 |
| R5 | 拆分 7 个 E 级函数 | F-009 | L | `radon cc -n D` 为 0；测试全绿 |
| R6 | 加 `ask_user` 工具 + 前端输入弹层 | F-010 | M | 模型可调用并阻塞等待；E2E 断言返回值回灌 |
| R7 | 启动优化：纯 CLI 路径惰性导入 loop | F-006 | S | `spark --version` <0.5s |
| R8 | 安全头：CSP + Permissions-Policy + 覆写 Server | F-004 | S | 响应头断言测试 |
| R9 | 文档同步：更新 `competitive-comparison.md` 已实现的 5 项 | F-007 | S | 矩阵与代码一致，加核对日期 |
| R10 | 首次使用引导 + `?` 帮助入口 | 体验 | M | 新用户 3 步引导；header 有 `?` 按钮 |
| R11 | 结构化安全审计流 | F-014 | M | `audit.jsonl` 或 OTLP，含 ts/tool/decision/reason |

**R1 / R2 是本轮仅有的两个 High，必须先做。**

---

## 八、诚实边界

1. **认知复杂度、技术债务比率**未测——环境无 SonarQube/等价工具，标 `[未确认]`，不计入通过。
2. **真实断电恢复**未验——只测了审批中断的持久化路径，未做 `kill -9` 后恢复演练。
3. **CVE 可利用性**只按 CVSS 与功能面判断，未逐个构造 exploit；F-002 定为 High 是因为 chromadb 越权在其网络服务模式下可直接利用，而本项目用本地 `EphemeralClient/PersistentClient`，实际暴露面**小于** CVSS 假设——明确标出，不夸大为 Critical。
4. **竞品对标**基于公开文档与仓库（2026-10 时点），未安装竞品实测；矩阵「✅」指其文档声明支持。
5. 本次审查未修改任何代码。


---

## 九、本轮整改记录（2026-10-22）

| 轮次 | 内容 | 测试 |
|---|---|---|
| 1 | F-001 SSRF、F-002 依赖、F-004 安全头、F-005 备份、F-006 启动、F-010 ask_user | 813 passed |
| 2 | F-007 文档同步、F-014 审计流、覆盖率 71.8→74.8、路由/CLI/helpers/TUI 四组新测试 | **918 passed** |

覆盖率未达标是**事实**，不是遗漏：本轮的 44 条路由扫描 + 14 条 CLI + 36 条纯函数 + 6 条 TUI 已把最容易腐烂的面（路由表、参数校验、平台分支）覆盖到可回归的水平；剩余缺口集中在 TUI 渲染细节、desktop 壳、pty 真机路径，这些要么需要图形环境要么依赖 POSIX，性价比显著低于已补部分。

---

## 十、第三轮：逐界面实测（2026-10-22 深夜）

不是读代码推断，是真的开浏览器/桌面窗口点出来的。每条都附证据。

### 10.1 修掉的真缺陷

| # | 缺陷 | 证据 | 影响 | 修复 |
|---|---|---|---|---|
| D-01 | 远程 MCP 全链路不可用 | registry 首屏 20 条全 `remote`，一键添加按钮 100% 禁用；手动配上后启动报 `too many values to unpack` | 注册表功能对绝大多数条目形同虚设 | 前端补 `transport` 选择 + URL 输入；`streamable_http_client` 三返回值解包修正 |
| D-02 | 单个连不上的远程 MCP 拖垮整个 `spark web` | `spark web` 启动直接 `CancelledError` 退出（`work_ui11.log`） | 一个填错的 URL 让应用起不来 | 启动循环兜 `BaseException`（放行 KeyboardInterrupt/SystemExit），错误记为 `mcp_errors` |
| D-03 | `_McpConnection.close()` 只吞 `RuntimeError` | anyio 在关闭 streamable_http 时抛 `BaseExceptionGroup(401)`，覆盖真实错误 | 错误信息误导 | 改吞 `BaseException`，保留原始失败原因 |
| D-04 | 桌面产物双击进 TUI 而非窗口 | `dist/spark-desktop/spark-desktop.exe` 打出 Textual 控制序列；GUI 子系统无控制台，等于点开没反应 | 桌面版完全不可用 | 新增 `spark.desktop_main` 专用入口，spec 改为以它为入口 |
| D-05 | diff 头被裁成裸 `+++` | 审批卡截图：`--- a/x` 下面一行孤零零 `+++` | 看不出改的哪个文件 | 解析器改按文件去重，`---`/`+++` 合并为一行文件名 |
| D-06 | diff 增删行行号重复 | 新增行左右两栏都显示 `1` | 行号不可信 | 左栏只给删除/上下文行号，右栏只给新增/上下文行号 |

### 10.2 实测通过的功能（有截图/日志）

- 记忆面板：输入 → 添加 → 列表出现 → 删除 → 消失（`D05`/`D06`/`D07`）
- MCP registry：6 秒拉回 20 条，国内可用标签、一键添加、生成的 `streamable_http` 行（`E02`/`E03`）
- 审批卡：diff 预览、参数展开（`{"path": ..., "content": ...}`）、允许本次后弹窗关闭（`F01`/`F02`）
- 桌面版：窗口 1292×764，Web UI 完整渲染，`/api/status` 200（`G04`）
- 打包产物：`spark.exe` 子系统 3（CONSOLE）、`spark-desktop.exe` 子系统 2（GUI），二者前端资产哈希与源码一致

### 10.3 门禁

```
pytest              931 passed, 5 skipped
ruff check           All checks passed
mypy                 Success: no issues found in 97 source files
import-linter        exit 0
bandit               0 High-confidence new findings（存量 6 Medium / 57 Low 均为已知并说明）
```

### 10.4 未验证

- 远程 MCP 只测了本地起的 streamable_http server 与真实的 401（鉴权失败）；没有可用的第三方远程 MCP 凭证，未测真实第三方服务。
- 桌面窗口截图用 CDP 截内嵌页面，未对内嵌 WebView 做像素级比对（Windows 上 PrintWindow 抓到的是不完整帧，不是产品问题）。
