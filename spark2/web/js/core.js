/* core.js —— 全局状态、通用工具、主题、运行状态管理（ES Module） */
"use strict";

import { openDrawer } from "./settings.js"; // 仅 updateRunBadge 按钮回调使用（运行时调用，循环依赖安全）

/* 元素缓存：只缓存「引用稳定」的静态容器（msgList/chat/input 等）；
   动态重绘的列表内容不经过 $，由各渲染函数直接管理。 */
const _domCache = {};
const $ = s => _domCache[s] || (_domCache[s] = document.querySelector(s));
const $$ = s => Array.from(document.querySelectorAll(s));

// 令牌来源优先级：URL ?token=（spark2 web 打印的地址）> localStorage。
// 未设置令牌时服务端不校验，直接进入正常界面。
const urlToken = new URLSearchParams(location.search).get("token") || "";
if (urlToken) localStorage.setItem("spark2_token", urlToken);

const state = {
  token: urlToken || localStorage.getItem("spark2_token") || "",
  cfg: null, presets: {}, modes: [],
  sessions: [], sid: null,
  // 运行状态按会话管理：sid -> true（多会话可并行，A 在跑不影响 B 发送）
  runningSids: {},
  approval: null, lastUsage: 0,
  mcpServers: [],
};

/* 跨模块共享的可变运行态：import 绑定只读，可变状态统一放对象属性（可写）。
   当前仅一个成员；后续共享状态都加在这里，避免再出现跨文件顶层 let。 */
const runtime = { curAssistant: null }; // 当前回合的助手消息元素

let toastTimer;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

async function api(path, opts = {}) {
  opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (state.token) opts.headers["X-Spark-Token"] = state.token;
  const res = await fetch(path, opts);
  if (res.status === 401) { openModal("tokenModal"); throw new Error("auth"); }
  return res;
}

function esc(s) { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function fmtTime(iso) { const d = new Date(iso); return isNaN(d) ? "" : (d.getMonth()+1) + "月" + d.getDate() + "日 " + String(d.getHours()).padStart(2,"0") + ":" + String(d.getMinutes()).padStart(2,"0"); }
function shortPath(p) { const s = String(p); const seg = s.split("/"); return seg.length > 3 ? "/…/" + seg.slice(-2).join("/") : s; }
function safeParseHeaders(s) {
  try {
    const o = JSON.parse(s);
    return (o && typeof o === "object" && !Array.isArray(o)) ? o : {};
  } catch (e) { toast("请求头需是 JSON 对象（如 {\"Authorization\":\"Bearer xxx\"}）"); return {}; }
}

/* ---------- 主题（默认跟随系统，可手动切换并记忆） ---------- */
const THEME_SUN = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const THEME_MOON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
function systemTheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function initTheme() {
  const saved = localStorage.getItem("spark2_theme");
  applyTheme(saved === "light" || saved === "dark" ? saved : systemTheme());
}
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("spark2_theme", t);
  const b = $("#btnTheme");
  if (b) { b.innerHTML = t === "dark" ? THEME_SUN : THEME_MOON; b.title = t === "dark" ? "切换到浅色" : "切换到深色"; }
}

/* ---------- 原生 dialog 开关（免费焦点圈闭 + Esc） ---------- */
function openModal(id) {
  const d = document.getElementById(id);
  if (d && !d.open) d.showModal();
}
function closeModal(id) {
  const d = document.getElementById(id);
  if (d && d.open) d.close();
}
function isModalOpen(id) {
  const d = document.getElementById(id);
  return !!(d && d.open);
}

/* ---------- 运行状态（按会话）与顶栏徽标 ---------- */
function isSidRunning(sid) { return !!state.runningSids[sid]; }
function runningCount() { return Object.keys(state.runningSids).length; }

function updateRunBadge() {
  const b = $("#runBadge");
  if (!b) return;
  const n = runningCount();
  const waiting = !!state.approval;
  if (waiting) {
    b.classList.add("waiting");
    b.innerHTML = '<span class="dot"></span>等待你确认审批';
    b.style.display = "";
    b.title = "Agent 正在等你审批，点此回到审批弹窗";
    b.onclick = () => openModal("approvalModal");
    return;
  }
  if (n) {
    b.classList.remove("waiting");
    b.innerHTML = '<span class="dot"></span>' + n + " 个会话运行中";
    b.style.display = "";
    b.title = "点击打开会话列表，可查看或停止运行中的会话";
    b.onclick = () => openDrawer("drawerSessions");
    return;
  }
  b.style.display = "none";
  b.onclick = null;
}

function updateRunningUI() {
  const run = isSidRunning(state.sid);
  const btn = $("#btnSend");
  if (btn) btn.disabled = run;
  const can = $("#btnCancel");
  if (can) can.style.display = run ? "" : "none";
  const inp = $("#input");
  if (inp) {
    if (run && state.approval) inp.placeholder = "⏳ 等待你确认审批…";
    else if (run) inp.placeholder = "该会话正在处理，可先切换其他会话或停止…";
    else inp.placeholder = "描述你想做的事，例如：帮我修一下登录接口的 bug…";
  }
  updateRunBadge();
}

export {
  $, $$, state, runtime, toast, api, esc, fmtTime, shortPath, safeParseHeaders,
  systemTheme, initTheme, applyTheme, openModal, closeModal, isModalOpen,
  isSidRunning, runningCount, updateRunningUI,
};
