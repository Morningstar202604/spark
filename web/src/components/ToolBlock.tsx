import { memo, useState } from "react"
import type { ToolCallState } from "../store/chatStore"
import { cls, Spinner } from "./ui"
import { IconCheck, IconChevronRight, IconFile, IconFolder, IconSearch, IconTerminal, IconX } from "./icons"
import Markdown from "./Markdown"

function toolIcon(name: string) {
  const n = name.toLowerCase()
  if (n.includes("shell") || n.includes("bg_") || n.includes("execute")) return <IconTerminal size={13} />
  if (n.includes("search") || n.includes("grep") || n.includes("glob") || n.includes("web")) return <IconSearch size={13} />
  if (n.includes("git") || n.includes("worktree")) return <IconFolder size={13} />
  return <IconFile size={13} />
}

/** 参数摘要：优先 path/command/file 等关键字段 */
function argSummary(args: unknown): string {
  if (typeof args !== "object" || args === null) return ""
  const a = args as Record<string, unknown>
  const pick = a.path ?? a.file ?? a.command ?? a.pattern ?? a.query ?? a.url ?? a.name
  if (typeof pick === "string" && pick) return pick
  const json = JSON.stringify(a)
  return json.length > 80 ? json.slice(0, 80) + "…" : json
}

function looksLikeDiff(text: string): boolean {
  const lines = text.split("\n")
  return lines.some((l) => l.startsWith("@@")) && (lines.some((l) => l.startsWith("+")) || lines.some((l) => l.startsWith("-")))
}

export const ToolBlock = memo(function ToolBlock({ tool }: { tool: ToolCallState }) {
  const [open, setOpen] = useState(false)
  const { name, status, result, arguments: args, durationMs } = tool
  const running = status === "running"

  const resultText = result === undefined ? "" : typeof result === "string" ? result : JSON.stringify(result, null, 2)

  return (
    <div
      className={cls(
        "my-1.5 overflow-hidden rounded-lg border bg-panel transition-colors",
        running ? "border-line2" : status === "ok" ? "border-line" : "border-err/40",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cls(
          "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors",
          running ? "bg-panel" : "bg-panel hover:bg-hover",
        )}
        aria-expanded={open}
      >
        <span className={cls("shrink-0 text-ink3 transition-transform", open && "rotate-90")}>
          <IconChevronRight size={12} />
        </span>
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-hover text-ink2">{toolIcon(name)}</span>
        <span className="font-mono text-xs font-semibold text-ink">{name}</span>
        <span className="min-w-0 truncate font-mono text-[11px] text-ink3">{argSummary(args)}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {running ? (
            <>
              <Spinner size={11} className="text-accent" />
              <span className="text-[11px] text-ink2">运行中</span>
            </>
          ) : status === "ok" ? (
            <>
              <IconCheck size={12} className="text-ok" />
              <span className="text-[11px] text-ok">完成</span>
            </>
          ) : (
            <>
              <IconX size={12} className="text-err" />
              <span className="text-[11px] text-err">失败</span>
            </>
          )}
          {!running && durationMs !== undefined && <span className="font-mono text-[10px] text-ink3">{(durationMs / 1000).toFixed(1)}s</span>}
        </span>
      </button>

      {open && (
        <div className="anim-fade border-t border-line px-3 py-2.5">
          <div className="mb-1 text-[10px] font-semibold tracking-wider text-ink3 uppercase">参数</div>
          <pre className="max-h-56 overflow-auto rounded-md bg-code p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-ink2">
            {JSON.stringify(args, null, 2)}
          </pre>
          {resultText && (
            <>
              <div className="mt-2 mb-1 text-[10px] font-semibold tracking-wider text-ink3 uppercase">
                结果{resultText.length > 4000 ? `（截断 ${resultText.length} 字符）` : ""}
              </div>
              {looksLikeDiff(resultText) ? (
                <Markdown text={"```diff\n" + resultText.replace(/^"|"$/g, "").slice(0, 12000) + "\n```"} />
              ) : (
                <pre className="max-h-72 overflow-auto rounded-md bg-code p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-ink2">
                  {resultText.slice(0, 4000)}
                </pre>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
})

export default ToolBlock
