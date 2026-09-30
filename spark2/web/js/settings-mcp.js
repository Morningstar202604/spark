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
