/* inputimg.js —— 输入栏图片粘贴/拖拽 → 预览 → 随消息发送（多模态识图）
   图片暂存于 runtime.pendingImages（{data: base64, mime, name}），
   sse.js send() 发送后统一清空。 */
"use strict";

import { $, runtime, toast } from "./core.js";

const MAX_IMG = 3;                 // 最多 3 张
const MAX_BYTES = 2 * 1024 * 1024; // 单张 ≤2MB
const ALLOW = ["image/png", "image/jpeg", "image/webp", "image/gif"];

export function initInputImg() {
  const ta = $("#input");
  const preview = $("#imgPreview");
  if (!ta || !preview) return;
  runtime.pendingImages = [];

  function renderPreview() {
    preview.innerHTML = "";
    preview.hidden = runtime.pendingImages.length === 0;
    runtime.pendingImages.forEach((img, i) => {
      const cell = document.createElement("span");
      cell.className = "imgcell";
      const im = document.createElement("img");
      im.src = `data:${img.mime};base64,${img.data}`;
      im.alt = img.name || "图片";
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "imgdel";
      rm.title = "移除";
      rm.textContent = "×";
      rm.addEventListener("click", () => {
        runtime.pendingImages.splice(i, 1);
        renderPreview();
      });
      cell.append(im, rm);
      preview.append(cell);
    });
  }

  function addFiles(files) {
    for (const f of files) {
      if (runtime.pendingImages.length >= MAX_IMG) { toast("图片最多 3 张"); break; }
      if (!ALLOW.includes(f.type)) { toast("仅支持 PNG / JPG / WebP / GIF 图片"); continue; }
      if (f.size > MAX_BYTES) { toast("单张图片需 ≤ 2MB"); continue; }
      const fr = new FileReader();
      fr.onload = () => {
        const data = String(fr.result || "").split(",")[1] || "";
        if (!data) return;
        runtime.pendingImages.push({ data, mime: f.type, name: f.name });
        renderPreview();
      };
      fr.readAsDataURL(f);
    }
  }

  ta.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  ta.addEventListener("drop", (e) => {
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  ta.addEventListener("dragover", (e) => e.preventDefault());

  // 输入栏「添加图片」按钮：隐藏 file input 触发选择（同一套 addFiles 管线）
  const attach = $("#btnAttach");
  if (attach) {
    const fi = document.createElement("input");
    fi.type = "file";
    fi.accept = ALLOW.join(",");
    fi.multiple = true;
    fi.style.display = "none";
    document.body.appendChild(fi);
    attach.addEventListener("click", () => fi.click());
    fi.addEventListener("change", () => {
      if (fi.files.length) addFiles(fi.files);
      fi.value = "";
    });
  }
}
