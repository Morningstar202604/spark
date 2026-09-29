/* sessions.js —— 会话列表（分组 + 搜索 + 运行态/停止）、选择、新建、空状态引导 */
"use strict";

async function loadSessions(silent) {
  try {
    const res = await api("/api/sessions"); state.sessions = await res.json();
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
  // 过滤
  let list = state.sessions;
  if (q) list = list.filter(s => (s.title || "").toLowerCase().includes(q) || (s.workdir || "").toLowerCase().includes(q));
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
  const b = document.createElement("div"); b.className = "sess" + (s.id === state.sid ? " active" : "");
  const run = isSidRunning(s.id);
  const stopBtn = run
    ? '<button class="mini stop" data-stop="' + esc(s.id) + '" title="停止该会话">■ 停止</button>'
    : "";
  b.innerHTML =
    '<div class="t">' + esc(s.title) + (run ? ' <span class="rind" title="正在运行">● 运行中</span>' : "") + '</div>' +
    '<div class="s">' + fmtTime(s.updated) + ' · ' + s.messages + " 条消息</div>" +
    '<div class="w">' + esc(s.workdir || "") + '</div>' +
    '<div class="srow">' + stopBtn +
    '<button class="mini" data-act="rename">重命名</button><button class="mini" data-act="fork">分叉</button>' +
    '<button class="mini" data-act="export">导出</button><button class="del" data-del="1">删除</button></div>';
  b.querySelector(".t").onclick = () => selectSession(s.id);
  b.querySelector("[data-stop]").onclick = e => { e.stopPropagation(); cancelSession(s.id); };
  b.querySelector("[data-act=rename]").onclick = async () => {
    const title = prompt("会话新标题（留空取消）", s.title);
    if (!title) return;
    try {
      const r = await api("/api/sessions/" + s.id, { method: "PATCH", body: JSON.stringify({ title: title.trim().slice(0, 60) }) });
      if (!r.ok) { toast("重命名失败"); return; }
      toast("已重命名");
      if (state.sid === s.id) $("#sessionLine").textContent = title.trim().slice(0, 60) + " · " + (s.workdir || "");
      await loadSessions(true);
    } catch (err) { toast("重命名失败"); }
  };
  b.querySelector("[data-act=export]").onclick = async () => {
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
  };
  b.querySelector("[data-act=fork]").onclick = async () => {
    try {
      const r = await api("/api/sessions/" + s.id + "/fork", { method: "POST" });
      if (!r.ok) { toast("分叉失败"); return; }
      const meta = await r.json();
      toast("已分叉出新会话");
      await loadSessions(true);
      selectSession(meta.id);
    } catch (err) { toast("分叉失败"); }
  };
  b.querySelector(".del").onclick = async e => {
    e.stopPropagation();
    if (!confirm("删除会话「" + s.title + "」？此操作不可恢复。")) return;
    try {
      const r = await api("/api/sessions/" + s.id, { method: "DELETE" });
      if (!r.ok) { toast("删除失败"); return; }
      toast("会话已删除");
      if (state.sid === s.id) { state.sid = null; $("#msgList").innerHTML = ""; curAssistant = null; showEmptyIfNeeded(); updateRunningUI(); }
      await loadSessions(true);
    } catch (err) { toast("删除失败"); }
  };
  return b;
}

async function cancelSession(sid) {
  try {
    await api("/api/cancel", { method: "POST", body: JSON.stringify({ session_id: sid }) });
    toast("已请求停止会话");
  } catch (e) { toast("停止失败"); }
}

function showEmptyIfNeeded() {
  const e = $("#emptyState");
  if (!state.sid) { e.style.display = ""; updateSetupState(); return; }
  const has = $("#msgList").children.length > 0;
  e.style.display = has ? "none" : "";
}

async function selectSession(sid) {
  state.sid = sid; closeDrawer("drawerSessions");
  const res = await api("/api/sessions/" + sid);
  const data = await res.json();
  const meta = data.meta || {};
  $("#sessionLine").textContent = (meta.title || "会话") + " · " + (meta.workdir || "");
  $("#msgList").innerHTML = ""; curAssistant = null;
  for (const m of data.messages) renderHistory(m);
  renderSessions();
  showEmptyIfNeeded();
  autoScroll();
  updateRunningUI();
  // 终端 tab 跟随会话：切换到当前会话的终端组
  if (window.termState && termState.groups) renderTermTabs();
}

function renderHistory(m) {
  if (m.role === "user") { addUserMsg(m.content || ""); }
  else if (m.role === "assistant") { const el = newAssistant(); el.querySelector(".text").textContent = m.content || ""; }
}

/* 新建会话：未配置模型/工作目录时直接引导到对应设置面板，而不是弹个空设置抽屉 */
async function newSession() {
  if (!state.cfg) { toast("配置尚未加载，稍后再试"); return; }
  const missing = setupMissing();
  if (missing.length) {
    const label = missing.includes("workdir") ? "工作目录" : "模型配置";
    toast("请先配置" + label);
    openPane(missing.includes("workdir") ? "paneWorkspace" : "paneModel");
    return;
  }
  const res = await api("/api/sessions", { method: "POST", body: JSON.stringify({ workdir: state.cfg.workdir }) });
  if (res.status === 400) { const e = await res.json(); toast(e.detail || "请先设置工作目录"); openPane("paneWorkspace"); return; }
  const meta = await res.json();
  await loadSessions(true); await selectSession(meta.id);
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
  const box = $("#setupState"); if (!box) return;
  const miss = setupMissing();
  let html = "";
  html += miss.includes("model") ? '<span class="miss">✗ 模型未配置</span>' : '<span class="ok">✓ 模型已配置</span>';
  html += miss.includes("workdir") ? '<span class="miss">✗ 工作目录未设置</span>' : '<span class="ok">✓ 工作目录已设置</span>';
  box.innerHTML = html;
}

/* 「开始使用」：按缺失情况直达对应设置面板 */
function ensureStart() {
  if (!state.cfg) { toast("配置尚未加载，稍后再试"); return; }
  const miss = setupMissing();
  if (miss.includes("workdir")) { openPane("paneWorkspace"); toast("先指定工作目录（Agent 的活动范围）"); return; }
  if (miss.includes("model")) { openPane("paneModel"); toast("先选模型服务并填 Key（也可用演示模式）"); return; }
  newSession();
}
