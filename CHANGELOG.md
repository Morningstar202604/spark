# Changelog

本项目遵循语义化版本。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [Unreleased]

### 变更
- **包名统一为 `spark`（破坏性变更，无兼容回退）**：Python 包目录、命令入口、配置/数据目录彻底统一为 `spark`——配置目录改为 `~/.spark`、环境变量改为 `SPARK_HOME`，前端 localStorage 键 / 会话导出目录 / 插件模块名同步改 `spark_*`；命令行入口统一为 `python -m spark` / `spark`。旧命名路径与旧环境变量不再读取。

### 新增
- **Web 默认监听 0.0.0.0**：`spark web` 默认 host 由 `127.0.0.1` 改为 `0.0.0.0`，外部预览/代理可直接访问；打印地址自动用 `127.0.0.1` 便于本机打开；本机使用可显式传 `--host 127.0.0.1`。
- **uvicorn 日志级别可调**：`spark web` 新增 `--log-level`（默认 info），线上排障不必再翻无输出的 warning 日志。
- **pip 过旧提示**：README 与 pyproject.toml 注释补充"pip < 23.2 装不上 fastapi/tomlkit"的提示（先 `python -m pip install --upgrade pip`）。

### 修复
- **Web 终端 WS 404**：`uvicorn[standard]` 缺 websockets 时终端面板无法建立连接，README 已说明补装方式。
- **多 agent 并行探索（Agent Teams 最小版）**：`explore_parallel` 工具并发派出多个只读子 agent 探索不同目录/主题并合并结果，大仓库理解速度质变（对标 Claude Code Agent Teams）。
- **多模态识图**：前端支持粘贴/拖拽图片（≤3 张、单张 ≤2MB），模型支持时以 `image_url` 消息送入；不支持的模型自动降级为纯文本。
- **改完自动验证**：`apply_patch` 成功后自动运行受影响测试（`pytest -q`，120s 超时）并回填结果；无测试项目自动跳过；可在设置「高级」关闭。
- 文件树治理：新增 `CHANGELOG.md`、`Makefile`、`[tool.ruff]` 配置；`pyproject.toml` 文档改中英双语；移除 `docs/` 官网静态站与 GitHub Pages CI（重资产移出仓库）。
- **只读分析模式（Plan）**：审批模式新增 `plan` 档，对标 Claude Code Plan Mode——写文件/执行命令一律拒绝（优先于会话内始终允许），Agent 只分析不动手；设置与主界面快捷下拉均可切换。
- **HTTP 代理配置**：模型面板新增代理地址（如 `http://127.0.0.1:7890`），国内网络连境外模型服务刚需；留空仍尊重系统环境变量。
- **环境变量 API Key**：配置未填密钥时自动回退 `SPARK_API_KEY` → `<provider>_API_KEY`（DeepSeek/通义/智谱/Kimi/豆包/Ollama），环境变量密钥不会写盘固化。
- **项目级指令文件**：工作目录存在 `CLAUDE.md` 时自动追加为最高优先级系统提示词（对标 CLAUDE.md / .cursorrules），≤64KB、零依赖。

### 前端设计与交互（本轮重点，对标主流 AI 助手/Agent 设计语言）
- **命令面板（Ctrl/Cmd+K）**：新建会话 / 设置各面板 / 切换主题 / 审批模式四档 / 清空视图，模糊过滤 + ↑↓ 键盘导航 + Esc 关闭。
- **顶栏图标化**：终端/检查点/用量/会话/设置全部换 SVG 图标，观感对齐主流工具。
- **空态升级**：新增「建议问题」chips（点击即建会话并发送），引导更符合主流 AI 助手范式。
- **输入区现代化**：白底圆角容器 + 添加图片按钮（文件选择/粘贴/拖拽三入口）+ 圆形渐变发送按钮；运行中自动变「停止」按钮（点击即取消）。
- **消息流细节**：助手消息加角色行 + hover 操作栏（复制 / 移除本条）；代码块头部加「复制代码」按钮（事件委托，流式渲染后仍生效）。
- 背景色柔化（`--bg` 微调）、设置导航选中态加左侧强调条、触屏端操作按钮常显。

### 设计打磨（照片诊断）
- 空态建议 chips 补齐 6 个（宽屏 3+3 对齐，不再单行孤立）。
- 命令面板加分栏标题（会话 / 设置 / 外观 / 模式 / 帮助），对标 Raycast / VS Code。
- 设置导航 6 项补 SVG 图标 + 选中态主色，与顶栏图标化一致。
- 底部快捷键提示改为 kbd 键帽样式，文案精简。
- 「去设置」按钮加箭头与 hover 底纹，精致化。

### 对标补齐 7 项差距（web 工具 / fallback / 消息编辑重发 / 全文搜索 / 上下文水位）
- **联网工具**：`web_search` + `read_url`（只读、免审批），Agent 可获取最新文档/报错/API 变更。
- **备用模型故障切换**：主模型不可用（限流/故障/配置错误）自动切 `fallback_model`（设置·模型面板可配），对标 Claude Code fallback model。
- **消息编辑并重发**：用户消息操作栏加「编辑」→ 载入原文、截断会话、修改重发；删除消息改为持久生效（刷新不复活）。
- **会话全文搜索**：搜索框支持标题/目录/消息正文全文命中，卡片显示匹配片段（防抖 300ms）。
- **上下文水位**：会话页显示已用/上限进度条 + 数字（`GET /api/sessions/{sid}/context`），超窗预警。
- 修复：mock 演示模式下 `demo_mode` 缺失导致空态引导无法新建会话；`truncate` 截断到首条误判"消息不存在"。

### 语音输入 + MCP 市场（7 项差距最后 2 项）
- **语音输入**：输入栏麦克风按钮（Web Speech API，中文听写，追加进输入框；不支持浏览器自动隐藏、录音中红色脉冲动画）。
- **官方 MCP 市场**：集成面板加 8 个官方 server 一键安装（filesystem/fetch/memory/sequential-thinking/git/time/brave-search/everything），安装即进 MCP 列表、按钮变「已安装」，保存生效。
- 修复：`tests/test_fallback.py` 三个测试 fixture 漏传（monkeypatch）——全量 213 passed / ruff 全过。

### 云知声 u2-flash 真模型全功能实测（36 项全通过）
- 新增 **unisound 预设**（https://maas-api.unisound.com/v1 · u2-flash · UNISOUND_API_KEY 环境变量）。
- 实测覆盖：流式对话/多轮上下文、代码工具链（write_file/read_file/list_dir 落盘验证）、
  只读自动放行（suggest）、web_search/read_url 联网、会话 CRUD/重命名/分叉/truncate/
  持久删除/全文搜索/上下文水位、记忆读写、用量统计、插件列表、代码索引（index_project+
  search_symbol）、fallback 故障切换。
- **修复 3 处真 bug（fallback 链路三连断）**：`set_config` 保存字段漏 fallback_model
  （前端填了保存即丢）→ 已补保存 + 回显；`server.py` 构造 provider_cfg 漏传
  fallback_model（HTTP 层永不切换）→ 已补；fallback_model 空串被通用字段逻辑跳过
  （无法取消）→ 已单独支持显式清空。
- **修复密钥打码写回 bug**：`mask_key` 输出含前缀/后缀（sk-6*********22g），旧判定只
  拦全星号，前端全量保存会把脱敏值当真 key 写回损坏 → 新增 `is_masked_key`（8 连星
  或全星号即拦）。
- 新增回归测试：test_api_config（fallback 保存/清空 + masked key 不覆盖）、test_config。

### 修复
- 首屏引导「去设置」死按钮：补绑事件（打开设置抽屉 + 定位对应面板）。
- 集成面板 MCP 长命令横向溢出：toolrow 改 flex-wrap + code overflow-wrap。
- 内置终端 xterm 不随视口适配：创建即 fit + resize 事件同步前端尺寸（此前默认 80 列在窄屏溢出）。
- 多视口走查（1920→390 六档）：主页面/全部抽屉/审批 modal 溢出 0、console 零错误。

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