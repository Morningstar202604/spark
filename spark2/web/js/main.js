/* main.js —— 事件绑定、顶栏菜单、@ 文件补全、快捷键、初始化 */
"use strict";

/* ---------- @ 文件补全（GET /api/fs，工作目录内只读浏览） ---------- */
const atState = { items: [], sel: 0, dirPart: "", start: 0 };
let atSeq = 0;
function closeAt() { const p = $("#atPop"); p.classList.remove("open"); p.innerHTML = ""; atState.items = []; }
function atCheck() {
  const ta = $("#input");
  const before = ta.value.slice(0, ta.selectionStart);
  const m = before.match(/(?:^|\s)@([A-Za-z0-9_\-./\\]*)$/);
  if (!m || m[1].includes("..")) { closeAt(); return; }
  const token = m[1];
  const slash = Math.max(token.lastIndexOf("/"), token.lastIndexOf("\\"));
  atState.dirPart = slash >= 0 ? token.slice(0, slash + 1) : "";
  const namePart = slash >= 0 ? token.slice(slash + 1) : token;
  atState.start = before.length - 1 - token.length;
  atFetch(atState.dirPart, namePart);
}
async function atFetch(dirPart, namePart) {
  const seq = ++atSeq;
  let data;
  try {
    const r = await api("/api/fs?sid=" + encodeURIComponent(state.sid || "") + "&path=" + encodeURIComponent(dirPart));
    if (!r.ok) { closeAt(); return; }
    data = await r.json();
  } catch (e) { closeAt(); return; }
  if (seq !== atSeq) return;
  const np = namePart.toLowerCase();
  const items = (data.entries || []).filter(e => e.name.toLowerCase().startsWith(np)).slice(0, 10);
  if (!items.length) { closeAt(); return; }
  atState.items = items; atState.sel = 0;
  renderAt();
}
function renderAt() {
  const pop = $("#atPop");
  pop.innerHTML = atState.items.map((it, i) =>
    '<div class="atitem' + (i === atState.sel ? " sel" : "") + '" data-i="' + i + '"><span class="atn">' + esc(it.name) + '</span><span class="att">' + (it.dir ? "目录" : "文件") + '</span></div>'
  ).join("");
  pop.classList.add("open");
  pop.querySelectorAll(".atitem").forEach(el => {
    el.addEventListener("mousedown", ev => { ev.preventDefault(); atState.sel = +el.dataset.i; atPick(); });
  });
}
function atMove(d) {
  const n = atState.items.length; if (!n) return;
  atState.sel = (atState.sel + d + n) % n;
  renderAt();
}
function atKey(e) {
  if (e.key === "ArrowDown") { e.preventDefault(); atMove(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); atMove(-1); }
  else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); atPick(); }
  else if (e.key === "Escape") { e.preventDefault(); closeAt(); }
  // 其余按键照常输入，input 事件会重新触发 atCheck
}
function atPick() {
  const it = atState.items[atState.sel]; if (!it) { closeAt(); return; }
  const ta = $("#input");
  const start = atState.start;
  const ins = "@" + atState.dirPart + it.name + (it.dir ? "/" : "");
  ta.value = ta.value.slice(0, start) + ins + ta.value.slice(ta.selectionStart);
  ta.setSelectionRange(start + ins.length, start + ins.length);
  ta.focus();
  if (it.dir) atCheck(); else closeAt();
  autoGrow();
}

/* ---------- 顶栏 ⋯ 菜单 ---------- */
function toggleMenu() { $("#tbMenu").classList.toggle("open"); }
function closeMenu() { $("#tbMenu").classList.remove("open"); }

/* ---------- 事件绑定 ---------- */
function bind() {
  bindScrollStick();
  $("#btnSend").onclick = send;
  $("#input").addEventListener("keydown", e => {
    // @ 补全弹层打开时优先消费方向键/回车/Esc
    if ($("#atPop").classList.contains("open")) { atKey(e); return; }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  $("#input").addEventListener("input", () => { autoGrow(); atCheck(); });
  $("#btnCancel").onclick = () => cancelSession(state.sid);

  /* 会话抽屉 */
  $("#btnSessions").onclick = () => { openDrawer("drawerSessions"); loadSessions(true).catch(() => {}); };
  $("#sessSearch").addEventListener("input", renderSessions);

  /* 用量 / 检查点 */
  $("#btnUsage").onclick = () => { openDrawer("drawerUsage"); loadUsage(); };
  $("#btnGit").onclick = () => { openDrawer("drawerGit"); loadGit(); };
  $("#btnGitRefresh").onclick = () => loadGit();
  $("#btnCheckpoint").onclick = () => doCheckpoint();
  $("#btnGitReset").onclick = () => doGitReset();
  $("#ovGit").onclick = () => closeDrawer("drawerGit");

  /* 记忆 */
  $("#btnAddMem").onclick = () => addMemory();

  /* 设置面板 */
  document.querySelectorAll(".setnav button").forEach(b => b.onclick = () => {
    document.querySelectorAll(".setnav button").forEach(x => x.classList.toggle("on", x === b));
    document.querySelectorAll(".setpane").forEach(p => p.classList.toggle("on", p.id === b.dataset.pane));
    updHelp(b.dataset.pane);
  });
  document.querySelector(".setpanes").addEventListener("focusin", e => {
    const f = e.target.closest(".field"); if (!f) return;
    const lab = f.querySelector("label"); if (!lab) return;
    $("#helpFieldTitle").textContent = lab.textContent.replace(/\s+/g, " ").trim();
    const hint = f.querySelector(".hint2");
    $("#helpFieldDesc").textContent = hint ? hint.textContent.replace(/\s+/g, " ").trim() : "改动后点右下角「保存设置」生效。";
    $("#helpField").hidden = false;
  });
  // 设置字段改动 → 未保存提示
  $$(".setpanes input, .setpanes select, .setpanes textarea").forEach(el => {
    el.addEventListener("input", () => markDirty(true));
    el.addEventListener("change", () => markDirty(true));
  });
  initTermDrag();
  $("#btnSettings").onclick = () => {
    openDrawer("drawerSettings");
    const v = (state.cfg && state.cfg.version) || "";
    $("#aboutVer").textContent = v ? "v" + v : "";
    const act = () => document.querySelector(".setnav button.on");
    if (act()) updHelp(act().dataset.pane);
    Promise.all([loadMemory(), loadPlugins(), loadRecentDirs()]).then(() => { if (act()) updHelp(act().dataset.pane); });
  };
  $("#btnTheme").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  $("#btnTerm").onclick = toggleTerm;
  $("#btnTermAdd").onclick = addTermTab;
  $("#btnNewSession").onclick = newSession;
  document.querySelectorAll(".drawer .iconbtn[data-close]").forEach(b => b.onclick = () => closeDrawer(b.dataset.close));
  $("#ovSessions").onclick = () => closeDrawer("drawerSessions");
  $("#ovSettings").onclick = () => closeDrawer("drawerSettings");
  $("#ovUsage").onclick = () => closeDrawer("drawerUsage");

  /* 审批 */
  $("#btnAllow").onclick = () => answerApproval("allow");
  $("#btnDeny").onclick = () => answerApproval("deny");
  $("#btnDenyX").onclick = () => answerApproval("deny");
  $("#btnAlways").onclick = () => answerApproval("always");

  /* 顶栏 ⋯ 菜单 */
  $("#btnMenu").onclick = e => { e.stopPropagation(); toggleMenu(); };
  document.addEventListener("click", e => { if (!e.target.closest(".menuwrap")) closeMenu(); });
  $("#mNewSession").onclick = () => { closeMenu(); newSession(); };
  $("#mTerm").onclick = () => { closeMenu(); toggleTerm(); };
  $("#mGit").onclick = () => { closeMenu(); $("#btnGit").click(); };
  $("#mUsage").onclick = () => { closeMenu(); $("#btnUsage").click(); };
  $("#mSessions").onclick = () => { closeMenu(); $("#btnSessions").click(); };
  $("#mSettings").onclick = () => { closeMenu(); $("#btnSettings").click(); };
  $("#mTheme").onclick = () => { closeMenu(); $("#btnTheme").click(); };

  /* 其他 */
  $("#btnAddMcp").onclick = addMcp;
  $("#btnStart").onclick = ensureStart;
  $("#fProvider").onchange = () => { presetChanged(); markDirty(true); };
  $("#fMemoryEmbed").onchange = () => { memModelRow(); markDirty(true); };
  $("#btnTest").onclick = testConn;
  $("#btnSaveCfg").onclick = saveCfg;
  $("#btnTokenOk").onclick = () => {
    const t = $("#fToken").value.trim(); if (!t) return;
    state.token = t; localStorage.setItem("spark2_token", t);
    closeModal("tokenModal");
    init(true);
  };
  $("#btnClearToken").onclick = clearToken;

  document.addEventListener("keydown", e => {
    // Esc 关闭任何打开的抽屉/菜单（弹窗由原生 dialog 自处理，不干预）
    if (e.key === "Escape" && !isModalOpen("approvalModal") && !isModalOpen("tokenModal")) {
      $$(".drawer.open").forEach(d => closeDrawer(d.id));
      closeMenu(); closeAt();
      return;
    }
    if (isModalOpen("approvalModal")) {
      const k = e.key.toLowerCase();
      if (k === "a") { e.preventDefault(); answerApproval("allow"); }
      else if (k === "d") { e.preventDefault(); answerApproval("deny"); }
      else if (k === "s") { e.preventDefault(); answerApproval("always"); }
      else if (e.key === "Escape") { e.preventDefault(); answerApproval("deny"); } // Esc = 拒绝（与终端版一致）
      return;
    }
    if (e.key === "Escape") { closeDrawer("drawerSessions"); closeDrawer("drawerSettings"); closeDrawer("drawerGit"); return; }
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
      const k = e.key.toLowerCase();
      if (k === "n") { e.preventDefault(); newSession(); }
      else if (k === "s") { e.preventDefault(); openDrawer("drawerSessions"); }
    }
  });
}

/* ---------- 初始化 ---------- */
/* 启动依赖自检：模块按序加载（core→render→sessions→sse→approval→settings→terminal→main），
   顺序错/缺文件时这里直接给出明确报错，而不是运行到一半白屏。
   注意：state/termState 等是顶层 const（不挂 window），函数才挂 window；
   统一用 typeof eval(fn) 沿作用域链检查（fn 来自下方硬编码白名单，无注入面）。 */
const __DEP_REQS = {
  "core": ["state", "api", "esc", "toast", "$", "$$", "applyTheme", "initTheme", "openModal", "updateRunningUI"],
  "components": ["SparkMsg", "SparkToolCard", "SparkSessionCard", "SparkMcpRow", "SparkMemRow", "SparkGitRow"],
  "render": ["mdToHtml", "addUserMsg", "newAssistant", "errorMsg", "showEmptyIfNeeded", "autoScroll", "bindScrollStick"],
  "sessions": ["loadSessions", "selectSession", "renderHistory", "newSession", "showEmptyIfNeeded"],
  "sse": ["send", "handleEvent"],
  "approval": ["openApproval", "selectedFiles", "updateAllowBtn"],
  "settings": ["loadConfig", "openDrawer", "closeDrawer", "markDirty", "loadMemory", "loadRecentDirs", "renderMcp", "loadUsage", "loadGit", "doCheckpoint", "doGitReset", "loadPlugins", "addMemory", "addMcp"],
  "terminal": ["toggleTerm", "addTermTab", "closeTermTab", "renderTermTabs", "termState"],
  "main": ["bind", "init"],
};
(function depsSelfCheck() {
  const missing = [];
  for (const [mod, fns] of Object.entries(__DEP_REQS)) {
    for (const fn of fns) {
      let ok = true;
      try { if (typeof eval(fn) === "undefined") ok = false; } catch (e) { ok = false; }
      if (!ok) missing.push(mod + "." + fn);
    }
  }
  if (missing.length) {
    console.error("[spark] 前端模块加载缺失：", missing.join(", "));
    const bar = document.getElementById("sessionLine");
    if (bar) bar.textContent = "前端加载异常：" + missing.length + " 个依赖缺失（见控制台），请检查 js 加载顺序";
  }
})();

async function init(force) {
  initTheme();
  try { await loadConfig(); } catch (e) { if (e.message === "auth") return; toast("配置加载失败"); }
  try { await loadSessions(); } catch {}
  if (state.sessions.length && !state.sid) selectSession(state.sessions[0].id).catch(() => {});
  if (!state.sessions.length) {
    $("#sessionLine").textContent = "还没有会话";
    showEmptyIfNeeded();
  }
  updateRunningUI();
}

bind();
init();
