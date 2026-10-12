# Changelog

本项目遵循语义化版本。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

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