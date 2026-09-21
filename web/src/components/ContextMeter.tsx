import type { ContextUsage } from "../types"

function fmt(n: number): string {
  if (n >= 1024) return `${(n / 1024).toFixed(1)}k`
  return String(n)
}

export default function ContextMeter({ usage }: { usage: ContextUsage | null | undefined }) {
  if (!usage || !usage.limit) return null
  const percent = Math.min(100, usage.percent)
  const color =
    percent >= 85 ? "bg-red-500" : percent >= 65 ? "bg-amber-400" : "bg-spark-accent"
  return (
    <div className="flex items-center gap-1.5" title={`上下文约 ${fmt(usage.used)} / ${fmt(usage.limit)} tokens，超过 ${85}% 自动压缩`}>
      <div className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-spark-line sm:block sm:w-24">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${Math.max(2, percent)}%` }} />
      </div>
      <span className={`text-[10px] tabular-nums sm:hidden ${percent >= 85 ? "text-spark-err" : "text-spark-muted"}`}>{percent.toFixed(0)}%</span>
      <span className={`hidden text-[10px] tabular-nums sm:inline ${percent >= 85 ? "text-spark-err" : "text-spark-muted"}`}>
        上下文 {percent.toFixed(0)}%
      </span>
    </div>
  )
}
