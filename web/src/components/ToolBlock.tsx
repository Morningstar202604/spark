import { memo, useState } from "react"
import type { ToolCallState } from "../store/chatStore"
import { cls, Spinner } from "./ui"
import { IconCheck, IconChevronRight, IconCommand, IconFile, IconFolder, IconGlobe, IconSearch, IconTerminal, IconX } from "./icons"
import { DiffView } from "./DiffView"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/** 工具 → 卡片类型分发（IDE 工件语义） */
type CardKind = "diff" | "terminal" | "subagent" | "web" | "default"

function toolKind(name: string, resultText: string): CardKind {
  const n = name.toLowerCase()
  if (n === "git_diff" || n === "apply_patch" || n === "write_file") return "diff"
  if (n.includes("shell") || n.startsWith("bg_") || n === "run_tests") return "terminal"
  if (n === "task") return "subagent"
  if (n.includes("web") || n.includes("search") || n === "browse") return "web"
  if (looksLikeDiff(resultText)) return "diff"
  return "default"
}

function toolIcon(name: string) {
  const n = name.toLowerCase()
  if (n.includes("shell") || n.startsWith("bg_") || n === "run_tests") return <IconTerminal size={13} />
  if (n.includes("search") || n.includes("grep") || n.includes("glob")) return <IconSearch size={13} />
  if (n.includes("git") || n.includes("worktree")) return <IconFolder size={13} />
  if (n.includes("web") || n === "browse") return <IconGlobe size={13} />
  if (n === "task") return <IconCommand size={13} />
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

function resultTextOf(tool: ToolCallState): string {
  const { result } = tool
  if (result === undefined) return ""
  if (typeof result === "string") return result
  try {
    return JSON.stringify(result, null, 2)
  } catch {
    return String(result)
  }
}

function SubagentCard({ resultText }: { resultText: string }) {
  // task 工具返回子代理结构（summary/result 等）——尽量展示语义化信息
  let obj: Record<string, unknown> | null = null
  try {
    obj = JSON.parse(resultText)
  } catch {
    /* 非 JSON 直接文本展示 */
  }
  const summary = obj ? (obj.summary ?? obj.result ?? obj.output) : null
  return (
    <div className="rounded-md bg-code px-3 py-2.5 font-mono text-[11px] whitespace-pre-wrap break-all text-ink2">
      {typeof summary === "string" ? summary : resultText.slice(0, 1200)}
    </div>
  )
}

export const ToolBlock = memo(function ToolBlock({ tool }: { tool: ToolCallState }) {
  const [open, setOpen] = useState(false)
  const lang = useUiStore((s) => s.lang)
  const { name, status, arguments: args, durationMs } = tool
  const running = status === "running"
  const resultText = resultTextOf(tool)
  const kind = toolKind(name, resultText)

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
              <span className="text-[11px] text-ink2">{t("common.running", lang)}</span>
            </>
          ) : status === "ok" ? (
            <>
              <IconCheck size={12} className="text-ok" />
              <span className="text-[11px] text-ok">{t("common.completed", lang)}</span>
            </>
          ) : (
            <>
              <IconX size={12} className="text-err" />
              <span className="text-[11px] text-err">{t("common.failed", lang)}</span>
            </>
          )}
          {!running && durationMs !== undefined && <span className="font-mono text-[10px] text-ink3">{(durationMs / 1000).toFixed(1)}s</span>}
        </span>
      </button>

      {open && (
        <div className="anim-fade border-t border-line px-3 py-2.5">
          <div className="mb-1 text-[10px] font-semibold tracking-wider text-ink3 uppercase">{t("common.args", lang)}</div>
          <pre className="max-h-56 overflow-auto rounded-md bg-code p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-ink2">
            {JSON.stringify(args, null, 2)}
          </pre>

          {resultText && (
            <>
              <div className="mt-2 mb-1 text-[10px] font-semibold tracking-wider text-ink3 uppercase">
                {t("tool.result", lang)}{resultText.length > 6000 ? t("tool.truncated", lang, { n: resultText.length }) : ""}
              </div>

              {kind === "diff" ? (
                <DiffView diff={resultText} />
              ) : kind === "terminal" ? (
                <div className="overflow-hidden rounded-lg border border-line bg-code">
                  <div className="flex items-center gap-1.5 border-b border-line bg-panel px-3 py-1.5">
                    <span className="h-2 w-2 rounded-full bg-err/70" />
                    <span className="h-2 w-2 rounded-full bg-warn/70" />
                    <span className="h-2 w-2 rounded-full bg-ok/70" />
                    <span className="ml-1 font-mono text-[10px] text-ink3">{name}</span>
                  </div>
                  <pre className="max-h-72 overflow-auto p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-ink2">
                    {resultText.slice(0, 6000)}
                  </pre>
                </div>
              ) : kind === "subagent" ? (
                <SubagentCard resultText={resultText} />
              ) : (
                <pre className="max-h-72 overflow-auto rounded-md bg-code p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-ink2">
                  {resultText.slice(0, 6000)}
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
