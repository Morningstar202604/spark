/* sessions.js —— 会话列表（分组 + 搜索 + 运行态/停止）、选择、新建、空状态引导 */
"use strict";

import { api, state, $, esc, shortPath, fmtTime, toast, isSidRunning, updateRunningUI, runtime } from "./core.js";
import { addUserMsg, newAssistant, autoScroll, mdToHtml, updateMeter } from "./render.js";
import { closeDrawer, openPane } from "./settings.js";
import { termState, renderTermTabs } from "./terminal.js";

async function loadSessions(silent) {
  const q = ($("#sessSearch").value || "").trim();
  try {
    const res = await api("/api/sessions" + (q ? "?q=" + encodeURIComponent(q) : ""));
    state.sessions = await res.json();
  } catch (e) { if (!silent) throw e; return; }
  renderSessions();
}

function renderSessions() {
  const box = $("#sessionList"); if (!box) return;
  const q = ($("#sessSearch").value || "").trim().toLowerCase();
  box.innerHTML = "";
  if (!state.sessions.length) {
    box.innerHTML = '<div style="color:var(--dim);font-size:13px;text-align:center;padding:20px 0">还没有会话，点上面新建</div>';
    return;
  }
  // 过滤：有后端全文结果（match）时直接用；否则本地按标题/目录
  let list = state.sessions;
  if (q && !state.sessions.some(s => s.match)) {
    list = list.filter(s => (s.title || "").toLowerCase().includes(q) || (s.workdir || "").toLowerCase().includes(q));
  }
  if (!list.length) {
    box.innerHTML = '<div style="color:var(--dim);font-size:13px;text-align:center;padding:20px 0">没有匹配「' + esc(q) + '」的会话</div>';
    return;
  }
  // 按工作目录分组（组内按 updated 倒序）
  const groups = new Map();
  for (const s of [...list].sort((a, b) => (b.updated || "").localeCompare(a.updated || ""))) {
    const wd = s.workdir || "（未指定目录）";
    if (!groups.has(wd)) groups.set(wd, []);
    groups.get(wd).push(s);
  }
  for (const [wd, sessList] of groups) {
    const g = document.createElement("div"); g.className = "sessgrp";
    g.innerHTML = '<div class="sessgrphead"><span class="gw">' + esc(shortPath(wd)) + '</span><span class="gn">' + sessList.length + '</span></div>';
    const items = document.createElement("div");
    for (const s of sessList) items.appendChild(sessCard(s));
    g.appendChild(items);
    box.appendChild(g);
  }
}

function sessCard(s) {
  const card = document.createElement("spark-session-card");
  const run = isSidRunning(s.id);
  const match = s.match;
  card.setData({
    title: s.title,
    sub: (match && match.kind === "content"
      ? "匹配：" + (match.role === "user" ? "你" : "Spark") + " · " + esc(match.snippet || "")
      : fmtTime(s.updated) + " · " + s.messages + " 条消息"),
    workdir: s.workdir || "",
    running: run,
    active: s.id === state.sid,
    actions: [
      run ? { label: "■ 停止", kind: "stop", fn: () => cancelSession(s.id) } : null,
      { label: "重命名", kind: "", fn: renameSession },
      { label: "分叉", kind: "", fn: forkSession },
      { label: "导出", kind: "", fn: exportSession },
      { label: "删除", kind: "del", fn: deleteSession },
    ].filter(Boolean),
    onSelect: () => selectSession(s.id),
  });
  return card;

  async function renameSession() {
    const title = prompt("会话新标题（留空取消）", s.title);
    if (!title) return;
    try {
      const r = await api("/api/sessions/" + s.id, { method: "PATCH", body: JSON.stringify({ title: title.trim().slice(0, 60) }) });
      if (!r.ok) { toast("重命名失败"); return; }
      toast("已重命名");
      if (state.sid === s.id) $("#sessionLine").textContent = s.workdir || "未指定目录";
      await loadSessions(true);
    } catch (err) { toast("重命名失败"); }
  }
  async function exportSession() {
    try {
      const r = await api("/api/sessions/" + s.id);
      const d = await r.json();
      let md = "# " + (d.meta.title || "会话") + "\n\n";
      for (const m of d.messages) {
        if (m.role === "user") md += "## 你\n\n" + (m.content || "") + "\n\n";
        else if (m.role === "assistant") md += "## Spark\n\n" + (m.content || "") + "\n\n";
      }
      const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (d.meta.title || "会话").replace(/[\\/:*?"<>|]/g, "_").slice(0, 40) + ".md";
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(a.href);
      toast("已导出 Markdown");
    } catch (err) { toast("导出失败"); }
  }
  async function forkSession() {
    try {
      const r = await api("/api/sessions/" + s.id + "/fork", { method: "POST" });
      if (!r.ok) { toast("分叉失败"); return; }
      const meta = await r.json();
      toast("已分叉出新会话");
      await loadSessions(true);
      selectSession(meta.id);
    } catch (err) { toast("分叉失败"); }
  }
  async function deleteSession() {
    if (!confirm("删除会话「" + s.title + "」？此操作不可恢复。")) return;
    try {
      const r = await api("/api/sessions/" + s.id, { method: "DELETE" });
      if (!r.ok) { toast("删除失败"); return; }
      toast("会话已删除");
      if (state.sid === s.id) { state.sid = null; $("#msgList").innerHTML = ""; runtime.curAssistant = null; showEmptyIfNeeded(); updateRunningUI(); }
      await loadSessions(true);
    } catch (err) { toast("删除失败"); }
  }
}

async function cancelSession(sid) {
  try {
    await api("/api/cancel", { method: "POST", body: JSON.stringify({ session_id: sid }) });
    toast("已请求停止会话");
  } catch (e) { toast("停止失败"); }
}

function showEmptyIfNeeded() {
  const e = $("#emptyState");
  const hasMessages = state.sid && $("#msgList").children.length > 0;
  // 空态 flex 垂直居中（body.has-empty）；有消息时切回流式布局
  document.body.classList.toggle("has-empty", !hasMessages);
  if (!state.sid) { e.style.display = ""; updateSetupState(); return; }
  const has = $("#msgList").children.length > 0;
  e.style.display = has ? "none" : "";
  // 有会话但无消息（如刚新建的空会话）：引导区同样展示，徽标/副标题实时刷新
  if (!has) updateSetupState();
}

async function selectSession(sid) {
  if (sid === state.sid) return;
  state.sid = sid; closeDrawer("drawerSessions");
  const res = await api("/api/sessions/" + sid);
  const data = await res.json();
  const meta = data.meta || {};
  $("#sessionLine").textContent = meta.workdir || "未指定目录";
  $("#msgList").innerHTML = ""; runtime.curAssistant = null;
  for (const m of data.messages) renderHistory(m);
  renderSessions();
  showEmptyIfNeeded();
  autoScroll();
  updateRunningUI();
  // 终端 tab 跟随会话：切换到当前会话的终端组（模块化后直接引用，不再走 window）
  if (termState.groups) renderTermTabs();
  refreshCtx();
}

/* 上下文水位：进入会话/发送完成后拉全量估算（对标主流 agent 的上下文进度条） */
async function refreshCtx() {
  if (!state.sid) return;
  try {
    const res = await api("/api/sessions/" + state.sid + "/context");
    const d = await res.json();
    updateMeter(d.used, d.max);
  } catch (e) { /* 静默：估算失败不影响使用 */ }
}

function renderHistory(m) {
  if (m.role === "user") {
    const c = m.content;
    // 图片消息：content 为 parts 数组（text + image_url），文本部分回填 + 标注图片数
    const text = Array.isArray(c) ? c.filter(p => p.type === "text").map(p => p.text).join("\n") : (c || "");
    const imgCount = Array.isArray(c) ? c.filter(p => p.type === "image_url").length : 0;
    addUserMsg(text, imgCount ? `（附 ${imgCount} 张图片）` : "", m.id);
  }
  else if (m.role === "assistant") {
    const el = newAssistant();
    if (m.id) el.setAttribute("data-mid", m.id);
    el.dataset.raw = m.content || "";
    el.querySelector(".text").innerHTML = mdToHtml(m.content || "");
  }
  // 其他 role（tool 等）暂不做重放：后端只持久化 user/assistant，思考与工具卡是流式临时渲染
}

/* 新建会话：未配置模型/工作目录时直接引导到对应设置面板，而不是弹个空设置抽屉
   返回新会话 id；未配置被引导时返回 null（调用方可据此决定是否继续发消息） */
async function newSession() {
  if (!state.cfg) { toast("配置尚未加载，稍后再试"); return null; }
  const missing = setupMissing();
  if (missing.length) {
    const label = missing.includes("workdir") ? "工作目录" : "模型配置";
    toast("请先配置" + label);
    openPane(missing.includes("workdir") ? "paneWorkspace" : "paneModel");
    return null;
  }
  const res = await api("/api/sessions", { method: "POST", body: JSON.stringify({ workdir: state.cfg.workdir }) });
  if (res.status === 400) { const e = await res.json(); toast(e.detail || "请先设置工作目录"); openPane("paneWorkspace"); return null; }
  const meta = await res.json();
  await loadSessions(true); await selectSession(meta.id);
  return meta.id;
}

/* 配置检查：返回缺失项列表（"workdir" | "model"） */
function setupMissing() {
  const c = state.cfg; if (!c) return ["workdir", "model"];
  const miss = [];
  if (!(c.workdir || "").trim()) miss.push("workdir");
  const hasModel = (c.base_url && c.model) || (c.api_key && c.model);
  if (!hasModel && !c.demo_mode) miss.push("model");
  return miss;
}

function updateSetupState() {
  // 状态徽标写进步骤卡（#stModel / #stWorkdir），替代旧的独立 #setupState 区块
  const c = state.cfg;
  const sub = $("#emptySub");
  if (!c) {
    // 配置尚未加载：徽标与副标题统一显示加载中，等待 loadConfig 完成后再刷新
    for (const id of ["stModel", "stWorkdir"]) { const el = document.getElementById(id); if (el) el.innerHTML = '<i class="ss">…</i>'; }
    if (sub) sub.textContent = "正在读取本地配置…";
    return;
  }
  const miss = setupMissing();
  const m = $("#stModel");
  if (m) {
    if (miss.includes("model")) m.innerHTML = '<i class="ss miss">✗ 未配置</i>';
    else if (c.demo_mode) m.innerHTML = '<i class="ss demo">演示模式</i>';
    else m.innerHTML = '<i class="ss ok">✓ 已配置</i>';
  }
  const w = $("#stWorkdir");
  if (w) w.innerHTML = miss.includes("workdir") ? '<i class="ss miss">✗ 未设置</i>' : '<i class="ss ok">✓ 已设置</i>';
  // 副标题展示实时配置摘要（模型名 / 演示模式 / 工作目录），不做静态文案
  if (sub) {
    const modelDesc = c.demo_mode ? "演示模式" : (c.model || "未配置模型");
    const parts = [modelDesc];
    parts.push(c.workdir ? c.workdir : "工作目录未设置");
    sub.textContent = parts.join(" · ");
  }
}

/* 「开始使用」：按缺失情况直达对应设置面板 */
function ensureStart() {
  if (!state.cfg) { toast("配置尚未加载，稍后再试"); return; }
  const miss = setupMissing();
  if (miss.includes("workdir")) { openPane("paneWorkspace"); toast("先指定工作目录（Agent 的活动范围）"); return; }
  if (miss.includes("model")) { openPane("paneModel"); toast("先选模型服务并填 Key（也可用演示模式）"); return; }
  newSession();
}

export {
  loadSessions, renderSessions, selectSession, cancelSession, showEmptyIfNeeded,
  newSession, ensureStart, renderHistory, updateSetupState, refreshCtx,
};
