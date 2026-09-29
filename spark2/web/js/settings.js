/* settings.js —— 设置抽屉（6 面板）、未保存提示、记忆/用量/检查点/插件/MCP */
"use strict";

/* ---------- 抽屉通用 ---------- */
function openDrawer(id) { $("#" + id).classList.add("open"); $("#ov" + id.replace("drawer", "")).style.display = "block"; }
function closeDrawer(id) { $("#" + id).classList.remove("open"); $("#ov" + id.replace("drawer", "")).style.display = "none"; }

/* 打开设置抽屉并激活指定面板（供首屏引导直达） */
function openPane(paneId) {
  openDrawer("drawerSettings");
  const btn = document.querySelector('.setnav button[data-pane="' + paneId + '"]');
  if (btn) {
    document.querySelectorAll(".setnav button").forEach(x => x.classList.toggle("on", x === btn));
    document.querySelectorAll(".setpane").forEach(p => p.classList.toggle("on", p.id === paneId));
    if (typeof updHelp === "function") updHelp(paneId);
  }
}

/* ---------- 未保存提示 ---------- */
function markDirty(d) {
  const st = $("#cfgStatus");
  if (!st) return;
  if (d) { st.className = "statusline dirty"; st.textContent = "有未保存修改，记得点右下角「保存设置」"; }
  else { st.className = "statusline"; st.textContent = ""; }
}

/* ---------- 配置加载 ---------- */
async function loadConfig() {
  const res = await api("/api/config");
  const data = await res.json();
  state.cfg = data.current; state.cfg.version = data.version; state.presets = data.presets; state.modes = data.approval_modes;
  const sel = $("#fProvider"); sel.innerHTML = "";
  for (const [k, v] of Object.entries(data.presets)) {
    const o = document.createElement("option"); o.value = k; o.textContent = v.label; if (k === data.current.provider) o.selected = true; sel.appendChild(o);
  }
  const am = $("#fApproval"); am.innerHTML = "";
  for (const m of data.approval_modes) { const o = document.createElement("option"); o.value = m.value; o.textContent = m.label; if (m.value === data.current.approval_mode) o.selected = true; am.appendChild(o); }
  $("#fBaseUrl").value = data.current.base_url || "";
  $("#fModel").value = data.current.model || "";
  $("#fFastModel").value = data.current.model_fast || "";
  $("#fKey").value = data.current.api_key || "";
  $("#fWorkdir").value = data.current.workdir || "";
  $("#fMaxCtx").value = data.current.max_context_tokens || 32000;
  $("#fMemoryEmbed").value = data.current.memory_embedding || "off";
  $("#fMemModel").value = data.current.memory_embed_model || "";
  const tokIn = $("#fAuthToken");
  tokIn.value = "";
  tokIn.placeholder = data.current.token_set ? "已设置令牌（输入新值可更换）" : "留空 = 本机免登录（默认）";
  $("#btnClearToken").style.display = data.current.token_set ? "" : "none";
  memModelRow();
  // 高级项回填
  $("#fSysPrompt").value = data.current.system_prompt || "";
  $("#fProtPaths").value = (data.current.protected_paths || []).join("\n");
  $("#fMaxTurns").value = data.current.max_turns || 25;
  $("#fToolTimeout").value = data.current.tool_timeout || 180;
  const tv = data.current.temperature;
  $("#fTemp").value = (tv === "" || tv == null) ? "" : tv;
  const mv = data.current.max_tokens;
  $("#fMaxTokens").value = (mv === "" || mv == null) ? "" : mv;
  $("#fRouteOn").checked = data.current.route_enabled !== false;
  $("#fRouteKw").value = data.current.route_keywords || "";
  const pr = data.current.usage_pricing || {};
  $("#fPricing").value = Object.keys(pr).length ? JSON.stringify(pr, null, 2) : "";
  $("#dirsBox").innerHTML = Object.entries(data.dirs || {}).map(([k, v]) => '<div class="drow"><span class="dk">' + esc(k) + '</span><span class="dv">' + esc(v) + '</span></div>').join("");
  // 输入区快捷审批下拉（与设置里的默认审批模式同步）
  const qa = $("#fQuickAp"); qa.innerHTML = "";
  for (const m of data.approval_modes) {
    const o = document.createElement("option"); o.value = m.value; o.textContent = m.label;
    if (m.value === data.current.approval_mode) o.selected = true;
    qa.appendChild(o);
  }
  $("#version").textContent = "v" + (data.version || "");
  state.mcpServers = (data.mcp && data.mcp.servers) || [];
  state.mcpServers.forEach(s => { s.transport = s.transport || "stdio"; s.headersJson = (s.headers && Object.keys(s.headers).length) ? JSON.stringify(s.headers) : ""; });
  renderMcp();
  updateMeter(0);
  markDirty(false);
}

function presetChanged() {
  const p = state.presets[$("#fProvider").value];
  if (p) { $("#fBaseUrl").value = p.base_url || ""; $("#fModel").value = p.model || ""; }
}
function memModelRow() {
  $("#fMemModelRow").style.display = $("#fMemoryEmbed").value === "local" ? "" : "none";
}

async function saveCfg() {
  const body = {
    provider: $("#fProvider").value,
    base_url: $("#fBaseUrl").value.trim(),
    model: $("#fModel").value.trim(),
    model_fast: $("#fFastModel").value.trim(),
    api_key: $("#fKey").value,
    workdir: $("#fWorkdir").value.trim(),
    approval_mode: $("#fApproval").value,
    max_context_tokens: parseInt($("#fMaxCtx").value, 10) || 32000,
    memory_embedding: $("#fMemoryEmbed").value,
    memory_embed_model: $("#fMemModel").value.trim(),
    mcp_servers: state.mcpServers.map(s => ({
      name: s.name, transport: s.transport || "stdio",
      command: s.command || "", args: s.args || [],
      url: s.url || "",
      headers: (s.headersJson && s.headersJson.trim()) ? safeParseHeaders(s.headersJson) : {},
    })),
    // 高级项
    system_prompt: $("#fSysPrompt").value,
    protected_paths: $("#fProtPaths").value.split("\n").map(s => s.trim()).filter(Boolean),
    max_turns: parseInt($("#fMaxTurns").value, 10) || 25,
    tool_timeout: parseInt($("#fToolTimeout").value, 10) || 180,
    temperature: $("#fTemp").value.trim(),
    max_tokens: $("#fMaxTokens").value.trim(),
    route_enabled: $("#fRouteOn").checked,
    route_keywords: $("#fRouteKw").value.trim(),
  };
  // 成本单价：留空 = 清空覆盖；非空必须是合法 JSON 对象（否则拒绝保存）
  const pt = $("#fPricing").value.trim();
  if (pt) {
    let po;
    try { po = JSON.parse(pt); } catch (e) { $("#cfgStatus").className = "statusline bad"; $("#cfgStatus").textContent = "成本单价不是合法 JSON，未保存。"; toast("成本单价 JSON 解析失败"); return; }
    if (!po || typeof po !== "object" || Array.isArray(po)) { $("#cfgStatus").className = "statusline bad"; $("#cfgStatus").textContent = "成本单价需是 JSON 对象（键 = 模型名）。"; return; }
    body.usage_pricing = po;
  } else {
    body.usage_pricing = {};
  }
  // 访问令牌：输入非空才提交（避免普通保存误清除）；清除走独立按钮
  const atok = $("#fAuthToken").value.trim();
  if (atok) body.token = atok;
  try {
    const res = await api("/api/config", { method: "POST", body: JSON.stringify(body) });
    const data = await res.json(); state.cfg = data.current;
    if (atok) {
      state.token = atok;
      localStorage.setItem("spark2_token", atok);
      $("#fAuthToken").value = "";
    }
    $("#cfgStatus").className = "statusline ok"; $("#cfgStatus").textContent = "已保存。";
    $("#fQuickAp").value = $("#fApproval").value;  // 快捷下拉与新默认值对齐
    toast("设置已保存");
    loadSessions(true);
    loadMemory();
    loadRecentDirs();
    updateSetupState();
  } catch (e) { $("#cfgStatus").className = "statusline bad"; $("#cfgStatus").textContent = "保存失败：" + (e.message || e); }
}

async function clearToken() {
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify({ token: "" }) });
    state.token = "";
    localStorage.removeItem("spark2_token");
    toast("已清除访问令牌，本机免登录");
    await loadConfig();
  } catch (e) { toast("清除失败：" + (e.message || e)); }
}

async function testConn() {
  $("#cfgStatus").className = "statusline"; $("#cfgStatus").textContent = "正在测试…";
  const body = {
    base_url: $("#fBaseUrl").value.trim(),
    model: $("#fModel").value.trim(),
    api_key: $("#fKey").value,
  };
  try {
    const res = await api("/api/test-connection", { method: "POST", body: JSON.stringify(body) });
    const d = await res.json();
    $("#cfgStatus").className = "statusline " + (d.ok ? "ok" : "bad");
    $("#cfgStatus").textContent = d.ok ? "连接成功" : d.message;
  } catch (e) { $("#cfgStatus").className = "statusline bad"; $("#cfgStatus").textContent = "请求失败：" + (e.message || e); }
}

/* ---------- 最近工作目录 ---------- */
async function loadRecentDirs() {
  const box = $("#recentDirs"); if (!box) return;
  let dirs = [];
  try { const r = await api("/api/recent-dirs"); const d = await r.json(); dirs = d.dirs || []; }
  catch (e) { return; }
  if (!dirs.length) { box.innerHTML = ""; return; }
  box.innerHTML = '<div class="hint2" style="margin-top:4px">最近使用：</div>' +
    dirs.map(d => '<button class="rd" data-d="' + esc(d) + '" title="' + esc(d) + '">' + esc(shortPath(d)) + '</button>').join("");
  box.querySelectorAll(".rd").forEach(b => b.onclick = () => { $("#fWorkdir").value = b.dataset.d; markDirty(true); });
}

/* ---------- 用量统计 ---------- */
function fmtTokens(n) { return n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); }
async function loadUsage() {
  const box = $("#usageBox");
  let data;
  try {
    const res = await api("/api/usage");
    data = await res.json();
  } catch (e) { box.innerHTML = '<div class="memempty">加载失败</div>'; return; }
  const t = data.totals || {};
  const rows = data.top_sessions || [];
  if (!t.calls) { box.innerHTML = '<div class="memempty">还没有用量记录。每轮模型调用都会统计 tokens 与估算费用（单价按 2026-09 现役官方价，可在配置里覆盖）。</div>'; return; }
  let html = '<div style="font-size:13px;line-height:1.9">' +
    '<div><b>' + fmtTokens(t.total_tokens || 0) + '</b> tokens（输入 ' + fmtTokens(t.prompt_tokens || 0) + ' / 输出 ' + fmtTokens(t.completion_tokens || 0) + '）· ' + (t.calls || 0) + ' 次调用</div>' +
    '<div>估算费用：<b style="color:var(--teal)">¥' + (t.est_cost || 0).toFixed(4) + '</b> <span class="hint2">（近 ' + (data.days || 30) + ' 天）</span></div>' +
    '</div>';
  if (rows.length) {
    const max = Math.max(...rows.map(r => r.est_cost || 0), 0.0001);
    html += '<div class="sec-divider">会话排行</div>';
    for (const r of rows) {
      const w = Math.max(4, Math.round(((r.est_cost || 0) / max) * 100));
      html += '<div style="margin:6px 0"><div style="font-size:12px;display:flex;justify-content:space-between"><span>' + esc(String(r.session_id).slice(0, 8)) + '</span><span>' + fmtTokens(r.total_tokens || 0) + ' · ¥' + (r.est_cost || 0).toFixed(4) + '</span></div>' +
        '<div class="ubar"><div style="width:' + w + '%"></div></div></div>';
    }
  }
  box.innerHTML = html;
}

/* ---------- 插件 ---------- */
async function loadPlugins() {
  const box = $("#pluginList");
  let data;
  try {
    const res = await api("/api/plugins");
    data = await res.json();
  } catch (e) { box.innerHTML = '<span>加载失败</span>'; return; }
  const items = data.plugins || [];
  if (!items.length) {
    box.innerHTML = '<span>暂无插件。在 ' + esc(data.dir || "~/.spark2/plugins") + ' 放一个 .py（导出 tools 或 register(registry)），重启生效。</span>';
    return;
  }
  box.innerHTML = items.map(p =>
    '<div class="mem"><span class="mk">' + esc(p.name) + '</span><span class="mv">' +
    (p.error ? '<span style="color:var(--red)">加载失败：' + esc(p.error) + '</span>' : '工具：' + esc((p.tools || []).join("、") || "（无）")) +
    '</span></div>'
  ).join("");
}

/* ---------- 长期记忆 ---------- */
async function loadMemory() {
  const box = $("#memoryList");
  if (!state.cfg || !state.cfg.workdir) { box.innerHTML = '<div class="memempty">先在工作目录设置里指定项目路径</div>'; return; }
  let data;
  try {
    const res = await api("/api/memory?workdir=" + encodeURIComponent(state.cfg.workdir));
    data = await res.json();
  } catch (e) { box.innerHTML = '<div class="memempty">加载失败</div>'; return; }
  if (!data.items.length) { box.innerHTML = '<div class="memempty">还没有记忆。对话里说「记住 XX 是 YY」，Agent 就会记在这里。</div>'; return; }
  box.innerHTML = "";
  for (const it of data.items) {
    const d = document.createElement("div"); d.className = "mem";
    d.innerHTML = '<span class="mk">' + esc(it.key) + '</span><span class="mv">' + esc(it.value) + '</span><span class="md">' + fmtTime(it.created_at) + '</span><button data-edit="' + it.id + '" title="修改内容">编辑</button><button data-id="' + it.id + '">删除</button>';
    box.appendChild(d);
  }
  box.querySelectorAll("button[data-id]").forEach(b => b.onclick = async () => {
    try {
      await api("/api/memory/" + b.dataset.id, { method: "DELETE" });
      toast("已删除这条记忆");
      loadMemory();
    } catch (e) { toast("删除失败"); }
  });
  box.querySelectorAll("button[data-edit]").forEach(b => b.onclick = async () => {
    const row = b.closest(".mem");
    const key = row.querySelector(".mk").textContent;
    const oldVal = row.querySelector(".mv").textContent;
    const nv = prompt("修改「" + key + "」：", oldVal);
    if (nv === null || !nv.trim() || nv.trim() === oldVal) return;
    try {
      const r = await api("/api/memory", { method: "POST", body: JSON.stringify({ workdir: state.cfg.workdir, key, value: nv.trim() }) });
      if (!r.ok) { const e = await r.json().catch(() => ({})); toast(e.detail || "修改失败"); return; }
      toast("已更新「" + key + "」");
      loadMemory();
    } catch (e) { toast("修改失败"); }
  });
}

async function addMemory() {
  if (!state.cfg || !state.cfg.workdir) { toast("先在设置里指定工作目录"); return; }
  const key = $("#fMemKey").value.trim(), value = $("#fMemVal").value.trim();
  if (!key || !value) { toast("key 与内容都要填"); return; }
  try {
    const r = await api("/api/memory", { method: "POST", body: JSON.stringify({ workdir: state.cfg.workdir, key, value }) });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast(e.detail || "保存失败"); return; }
    toast("已记住「" + key + "」");
    $("#fMemKey").value = ""; $("#fMemVal").value = "";
    loadMemory();
  } catch (e) { toast("保存失败"); }
}

/* ---------- 检查点（git） ---------- */
async function loadGit() {
  const side = $("#gitSide"), list = $("#gitList");
  let data;
  try {
    const r = await api("/api/git?sid=" + encodeURIComponent(state.sid || ""));
    data = await r.json();
  } catch (e) { side.textContent = "加载失败"; return; }
  if (!data.repo) {
    side.textContent = data.reason || "不可用";
    list.innerHTML = '<div class="memempty">工作目录不是 git 仓库，检查点未启用（在项目里执行 git init 即可开启）。Agent 写文件前的自动检查点同样依赖 git。</div>';
    return;
  }
  side.innerHTML = '<span class="badge">分支 ' + esc(data.branch) + '</span>' +
    '<span class="badge' + (data.changes ? " dirty" : "") + '">' + (data.changes ? data.changes + " 处未存档改动" : "工作区干净") + '</span>' +
    '<span>' + data.checkpoints.length + ' 个检查点</span>';
  if (!data.checkpoints.length) { list.innerHTML = '<div class="memempty">还没有检查点，点上面「存档当前」。</div>'; return; }
  list.innerHTML = "";
  data.checkpoints.forEach((c, i) => {
    const d = document.createElement("div"); d.className = "gitrow";
    d.innerHTML = '<span class="gh">' + esc(c.hash) + '</span><span class="gt">' + esc(c.time) + '</span><span class="gm">' + esc(c.message) + '</span>' +
      (i === 0 ? '<button class="del" title="回滚到该存档">回滚</button>' : "");
    list.appendChild(d);
  });
  list.querySelectorAll("button.del").forEach(b => b.onclick = () => doGitReset());
}
async function doCheckpoint() {
  if (!state.sid) { toast("先新建或选择会话"); return; }
  try {
    const r = await api("/api/git/checkpoint", { method: "POST", body: JSON.stringify({ sid: state.sid, message: $("#fCpMsg").value }) });
    const d = await r.json();
    if (!r.ok) { toast(d.detail || "存档失败"); return; }
    toast("已存档"); $("#fCpMsg").value = ""; loadGit();
  } catch (e) { toast("存档失败"); }
}
async function doGitReset() {
  if (!confirm("回滚到最近一次存档？未存档的改动将丢失（不可撤销）。")) return;
  try {
    const r = await api("/api/git/reset", { method: "POST", body: JSON.stringify({ sid: state.sid || "", confirm: "yes" }) });
    const d = await r.json();
    if (!r.ok) { toast(d.detail || "回滚失败"); return; }
    toast("已回滚到最近存档"); loadGit();
  } catch (e) { toast("回滚失败"); }
}

/* ---------- MCP 服务器管理 ---------- */
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

/* 设置面板帮助（右侧列） */
const SETTINGS_HELP = {
  paneModel: ["模型", "选择模型服务、填接口与密钥；下方采样参数控制回答风格。开启多模型路由后，简单问答自动走快速模型，动手任务始终走主模型。"],
  paneWorkspace: ["工作区", "Agent 的活动范围与安全边界：工作目录之外的写入、命令执行都会先征求你确认（自动模式除外），受保护路径任何模式都不许碰。"],
  paneMemory: ["记忆", "跨会话的长期记忆，按工作目录隔离——换项目不串记忆。关键词检索零依赖；语义检索需配置向量服务或本地嵌入模型。"],
  paneIntegrations: ["集成", "给 Agent 扩展工具：MCP 只读工具自动放行、写类进审批门；插件放本地目录重启生效；代码索引帮它摸清项目符号。"],
  paneAdvanced: ["高级", "本机访问令牌、Agent 人设与模型成本单价。令牌留空即本机免登录（服务仅监听 127.0.0.1）。"],
  paneAbout: ["关于", "版本与数据目录。全部数据在本地，仅你配置的模型服务发生网络调用。"],
};
function helpStat(pane) {
  const sel = id => { const e = $(id); return e && e.selectedIndex >= 0 ? e.options[e.selectedIndex].textContent : "—"; };
  const val = id => ($(id).value || "").trim();
  let rows = [];
  if (pane === "paneModel") rows = [["服务", sel("#fProvider")], ["模型", val("#fModel") || "—"], ["快速模型", val("#fFastModel") || "未启用"], ["路由", $("#fRouteOn").checked ? "已开启" : "关闭"]];
  else if (pane === "paneWorkspace") rows = [["目录", val("#fWorkdir") || "—"], ["审批", sel("#fApproval")], ["轮次上限", val("#fMaxTurns") || "25"], ["工具超时", (val("#fToolTimeout") || "180") + "s"]];
  else if (pane === "paneMemory") rows = [["检索方式", sel("#fMemoryEmbed")], ["嵌入模型", val("#fMemModel") || "—"]];
  else if (pane === "paneIntegrations") rows = [["MCP 服务器", String(document.querySelectorAll("#mcpList .mcprow").length)], ["插件", String(document.querySelectorAll("#pluginList .mem").length)]];
  else if (pane === "paneAdvanced") rows = [["访问令牌", $("#btnClearToken").style.display !== "none" ? "已设置" : "未设置（免登录）"], ["系统提示词", val("#fSysPrompt") ? "已自定义" : "内置默认"]];
  else if (pane === "paneAbout") rows = [["版本", $("#aboutVer").textContent || "—"]];
  $("#helpStat").innerHTML = rows.map(r => '<div class="r"><span class="k">' + r[0] + '</span><span class="v">' + String(r[1]).replace(/&/g, "&amp;").replace(/</g, "&lt;") + '</span></div>').join("");
}
function updHelp(pane) {
  const h = SETTINGS_HELP[pane]; if (!h) return;
  $("#helpTitle").textContent = h[0];
  $("#helpDesc").textContent = h[1];
  helpStat(pane);
  $("#helpField").hidden = true;
}
