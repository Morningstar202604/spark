/* approval.js —— 审批弹窗：多文件 diff 逐文件勾选、等待指示、A/D/S 快捷键 */
"use strict";

function openApproval(ev) {
  state.approval = ev;
  $("#apSummary").textContent = ev.summary || "";
  $("#apReason").textContent = "原因：" + (ev.reason || "");
  // diff 里的绝对路径换成相对路径，更易读
  let diff = ev.diff || "";
  const wd = state.cfg ? state.cfg.workdir : "";
  if (wd) diff = diff.split(wd).join(".");
  renderDiffPanel(diff, ev.tool);
  $("#approvalModal").classList.add("open");
  updateRunningUI();
}

/* 多文件 diff：按 "--- " 文件头切成文件块，逐块可展开/折叠；
   apply_patch 多文件时启用逐文件勾选（只允许勾选的文件落盘）。 */
function splitDiffBlocks(diff) {
  const lines = diff.split("\n");
  const blocks = [];
  let cur = null;
  for (const ln of lines) {
    if (ln.startsWith("--- ")) {
      if (cur) blocks.push(cur);
      cur = { head: [ln], body: [] };
    } else if (cur) {
      cur.body.push(ln);
    }
  }
  if (cur) blocks.push(cur);
  return blocks;
}

function diffCounts(block) {
  let add = 0, rem = 0;
  for (const ln of block.body) {
    if (ln.startsWith("+++") || ln.startsWith("@@")) continue; // 文件头/行号头不算增删
    if (ln.startsWith("+")) add++;
    else if (ln.startsWith("-")) rem++;
  }
  return [add, rem];
}

function diffPathName(block) {
  return (block.head[0] || "").slice(4).trim().replace(/^[ab]\//, "") || "(未知文件)";
}

function renderDiffPanel(diff, toolName) {
  const statEl = $("#apStat");
  const box = $("#apDiff");
  box.classList.add("apdiff");
  const blocks = splitDiffBlocks(diff);
  if (!blocks.length) {
    statEl.textContent = "";
    box.innerHTML = '<div class="dfile"><div class="dfbody" style="display:block"><pre>' + esc(diff || "（无 diff）") + "</pre></div></div>";
    return;
  }
  let totalA = 0, totalR = 0;
  const perFile = toolName === "apply_patch" && blocks.length > 1;
  const cards = blocks.map((b, i) => {
    const [a, r] = diffCounts(b);
    totalA += a; totalR += r;
    const pathLine = diffPathName(b);
    const openCls = i === 0 ? " open" : "";
    const pick = perFile
      ? '<label class="dpick" title="取消勾选 = 跳过该文件"><input type="checkbox" data-fname="' + esc(pathLine) + '" checked>应用</label>'
      : "";
    return '<div class="dfile' + openCls + '" data-fname="' + esc(pathLine) + '">' +
      '<div class="dfhead"><span class="darrow">▶</span><span class="dname">' + esc(pathLine) + '</span>' +
      '<span class="dbadge">+' + a + ' −' + r + '</span>' + pick + '</div>' +
      '<div class="dfbody"><pre>' + highlightDiff(b.body.join("\n")) + '</pre></div></div>';
  }).join("");
  statEl.textContent = (perFile ? "逐文件勾选：取消 = 跳过该文件（仅应用勾选的） · " : "") +
    blocks.length + " 个文件 · +" + totalA + " −" + totalR + " · 点文件名展开/收起";
  box.innerHTML = cards;
  box.querySelectorAll(".dfhead").forEach(h => {
    h.onclick = e => { if (e.target.closest(".dpick")) return; h.parentElement.classList.toggle("open"); };
  });
  if (perFile) {
    box.querySelectorAll('.dfile input[data-fname]').forEach(cb => {
      cb.onchange = () => {
        const card = cb.closest(".dfile");
        card.classList.toggle("skip", !cb.checked);
        updateAllowBtn();
      };
    });
  }
  updateAllowBtn();
}

function selectedFiles() {
  const boxes = $$("#apDiff .dfile input[data-fname]");
  if (!boxes.length) return null; // 非逐文件场景
  const picked = boxes.filter(cb => cb.checked).map(cb => cb.dataset.fname);
  return picked.length === boxes.length ? null : picked; // 全勾 = 全部允许（files=null）
}

function updateAllowBtn() {
  const b = $("#btnAllow"); if (!b) return;
  const picked = selectedFiles();
  if (picked === null) b.innerHTML = '允许全部 <span class="kbd">A</span>';
  else b.textContent = "允许所选 " + picked.length + "/" + $$("#apDiff .dfile input[data-fname]").length + " 个文件";
}

async function answerApproval(action) {
  const ev = state.approval; if (!ev) return;
  state.approval = null; $("#approvalModal").classList.remove("open");
  let files = undefined;
  if (action === "allow") files = selectedFiles(); // null=全选；列表=勾选；[]=全跳过
  try {
    await api("/api/approval", { method: "POST", body: JSON.stringify({ request_id: ev.request_id, action, tool: ev.tool, files }) });
  } catch (e) { toast("审批提交失败"); }
  updateRunningUI();
}
