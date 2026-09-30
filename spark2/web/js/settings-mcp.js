/* settings-mcp.js —— MCP 服务器管理面板：列表 / 增删 / 双传输 */
"use strict";

import { $, state } from "./core.js";
import { markDirty } from "./settings.js";

function renderMcp() {
  const box = $("#mcpList"); box.innerHTML = "";
  state.mcpServers.forEach((s, i) => {
    const d = document.createElement("spark-mcp-row");
    d.setServer(s, i);
    d.addEventListener("spark-mcp:remove", e => {
      state.mcpServers.splice(e.detail.idx, 1);
      renderMcp();
    });
    box.appendChild(d);
  });
}
function addMcp() {
  state.mcpServers.push({ name: "", command: "", args: [], env: {}, transport: "stdio", url: "", headersJson: "" });
  renderMcp();
  const rows = $("#mcpList").querySelectorAll("spark-mcp-row");
  const last = rows[rows.length - 1];
  if (last) last.querySelector("input[data-k=name]").focus();
  markDirty(true);
}

export { renderMcp, addMcp };

/* ---------- 官方 MCP 市场：常用 server 一键安装（对标主流 agent 的集成市场） ---------- */
const MARKET = [
  { id: "filesystem", name: "Filesystem", desc: "读写本地文件系统（安装后路径参数可改，默认当前目录）", cmd: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem", "."], transport: "stdio" },
  { id: "fetch", name: "Fetch", desc: "抓取网页并转成干净的 Markdown，Agent 可读任意 URL", cmd: "npx", args: ["-y", "@modelcontextprotocol/server-fetch"], transport: "stdio" },
  { id: "memory", name: "Memory", desc: "知识图谱记忆：让 Agent 跨会话记住实体与关系", cmd: "npx", args: ["-y", "@modelcontextprotocol/server-memory"], transport: "stdio" },
  { id: "sequential-thinking", name: "Sequential Thinking", desc: "逐步思考：复杂问题分步推理，结果更稳", cmd: "npx", args: ["-y", "@modelcontextprotocol/server-sequential-thinking"], transport: "stdio" },
  { id: "git", name: "Git", desc: "Git 仓库操作：提交、分支、日志（写操作走审批）", cmd: "npx", args: ["-y", "@modelcontextprotocol/server-git"], transport: "stdio" },
  { id: "time", name: "Time", desc: "查询当前时间与时区，AI 感知真实时间", cmd: "npx", args: ["-y", "@modelcontextprotocol/server-time"], transport: "stdio" },
  { id: "brave-search", name: "Brave Search", desc: "联网搜索（需 BRAVE_API_KEY 环境变量）", cmd: "npx", args: ["-y", "@modelcontextprotocol/server-brave-search"], transport: "stdio" },
  { id: "everything", name: "Everything", desc: "官方全能力示例 server，测试各类型工具", cmd: "npx", args: ["-y", "@modelcontextprotocol/server-everything"], transport: "stdio" },
];

export function renderMarket() {
  const box = $("#mcpMarket"); if (!box) return;
  box.innerHTML = "";
  MARKET.forEach(m => {
    const installed = state.mcpServers.some(s => s.name === m.name.toLowerCase());
    const cell = document.createElement("div");
    cell.className = "mcpcard";
    const info = document.createElement("div");
    info.className = "mcpcard-info";
    const t = document.createElement("div");
    t.className = "mcpcard-name";
    t.textContent = m.name;
    const d = document.createElement("div");
    d.className = "mcpcard-desc";
    d.textContent = m.desc;
    info.append(t, d);
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn " + (installed ? "ghost" : "");
    b.textContent = installed ? "已安装" : "安装";
    b.disabled = installed;
    b.onclick = () => {
      if (installed) return;
      state.mcpServers.push({
        name: m.name.toLowerCase(), command: m.cmd, args: [...m.args],
        env: {}, transport: m.transport, url: "", headersJson: "",
      });
      renderMcp();
      renderMarket();
      markDirty(true);
      const rows = $("#mcpList").querySelectorAll("spark-mcp-row");
      const last = rows[rows.length - 1];
      if (last) last.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    cell.append(info, b);
    box.appendChild(cell);
  });
}
