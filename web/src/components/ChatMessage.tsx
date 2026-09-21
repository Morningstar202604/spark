import { useState } from "react"
import Markdown from "./Markdown"
import type { ToolCall } from "../types"

export interface ChatItem {
  kind: "message" | "tool" | "notice"
  role: "user" | "assistant" | "error"
  text: string
  streaming?: boolean
  call?: ToolCall
  result?: unknown
  ok?: boolean
  images?: string[]
}

const roleMeta: Record<ChatItem["role"], { label: string; dot: string }> = {
  user: { label: "you", dot: "bg-spark-user" },
  assistant: { label: "spark", dot: "bg-spark-accent" },
  error: { label: "error", dot: "bg-spark-err" },
}

function looksLikeDiff(text: string): boolean {
  const lines = text.split("\n").filter(Boolean)
  return lines.length > 1 && lines.filter((l) => /^[+-]/.test(l)).length >= 2
}

function ToolCallBlock({ name, call, result, ok }: { name: string; call: ToolCall; result?: unknown; ok?: boolean }) {
  const [open, setOpen] = useState(false)
  const border = ok === undefined ? "border-spark-line" : ok ? "border-emerald-900/60" : "border-red-900/60"
  const resultText = result === undefined ? "" : typeof result === "string" ? result : JSON.stringify(result, null, 2)
  const isDiff = name === "apply_patch" || looksLikeDiff(resultText)
  return (
    <div className={`w-full max-w-4xl overflow-hidden rounded-lg border ${border} bg-spark-panel text-xs`}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className={`text-[10px] transition-transform ${open ? "rotate-90" : ""} text-spark-muted`}>▶</span>
        <span className="rounded bg-amber-950 px-1.5 py-0.5 font-mono text-[10px] font-bold text-amber-300">{name}</span>
        <span className="truncate font-mono text-[11px] text-spark-muted">
          {typeof call.arguments === "object" && call.arguments !== null
            ? String((call.arguments as Record<string, unknown>).path || (call.arguments as Record<string, unknown>).command || JSON.stringify(call.arguments).slice(0, 60))
            : ""}
        </span>
        <span className="ml-auto shrink-0 text-[10px]">
          {ok === false && <span className="text-spark-err">失败</span>}
          {ok === true && <span className="text-spark-ok">完成</span>}
          {ok === undefined && <span className="animate-pulse text-spark-tool">运行中…</span>}
        </span>
      </button>
      {open && (
        <div className="border-t border-spark-line px-3 py-2">
          <div className="mb-1 text-[10px] tracking-wider text-spark-muted uppercase">参数</div>
          <pre className="mb-2 overflow-auto rounded bg-[#0c1117] p-2 font-mono text-[11px] text-spark-muted whitespace-pre-wrap break-all">
            {JSON.stringify(call.arguments, null, 2)}
          </pre>
          {resultText && (
            <>
              <div className="mb-1 text-[10px] tracking-wider text-spark-muted uppercase">结果</div>
              {isDiff ? (
                <Markdown text={"```diff\n" + resultText.replace(/^"|"$/g, "") + "\n```"} />
              ) : (
                <pre className="max-h-64 overflow-auto rounded bg-[#0c1117] p-2 font-mono text-[11px] whitespace-pre-wrap break-all text-spark-text">
                  {resultText.slice(0, 4000)}
                </pre>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function ChatMessage({ item }: { item: ChatItem }) {
  if (item.kind === "tool") {
    return <ToolCallBlock name={item.call?.name || "tool"} call={item.call!} result={item.result} ok={item.ok} />
  }
  if (item.kind === "notice") {
    return (
      <div className="mx-auto flex w-fit max-w-full items-center gap-2 rounded-full border border-dashed border-spark-line bg-spark-panel/60 px-3.5 py-1.5 text-[11px] text-spark-muted">
        <span className="shrink-0 text-spark-accent">⟳</span>
        <span className="break-words">{item.text}</span>
      </div>
    )
  }
  const meta = roleMeta[item.role]
  const [copied, setCopied] = useState(false)
  return (
    <div
      className={`group w-full max-w-4xl rounded-xl border px-4 py-3 ${
        item.role === "user"
          ? "ml-auto w-fit max-w-[92%] border-blue-900/70 bg-blue-950/20"
          : item.role === "error"
            ? "border-red-900 bg-red-950/20"
            : "border-spark-line bg-spark-panel"
      }`}
    >
      <div className="mb-1.5 flex items-center gap-2">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${meta.dot}`} />
        <span className="text-[10px] font-bold tracking-widest text-spark-muted uppercase">{meta.label}</span>
        {item.streaming && <span className="h-3 w-1.5 animate-pulse bg-spark-accent" />}
        {item.role === "assistant" && !item.streaming && item.text && (
          <button
            type="button"
            onClick={() => {
              navigator.clipboard?.writeText(item.text)
              setCopied(true)
              setTimeout(() => setCopied(false), 1200)
            }}
            className="ml-auto shrink-0 rounded px-1.5 py-0.5 text-[10px] text-spark-muted transition-opacity hover:bg-spark-line md:opacity-0 md:group-hover:opacity-100"
          >
            {copied ? "已复制" : "复制"}
          </button>
        )}
      </div>
      {item.images && item.images.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {item.images.map((src, i) => (
            <img key={i} src={src} alt="attached" className="max-h-44 max-w-full rounded-lg border border-spark-line" />
          ))}
        </div>
      )}
      {item.role === "assistant" ? (
        <Markdown text={item.text || (item.streaming ? "" : "（空回复）")} />
      ) : (
        <div className="text-sm whitespace-pre-wrap break-words">{item.text}</div>
      )}
    </div>
  )
}
