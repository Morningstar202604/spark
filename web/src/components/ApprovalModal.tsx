import { memo } from "react"
import { useSessionStore } from "../store/sessionStore"
import { Button, Spinner } from "./ui"
import { DiffView } from "./DiffView"
import { IconFileDiff } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/** 工具审批卡片：Claude Code 风格的底部操作审批 */
export const ApprovalModal = memo(function ApprovalModal() {
  const approval = useSessionStore((s) => s.approval)
  const busy = useSessionStore((s) => s.approvalBusy)
  const handleApproval = useSessionStore((s) => s.handleApproval)
  const lang = useUiStore((s) => s.lang)

  if (!approval) return null
  const { info, tool } = approval
  const hasDiff = Boolean(info.diff && info.diff.trim())
  const diff = info.diff ? info.diff.replace(/^"|"$/g, "") : ""

  return (
    <div className="fixed inset-x-0 bottom-0 z-50 flex justify-center p-3 sm:p-4">
      <div className="anim-fade-up w-full max-w-2xl overflow-hidden rounded-2xl border border-line bg-panel shadow-[var(--shadow)]">
        <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-warn/15 text-warn">
            {hasDiff ? <IconFileDiff size={14} /> : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M12 9v4M12 17h.01" />
                <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
              </svg>
            )}
          </span>
          <span className="text-sm font-semibold text-ink">{t("approval.title", lang)}</span>
          <span className="ml-auto rounded-md bg-hover px-2 py-0.5 font-mono text-[11px] text-ink2">{tool.name}</span>
        </div>

        <div className="max-h-[38vh] overflow-y-auto px-4 py-3">
          <p className="text-[13px] leading-relaxed text-ink2 whitespace-pre-wrap break-words">{info.summary}</p>
          {hasDiff && (
            <div className="mt-2">
              <DiffView diff={diff} />
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2 border-t border-line bg-panel2 px-4 py-3 sm:flex-row sm:items-center">
          <Button variant="ghost" full className="sm:flex-none" onClick={() => handleApproval("deny")} disabled={busy}>
            {t("approval.deny", lang)}
          </Button>
          <Button variant="outline" full className="sm:flex-none" onClick={() => handleApproval("allow")} disabled={busy}>
            {t("approval.allowOnce", lang)}
          </Button>
          <Button variant="primary" full onClick={() => handleApproval("allow_always")} disabled={busy}>
            {busy ? (
              <span className="flex items-center gap-1.5">
                <Spinner size={12} /> {t("approval.busy", lang)}
              </span>
            ) : (
              t("approval.allowAlways", lang)
            )}
          </Button>
        </div>
      </div>
    </div>
  )
})
