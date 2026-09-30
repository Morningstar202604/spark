# Spark 架构说明

## 顶层结构

```
spark2/
├── config.py          # 配置层：TOML 读写、国产模型预设、密钥打码（mask_key/is_masked_key）
├── loop.py            # 主循环：计划→工具→审批→执行→总结，多模型路由 + fallback 切换
├── compaction.py      # 上下文管理：超窗把旧消息折叠成摘要（不是硬删）
├── provider.py        # OpenAI 兼容客户端：流式调用 /chat/completions（httpx，零额外依赖）
├── approval.py        # 审批门：写文件/命令按模式（suggest/auto-edit/full-auto/plan）放行
├── executor.py        # 工具执行器：逐工具执行 + 超时 + 取消
├── store.py           # 会话存储：JSONL 消息、标题、全文搜索、truncate/delete
├── memory.py          # 跨会话记忆：关键词/API/本地三种检索
├── usage.py           # 用量与成本统计
├── tools/             # 工具注册表：fs(读/写/改/搜索)、web(联网)、索引、MCP、子 agent
├── web/               # Web 层：FastAPI + 静态前端
│   ├── server.py      #   路由主装配、SSE 流、PTY 终端
│   ├── api_*.py       #   config / sessions / data / approval 等分组路由
│   ├── index.html     #   单页前端（原生 Web Components + ES Modules，16 模块）
│   └── js|css/        #   前端模块：core/render/sse/sessions/settings*/components/mic...
└── tui/               # 终端 TUI（可选）
```

## 数据流（一次对话）

```
用户输入 → server.py 建 SSE 流 → loop.py
  ├─ 计划阶段：多 agent explore（并行只读调研）→ 产出计划
  ├─ 执行阶段：模型调用（stream_chat）→ 工具调用 → 审批门 → executor 执行 → 结果回喂
  ├─ 上下文管理：超窗 compact_messages 折叠摘要
  └─ 验证闭环：apply_patch 后自动跑测试（可关）
事件经 SSE 推送前端：hello → reasoning/text/tool_start/approval/tool_result → done
```

## 安全模型

- **审批门**：写入与命令按 `approval_mode` 分级（只读自动放行；写类询问/自动/全自动）
- **Prompt Injection 防护**：文件内容/搜索结果按不可信内容处理
- **密钥保护**：`mask_key` 打码回显；`is_masked_key` 拦截打码值写回（防覆盖真 key）
- **保护路径**：`protected_paths` 下永远拒绝写入
- **本地优先**：记忆、会话、配置全在本地目录，无隐性云端调用

## 前端（原生、无构建）

- ES Modules：`main.js` 单入口，16 个模块显式依赖
- Web Components：`components.js` 模板（消息/会话卡片/MCP 行等）
- 设计令牌：CSS 变量（`--accent`/`--bg-*`/`--sp-*`），亮/暗主题跟随系统
- 能力清单：命令面板 Ctrl+K、消息编辑重发、语音输入（Web Speech）、
  上下文水位、全文搜索、MCP 市场一键安装、终端（PTY/WS）、多模态识图
```
