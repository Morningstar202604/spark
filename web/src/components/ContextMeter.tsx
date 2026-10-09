import type { ContextUsage } from "../types"
import { t } from "../i18n"
import { useUiStore } from "../store/uiStore"

function fmt(n: number): string {
  if (n >= 1024) return `${(n / 1024).toFixed(1)}k`
  return String(n)
}

export default function ContextMeter({ usage }: { usage: ContextUsage | null | undefined }) {
  const lang = useUiStore((s) => s.lang)
  if (!usage || !usage.limit) return null
  const percent = Math.min(100, usage.percent || 0)
  const color =
    percent >= 85 ? "bg-red-500" : percent >= 65 ? "bg-amber-400" : "bg-spark-accent"
  return (
    <div className="flex items-center gap-1.5" title={t("context.title", lang, { used: fmt(usage.used), limit: fmt(usage.limit), threshold: 85 })}>
      <div className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-spark-line sm:block sm:w-24">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${Math.max(2, percent)}%` }} />
      </div>
      <span className={`text-[10px] tabular-nums sm:hidden ${percent >= 85 ? "text-spark-err" : "text-spark-muted"}`}>{percent.toFixed(0)}%</span>
      <span className={`hidden text-[10px] tabular-nums sm:inline ${percent >= 85 ? "text-spark-err" : "text-spark-muted"}`}>
        {t("context.used", lang, { percent: percent.toFixed(0) })}
      </span>
    </div>
  )
}
