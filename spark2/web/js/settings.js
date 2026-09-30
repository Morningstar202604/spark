/* settings.js —— 设置抽屉公共层（模型 / 工作区 / 高级 / 关于 4 面板在此）。
   记忆/用量/插件/检查点/MCP 面板在 settings-memory / settings-usage /
   settings-plugins / settings-git / settings-mcp。 */
"use strict";

import { $, api, state, esc, shortPath, toast, safeParseHeaders } from "./core.js";
import { updateMeter } from "./render.js";
import { loadSessions, updateSetupState } from "./sessions.js";
import { renderMcp } from "./settings-mcp.js";
import { loadMemory } from "./settings-memory.js";

/* ---------- 抽屉通用 ---------- */
function _overlay(id) { return document.querySelector('.overlay[data-for="' + id + '"]'); }
function openDrawer(id) { $("#" + id).classList.add("open"); const ov = _overlay(id); if (ov) ov.style.display = "block"; }
function closeDrawer(id) { $("#" + id).classList.remove("open"); const ov = _overlay(id); if (ov) ov.style.display = "none"; }

/* 打开设置抽屉并激活指定面板（供首屏引导直达） */
function openPane(paneId) {
  openDrawer("drawerSettings");
  const btn = document.querySelector('.setnav button[data-pane="' + paneId + '"]');
  if (btn) {
    document.querySelectorAll(".setnav button").forEach(x => x.classList.toggle("on", x === btn));
    document.querySelectorAll(".setpane").forEach(p => p.classList.toggle("on", p.id === paneId));
    updHelp(paneId);
  }
}

/* ---------- 未保存提示 ---------- */
function markDirty(d) {
  const st = $("#cfgStatus");
  if (!st) return;
  if (d) { st.className = "statusline dirty"; st.textContent = "有未保存修改，记得点右下角「保存设置」"; }
  else { st.className = "statusline"; st.textContent = ""; }
}

/* ---------- 配置加载（模型 / 工作区 / 高级面板字段 + 全局状态） ---------- */
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
  $("#fAutoVerify").checked = data.current.auto_verify !== false;
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
    auto_verify: $("#fAutoVerify").checked,
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

/* ---------- 最近工作目录（模型面板内快捷选择） ---------- */
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
export {
  openDrawer, closeDrawer, openPane, markDirty, loadConfig, saveCfg, testConn,
  clearToken, loadRecentDirs, presetChanged, memModelRow, updHelp,
};
