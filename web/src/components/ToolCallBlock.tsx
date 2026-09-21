import { useState } from "react"
import type { ToolCall } from "../types"

interface Props {
  name: string
  call: ToolCall
  result?: unknown
  ok?: boolean
}

export default function ToolCallBlock({ name, call, result, ok }: Props) {
  const [open, setOpen] = useState(false)
  const border = ok === undefined ? "border-spark-line" : ok ? "border-spark-ok/40" : "border-spark-err/60"
  return (
    <div className={`max-w-full rounded-lg border ${border} bg-spark-panel font-mono text-xs sm:max-w-3xl`}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-spark-tool"
      >
        <span className={`transition-transform ${open ? "rotate-90" : ""}`}>▸</span>
        <span className="font-bold">{name}</span>
        {ok === false && <span className="rounded bg-red-950 px-1.5 py-0.5 text-[10px] font-bold text-spark-err">failed</span>}
        {ok === true && <span className="rounded bg-emerald-950 px-1.5 py-0.5 text-[10px] font-bold text-spark-ok">done</span>}
        {ok === undefined && <span className="animate-pulse text-spark-muted">running…</span>}
      </button>
      {open && (
        <div className="border-t border-spark-line px-3 py-2 whitespace-pre-wrap break-all text-spark-muted">
          <div>args: {JSON.stringify(call.arguments)}</div>
          {result !== undefined && <div className="mt-1">result: {JSON.stringify(result).slice(0, 2000)}</div>}
        </div>
      )}
    </div>
  )
}
