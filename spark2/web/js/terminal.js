/* terminal.js —— 内置终端：tab 按会话分组（切会话自动换目录）、可关闭、高度拖拽 */
"use strict";

import { $, $$, api, toast, state } from "./core.js";

// 终端 tab 按会话分组：sid -> tabs[]。切会话后展示该会话自己的终端组，
// 后端 PtyManager 也按 (sid, tab_id) 建 session —— 不会在旧目录敲命令。
const termState = { groups: {}, activeId: null, barOpen: false };

function loadXterm(cb) {
  if (window.Terminal) { cb(); return; }
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "/vendor/xterm.min.css";
  document.head.appendChild(css);
  const s = document.createElement("script");
  s.src = "/vendor/xterm.min.js";
  s.onload = () => cb();
  s.onerror = () => toast("终端组件加载失败：缺少 web/vendor/xterm.min.js");
  document.head.appendChild(s);
}

function termWsUrl(tabId) {
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  return proto + location.host + "/ws/pty?token=" + encodeURIComponent(state.token) +
    "&sid=" + encodeURIComponent(state.sid || "") + "&tab=" + encodeURIComponent(tabId);
}

function currentTermTabs() { return termState.groups[state.sid || ""] || (termState.groups[state.sid || ""] = []); }

function activateTermTab(id) {
  termState.activeId = id;
  const tabs = currentTermTabs();
  tabs.forEach(t => {
    t.box.style.display = t.id === id ? "" : "none";
    t.btn.classList.toggle("active", t.id === id);
  });
  const tab = tabs.find(t => t.id === id);
  if (tab) tab.term.focus();
}

/* 重新渲染当前会话的终端 tab 条（切会话 / 增删 tab 后调用） */
function renderTermTabs() {
  const bar = $("#termTabs"); if (!bar) return;
  const tabs = currentTermTabs();
  // 移除旧按钮（保留末尾 ＋）
  $$("#termTabs .termtab").forEach(b => b.remove());
  const add = $("#btnTermAdd");
  tabs.forEach(t => {
    bar.insertBefore(t.btn, add);
  });
  if (tabs.length) {
    const cur = tabs.find(t => t.id === termState.activeId) || tabs[tabs.length - 1];
    activateTermTab(cur.id);
  }
}

function addTermTab() {
  const sid = state.sid || "";
  loadXterm(() => {
    const tabId = Math.random().toString(16).slice(2, 10);
    const tabs = currentTermTabs();
    const label = "终端 " + (tabs.length + 1);
    const btn = document.createElement("button");
    btn.className = "termtab";
    btn.innerHTML = '<span>' + label + '</span><span class="x" title="关闭此终端">✕</span>';
    btn.title = "工作目录：" + (state.cfg && state.cfg.workdir ? state.cfg.workdir : "当前会话目录");
    btn.querySelector(".x").onclick = e => { e.stopPropagation(); closeTermTab(sid, tabId); };
    btn.onclick = () => activateTermTab(tabId);
    const box = document.createElement("div");
    box.style.display = "none";
    box.innerHTML = '<div class="termph">连接中…</div>';
    $("#termStage").appendChild(box);
    const term = new Terminal({
      fontFamily: '"PingFang SC","Noto Sans SC",ui-monospace,Menlo,Consolas,monospace',
      fontSize: 13, lineHeight: 1.25, cursorBlink: true, scrollback: 5000,
      theme: { background: "#0b1220", foreground: "#e8eef7", cursor: "#2dd4bf", selectionBackground: "#22304a" },
    });
    const ws = new WebSocket(termWsUrl(tabId));
    const tab = { id: tabId, term, ws, box, btn, label };
    tabs.push(tab);
    renderTermTabs();
    ws.onopen = () => {
      const ph = box.querySelector(".termph");
      if (ph) ph.remove();
      term.open(box);
      term.focus();
      fitTerm(term, box);   // 创建即适配容器宽度（默认 80 列在窄屏会横向溢出）
      sendTermSize(tabId, box, ws);
    };
    ws.onmessage = e => {
      let m; try { m = JSON.parse(e.data); } catch { return; }
      if (m.type === "out") term.write(m.data);
      else if (m.type === "err") term.write("\r\n[终端] " + m.message + "\r\n");
      else if (m.type === "exit") term.write("\r\n[终端] 进程已退出，可关闭此页或新建终端\r\n");
    };
    ws.onclose = () => term.write("\r\n[终端] 连接已断开（重新打开面板可恢复）\r\n");
    term.onData(d => { if (ws.readyState === 1) ws.send(JSON.stringify({ type: "in", data: d })); });
    // 视口变化（含移动端横竖屏/窗口缩放）：同时重设 xterm 前端尺寸与后端 cols/rows，
    // 否则终端保持创建时的宽度，窄屏会横向溢出（此前只发后端不 resize 前端）
    window.addEventListener("resize", () => {
      if (termState.activeId === tabId) {
        fitTerm(term, box);
        sendTermSize(tabId, box, ws);
      }
    });
  });
}

/* 关闭终端 tab：断 ws + 通知后端杀 shell 子进程 */
function closeTermTab(sid, tabId) {
  const tabs = termState.groups[sid] || [];
  const i = tabs.findIndex(t => t.id === tabId);
  if (i < 0) return;
  const [tab] = tabs.splice(i, 1);
  try { tab.ws.close(); } catch (e) {}
  tab.box.remove();
  tab.btn.remove();
  try {
    api("/api/pty/close", { method: "POST", body: JSON.stringify({ sid, tab: tabId }) }).catch(() => {});
  } catch (e) {}
  if (termState.activeId === tabId) termState.activeId = null;
  renderTermTabs();
  toast("终端已关闭");
}

function fitTerm(term, box) {
  const w = box.clientWidth || 640, h = box.clientHeight || 240;
  term.resize(Math.max(20, Math.floor(w / 9)), Math.max(5, Math.floor(h / 18)));
}

function sendTermSize(tabId, box, ws) {
  if (ws.readyState !== 1) return;
  const w = box.clientWidth || 640, h = box.clientHeight || 240;
  const cols = Math.max(20, Math.floor(w / 9));
  const rows = Math.max(5, Math.floor(h / 18));
  ws.send(JSON.stringify({ type: "resize", cols, rows }));
}

function toggleTerm() {
  const bar = $("#termbar");
  const willOpen = !bar.classList.contains("open");
  bar.classList.toggle("open", willOpen);
  termState.barOpen = willOpen;
  if (willOpen) {
    const tabs = currentTermTabs();
    if (!tabs.length) addTermTab();
    else {
      const tab = tabs.find(t => t.id === termState.activeId) || tabs[0];
      if (tab) tab.term.focus();
    }
  }
}

function termResizeAll() { currentTermTabs().forEach(t => sendTermSize(t.id, t.box, t.ws)); }

function initTermDrag() {
  const handle = $("#termHandle"), bar = $("#termbar");
  const saved = parseInt(localStorage.getItem("spark2_term_h") || "", 10);
  if (saved) bar.style.height = Math.max(120, Math.min(saved, Math.round(window.innerHeight * 0.6))) + "px";
  let dragging = false, startY = 0, startH = 0;
  handle.addEventListener("mousedown", e => {
    dragging = true; startY = e.clientY; startH = bar.offsetHeight; e.preventDefault();
    const move = ev => {
      if (!dragging) return;
      const h = Math.max(120, Math.min(startH + (startY - ev.clientY), Math.round(window.innerHeight * 0.6)));
      bar.style.height = h + "px"; termResizeAll();
    };
    const up = () => {
      dragging = false;
      localStorage.setItem("spark2_term_h", String(bar.offsetHeight));
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      termResizeAll();
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
  handle.addEventListener("dblclick", () => {
    bar.style.height = ""; localStorage.removeItem("spark2_term_h"); termResizeAll();
  });
}

export { termState, toggleTerm, addTermTab, closeTermTab, renderTermTabs, initTermDrag };
