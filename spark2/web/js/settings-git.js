/* settings-git.js —— 检查点面板：git 状态 / 存档 / 回滚（settings.js 拆分） */
"use strict";

async function loadGit() {
  const side = $("#gitSide"), list = $("#gitList");
  let data;
  try {
    const r = await api("/api/git?sid=" + encodeURIComponent(state.sid || ""));
    data = await r.json();
  } catch (e) { side.textContent = "加载失败"; return; }
  if (!data.repo) {
    side.textContent = data.reason || "不可用";
    list.innerHTML = '<div class="memempty">工作目录不是 git 仓库，检查点未启用（在项目里执行 git init 即可开启）。Agent 写文件前的自动检查点同样依赖 git。</div>';
    return;
  }
  side.innerHTML = '<span class="badge">分支 ' + esc(data.branch) + '</span>' +
    '<span class="badge' + (data.changes ? " dirty" : "") + '">' + (data.changes ? data.changes + " 处未存档改动" : "工作区干净") + '</span>' +
    '<span>' + data.checkpoints.length + ' 个检查点</span>';
  if (!data.checkpoints.length) { list.innerHTML = '<div class="memempty">还没有检查点，点上面「存档当前」。</div>'; return; }
  list.innerHTML = "";
  data.checkpoints.forEach((c, i) => {
    const d = document.createElement("spark-git-row");
    d.setData(c, i === 0 ? () => doGitReset() : null);
    list.appendChild(d);
  });
}
async function doCheckpoint() {
  if (!state.sid) { toast("先新建或选择会话"); return; }
  try {
    const r = await api("/api/git/checkpoint", { method: "POST", body: JSON.stringify({ sid: state.sid, message: $("#fCpMsg").value }) });
    const d = await r.json();
    if (!r.ok) { toast(d.detail || "存档失败"); return; }
    toast("已存档"); $("#fCpMsg").value = ""; loadGit();
  } catch (e) { toast("存档失败"); }
}
async function doGitReset() {
  if (!confirm("回滚到最近一次存档？未存档的改动将丢失（不可撤销）。")) return;
  try {
    const r = await api("/api/git/reset", { method: "POST", body: JSON.stringify({ sid: state.sid || "", confirm: "yes" }) });
    const d = await r.json();
    if (!r.ok) { toast(d.detail || "回滚失败"); return; }
    toast("已回滚到最近存档"); loadGit();
  } catch (e) { toast("回滚失败"); }
}
