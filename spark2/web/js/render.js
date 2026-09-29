/* render.js —— 消息渲染：用户/助手/思考/计划/工具卡/错误、上下文水位 */
"use strict";

function addUserMsg(text) {
  const el = document.createElement("div"); el.className = "msg user"; el.textContent = text;
  $("#msgList").appendChild(el); showEmptyIfNeeded(); autoScroll();
}

function newAssistant() {
  const el = document.createElement("div"); el.className = "msg assistant";
  el.innerHTML = '<div class="text"></div><button class="copy" title="复制内容" aria-label="复制内容"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>';
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
