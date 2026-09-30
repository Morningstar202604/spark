/* settings-plugins.js —— 插件面板：列表与状态 */
"use strict";

import { $, api, esc } from "./core.js";

async function loadPlugins() {
  const box = $("#pluginList");
  let data;
  try {
    const res = await api("/api/plugins");
    data = await res.json();
  } catch (e) { box.innerHTML = '<span>加载失败</span>'; return; }
  const items = data.plugins || [];
  if (!items.length) {
    box.innerHTML = '<span>暂无插件。在 ' + esc(data.dir || "~/.spark2/plugins") + ' 放一个 .py（导出 tools 或 register(registry)），重启生效。</span>';
    return;
  }
  box.innerHTML = items.map(p =>
    '<div class="mem"><span class="mk">' + esc(p.name) + '</span><span class="mv">' +
    (p.error ? '<span style="color:var(--red)">加载失败：' + esc(p.error) + '</span>' : '工具：' + esc((p.tools || []).join("、") || "（无）")) +
    '</span></div>'
  ).join("");
}

export { loadPlugins };
