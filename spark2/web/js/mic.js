/* mic.js —— 语音输入（Web Speech API，中文识别，零依赖）
   点击麦克风开始听写，再次点击停止；识别结果追加进输入框。
   浏览器不支持 SpeechRecognition 时隐藏按钮（优雅降级）。 */
"use strict";

import { $, toast } from "./core.js";
import { autoGrow } from "./render.js";

export function initMic() {
  const btn = $("#btnMic");
  const ta = $("#input");
  if (!btn || !ta) return;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { btn.hidden = true; return; }

  let rec = null;
  let listening = false;

  function setState(on) {
    listening = on;
    btn.classList.toggle("on", on);
    btn.title = on ? "停止听写" : "语音输入";
    if (on) { btn.classList.add("pulse"); } else { btn.classList.remove("pulse"); }
  }

  function stop() {
    if (rec) { try { rec.stop(); } catch (e) { /* ignore */ } }
  }

  btn.onclick = () => {
    if (listening) { stop(); return; }
    try {
      rec = new SR();
      rec.lang = "zh-CN";
      rec.continuous = false;
      rec.interimResults = true;
      let draft = "";
      rec.onresult = (ev) => {
        let text = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          text += ev.results[i][0].transcript;
        }
        // 仅当有内容时写入输入框（追加，保留已手输的部分）
        if (text) {
          draft = text;
          const cur = ta.value.replace(/\s*$/, "");
          ta.value = cur ? cur + " " + text : text;
          autoGrow();
        }
      };
      rec.onend = () => {
        setState(false);
        if (!draft) toast("没有识别到语音");
        rec = null;
      };
      rec.onerror = (ev) => {
        setState(false);
        if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
          toast("需要麦克风权限，请在浏览器地址栏允许");
        } else if (ev.error !== "aborted") {
          toast("语音识别失败：" + ev.error);
        }
        rec = null;
      };
      rec.start();
      setState(true);
    } catch (e) {
      toast("语音输入不可用：" + (e.message || e));
    }
  };

  // 失焦/关闭时若在听写则停止
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); });
}
