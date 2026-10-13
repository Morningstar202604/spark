import { memo, useState } from "react"
import type { ReactNode } from "react"
import type { PlanStep } from "../types"
import { cls } from "./ui"
import { IconCheck, IconChevronDown } from "./icons"

const statusMeta: Record<PlanStep["status"], { label: string; icon: ReactNode; cls: string }> = {
  pending: { label: "待办", icon: <span className="h-3 w-3 rounded-full border-2 border-ink3" />, cls: "text-ink3" },
  in_progress: { label: "进行中", icon: <span className="spin h-3 w-3 rounded-full border-2 border-accent border-t-transparent" />, cls: "text-accent" },
  completed: { label: "已完成", icon: <IconCheck size={13} className="text-ok" />, cls: "text-ok" },
}

/** 计划卡片：垂直步骤列表 + 进度条 */
export const PlanCard = memo(function PlanCard({ steps }: { steps: PlanStep[] }) {
  const [open, setOpen] = useState(true)
  if (!steps.length) return null
  const done = steps.filter((s) => s.status === "completed").length
  const pct = Math.round((done / steps.length) * 100)

  return (
    <div className="my-2 overflow-hidden rounded-xl border border-line bg-panel">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition-colors hover:bg-hover"
        aria-expanded={open}
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-accent-soft text-accent">
          <IconChevronDown size={13} className={cls("transition-transform", !open && "-rotate-90")} />
        </span>
        <span className="text-sm font-semibold text-ink">任务计划</span>
        <span className="text-[11px] text-ink3">
          {done}/{steps.length} 完成
        </span>
        <span className="ml-auto flex w-28 items-center gap-2">
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-hover">
            <span className="block h-full rounded-full bg-accent transition-all duration-500" style={{ width: `${pct}%` }} />
          </span>
          <span className="w-9 text-right font-mono text-[11px] text-ink3">{pct}%</span>
        </span>
      </button>
      {open && (
        <ol className="anim-fade border-t border-line px-3.5 py-2">
          {steps.map((s, i) => {
            const m = statusMeta[s.status]
            return (
              <li key={i} className="flex items-start gap-2.5 py-1.5">
                <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">{m.icon}</span>
                <span
                  className={cls(
                    "min-w-0 flex-1 text-[13px] leading-snug",
                    s.status === "completed" ? "text-ink3 line-through decoration-ink3/50" : s.status === "in_progress" ? "text-ink" : "text-ink2",
                  )}
                >
                  {s.title}
                </span>
                <span className={cls("shrink-0 text-[10px]", m.cls)}>{m.label}</span>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
})
