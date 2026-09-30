/* settings-mcp.js —— MCP 服务器管理面板：列表 / 增删 / 双传输（settings.js 拆分） */
"use strict";

function renderMcp() {
  const box = $("#mcpList"); box.innerHTML = "";
  state.mcpServers.forEach((s, i) => {
    s.transport = s.transport || "stdio";
    const http = s.transport === "http";
    const d = document.createElement("div"); d.className = "mcprow" + (http ? " http" : "");
    d.innerHTML =
      '<div class="mcphead">' +
        '<input data-k="name" placeholder="名称，如 filesystem" value="' + esc(s.name || "") + '">' +
        '<select data-k="transport">' +
          '<option value="stdio"' + (!http ? " selected" : "") + '>本地进程</option>' +
          '<option value="http"' + (http ? " selected" : "") + '>远程 HTTP</option>' +
        '</select>' +
        '<button class="del" data-del="' + i + '" title="删除">✕</button>' +
      '</div>' +
      '<div class="mcplines" data-k="stdioLines"' + (http ? ' style="display:none"' : "") + '>' +
        '<input data-k="command" placeholder="命令，如 npx / python" value="' + esc(s.command || "") + '">' +
        '<input data-k="args" placeholder="参数，空格分隔，如 -y @modelcontextprotocol/server-filesystem /path" value="' + esc((s.args || []).join(" ")) + '">' +
      '</div>' +
      '<div class="mcplines" data-k="httpLines"' + (!http ? ' style="display:none"' : "") + '>' +
        '<input data-k="url" placeholder="https://mcp.example.com/mcp" value="' + esc(s.url || "") + '">' +
        '<input data-k="headers" placeholder="请求头 JSON，如 {\"Authorization\":\"Bearer xxx\"}" value="' + esc(s.headersJson || "") + '">' +
      '</div>' +
      '<div class="mcpdesc">' + (http ? "Streamable HTTP · 2026 无状态协议" : "本地子进程 · stdio") + '</div>';
    d.querySelectorAll("input[data-k], select[data-k]").forEach(inp => {
      const apply = () => {
        const k = inp.dataset.k;
        if (k === "args") s.args = inp.value.trim() ? inp.value.trim().split(/\s+/) : [];
        else if (k === "headers") s.headersJson = inp.value;
        else s[k] = inp.value.trim();
        markDirty(true);
      };
      // input 用 oninput；select 用 onchange（select 不触发 input 事件）
      if (inp.tagName === "SELECT") {
        inp.onchange = () => {
          s.transport = inp.value;
          renderMcp();
          const rows = box.querySelectorAll(".mcprow");
          const cur = rows[i];
          if (cur) cur.querySelector("input[data-k=" + (s.transport === "http" ? "url" : "command") + "]").focus();
          markDirty(true);
        };
      } else {
        inp.oninput = apply;
      }
    });
    d.querySelector(".del").onclick = () => { state.mcpServers.splice(i, 1); renderMcp(); markDirty(true); };
    box.appendChild(d);
  });
}
function addMcp() {
  state.mcpServers.push({ name: "", command: "", args: [], env: {}, transport: "stdio", url: "", headersJson: "" });
  renderMcp();
  const rows = $("#mcpList").querySelectorAll(".mcprow");
  const last = rows[rows.length - 1];
  if (last) last.querySelector("input[data-k=name]").focus();
  markDirty(true);
}
