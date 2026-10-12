# 双树收敛路线图（2026-10-09）

> 裁决依据见 `docs/decisions/LOG.md` 2026-10-09 三条决策。
> 结论：**src/spark 是唯一主干**（pip 发行、pytest 实测、最新提交三方一致），
> 根目录 spark/ 是待退役的平行旧实现。本文档是退役前的迁移与验收清单。

## 1. 为什么是 src 树

| 证据 | 指向 |
| --- | --- |
| `pyproject.toml` packages.find `where=["src"]` | pip 安装的是 src |
| `pyproject.toml` pytest `pythonpath=["src"]` | pytest 实测的是 src |
| 最新功能提交 b8db2ea（i18n/skills/权限规则/前端重构） | 开发方向在 src |
| 根树 CI 静态检查（已修正指向 src） | 历史误指，已纠正 |

## 2. 本次已完成

- `src/spark/core/loop.py:96` hooks 哑弹修复（`self._hooks` → `self.hooks`）
- CI（`.github/workflows/ci.yml`）与 Makefile 的 ruff/mypy/bandit 目标改为 `src/spark`
- 桌面形态：`src/spark/desktop.py` + `spark desktop` 命令 + `pyproject.toml` desktop extra
  （`serve_web` 构建段抽出为 `build_web_server`，行为兼容）
- 品牌收敛：删除 v0.9.0 遗留 `README.zh.md`；es/ja 语言链接指向 `README_zh.md`；
  `docs/INSTALL.md`（中英）仓库地址 x33834 → badhope

## 3. 回迁清单（根树 → src，按优先级）

执行方式：以根树文件为参考实现，按 src 树的模块约定重写接入
（registry/ToolContext 协议、pydantic config、policy 决策层），不是复制粘贴。

| 优先级 | 能力 | 根树参考文件 | 说明 |
| --- | --- | --- | --- |
| P0 | 出口脱敏 sanitize | `spark/sanitize.py` | SSE/错误出口统一脱敏，安全特性，接入点在 web server 事件出口 |
| P0 | 审批门决策层对齐 | `spark/approval.py` + `src/spark/policy.py` | 对比两套审批语义，合并为单一决策层 |
| P0 | 模型路由/容灾 | `spark/routing.py`、`provider.py` 的 fallback | 对齐 README 宣传的 fast/fallback 路由 |
| P1 | 子 Agent | `spark/subagent.py`、`tools/subagent.py` | explore 只读 / general 过审批；src 树当前缺失 |
| P1 | 代码索引 | `spark/codeindex.py`、`tools/codeindex_tools.py` | ast 符号索引，全只读 |
| P1 | 持久终端 PTY | `spark/pty.py`、`tools/terminal.py` | xterm.js 面板依赖 WebSocket，src web 是手写 HTTP，需一并评估 |
| P1 | 斜杠命令 | `spark/slash.py` | 前端已引用 `/api/slash-commands` |
| P2 | 插件/能力点 | `spark/plugins.py`、`capabilities.py` | `~/.spark/plugins/` 动态工具 |
| P2 | 用量成本面板 | `spark/usage.py` | JSONL 落账 + 会话排行 |
| P2 | trace/truncation/shell_env/recent_dirs | 同名文件 | 按需小型迁移 |
| P2 | office/knowledge/report 工具 | `spark/tools/office.py` 等 | 领域工具，按需求取舍 |

## 4. 根树退役前置条件（全部满足才可 `git rm -r spark/`）

| # | 条件 | 状态（2026-10-10 实测） |
| --- | --- | --- |
| 1 | 第 3 节 P0 项全部迁入 src 并有测试 | ✅ sanitize（`src/spark/sanitize.py` + `tests/test_sanitize.py` 13 例，修复空 secrets 时跳过主目录/堆栈脱敏的缺口）；审批决策层（`src/spark/policy.py` + test_wildcard_permission/test_config_acl 等）；容灾由 LiteLLM retry/fallbacks 承接（README 未宣传旧路由，无需对齐） |
| 2 | `pytest -q` 全绿 | ✅ 362 passed / 2 skipped（另见第 6 节） |
| 3 | `ruff check src/spark tests`、`mypy src/spark`、`bandit -r src/spark` 清零 | ✅ ruff 0；mypy 0（48 文件）；bandit -ll -ii 退出码 0（HIGH/MED 0，43 LOW 为既定放行项） |
| 4 | 前端 `web/` 构建产物路径与 `src/spark/web/` 打包验证通过 | ✅ wheel 实测含 `spark/web/dist/*`（assets 齐）+ `index.html` 兜底；`ui_dir_for()` 指向包内 dist |
| 5 | 根树专属测试文件（约 16 个，引用根树 FastAPI 版 web/loop/provider）迁移或合并 | ✅ 可迁移的已改适配 src 并随主测试跑（test_store_fork/test_msg_ops/test_recent_dirs 等）；纯根树能力的归档至 `tests/legacy_root/`（conftest `collect_ignore_glob`），作为 P1 回迁规格 |
| 6 | `spark.spec` / `spark-onefile.spec` 改为打包 src 树并实测 | ✅ 两 spec 改为 `src/spark/__main__.py` 入口、清理根树幽灵 hiddenimports（uvicorn/api_*/subagent/codeindex/plugins/tomlkit/docx 等）、补 textual+chromadb+pywebview/clr；PyInstaller 6.22.3 构建 + 二进制冒烟见第 6 节。冻结首启两类断点：litellm 经 `importlib.resources` 读包内 JSON（模型价格表/tokenizer 词表/endpoints）→ spec 解析期递归收集全部 JSON+tokenizers 包；chromadb 经 Settings 配置项动态 `import_module` 解析组件类（默认 `chromadb.api.rust.RustBindingsAPI` + telemetry posthog + sqlite/executor 等默认实现）→ 显式 hiddenimports（含 63MB `chromadb_rust_bindings.pyd` 随依赖自动入包） |
| 7 | `pytest --cov=spark --cov-fail-under=65`（CI 门禁） | ✅ TOTAL 72%；`pytest-cov` 补进 dev extras（原 CI 装不到会直接失败） |

根树退役动作（`git rm -r spark/`、删 `README.zh.md`）已在工作树 staged，等本次收尾提交时一并落地。

## 5. 桌面 + Web 双形态

同一后端，两种入口：

- `spark web` — 浏览器访问（`--host 127.0.0.1` 默认，暴露局域网需令牌）
- `spark desktop` — pywebview 原生窗口，固定回环，窗口关闭即优雅停机
  （`server.shutdown()` → `server_close()` → `store.close()`）
- 打包：PyInstaller spec 中引入 `pywebview` 钩子；Windows 分发零额外运行时
  （Edge WebView2 系统自带）

## 6. 测试基线（2026-10-10 实测，Windows / Python 3.13.14 venv）

依赖：fastapi、uvicorn[standard]、langgraph、litellm、textual、pywebview、
llama-index-core、chromadb 1.5.9（**pyproject 原锁 `<1.0` 无法在 Py3.13 安装，
已放宽为 `>=1.0`**，与 REFACTOR-REPORT 声明一致）。

- **362 passed / 2 skipped**（覆盖率 TOTAL 72%，CI 门禁 65%）
- `ruff check src/spark tests` 0 错误；`mypy src/spark --ignore-missing-imports` 0 错误（48 文件）
- `bandit -r src/spark -ll -ii -c bandit.yaml` 退出码 0（HIGH/MED 0）
- wheel 实测：`spark/web/dist/*`、`index.html`、`prompts/*` 均入包；装入干净 venv 后
  `spark version` 与 `ui_dir_for()`（指向包内 dist）通过
- Web 端到端冒烟（mock provider）：静态页 / status / sessions / config / models /
  memory / checkpoints / agents_md / chat（含 list_dir 工具回合 tool_start+tool_end）/
  chat/stream（SSE 帧）/ sessions/new / history 持久化全部通过
- CLI 冒烟：`spark exec "list files" --provider mock --approval full-auto` 完成两回合；
  `spark doctor` 正常输出体检（无 API Key 时报 bad 并给修复建议，退出码 1 属预期）
- 冻结二进制冒烟（PyInstaller 6.22.3，`spark.spec` 目录构建）：`spark.exe version` /
  `spark.exe exec`（mock 全链路：litellm+chromadb+llama-index+langgraph）/ `spark.exe doctor`
  全部通过； litellm 资源数据（model_prices/tokenizers/endpoints JSON）由 spec 递归收集，
  空 tokenizers 包（仅数据）单独入 hiddenimports

2026-10-09 旧基线（331 passed / 10 failed / 8 errors / 1 skipped）的失败全部源于
根树能力缺失，随本轮回迁与适配清零。

CI 从未运行的问题依旧：门禁写在 GitHub Actions，仓库在 GitCode 托管。

