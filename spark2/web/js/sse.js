/* sse.js —— 发送 / SSE 流 / 事件分发（运行状态按会话记录） */
"use strict";

import { $, state, toast, openModal, updateRunningUI, isSidRunning, runtime } from "./core.js";
import {
  addUserMsg, errorMsg, getStreamSpan, mdToHtml, autoScroll, appendThinking,
  planCard, toolCard, updateToolResult, updateMeter, markPlanDone, autoGrow,
} from "./render.js";
import { loadSessions } from "./sessions.js";
import { openApproval } from "./approval.js";
import { openDrawer } from "./settings.js";

let mdRaf = 0; // 流式 Markdown 渲染帧节流（见 handleEvent text 分支）

async function send() {
  const prompt = $("#input").value.trim();
  if (!prompt) return;
  if (!state.sid) { toast("请先新建或选择一个会话"); openDrawer("drawerSessions"); return; }
  if (isSidRunning(state.sid)) return;
  state.runningSids[state.sid] = true; updateRunningUI();
  addUserMsg(prompt); $("#input").value = ""; autoGrow();
  runtime.curAssistant = null;
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Spark-Token": state.token },
      body: JSON.stringify({ session_id: state.sid, prompt, approval_mode: $("#fQuickAp").value || undefined }),
    });
    if (res.status === 401) { openModal("tokenModal"); return; }
    if (!res.ok) { const e = await res.json().catch(() => ({})); errorMsg(e.detail || "请求失败（" + res.status + "）"); return; }
    const reader = res.body.getReader(); const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const line = chunk.trim(); if (!line.startsWith("data:")) continue;
        let ev; try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
        handleEvent(ev);
      }
    }
  } catch (e) { errorMsg("连接中断：" + (e.message || e)); }
  delete state.runningSids[state.sid];
  updateRunningUI(); markPlanDone();
  loadSessions(true);
}

function handleEvent(ev) {
  switch (ev.type) {
    case "text":
      // Markdown 流式渲染：累积原文，rAF 合并同帧多次 delta 再全量重渲染（长回复不卡）
      getStreamSpan();
      runtime.curAssistant.dataset.raw = (runtime.curAssistant.dataset.raw || "") + ev.delta;
      if (!mdRaf) {
        mdRaf = requestAnimationFrame(() => {
          mdRaf = 0;
          const el = runtime.curAssistant && runtime.curAssistant.querySelector(".text");
          if (el) el.innerHTML = mdToHtml(runtime.curAssistant.dataset.raw || "");
          autoScroll();
        });
      }
      break;
    case "reasoning": appendThinking(ev.delta); break;
    case "plan": planCard(ev.steps || []); break;
    case "tool_start": { const card = toolCard(ev); card.dataset.id = ev.id; break; }
    case "tool_result": updateToolResult(ev); break;
    case "approval": openApproval(ev); break;
    case "error": errorMsg(ev.message || "出错了"); break;
    case "usage": updateMeter(ev.estimated); break;
    case "done": if (ev.reason === "max_turns") errorMsg("已达到最大轮次，请分步提问。"); break;
  }
}

export { send, handleEvent };
