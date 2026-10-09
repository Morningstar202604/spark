import type { ApprovalInfo } from "../types"
import { t } from "../i18n"
import { useUiStore } from "../store/uiStore"

interface Props {
  approval: ApprovalInfo
  onDecide: (decision: "allow" | "allow_always" | "deny") => void
  busy: boolean
}

export default function ApprovalCard({ approval, onDecide, busy }: Props) {
  const lang = useUiStore((s) => s.lang)
  return (
    <section
      role="group"
      aria-live="polite"
      aria-labelledby={`approval-title-${approval.id ?? "pending"}`}
      className="max-w-full rounded-xl border border-spark-tool/55 bg-spark-panel sm:max-w-3xl"
    >
      <div
        id={`approval-title-${approval.id ?? "pending"}`}
        className="border-b border-spark-line px-4 py-2 text-[11px] font-bold tracking-widest text-spark-tool uppercase"
      >
        {t("approval.title", lang)}
      </div>
      <pre
        tabIndex={0}
        role="region"
        aria-label={t("approval.summary", lang)}
        className="max-h-64 overflow-auto px-4 py-3 font-mono text-xs whitespace-pre-wrap break-words text-spark-text"
      >
        {approval.summary}
        {approval.diff ? `\n\n${approval.diff}` : ""}
      </pre>
      <p id="allow-desc" className="sr-only">{t("approval.allowOnce", lang)}</p>
      <p id="allow-always-desc" className="sr-only">{t("approval.allowAlwaysDesc", lang)}</p>
      <p id="deny-desc" className="sr-only">{t("approval.deny", lang)}</p>
      <div className="flex flex-wrap gap-2 px-4 py-3">
        <button
          type="button"
          disabled={busy}
          aria-describedby="allow-desc"
          aria-disabled={busy || undefined}
          onClick={() => onDecide("allow")}
          className="rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-spark-on-accent hover:opacity-90 disabled:opacity-50"
        >
          {t("approval.allowOnce", lang)}
        </button>
        <button
          type="button"
          disabled={busy}
          aria-describedby="allow-always-desc"
          aria-disabled={busy || undefined}
          onClick={() => onDecide("allow_always")}
          title={t("approval.allowAlwaysDesc", lang)}
          className="rounded-lg border border-spark-line px-4 py-2 text-sm font-bold text-spark-text hover:opacity-80 disabled:opacity-50"
        >
          {t("approval.allowAlways", lang)}
        </button>
        <button
          type="button"
          disabled={busy}
          aria-describedby="deny-desc"
          aria-disabled={busy || undefined}
          onClick={() => onDecide("deny")}
          className="rounded-lg bg-spark-err/12 px-4 py-2 text-sm font-bold text-spark-err hover:opacity-80 disabled:opacity-50"
        >
          {t("approval.deny", lang)}
        </button>
      </div>
    </section>
  )
}
