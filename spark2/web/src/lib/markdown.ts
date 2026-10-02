import { escapeHtml } from "./utils";

function safeHref(url: string): boolean {
  if (/^(https?:|mailto:)/i.test(url)) return true;
  if (url.startsWith("/") || url.startsWith("./") || url.startsWith("../")) return true;
  return false;
}

export function mdToHtml(src: string): string {
  let s = escapeHtml(String(src));
  const blocks: string[] = [];

  s = s.replace(/```([\w+-]*)[^\n]*\n?([\s\S]*?)```/g, (_m, lang, code) => {
    const i = blocks.length;
    blocks.push(
      `<pre class="mdcode"><span class="mdcodelang">${escapeHtml(lang || "")}</span>` +
      `<button class="mdcopy" type="button" title="复制代码">复制</button>` +
      `<code>${escapeHtml(code.replace(/\s+$/, ""))}</code></pre>`
    );
    return `\u0000B${i}\u0000`;
  });

  s = s.replace(/`([^`\n]+)`/g, (_m, c) => `<code class="mdinl">${c}</code>`);

  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => {
    if (safeHref(u)) {
      return `<a href="${escapeHtml(u)}" target="_blank" rel="noopener noreferrer">${t}</a>`;
    }
    return m;
  });

  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");

  const lines = s.split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    const isTable = /^\|.*\|$/.test(l.trim()) && i + 1 < lines.length && /^\|[\s\-:|]+\|$/.test(lines[i + 1].trim());
    if (isTable) {
      const cells = (row: string) =>
        row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(l);
      i += 2;
      const rows: string[] = [];
      while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) {
        rows.push(cells(lines[i]));
        i++;
      }
      out.push(
        `<table class="mdtable"><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>` +
        rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("") +
        `</tbody></table>`
      );
      continue;
    }

    let m = l.match(/^(#{1,4})\s+(.*)$/);
    if (m) {
      out.push(`<h${m[1].length} class="mdh">${m[2]}</h${m[1].length}>`);
      i++;
      continue;
    }
    m = l.match(/^(?:[-*])\s+(.*)$/);
    if (m) {
      out.push(`<div class="mdli">${m[1]}</div>`);
      i++;
      continue;
    }
    m = l.match(/^\d+[.)]\s+(.*)$/);
    if (m) {
      out.push(`<div class="mdli mdnum">${m[1]}</div>`);
      i++;
      continue;
    }
    if (/^&gt;\s?/.test(l)) {
      out.push(`<blockquote class="mdq">${l.replace(/^&gt;\s?/, "")}</blockquote>`);
      i++;
      continue;
    }
    if (/^-{3,}$/.test(l.trim())) {
      out.push('<hr class="mdhr" />');
      i++;
      continue;
    }
    out.push(l);
    i++;
  }
  s = out.join("\n");
  s = s.replace(/\u0000B(\d+)\u0000/g, (_m, idx) => blocks[+idx]);
  return s;
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
