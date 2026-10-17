# Changelog

本项目遵循语义化版本。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [0.9.3] - 2026-10-17

全量源码走查后的首批修复（审查记录见 `docs/reviews/`）。

### 修复

- **bwrap 沙箱断网（P0）**：`bwrap_command` 的 `--unshare-all` 会连网络命名空间一起隔离，
  与"不隔离网络"的契约相悖——安装了 bubblewrap 的 Linux 在 workspace 模式下
  pip/npm 等网络命令全部失败。补 `--share-net` 保留网络。
- **FTS 搜索索引跨会话误删（P0）**：`delete_messages_after` / `truncate_messages`
  按全局 `rowid > X` 删 FTS，单库多会话时会连带删掉其他会话消息的索引。
  改为按 `session_id` 子查询精确删除，并先于消息本体删除。
- **向量记忆默认配置自相矛盾（P0）**：embedding 请求默认打到 chat 端点
  （如 DeepSeek，无 `/embeddings` 路由），每轮失败静默降级关键词。
  向量检索改为显式 opt-in（`memory.embedding_enabled`，默认关），
  并支持独立 `embedding_base_url` / `embedding_api_key`。

### 变更

- 压缩摘要优先走 Provider 抽象（鉴权/重试单一真相源），litellm 直连降为兜底；
  mock provider 不再误触 litellm 真实路径。
- 工具 dispatch 表随实例构建一次并缓存，不再每轮重建。

### CI

- 新增 `mirror.yml`：主仓 push 后自动同步 Gitee / GitCode / 两 GitHub 镜像仓（分支+标签）。

## [0.9.2] - 2026-10-14

CI 转绿批次：修复 GitHub Actions 长期全红的五个独立根因，全部先在本地以 CI
同款命令复现并跑通（含 Linux 平台模拟与真实 3.11 解释器），再推送。

### 修复（CI / Security）

- **bandit 配置键名错误（CI 长期红的直接原因）**：`bandit.yaml` 使用了不存在的
  `skipped_tests` 键（bandit 只认 `skips`），导致整份跳过列表静默失效，`-ll -ii`
  下 5 个误报持续触发非零退出。A/B 对照实验：旧键 exit=1/5 问题 → 新键 exit=0/0。
- **mypy 在 Linux 上报 Windows 专属常量**：`subprocess.CREATE_NO_WINDOW` /
  `CREATE_NEW_PROCESS_GROUP` 只在 Windows 的 typeshed 中存在，CI（ubuntu）报
  `Module has no attribute`（2 处），而本机 Windows 永远看不到。新增
  `src/spark/platform_compat.py` 用 `getattr(x, name, 0)` 统一取值，四个调用点收敛。
- **配置文件 ACL 判定在 GitHub runner 上误判**：`win_acl_restricted()` 用
  `len(aces) == 1` 精确计数，而 runner 上 `icacls /inheritance:r` 后残留 4 条系统条目
  （runneradmin / SYSTEM / Administrators / OWNER RIGHTS）→ 把已收紧的文件判为不安全，
  导致 2 个 Windows 测试失败、Release 的 Windows 构建整体失败。改为安全目标导向：
  只要 `BUILTIN\Users` / `Everyone` / `NT AUTHORITY\Authenticated Users` /
  `INTERACTIVE` 均不可见即达标（并保留"继承标记 (I) 必须不存在"断言）。
- **Security 的 secret 扫描命令语法错误**：`pre-commit run gitleaks trufflehog` 不被
  pre-commit 接受（它只接受单个 hook id），job 以 usage error 失败而非扫描发现问题。
  拆为两次调用；`uv audit` / `pip-audit` 步骤级补 `continue-on-error`，与 job 级
  "治理看板不阻断合并"的设计意图一致。
- **Release 资产上传冲突 + macOS runner 不可用**：三个平台 job 并发写同一个 Release，
  后到者触发 "Cannot upload asset to an immutable release"；`macos-13` 在免费额度下
  永不获得 runner（实测排队近 2 小时）。改为构建矩阵只产出 workflow artifact，
  再由单一 `publish` job 汇总上传；macOS runner 换 `macos-latest`（产物更名
  `spark-macos-arm64`）。
- **MCP 启动在 Python 3.11 上跨 task 退出 anyio cancel scope（test 3.11 剩余红）**：
  `_start_on_owner` 用 `asyncio.wait_for(conn.start(), timeout=15)` 包裹连接，超时
  会取消承载连接的 task，而 anyio 的 cancel scope 是在该 task 中进入的；之后
  `close()` 在另一 task 里 `aclose()` 抛 `RuntimeError: Attempted to exit cancel
  scope in a different task than it was entered in`（Python 3.11 + anyio 4.x）。
  改为超时内移到 `start()`（`asyncio.timeout`）实施、调用方不再包装；`close()` 对
  已部分展开的 exit stack 吞掉该 RuntimeError（资源已释放），失败日志补
  `exc_info`。真 3.11.15 全量验证：630 passed / 5 skipped。

### 新增

- `src/spark/platform_compat.py`：跨平台 subprocess 标志的安全取值（`CREATE_NO_WINDOW`
  / `CREATE_NEW_PROCESS_GROUP` / `DETACHED_PROCESS`）。
- `tests/test_acl_predicate.py`：4 例锁定 ACL 判定的安全语义（runner 形态应判为已收紧、
  含宽泛用户组应判为未收紧、系统/管理员条目不算宽泛、非 Windows 返回 False）。

### 验证

- 本地 CI 仿真：ruff PASS；mypy（`--platform linux` 与本地 Windows）双平台 PASS；
  bandit exit=0；pytest **630 passed / 5 skipped / 0 failed**，覆盖率 **73.44%**（门禁 65%）；
  gitleaks Passed。

## [0.9.1] - 2026-10-14

开源整理 + 真实模型端到端验证批次。全部改动经真实模型（OpenAI 兼容端点）验证，
全量测试 626 passed / 5 skipped / 0 failed，ruff/mypy 零错误。

### 修复（真实模型测试暴露，mock 永远测不到）

- **环境变量覆盖被配置文件压制**：`ConfigSources` 顺序错误，导致 `.env.example` 宣传的
  `SPARK_PROVIDER__*` / `SPARK_AGENT__*` 写法在有 `config.toml` 时静默失效，与文档承诺
  （环境变量 > TOML）相反。已修正源优先级，并补单元测试锁定。
- **任意自定义模型名无法使用**：`openai_compat` provider 把裸模型名直接交给 LiteLLM，
  后者只能按内置规则推断 provider——`glm-5.3-flash`、`Qwen-*`、以及 README 宣传的
  "任意 OpenAI 兼容 API" 全部报 `LLM Provider NOT provided`。已改为显式按 OpenAI 兼容处理
  （带 `/` 前缀的 LiteLLM 语法仍原样尊重）。
- **检查点回滚遇文件占用直接崩溃**：快照把活跃 SQLite 的 `-wal/-shm` 运行时文件打包，
  恢复时写回撞锁（Windows `OSError: [Errno 22]`）导致整个回滚失败。双层修复：快照侧排除
  这两个后缀（运行时文件、可重建），恢复侧单文件失败收集进 `errors` 且不中断其余文件。
- **MCP `bridge.start` 注册了工具却无法调用**：`ctx.mcp_call` 依赖调用方另行绑定，
  直接调用该 API（脚本/测试）时已注册工具全部以 "Unknown tool" 静默失败。已加兜底绑定。
- **`HookConfig.on_deny` 默认值与 TOML 路径不一致**：pydantic 默认 `warn` 而
  `HookRegistry.from_config` 默认 `block`，通过配置注册的拦截型 hook 实际只警告不拦截。
  已统一为 `block`。
- **`/api/status` 的 `has_api_key` 不识别环境变量**：部署为环境变量供 key 时误显示"未配置"。
- **进程内首次 system prompt 构建多付约 12 秒**：`detect_env` 串行 spawn 6 个版本探测
  子进程，Windows 上每个 2-3 秒。已改为并行探测。
- **`_similarity_clusters` 的 O(M²) 残留**：倒排索引优化后仍保留一段全配对循环，
  把优化完全抵消。已删除。

### 修复（此前批次，本版一并发布）

- **P1-2 旗舰工具 `apply_edit` 未纳入 WRITE_TOOLS**：full-auto / auto-edit 下被策略层
  一律拒绝、权限规则无法按路径约束它。
- **Web 保存设置损坏 MCP 配置**：往返链路丢失 `transport` / `http` / `allowed_tools`，
  在网页里保存一次即把 SSE/HTTP server 降级为 stdio 并清空 P1-9 最小权限白名单。
- **`save_config` 丢字段**：`PermissionRule.tool`（受限规则保存后静默扩大为任意工具）、
  `agent.system_prompt`（用户自定义系统提示词保存即丢失）、`streamable_http` transport。
- **项目级 `.spark.toml` 可注入 `agent.system_prompt`**：未纳入不可信配置剥离清单。
- **`web_search` 缺 `import json`**：成功路径必崩。
- **system_prompt / effort 经 Web UI 全链路不可用**：前端不发送、后端不接收、状态不回传、
  不落盘——HEAD 宣称的功能实际是死的。四层补齐。
- **审批档位三份定义不一致**：`onboard.py` 只认 3 档，`spark init --approval plan-only`
  等被错误拒绝。已统一以 config 为单一事实来源。
- **graph.py 的 interrupt resume 值被丢弃、`deny` 判决被忽略**：LangGraph 参考实现里
  用户点"拒绝"工具照样执行。已修，并配真实 graph 驱动测试。
- **`core/loop.py` checkpoint 失败路径 UnboundLocalError**、**FTS 标题被每条非 user
  消息清除**、**`/api/agents_md` GET 缺路径守卫**、**pty rows/cols 非数字 500**。
- **前端**：Terminal 退出后"按键重启"因 stale closure 永久失效；BrowserModal Enter
  双请求；流断线自动重发导致重复执行同一 prompt（改为不重发 + 提示）；FileViewer 跨行
  语法高亮 token 掉色；`fetchWithTimeout` 监听器泄漏。

### 新增

- **真实模型验证矩阵**：18 项配置面端到端验证（system_prompt 注入 / effort / 六档审批 /
  四档沙箱 / 护栏 / hooks 拦截 / 检查点回滚 / 记忆检索注入 / MCP 自环 / 上下文压缩）。
- **测试补强**：`test_web_port.py`（端口自适应）、`test_prompt_cache.py`（提示词缓存与
  失效）、checkpoint WAL/SHM 排除与恢复容错用例、provider 模型名处理用例、
  重写的 `test_langgraph_path.py`（真实 graph 驱动，替代此前 4 个伪覆盖用例）。
- **`spark/asyncutil.py`**：统一"同步上下文安全运行协程"实现（此前 4 处各自内联）。
- **`spark/tools/_common.py`**：统一文本裁剪实现（此前 3 处）。
- **`docs/competitive-comparison.md`**：与 Qoder / ZCode / opencode / Codex CLI 的
  全功能对比（13 个功能域）。
- **四语 README 真实截图展示区**（中文/English/日本語/Español）。

### 变更

- **端口自适应**：`spark web` / `spark desktop` 省略 `--port` 时从 8000 起自动选择可用端口；
  显式指定被占用时给出明确提示。启动时打印访问地址（此前 logger 无 handler，用户看不到）。
- **缺 API Key 降级**：不再拒绝启动，自动进入演示模式（mock）并提示；在 Web 设置中
  填入密钥后立即切换真实模型，无需重启。`spark doctor` 相应从 `[bad]` 改 `[warn]`。
- **i18n 全量接入**：26 个前端组件接入中英词典（约 290 个替换点、词典 370 键），
  语言切换对全界面生效（含日期/时间 locale 跟随）。
- **系统提示词缓存**：按配置键 + 30s TTL 缓存，写工具执行后主动失效，避免每个工具
  回环重扫全仓（repo_map tree-sitter）。
- **清理退役物**：删除 `requirements.lock`（引用已移除的 fastapi/uvicorn 依赖与不存在的
  `office` extra）、`tests/legacy_root/`（已退役根树的归档测试，从未被收集）、
  `examples/plugins/`（目标契约已不存在、无法加载的死示例）、过期的历史审计文档；
  相应更新全部引用。
- **版本统一**：`pyproject.toml` 与 `spark.__version__` 统一为 `0.9.1`（此前分别停留
  在 0.1.0，与 CHANGELOG 的 0.8.0 及发布 tag 脱节）。
- **Windows EXE 重建**并冒烟通过（真实模型对话 + shell 工具调用）。

## [Unreleased]

### 变更（双树收敛收尾，2026-10-10）
- **根树退役，`src/spark` 成为唯一主干**：pip 发行（`packages.find where=["src"]`）、pytest
  （`pythonpath=["src"]`）、CI 门禁（ruff/mypy/bandit 改指 `src/spark`）三方一致；根目录
  `spark/` 平行实现删除，其专属测试中不可迁移部分归档至 `tests/legacy_root/`（作为 P1 能力
  回迁规格），可迁移部分已适配 src 随主测试运行。
- **修复 `core/compact.py` 的 `MessageRole` 双轨覆盖**：`from spark.models import MessageRole`
  覆盖 llama_index 的同名枚举，导致 `.SYSTEM` AttributeError、16 个测试文件收集失败；改用
  `LlamaMessageRole` 别名。此项修复后全量测试才具备可运行性。
- **质量门禁全绿**：`pytest -q` 362 passed / 2 skipped，覆盖率 TOTAL 72%（门禁 65%）；
  `ruff check src/spark tests` 0 错误；`mypy src/spark` 0 错误（48 文件，含 Windows 平台专属
  属性的窄类型 ignore）；`bandit -r src/spark -ll -ii` HIGH/MED 0。
- **P0 出口脱敏接入**：`src/spark/sanitize.py`（密钥/主目录/堆栈三层）接入 SSE 事件出口与
  错误文案，新增 `tests/test_sanitize.py` 13 例；修复"无 secrets 时整体跳过"缺口——主目录与
  堆栈属无条件脱敏项，本机无令牌/无 API Key 时同样生效。
- **PyInstaller 打包改指 src 树并实测**：两个 spec 入口改为 `src/spark/__main__.py`，清理根树
  幽灵 hiddenimports（uvicorn 子系统、`spark.web.api_*`、subagent/codeindex/plugins、tomlkit、
  docx/openpyxl/pypdf），补 textual/chromadb/pywebview+clr，`prompts` 入包；构建脚本改装
  `.[dev,desktop]`。PyInstaller 6.22.3 目录构建 + 二进制冒烟通过（version / exec 全链路 /
  doctor / web 启动）。litellm 经 `importlib.resources` 读取的包内 JSON（模型价格表、tokenizer
  词表、endpoints 配置）由 spec 解析期递归收集，chromadb 经 Settings 配置项动态 `import_module`
  解析的默认组件类（含 Rust 绑定扩展 `chromadb_rust_bindings`、telemetry posthog）显式入
  hiddenimports——这两类是冻结首启 ModuleNotFoundError/FileNotFoundError 的根因。
- **依赖声明修正**：`chromadb` 锁 `<1.0` 在 Python 3.13 不可安装，放宽为 `>=1.0`；`pytest-cov`
  补入 dev extras（此前 CI 的 `--cov-fail-under` 步骤装不到即失败）；新增 desktop extra
  （pywebview>=5.0，Windows 用系统 WebView2）。
- **Web 安全立场**：`spark web` 默认仅监听 127.0.0.1，非本机调用方必须携带每进程控制令牌
  （`X-Spark-Token`）；暴露局域网需显式 `--host 0.0.0.0` 并在启动时打印令牌。

### 新增
- **桌面形态 `spark desktop`**：pywebview 原生窗口壳，与 `spark web` 共用同一 HTTP 服务与
  鉴权；固定绑定回环，窗口关闭优雅停机（server shutdown → server_close → store.close）。
- **`sanitize_sse_payload`**：src 树事件协议（text/result/approval/tool/data）的浅递归脱敏，
  深度限 3 层，结构与非字符串不动。

## [0.8.0] - 2026-09-30

### 前端架构（本轮重点）
- **ES Modules 化**：14 个前端模块显式 `import/export`，单入口 `<script type="module" src="js/main.js">`；删除脚本顺序依赖与 `__DEP_REQS` eval 自检。
- **Web Components 组件层**：8 个自定义元素（消息/工具卡/计划卡/思考块/会话卡/MCP 行/记忆行/git 行），模板与行为分离，支持「先 setData 后挂载」。
- 修复 `autoGrow()` 从未定义导致 `@` 文件补全失效的潜伏 bug；修复 `window.termState` 恒 undefined 导致切会话不刷新终端组。
- 设计令牌系统 + `@layer` CSS 组织 + 原生 `<dialog>` + 毛玻璃 + 智能滚动 + 渲染节流 + 主题跟随系统。

### 后端与结构
- `loop.py` 801→353 行拆分：routing / truncation / compaction / prompt / execution 五个兄弟模块。
- `server.py` 877→308 行拆分：api_common / api_config / api_sessions / api_data 四域路由。
- 审批支持逐文件勾选（`apply_patch` 透传 files 过滤）；终端按会话分组（`(sid, tab_id)` 复合键）。
- wheel 修复前端资源缺失；`uvicorn[standard]` 依赖修复；vendor NOTICE 版权声明；启动依赖自检。

### 修复
- 进程组 SIGKILL（截断重试不残留子进程）；IO 线程化；上下文截断误判；MCP 输入双向写回。

## [0.7.0] - 2026-09-20

### 新增
- MCP 接入：stdio + Streamable HTTP 双传输，懒启动，写类工具进审批门。
- Prompt 注入防护：中英文特征正则，命中注入安全警告标记。
- 内置持久终端（pty），按会话隔离目录；Textual TUI。
- 轻量代码索引（Python ast 精确 / 其他语言行级）、检查点与写前自动快照。

### 修复
- 免登录令牌逻辑；Windows PTY 平台兼容；配置 ACL（600/icacls）。

## [0.6.0] - 2026-09-10

### 新增
- 首个可用版本：AgentLoop 事件流（计划→模型→工具→审批→执行）、会话存储、长期记忆（SQLite+FTS5）、用量成本记账、国产模型 PRESETS、子 agent（explore/general）、轻量插件点。

> AI生成