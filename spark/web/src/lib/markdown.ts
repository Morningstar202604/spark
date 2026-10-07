import { escapeHtml } from "./utils";
import { marked } from "marked";

/* eslint-disable @typescript-eslint/no-explicit-any */

// 自定义渲染器：保留 Spark 原有的 class 命名与代码块复制按钮
const renderer = {
  /* eslint-disable @typescript-eslint/no-unused-vars */
  code(this: any, code: string, infostring: string | undefined, _escaped: boolean) {
    const language = infostring || "";
    return (
      `<pre class="mdcode"><span class="mdcodelang">${escapeHtml(language)}</span>` +
      `<button class="mdcopy" type="button" title="复制代码">复制</button>` +
      `<code>${escapeHtml(code)}</code></pre>`
    );
  },
  heading(this: any, text: string, level: number, _raw: string) {
    const lvl = Math.min(level, 4);
    return `<h${lvl} class="mdh">${text}</h${lvl}>`;
  },
  /* eslint-enable @typescript-eslint/no-unused-vars */
};

// marked 全局配置：GFM 表格 + 换行转 <br> + 自定义渲染器
marked.setOptions({ gfm: true, breaks: true });
marked.use({ renderer });

// Spark 专用：[thinking] ... [/thinking] 块 → reasoning div（非标准 Markdown）
function preprocessThinking(src: string): string {
  return src.replace(
    /\[thinking\]\s*([\s\S]*?)\s*\[\/thinking\]/g,
    (_m, content) => `<div class="mdreason">思考过程：${content.trim()}</div>`
  );
}

export function mdToHtml(src: string): string {
  const preprocessed = preprocessThinking(String(src));
  return marked.parse(preprocessed) as string;
}

export function highlightDiff(diff: string): string {
  return escapeHtml(diff)
    .split("\n")
    .map((l) => {
      if (l.startsWith("+++") || l.startsWith("---") || l.startsWith("@@"))
        return `<span class="hdr">${escapeHtml(l)}</span>`;
      if (l.startsWith("+")) return `<span class="add">${escapeHtml(l)}</span>`;
      if (l.startsWith("-")) return `<span class="del">${escapeHtml(l)}</span>`;
      return escapeHtml(l);
    })
    .join("\n");
}
