# Changelog

本项目遵循语义化版本。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [Unreleased]

### 新增
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
