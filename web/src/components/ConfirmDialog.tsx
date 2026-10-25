import { useCallback, useEffect, useRef } from "react"
import { useUiStore } from "../store/uiStore"
import { Button, cls } from "./ui"
import { t } from "../i18n"

/**
 * 全站统一的确认对话框：替代原生 window.confirm（后者在移动端 / WebView
 * 里样式不统一、文案不可控，还会打断键盘焦点流）。状态在 uiStore，
 * 任何组件都可以 requestConfirm({...}) 弹出一个风格一致的确认。
 */
export function ConfirmDialog() {
  const req = useUiStore((s) => s.confirm)
  const dismiss = useUiStore((s) => s.dismissConfirm)
  const lang = useUiStore((s) => s.lang)
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (req) confirmRef.current?.focus()
  }, [req])

  const confirm = useCallback(() => {
    if (!req) return
    const fn = req.onConfirm
    dismiss()
    fn()
  }, [req, dismiss])

  useEffect(() => {
    if (!req) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault()
        dismiss()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [req, dismiss])

  if (!req) return null

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/45 p-4 backdrop-blur-[2px] anim-fade"
      onClick={dismiss}
      role="dialog"
      aria-modal="true"
      aria-label={req.title}
    >
      <div
        className="anim-fade-up w-full max-w-sm overflow-hidden rounded-2xl border border-line bg-panel shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 pt-4 pb-3">
          <h2 className="text-[15px] font-semibold text-ink">{req.title}</h2>
          {req.message && <p className="mt-1.5 text-[13px] leading-relaxed break-words text-ink2">{req.message}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-line bg-panel2/60 px-5 py-3">
          <Button size="sm" onClick={dismiss}>
            {req.cancelLabel ?? t("common.cancel", lang)}
          </Button>
          <button
            ref={confirmRef}
            type="button"
            onClick={confirm}
            className={cls(
              "inline-flex h-7 items-center justify-center rounded-lg px-2.5 text-xs font-medium transition-colors",
              req.danger === false
                ? "bg-accent text-on-accent hover:brightness-110"
                : "bg-err/12 text-err hover:bg-err/20",
            )}
          >
            {req.confirmLabel ?? t("common.delete", lang)}
          </button>
        </div>
      </div>
    </div>
  )
}
