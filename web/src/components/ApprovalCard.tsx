import type { ApprovalInfo } from "../types"

interface Props {
  approval: ApprovalInfo
  onDecide: (decision: "allow" | "allow_always" | "deny") => void
  busy: boolean
}

export default function ApprovalCard({ approval, onDecide, busy }: Props) {
  return (
    <div className="max-w-full rounded-xl border border-amber-700 bg-spark-panel sm:max-w-3xl">
      <div className="border-b border-spark-line px-4 py-2 text-[11px] font-bold tracking-widest text-spark-tool uppercase">
        需要审批 · Approval
      </div>
      <pre className="max-h-64 overflow-auto px-4 py-3 font-mono text-xs whitespace-pre-wrap break-words text-spark-text">
        {approval.summary}
        {approval.diff ? `\n\n${approval.diff}` : ""}
      </pre>
      <div className="flex flex-wrap gap-2 px-4 py-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide("allow")}
          className="rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-teal-950 hover:opacity-90 disabled:opacity-50"
        >
          允许
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide("allow_always")}
          className="rounded-lg bg-spark-line px-4 py-2 text-sm font-bold text-spark-text hover:opacity-80 disabled:opacity-50"
        >
          始终允许
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide("deny")}
          className="rounded-lg bg-red-950 px-4 py-2 text-sm font-bold text-spark-err hover:opacity-80 disabled:opacity-50"
        >
          拒绝
        </button>
      </div>
    </div>
  )
}
