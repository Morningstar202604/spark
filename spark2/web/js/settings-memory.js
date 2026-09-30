/* settings-memory.js —— 长期记忆面板：列表 / 删除 / 编辑 / 新增（settings.js 拆分） */
"use strict";

async function loadMemory() {
  const box = $("#memoryList");
  if (!state.cfg || !state.cfg.workdir) { box.innerHTML = '<div class="memempty">先在工作目录设置里指定项目路径</div>'; return; }
  let data;
  try {
    const res = await api("/api/memory?workdir=" + encodeURIComponent(state.cfg.workdir));
    data = await res.json();
  } catch (e) { box.innerHTML = '<div class="memempty">加载失败</div>'; return; }
  if (!data.items.length) { box.innerHTML = '<div class="memempty">还没有记忆。对话里说「记住 XX 是 YY」，Agent 就会记在这里。</div>'; return; }
  box.innerHTML = "";
  for (const it of data.items) {
    const d = document.createElement("spark-mem-row");
    d.setData(it);
    box.appendChild(d);
  }
  box.querySelectorAll("button[data-id]").forEach(b => b.onclick = async () => {
    try {
      await api("/api/memory/" + b.dataset.id, { method: "DELETE" });
      toast("已删除这条记忆");
      loadMemory();
    } catch (e) { toast("删除失败"); }
  });
  box.querySelectorAll("button[data-edit]").forEach(b => b.onclick = async () => {
    const row = b.closest(".mem");
    const key = row.querySelector(".mk").textContent;
    const oldVal = row.querySelector(".mv").textContent;
    const nv = prompt("修改「" + key + "」：", oldVal);
    if (nv === null || !nv.trim() || nv.trim() === oldVal) return;
    try {
      const r = await api("/api/memory", { method: "POST", body: JSON.stringify({ workdir: state.cfg.workdir, key, value: nv.trim() }) });
      if (!r.ok) { const e = await r.json().catch(() => ({})); toast(e.detail || "修改失败"); return; }
      toast("已更新「" + key + "」");
      loadMemory();
    } catch (e) { toast("修改失败"); }
  });
}

async function addMemory() {
  if (!state.cfg || !state.cfg.workdir) { toast("先在设置里指定工作目录"); return; }
  const key = $("#fMemKey").value.trim(), value = $("#fMemVal").value.trim();
  if (!key || !value) { toast("key 与内容都要填"); return; }
  try {
    const r = await api("/api/memory", { method: "POST", body: JSON.stringify({ workdir: state.cfg.workdir, key, value }) });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast(e.detail || "保存失败"); return; }
    toast("已记住「" + key + "」");
    $("#fMemKey").value = ""; $("#fMemVal").value = "";
    loadMemory();
  } catch (e) { toast("保存失败"); }
}
