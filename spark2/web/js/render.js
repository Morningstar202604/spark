/* render.js —— 消息渲染：用户/助手/思考/计划/工具卡/错误、上下文水位 */
"use strict";

function addUserMsg(text) {
  const el = document.createElement("div"); el.className = "msg user"; el.textContent = text;
  $("#msgList").appendChild(el); showEmptyIfNeeded(); autoScroll();
}

function newAssistant() {
  const el = document.createElement("div"); el.className = "msg assistant";
  el.innerHTML = '<div class="text"></div><button class="copy" title="复制内容" aria-label="复制内容"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>';
  el.dataset.raw = ""; // 流式原文缓冲：Markdown 每次全量重渲染
  el.querySelector(".copy").onclick = () => {
    const t = el.querySelector(".text").textContent;
    navigator.clipboard.writeText(t).then(() => toast("已复制"), () => toast("复制失败"));
  };
  $("#msgList").appendChild(el); curAssistant = el;
  return el;
}

function getStreamSpan() {
  if (!curAssistant) newAssistant();
  return curAssistant.querySelector(".text");
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
    blocks.push('<pre class="mdcode"><span class="mdcodelang">' + esc(lang || "") + '</span><code>' + esc(code.replace(/\s+$/, "")) + '</code></pre>');
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
  let t = curAssistant ? curAssistant.querySelector(".think") : null;
  if (!t) {
    const wrap = curAssistant || newAssistant();
    const d = document.createElement("details"); d.className = "think";
    d.innerHTML = "<summary>思考过程</summary><pre></pre>";
    wrap.appendChild(d); t = d;
  }
  t.querySelector("pre").textContent += text;
  autoScroll();
}

function planCard(steps) {
  const el = document.createElement("div"); el.className = "plan";
  let lis = ""; steps.forEach((s, i) => { lis += "<li>" + esc(s) + "</li>"; });
  el.innerHTML = '<div class="pt">计划</div><ol>' + lis + "</ol>";
  $("#msgList").appendChild(el);
}

function markPlanDone() {
  const p = $("#msgList").querySelector(".plan:not(.done)");
  if (p) { p.classList.add("done"); p.querySelectorAll("li").forEach(li => li.classList.add("done")); }
}

function toolCard(ev) {
  const card = document.createElement("div"); card.className = "tool";
  card.innerHTML =
    '<div class="thead"><span class="status">⏳</span><span class="tname">' + esc(ev.name) + '</span>' +
    '<span class="tsum">' + esc(ev.args_summary || "") + '</span><span class="tdur"></span></div>' +
    '<div class="tbody"><pre class="out"></pre></div>';
  card.querySelector(".thead").onclick = () => card.classList.toggle("open");
  const pre = card.querySelector(".out");
  if (ev.diff) { pre.classList.add("diff"); pre.innerHTML = highlightDiff(ev.diff); }
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
  const cards = $("#msgList").querySelectorAll(".tool");
  for (const c of cards) if (c.dataset.id === id) return c;
  return null;
}

function updateToolResult(ev) {
  const card = findToolCard(ev.id);
  if (!card) return;
  const ok = !!ev.approved;
  card.querySelector(".status").textContent = ok ? "✓" : "✗";
  card.querySelector(".status").style.color = ok ? "var(--green)" : "var(--red)";
  card.querySelector(".tdur").textContent = ev.duration_ms ? (ev.duration_ms / 1000).toFixed(1) + "s" : "";
  const pre = card.querySelector(".out");
  pre.classList.remove("diff");
  pre.textContent = ev.output || "";
  // 注入防护提示：工具返回内容疑似含注入指令（事件带 injected 标记）
  let warn = card.querySelector(".injwarn");
  if (ev.injected) {
    if (!warn) {
      warn = document.createElement("div");
      warn.className = "injwarn";
      warn.innerHTML = '<span class="injicon">⚠</span><div><b>已拦截注入指令</b><p>工具返回内容疑似包含恶意指令，已按普通文本忽略</p></div>';
      card.appendChild(warn);
    }
  } else if (warn) {
    warn.remove();
  }
  card.classList.add("open");
}

function errorMsg(text) {
  const el = document.createElement("div"); el.className = "msg error"; el.textContent = text;
  $("#msgList").appendChild(el); autoScroll();
}

function autoScroll() { const m = $("#chat"); m.scrollTop = m.scrollHeight; }

/* ---------- 上下文水位 ---------- */
function updateMeter(est) {
  if (est) state.lastUsage = est;
  const max = state.cfg ? (state.cfg.max_context_tokens || 32000) : 32000;
  const pct = Math.min(100, Math.round(state.lastUsage / max * 100));
  const m = $("#meter"); m.style.width = pct + "%"; m.classList.toggle("warn", pct > 80);
  m.title = "上下文约 " + state.lastUsage + " / " + max + " tokens（" + pct + "%）";
}
