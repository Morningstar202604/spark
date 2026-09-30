/* render.js —— 消息渲染：用户/助手/思考/计划/工具卡/错误、上下文水位、输入框自适应
   结构全部来自 <template> 组件（components.js），本文件只创建元素 + 填数据。 */
"use strict";

import { $, state, esc, runtime, toast } from "./core.js";
import { showEmptyIfNeeded } from "./sessions.js"; // 消息区空状态（运行时调用）

function addUserMsg(text, note) {
  const el = document.createElement("spark-msg");
  el.setAttribute("type", "user");
  $("#msgList").appendChild(el);
  el.msgEl.textContent = text;
  if (note) {
    const tag = document.createElement("span");
    tag.className = "img-note";
    tag.textContent = note;
    el.msgEl.append(" ", tag);
  }
  showEmptyIfNeeded(); autoScroll();
}

function newAssistant() {
  const el = document.createElement("spark-msg");
  el.setAttribute("type", "assistant");
  $("#msgList").appendChild(el);
  el.dataset.raw = ""; // 流式原文缓冲：Markdown 每次全量重渲染
  runtime.curAssistant = el;
  return el;
}

function getStreamSpan() {
  if (!runtime.curAssistant) newAssistant();
  return runtime.curAssistant.textEl;
}

/* ---------- 轻量安全 Markdown 渲染 ----------
   顺序：esc 全文 → 提取代码块 → 行内规则 → 行级规则 → 恢复代码块。
   全程在 esc 后的文本上操作，模型/用户内容中的 <script> 等被转义，
   不产生任何注入面。链接 href 白名单：http/https/mailto/相对路径，
   其余（如 javascript:）按纯文本输出。 */
function mdToHtml(src) {
  let s = esc(String(src));
  const blocks = [];
  s = s.replace(/```([\w+-]*)[^\n]*\n?([\s\S]*?)```/g, (m, lang, code) => {
    const i = blocks.length;
    blocks.push('<pre class="mdcode"><span class="mdcodelang">' + esc(lang || "") + '</span><button class="mdcopy" type="button" title="复制代码">复制</button><code>' + esc(code.replace(/\s+$/, "")) + '</code></pre>');
    return "\u0000B" + i + "\u0000";
  });
  // 行内代码
  s = s.replace(/`([^`\n]+)`/g, (m, c) => '<code class="mdinl">' + c + "</code>");
  // 链接（href 白名单）
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => {
    if (/^(https?:|mailto:)/i.test(u) || u.startsWith("/") || u.startsWith("./") || u.startsWith("../")) {
      return '<a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + t + "</a>";
    }
    return m;
  });
  // 粗体 / 斜体
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  // 行级：标题 / 列表 / 引用 / 分割线 / 表格
  const lines = s.split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    const isTable = /^\|.*\|$/.test(l.trim()) && i + 1 < lines.length && /^\|[\s\-:|]+\|$/.test(lines[i + 1].trim());
    if (isTable) {
      const cells = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      const head = cells(l);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) { rows.push(cells(lines[i])); i++; }
      out.push('<table class="mdtable"><thead><tr>' + head.map(c => "<th>" + c + "</th>").join("") + "</tr></thead><tbody>" +
        rows.map(r => "<tr>" + r.map(c => "<td>" + c + "</td>").join("") + "</tr>").join("") + "</tbody></table>");
      continue;
    }
    let m = l.match(/^(#{1,4})\s+(.*)$/);
    if (m) { out.push("<h" + m[1].length + ' class="mdh">' + m[2] + "</h" + m[1].length + ">"); i++; continue; }
    m = l.match(/^(?:[-*])\s+(.*)$/);
    if (m) { out.push('<div class="mdli">' + m[1] + "</div>"); i++; continue; }
    m = l.match(/^\d+[.)]\s+(.*)$/);
    if (m) { out.push('<div class="mdli mdnum">' + m[1] + "</div>"); i++; continue; }
    if (/^&gt;\s?/.test(l)) { out.push('<blockquote class="mdq">' + l.replace(/^&gt;\s?/, "") + "</blockquote>"); i++; continue; }
    if (/^-{3,}$/.test(l.trim())) { out.push('<hr class="mdhr" />'); i++; continue; }
    out.push(l); i++;
  }
  s = out.join("\n");
  // 恢复代码块
  s = s.replace(/\u0000B(\d+)\u0000/g, (m, i2) => blocks[+i2]);
  return s;
}

function appendThinking(text) {
  let t = runtime.curAssistant ? runtime.curAssistant.querySelector("spark-think") : null;
  if (!t) {
    const wrap = runtime.curAssistant || newAssistant();
    const d = document.createElement("spark-think");
    wrap.appendChild(d); t = d;
  }
  t.appendText(text);
  autoScroll();
}

function planCard(steps) {
  const el = document.createElement("spark-plan-card");
  el.setSteps(steps);
  $("#msgList").appendChild(el);
}

function markPlanDone() {
  const p = $("#msgList").querySelector("spark-plan-card");
  if (p && p.card && !p.card.classList.contains("done")) p.markDone();
}

function toolCard(ev) {
  const card = document.createElement("spark-tool-card");
  card.setTool(ev.name, ev.args_summary || "");
  if (ev.diff) card.setDiff(ev.diff);
  $("#msgList").appendChild(card);
  autoScroll();
  return card;
}

function highlightDiff(diff) {
  return esc(diff).split("\n").map(l => {
    if (l.startsWith("+++") || l.startsWith("---") || l.startsWith("@@")) return '<span class="hdr">' + esc(l) + "</span>";
    if (l.startsWith("+")) return '<span class="add">' + esc(l) + "</span>";
    if (l.startsWith("-")) return '<span class="del">' + esc(l) + "</span>";
    return esc(l);
  }).join("\n");
}

function findToolCard(id) {
  const cards = $("#msgList").querySelectorAll("spark-tool-card");
  for (const c of cards) if (c.dataset.id === id) return c;
  return null;
}

function updateToolResult(ev) {
  const card = findToolCard(ev.id);
  if (!card) return;
  const ok = !!ev.approved;
  card.setDuration(ev.duration_ms || 0);
  card.setResult(ok, ev.output || "");
  // 注入防护提示：工具返回内容疑似含注入指令（事件带 injected 标记）
  card.markInjected(!!ev.injected);
  card.open();
}

function errorMsg(text) {
  const el = document.createElement("spark-msg");
  el.setAttribute("type", "error");
  $("#msgList").appendChild(el);
  el.msgEl.textContent = text;
  autoScroll();
}

/* ---------- 输入框自适应高度（textarea 随内容增高，上限 160px） ---------- */
function autoGrow() {
  const ta = $("#input");
  if (!ta) return;
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

/* ---------- 智能滚动：用户上滚查历史时暂停跟随，回到底部附近自动恢复 ---------- */
let userScrolled = false;   // 用户主动离开底部
let scrollRaf = 0;          // rAF 合并高频调用
const SCROLL_STICK_MARGIN = 80; // 距底 80px 内视为"在底部"

function autoScroll() {
  if (userScrolled) return; // 用户在翻历史：不抢滚动
  if (scrollRaf) return;    // 已有待执行的帧，合并本次调用
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0;
    const m = $("#chat");
    if (m) m.scrollTop = m.scrollHeight;
  });
}

function bindScrollStick() {
  const m = $("#chat");
  if (!m) return;
  let last = m.scrollTop;
  m.addEventListener("scroll", () => {
    // 向上滚（scrollTop 减小）且离底部较远 → 暂停跟随
    if (last - m.scrollTop > 4 && m.scrollHeight - m.scrollTop - m.clientHeight > SCROLL_STICK_MARGIN) {
      userScrolled = true;
    } else if (m.scrollHeight - m.scrollTop - m.clientHeight <= SCROLL_STICK_MARGIN) {
      userScrolled = false; // 回到底部 → 恢复跟随
    }
    last = m.scrollTop;
  }, { passive: true });
}

/* ---------- 上下文水位 ---------- */
function updateMeter(est) {
  if (est) state.lastUsage = est;
  const max = state.cfg ? (state.cfg.max_context_tokens || 32000) : 32000;
  const pct = Math.min(100, Math.round(state.lastUsage / max * 100));
  const m = $("#meter"); m.style.width = pct + "%"; m.classList.toggle("warn", pct > 80);
  m.title = "上下文约 " + state.lastUsage + " / " + max + " tokens（" + pct + "%）";
}

/* ---------- Markdown 代码块「复制代码」按钮（事件委托，流式渲染后也生效） ---------- */
function initMdActions() {
  const list = $("#msgList");
  if (!list) return;
  list.addEventListener("click", ev => {
    const t = ev.target.closest(".mdcopy");
    if (!t) return;
    const code = t.parentElement.querySelector("code");
    if (code) {
      navigator.clipboard.writeText(code.textContent)
        .then(() => toast("代码已复制"), () => toast("复制失败"));
    }
  });
}

export { addUserMsg, newAssistant, getStreamSpan, mdToHtml, appendThinking, planCard,
  markPlanDone, toolCard, highlightDiff, updateToolResult, errorMsg, autoGrow,
  autoScroll, bindScrollStick, updateMeter, initMdActions,
};
