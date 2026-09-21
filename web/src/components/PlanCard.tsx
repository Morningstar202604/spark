import { useState } from "react"
import type { PlanStep } from "../types"

const ICONS: Record<PlanStep["status"], string> = {
  completed: "✓",
  in_progress: "◐",
  pending: "○",
}

const COLORS: Record<PlanStep["status"], string> = {
  completed: "text-spark-ok",
  in_progress: "text-spark-accent",
  pending: "text-spark-muted",
}

export default function PlanCard({ steps }: { steps: PlanStep[] }) {
  const [collapsed, setCollapsed] = useState(false)
  if (!steps.length) return null
  const done = steps.filter((s) => s.status === "completed").length
  return (
    <div className="rounded-xl border border-spark-line bg-spark-panel px-3 py-2 text-xs">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full items-center gap-2 text-left"
      >
        <span className="font-bold tracking-wider text-spark-accent uppercase">Plan</span>
        <span className="rounded bg-spark-line px-1.5 py-0.5 text-[10px] tabular-nums text-spark-muted">
          {done}/{steps.length}
        </span>
        <span className="ml-auto text-[10px] text-spark-muted">{collapsed ? "展开" : "收起"}</span>
      </button>
      {!collapsed && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {steps.map((s, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className={`shrink-0 ${COLORS[s.status]}`}>{ICONS[s.status]}</span>
              <span className={s.status === "completed" ? "text-spark-muted line-through" : "text-spark-text"}>
                {s.title}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
