/* settings-usage.js —— 用量成本面板：汇总 / 会话排行 */
"use strict";

import { $, api, esc } from "./core.js";

function fmtTokens(n) { return n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); }
async function loadUsage() {
  const box = $("#usageBox");
  let data;
  try {
    const res = await api("/api/usage");
    data = await res.json();
  } catch (e) { box.innerHTML = '<div class="memempty">加载失败</div>'; return; }
  const t = data.totals || {};
  const rows = data.top_sessions || [];
  if (!t.calls) { box.innerHTML = '<div class="memempty">还没有用量记录。每轮模型调用都会统计 tokens 与估算费用（单价按 2026-09 现役官方价，可在配置里覆盖）。</div>'; return; }
  let html = '<div style="font-size:13px;line-height:1.9">' +
    '<div><b>' + fmtTokens(t.total_tokens || 0) + '</b> tokens（输入 ' + fmtTokens(t.prompt_tokens || 0) + ' / 输出 ' + fmtTokens(t.completion_tokens || 0) + '）· ' + (t.calls || 0) + ' 次调用</div>' +
    '<div>估算费用：<b style="color:var(--teal)">¥' + (t.est_cost || 0).toFixed(4) + '</b> <span class="hint2">（近 ' + (data.days || 30) + ' 天）</span></div>' +
    '</div>';
  if (rows.length) {
    const max = Math.max(...rows.map(r => r.est_cost || 0), 0.0001);
    html += '<div class="sec-divider">会话排行</div>';
    for (const r of rows) {
      const w = Math.max(4, Math.round(((r.est_cost || 0) / max) * 100));
      html += '<div style="margin:6px 0"><div style="font-size:12px;display:flex;justify-content:space-between"><span>' + esc(String(r.session_id).slice(0, 8)) + '</span><span>' + fmtTokens(r.total_tokens || 0) + ' · ¥' + (r.est_cost || 0).toFixed(4) + '</span></div>' +
        '<div class="ubar"><div style="width:' + w + '%"></div></div></div>';
    }
  }
  box.innerHTML = html;
}

export { loadUsage, fmtTokens };
