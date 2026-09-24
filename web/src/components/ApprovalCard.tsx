import type { ApprovalInfo } from "../types"

interface Props {
  approval: ApprovalInfo
  onDecide: (decision: "allow" | "allow_always" | "deny") => void
  busy: boolean
}

export default function ApprovalCard({ approval, onDecide, busy }: Props) {
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
        需要审批 · Approval
      </div>
      <pre
        tabIndex={0}
        role="region"
        aria-label="待审批操作详情，可滚动"
        className="max-h-64 overflow-auto px-4 py-3 font-mono text-xs whitespace-pre-wrap break-words text-spark-text"
      >
        {approval.summary}
        {approval.diff ? `\n\n${approval.diff}` : ""}
      </pre>
      <div className="flex flex-wrap gap-2 px-4 py-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide("allow")}
          className="rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-spark-on-accent hover:opacity-90 disabled:opacity-50"
        >
          允许本次
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide("allow_always")}
          title="仅对完全相同的工具与参数生效"
          className="rounded-lg border border-spark-line px-4 py-2 text-sm font-bold text-spark-text hover:opacity-80 disabled:opacity-50"
        >
          始终允许（相同操作）
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide("deny")}
          className="rounded-lg bg-spark-err/12 px-4 py-2 text-sm font-bold text-spark-err hover:opacity-80 disabled:opacity-50"
        >
          拒绝
        </button>
      </div>
    </section>
  )
}
