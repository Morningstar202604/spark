/* cmd.js —— 命令面板（Ctrl/Cmd+K）：对标主流 AI 助手/IDE 的快速命令入口。
   轻量实现：overlay + 模糊过滤 + ↑↓ 键盘导航，零外部依赖。 */

"use strict";

import { $, state, applyTheme, esc } from "./core.js";
import { openDrawer, openPane } from "./settings.js";
import { newSession } from "./sessions.js";

const PANES = [
  ["paneModel", "模型", "服务 · 密钥 · 采样"],
  ["paneWorkspace", "工作区", "目录 · 审批 · 边界"],
  ["paneMemory", "记忆", "跨会话记住的事"],
  ["paneIntegrations", "集成", "MCP · 插件 · 索引"],
  ["paneAdvanced", "高级", "令牌 · 提示词 · 成本"],
];

export function initCmdPalette() {
  const box = $("#palette");
  const input = $("#paletteInput");
  const list = $("#paletteList");
  if (!box || !input || !list) return;

  let items = [];
  let sel = 0;

  function build() {
    const cur = document.documentElement.dataset.theme === "dark" ? "浅色" : "深色";
    const amode = (state.cfg && state.cfg.approval_mode) || "suggest";
    const items0 = [
      { g: "会话", label: "新建会话", desc: "开始一段新的对话", icon: "＋", key: "N", run: () => newSession() },
      { g: "会话", label: "打开会话列表", desc: "浏览 / 搜索历史会话", icon: "☰", run: () => openDrawer("drawerSessions") },
      { g: "会话", label: "清空当前会话视图", desc: "仅清空消息区显示，记录仍保留", icon: "✕", run: () => { $("#msgList").innerHTML = ""; } },
      { g: "设置", label: "设置 · 模型", desc: "服务 / 密钥 / 采样", icon: "⚙", run: () => { openDrawer("drawerSettings"); openPane("paneModel"); } },
      { g: "设置", label: "设置 · 工作区", desc: "目录 / 审批 / 边界", icon: "⚙", run: () => { openDrawer("drawerSettings"); openPane("paneWorkspace"); } },
      { g: "设置", label: "设置 · 记忆", desc: "跨会话记住的事", icon: "⚙", run: () => { openDrawer("drawerSettings"); openPane("paneMemory"); } },
      { g: "设置", label: "设置 · 集成", desc: "MCP / 插件 / 索引", icon: "⚙", run: () => { openDrawer("drawerSettings"); openPane("paneIntegrations"); } },
      { g: "设置", label: "设置 · 高级", desc: "令牌 / 提示词 / 成本", icon: "⚙", run: () => { openDrawer("drawerSettings"); openPane("paneAdvanced"); } },
      { g: "外观", label: "切换主题", desc: `当前 ${cur}，点击切换`, icon: "◐", key: "T", run: () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark") },
      { g: "模式", label: "审批模式 · 询问", desc: "写入与命令都要确认", icon: "✓", run: () => setApproval("suggest") },
      { g: "模式", label: "审批模式 · 自动编辑", desc: "工作区内写入不询问，命令询问", icon: "✓", run: () => setApproval("auto-edit") },
      { g: "模式", label: "审批模式 · 全自动", desc: "都不询问，谨慎使用", icon: "✓", run: () => setApproval("full-auto") },
      { g: "模式", label: "审批模式 · 只读分析（Plan）", desc: "禁用写入与命令", icon: "✓", run: () => setApproval("plan") },
      { g: "帮助", label: "打开终端", desc: "随当前会话目录的内置终端", icon: "▤", run: () => { document.getElementById("btnTerm").click(); } },
    ];
    items = items0.map(it => ({ ...it, g: it.g }));
    // 当前模式高亮
    items.forEach(it => { if (it.label.includes(`· ${labelOf(amode)}`)) it.cur = true; });
  }

  function labelOf(mode) {
    return { suggest: "询问", "auto-edit": "自动编辑", "full-auto": "全自动", plan: "只读分析（Plan）" }[mode] || mode;
  }

  function setApproval(mode) {
    const q = $("#fQuickAp");
    if (q && [...q.options].some(o => o.value === mode)) { q.value = mode; q.dispatchEvent(new Event("change", { bubbles: true })); }
  }

  function render(filter) {
    const f = (filter || "").toLowerCase().trim();
    const shown = items.filter(it =>
      !f || (it.label + it.desc + it.g).toLowerCase().includes(f)
    );
    if (!shown.length) {
      list.innerHTML = '<div class="palette-empty">没有匹配的命令</div>';
      return;
    }
    sel = Math.max(0, Math.min(sel, shown.length - 1));
    list.innerHTML = "";
    let curGroup = null;
    shown.forEach((it, i) => {
      // 分组标题（会话 / 设置 / 外观 / 模式 / 帮助），对标 Raycast / VS Code 命令面板
      if (it.g !== curGroup) {
        curGroup = it.g;
        const g = document.createElement("div");
        g.className = "palette-group";
        g.textContent = curGroup;
        list.appendChild(g);
      }
      const b = document.createElement("button");
      b.className = "palette-item" + (i === sel ? " sel" : "");
      b.innerHTML = '<span class="pi">' + esc(it.icon) + '</span><span class="pt"><b>' + esc(it.label) + "</b><i>" + esc(it.desc) + "</i></span>" + (it.key ? '<span class="pk">' + it.key + "</span>" : "");
      b.addEventListener("mousedown", ev => { ev.preventDefault(); close(); it.run(); });
      b.addEventListener("mouseenter", () => { sel = i; refresh(); });
      list.appendChild(b);
    });
    list._shown = shown;
  }

  function refresh() { render(input.value); }

  function open() {
    build();
    box.classList.add("open");
    input.value = "";
    sel = 0;
    render("");
    setTimeout(() => input.focus(), 20);
  }

  function close() {
    box.classList.remove("open");
  }

  function onKey(ev) {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
      ev.preventDefault();
      if (box.classList.contains("open")) close(); else open();
      return;
    }
    if (!box.classList.contains("open")) return;
    const shown = list._shown || [];
    if (ev.key === "Escape") { ev.preventDefault(); close(); return; }
    if (ev.key === "ArrowDown") { ev.preventDefault(); sel = Math.min(sel + 1, shown.length - 1); refresh(); return; }
    if (ev.key === "ArrowUp") { ev.preventDefault(); sel = Math.max(sel - 1, 0); refresh(); return; }
    if (ev.key === "Enter") {
      ev.preventDefault();
      const it = shown[sel];
      if (it) { close(); it.run(); }
      return;
    }
  }

  box.addEventListener("mousedown", ev => { if (ev.target === box) close(); });
  input.addEventListener("input", () => { sel = 0; render(input.value); });
  document.addEventListener("keydown", onKey);
}
