import { memo, useMemo } from "react"
import { cls } from "./ui"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

interface DiffLine {
  type: "file" | "header" | "hunk" | "context" | "add" | "remove"
  oldNo?: number
  newNo?: number
  text: string
}

/** 解析 unified diff 文本为渲染行（标准 git diff 输出格式）。 */
function parseDiff(raw: string): DiffLine[] {
  const lines = raw.split("\n")
  const out: DiffLine[] = []
  let oldBase = 0
  let newBase = 0
  let oldLine = 0
  let newLine = 0
  let currentFile = ""

  // `--- a/x` / `+++ b/x` 两行指的是同一个文件，只该显示一次文件名。
  // 早期把这两行原样当标题渲染，且对 `+++ b/x` 套用了 `b/` 后缀裁剪，
  // 结果界面上出现一行 `--- a/x` 加一行孤零零的 `+++`。
  const pushFile = (path: string) => {
    if (!path || path === "/dev/null") return
    if (currentFile === path) return
    currentFile = path
    out.push({ type: "file", text: path })
  }

  for (const line of lines) {
    if (line.startsWith("diff --git ")) {
      const m = line.match(/^diff --git a\/(.+?) b\//)
      pushFile(m ? m[1] : line.slice("diff --git ".length))
      continue
    }
    if (line.startsWith("index ") || line.startsWith("new file mode") || line.startsWith("deleted file mode")) {
      out.push({ type: "header", text: line })
      continue
    }
    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      const raw_path = line.slice(4).trim()
      pushFile(raw_path.replace(/^[ab]\//, ""))
      continue
    }
    if (line.startsWith("@@")) {
      const m = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
      oldBase = m ? Number(m[1]) : oldBase
      newBase = m ? Number(m[2]) : newBase
      oldLine = oldBase
      newLine = newBase
      out.push({ type: "hunk", text: line })
      continue
    }
    if (line.startsWith("+")) {
      out.push({ type: "add", newNo: newLine, text: line.slice(1) })
      newLine += 1
      continue
    }
    if (line.startsWith("-")) {
      out.push({ type: "remove", oldNo: oldLine, text: line.slice(1) })
      oldLine += 1
      continue
    }
    out.push({ type: "context", oldNo: oldLine, newNo: newLine, text: line })
    oldLine += 1
    newLine += 1
  }
  return out
}

/** 自绘 unified diff 视图（行号 + 增删高亮），用于工具结果与审批卡片。 */
export const DiffView = memo(function DiffView({ diff, maxLines = 400 }: { diff: string; maxLines?: number }) {
  const lang = useUiStore((s) => s.lang)
  const rows = useMemo(() => parseDiff(diff), [diff])
  const clipped = rows.length > maxLines
  const shown = clipped ? rows.slice(0, maxLines) : rows

  const stats = useMemo(() => {
    let add = 0
    let del = 0
    for (const r of rows) {
      if (r.type === "add") add += 1
      else if (r.type === "remove") del += 1
    }
    return { add, del }
  }, [rows])

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-code">
      <div className="flex items-center gap-2 border-b border-line bg-panel px-3 py-1.5">
        <span className="font-mono text-[10.5px] text-ink2">
          {stats.add > 0 && <span className="mr-2 text-ok">+{stats.add}</span>}
          {stats.del > 0 && <span className="text-err">-{stats.del}</span>}
        </span>
        <span className="ml-auto text-[10px] text-ink3">{clipped ? t("diff.truncated", lang, { n: rows.length }) : t("common.lines", lang, { n: rows.length })}</span>
      </div>
      <div className="max-h-[320px] overflow-auto font-mono text-[11px] leading-[1.5]">
        <table className="w-full border-collapse">
          <tbody>
            {shown.map((r, i) => {
              if (r.type === "file")
                return (
                  <tr key={i} className="border-y border-line bg-panel">
                    <td className="w-10" />
                    <td colSpan={2} className="px-2 py-0.5 font-semibold text-ink3">
                      {r.text}
                    </td>
                  </tr>
                )
              if (r.type === "header")
                return (
                  <tr key={i}>
                    <td className="w-10" />
                    <td colSpan={2} className="px-2 py-0.5 font-mono text-[10px] text-ink3/70">
                      {r.text}
                    </td>
                  </tr>
                )
              if (r.type === "hunk")
                return (
                  <tr key={i}>
                    <td className="w-10 bg-selected/60 text-right" />
                    <td colSpan={2} className="bg-selected/60 px-2 py-0.5 text-ink2">
                      {r.text}
                    </td>
                  </tr>
                )
              return (
                <tr
                  key={i}
                  className={cls(
                    r.type === "add" && "bg-ok/10",
                    r.type === "remove" && "bg-err/10",
                  )}
                >
                  <td className="w-10 select-none text-right text-ink3/70">
                    {r.type === "remove" ? r.oldNo : r.type === "context" ? r.oldNo : ""}
                  </td>
                  <td className="w-10 select-none text-right text-ink3/70">
                    {r.type === "add" ? r.newNo : r.type === "context" ? r.newNo : ""}
                  </td>
                  <td className={cls("px-2", r.type === "add" ? "text-ok" : r.type === "remove" ? "text-err" : "text-ink2")}>
                    <span className="select-none opacity-60">{r.type === "add" ? "+" : r.type === "remove" ? "−" : " "}</span>
                    {r.text}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {clipped && <p className="px-3 py-2 text-[10px] text-ink3">{t("diff.truncatedNote", lang)}</p>}
      </div>
    </div>
  )
})

export default DiffView
