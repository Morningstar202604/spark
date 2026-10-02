/* main.js —— 入口：事件绑定、顶栏菜单、@ 文件补全、快捷键、初始化
   以 ES Module 形式被 index.html 唯一引用；依赖图保证各模块先于本文件求值。 */
"use strict";

/* 组件注册副作用（import 即执行 customElements.define，必须在其他模块之前） */
import "./components.js";

import { $, $$, state, esc, api, toast, applyTheme, initTheme, openModal, closeModal, isModalOpen, updateRunningUI, isSidRunning } from "./core.js";
import { bindScrollStick, autoGrow } from "./render.js";
import { loadSessions, renderSessions, newSession, selectSession, cancelSession, showEmptyIfNeeded, ensureStart } from "./sessions.js";
import { send } from "./sse.js";
import { answerApproval } from "./approval.js";
import { openDrawer, closeDrawer, openPane, markDirty, loadConfig, loadRecentDirs, saveCfg, testConn, clearToken, presetChanged, memModelRow, updHelp } from "./settings.js";
import { addMcp } from "./settings-mcp.js";
import { loadMemory, addMemory } from "./settings-memory.js";
import { loadGit, doCheckpoint, doGitReset } from "./settings-git.js";
import { loadPlugins } from "./settings-plugins.js";
import { loadUsage } from "./settings-usage.js";
import { toggleTerm, addTermTab, initTermDrag } from "./terminal.js";
import { initInputImg } from "./inputimg.js";
import { initMic } from "./mic.js";
import { initCmdPalette } from "./cmd.js";
import { initMdActions } from "./render.js";

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
function toggleMenu() {
  const open = !$("#tbMenu").classList.contains("open");
  $("#tbMenu").classList.toggle("open", open);
  setMenuLock(open);
}
function closeMenu() { $("#tbMenu").classList.remove("open"); setMenuLock(false); }
// 菜单展开时锁定消息区滚动（用户点击菜单项 / 菜单外区域 / Esc 均解锁）
function setMenuLock(on) {
  const chat = $("#chat");
  if (!chat) return;
  chat.style.overflow = on ? "hidden" : "";
}

/* ---------- 事件绑定 ---------- */
function bind() {
  bindScrollStick();
  initInputImg();
  initMic();
  initCmdPalette();
  initMdActions();
  $("#btnSend").onclick = () => { if (isSidRunning(state.sid)) cancelSession(state.sid); else send(); };
  // 消息被手动移除后刷新空态
  $("#msgList").addEventListener("spark:msg-removed", showEmptyIfNeeded);
  // 编辑并重发：截断会话（删除该消息及之后）→ 原文载入输入框 → 修改后发送
  $("#msgList").addEventListener("spark:msg-edit", async (e) => {
    const { mid, text } = e.detail || {};
    if (!mid || !state.sid) return;
    try {
      const r = await api(`/api/sessions/${state.sid}/truncate`, {
        method: "POST", body: JSON.stringify({ message_id: mid }),
      });
      if (!r.ok) { const d = await r.json().catch(() => ({})); toast(d.detail || "编辑失败"); return; }
      const ta = $("#input");
      ta.value = text || "";
      autoGrow();
      ta.focus();
      // 本地移除该消息及之后的所有元素（消息卡 + 工具卡 + 计划卡 + 思考块）
      let hit = false;
      for (const el of [...$$("#msgList > *")]) {
        if (hit || el.getAttribute("data-mid") === mid) { hit = true; el.remove(); }
      }
      showEmptyIfNeeded();
      toast("已载入，可修改后重新发送");
    } catch (err) { toast("编辑失败"); }
  });
  // 空态建议问题 chips：点击直接填充并发送（配置缺失时 newSession 返回 null，只引导设置）
  $$(".chip").forEach(c => {
    c.addEventListener("click", () => {
      const q = c.dataset.q || c.textContent;
      newSession().then(id => {
        if (!id) return;
        const ta = $("#input");
        ta.value = q;
        autoGrow();
        send();
      });
    });
  });
  $("#input").addEventListener("keydown", e => {
    // @ 补全弹层打开时优先消费方向键/回车/Esc
    if ($("#atPop").classList.contains("open")) { atKey(e); return; }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  $("#input").addEventListener("input", () => { autoGrow(); atCheck(); });
  $("#btnCancel").onclick = () => cancelSession(state.sid);

  /* 会话抽屉 */
  $("#btnSessions").onclick = () => { openDrawer("drawerSessions"); loadSessions(true).catch(() => {}); };
  // 会话搜索：防抖 300ms 后走后端全文搜索（标题/目录/消息正文）
  let searchTimer = 0;
  $("#sessSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { loadSessions(true).catch(() => {}); }, 300);
  });

  /* 引导区「去设置」：打开设置抽屉并定位到对应面板（此前为死按钮） */
  $$(".golink").forEach(b => b.addEventListener("click", () => {
    openDrawer("drawerSettings");
    openPane(b.dataset.pane || "paneModel");
  }));

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
  // 窄屏（≤600px）顶栏快捷按钮整体隐藏，⋯ 菜单是这些功能的唯一入口
  $("#mSessions").onclick = () => { closeMenu(); $("#btnSessions").click(); };
  $("#mTerm").onclick = () => { closeMenu(); $("#btnTerm").click(); };
  $("#mGit").onclick = () => { closeMenu(); $("#btnGit").click(); };
  $("#mUsage").onclick = () => { closeMenu(); $("#btnUsage").click(); };
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

/* ---------- 初始化 ----------
   模块已保证全部依赖就绪（无需脚本顺序自检）；仅保留关键组件注册检查，
   用于 import 被裁剪/文件缺失时给出明确报错而非白屏。 */
function startupCheck() {
  const want = ["spark-msg", "spark-tool-card", "spark-plan-card", "spark-think", "spark-session-card", "spark-mcp-row", "spark-mem-row", "spark-git-row"];
  const missing = want.filter(n => !customElements.get(n));
  if (missing.length) {
    console.error("[spark] 组件注册缺失：", missing.join(", "));
    const bar = document.getElementById("sessionLine");
    if (bar) bar.textContent = "前端组件加载异常：" + missing.join(", ") + " 未注册（见控制台）";
  }
}

async function init(force) {
  initTheme();
  startupCheck();
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
