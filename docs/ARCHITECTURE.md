# Spark 架构说明

> English: [docs/en/ARCHITECTURE.md](en/ARCHITECTURE.md)

## 顶层结构

```
spark/
├── config.py          # 配置层：tomlkit 读写、国产模型预设 PRESETS、密钥打码（mask_key/is_masked_key）、随机访问令牌、密钥落盘 chmod 600
├── loop.py            # AgentLoop 主循环：单 asyncio 事件流，计划→工具→审批→执行→总结，多轮驱动并向 SSE 吐事件
├── execution.py       # 工具执行层（ToolExecutor）：审批裁决、写盘前 git 检查点、逐工具超时强杀、取消、子 Agent 事件嵌入（原 loop._execute_call 拆出）
├── routing.py         # 模型路由（纯函数）：命中强任务关键词走主模型，否则切快速模型；可关（route_enabled）/自定义关键词（route_keywords）
├── compaction.py      # 上下文压缩（纯函数）：超窗把旧消息折叠成摘要 + 清理孤儿 tool 消息（strip_orphans）
├── prompt.py          # 系统提示词与记忆上下文模板常量（SYSTEM_PROMPT_TEMPLATE 等）
├── truncation.py      # 输出截断判定（纯函数）：识别被长度上限截断的回复并触发分批重试
├── provider.py        # OpenAI 兼容客户端：流式 /chat/completions（httpx，零额外依赖）+ mock + token 估算
├── approval.py        # 审批门 ApprovalGate：按模式（suggest/auto-edit/full-auto/plan）放行/询问/拒绝，会话内“始终允许”记入 always 集合
├── store.py           # 会话存储：每会话一个 JSONL + 元信息 JSON，搜索/截断/分叉/删除
├── memory.py          # 跨会话长期记忆：SQLite + FTS5，按工作目录隔离；可选语义嵌入（off/api/local），显式 remember/forget
├── usage.py           # 用量与成本统计：JSONL 追加、内置单价（可覆盖）
├── slash.py           # 斜杠命令：把以 / 开头的输入展开为预设完整 prompt（list_commands/expand_slash）
├── sanitize.py        # 输出门禁：SSE 出口/对外错误统一脱敏（密钥、主目录路径、堆栈），模型侧不脱敏
├── plugins.py         # 轻量插件点：importlib 动态加载 ~/.spark/plugins/*.py，注册工具照常过审批门
├── subagent.py        # 子 Agent：explore（只读调查）/general（通用执行）；禁止递归派生，共用审批门与取消信号
├── codeindex.py       # 轻量代码索引 + 语法诊断（Python 用 ast，其它语言行级正则；.py 用 py_compile）
├── patch_apply.py     # 统一 diff 解析/校验/应用（apply_patch 底层，多文件、越界或受保护路径整体拒绝）
├── pty.py             # 内置持久终端：每 tab 一个 shell 子进程，双后端（POSIX ptyprocess / Windows pywinpty）
├── recent_dirs.py     # 最近工作目录列表（最多 8 条，存 ~/.spark/recent_dirs.json）
├── tui.py             # 终端 TUI（可选，单模块）
├── cli.py / __main__.py  # 命令行入口
├── tools/             # 工具注册表
│   ├── base.py        #   Tool 基座（name/description/JSON Schema/category/handler/preview）+ ToolContext；category = read/write/shell/system
│   ├── __init__.py    #   build_registry：汇总全部工具为 name→Tool，并给出 explore 子 Agent 的只读名单（_READONLY_NAMES）
│   ├── fs.py          #   read_file / write_file / list_dir / glob / search（rg）
│   ├── patch.py       #   apply_patch：一次应用多文件 unified diff（按文件预览审批）
│   ├── shell.py       #   run_shell：异步子进程、进程组治理、超时强杀、输出截断
│   ├── terminal.py    #   pty_run：交互式持久终端（多轮 send_input）
│   ├── web.py         #   web_search / read_url（只读联网）
│   ├── git.py         #   检查点 checkpoint / reset（用系统 git）
│   ├── plan.py        #   update_plan（system 类，拦截为 plan 事件）
│   ├── memory_tools.py#   remember / forget / memory_search
│   ├── knowledge.py   #   kb_add / kb_search（零依赖本地知识库）
│   ├── codeindex_tools.py # index_project / search_symbol / lint_file
│   ├── office.py      #   docx / xlsx / pdf 读写（可选依赖，缺失时明确提示）
│   ├── report.py      #   gen_report：自包含 HTML 报表（内联 SVG）
│   ├── session.py     #   会话导出为 Markdown / JSON
│   ├── subagent.py    #   spawn_subagent 入口（loop 特判嵌入事件）
│   ├── mcp.py         #   官方 MCP SDK，stdio / Streamable HTTP 双传输，动态注册为普通工具
│   └── injection.py   #   Prompt Injection 防护：把工具/文件返回内容标记为不可信数据
└── web/               # Web 层：FastAPI 后端 + React 前端
    ├── server.py      #   create_app 装配、SSE 对话流、审批/取消、WS 终端、GET / SPA + 静态兜底挂载
    ├── api_common.py  #   AppState、鉴权 check_token、config_payload、sse/json_body 等辅助
    ├── api_config.py  #   配置 / 测试连接 / 最近目录 / 用量 / 插件 / 斜杠命令
    ├── api_sessions.py#   会话 CRUD / 分叉 / 上下文水位 / 截断 / 删消息 / 导出
    ├── api_data.py    #   记忆 / 文件浏览 / git 检查点
    ├── src/           #   React 前端源码（main.tsx/App.tsx/components/lib/state.tsx/types.ts/styles）
    └── dist/          #   Vite 构建产物（提交进仓库），后端优先从此处伺服静态资源
```

## 数据流（一次对话）

```
用户输入 → server.py 建 SSE 流 → loop.py（AgentLoop.stream）
  ├─ 计划阶段：explore 子 Agent 并行只读调研 → update_plan 产出计划（plan 事件）
  ├─ 执行阶段：模型调用（stream_chat）→ 工具调用 → 审批门（approval.py）→ execution.py 执行 → 结果回喂
  ├─ 上下文管理：超窗由 compaction.py 折叠摘要；truncation.py 处理被截断回复
  └─ 验证闭环：apply_patch 成功后可自动跑受影响测试（auto_verify，可关）
事件经 SSE 推送前端：hello → status/plan/reasoning/text/tool_start/approval/tool_result/usage → done（含 assistant_msg_id）→ close
```

## 安全模型

- **审批门**（`approval.py`）：写入与命令按 `approval_mode` 分级（suggest 全问 / auto-edit 工作区内写不问命令问 / full-auto 都不问 / plan 只读）
- **逐文件审批**：`apply_patch` 在 `execution.py` 内可按文件粒度放行（前端审批回传 `files` 列表过滤）
- **Prompt Injection 防护**（`tools/injection.py`）：文件内容/搜索结果按不可信数据标记处理
- **密钥保护**：`mask_key` 打码回显；`is_masked_key` 拦截打码值写回（防覆盖真 key）
- **输出门禁**（`sanitize.py`）：SSE 出口与对外错误统一脱敏（密钥、主目录、堆栈），模型内部保留原始信息
- **保护路径**：`protected_paths` 下及工作区外写入一律拒绝
- **本地优先**：记忆、会话、配置全在本地目录，无隐性云端调用（语义嵌入需显式开启）

## 前端（React + Vite，构建后伺服）

- 技术栈：React 18 + TypeScript + Vite 5 + Tailwind CSS + Radix UI（对话框/下拉/浮层/标签页/提示等）+ lucide-react 图标
- 源码：`spark/web/src/**.tsx`
  - `main.tsx` 入口 / `App.tsx` 根组件 / `state.tsx` 全局状态
  - `components/`：ChatView、MessageList、Sidebar、SettingsDrawer、WelcomeView、ui（基础组件）
  - `lib/`：api（HTTP/SSE 客户端）、markdown、utils
  - `types.ts` 类型、`styles/global.css` 全局样式（Tailwind + 设计令牌）
- 构建：`npm run build` 产出 `spark/web/dist/`（含哈希资源的 index.html），该目录**提交进 git**；后端 `STATIC_DIR` 优先指向 dist，缺失时回落到 `spark/web/`（旧版单文件 `index.html` 保留作兜底）
- 能力：命令面板 Ctrl+K、消息编辑重发、语音输入（Web Speech）、上下文水位、全文搜索、MCP 市场一键安装、终端（PTY/WS）、多模态识图

> AI生成
