import { useEffect, useRef } from "react"
import { t, type TranslationKey } from "../i18n"
import { useUiStore } from "../store/uiStore"
import { cls } from "./ui"
import { IconCheck, IconInfo, IconX } from "./icons"

type Kind = "error" | "info" | "success"

const kindStyle: Record<Kind, string> = {
  error: "border-err/40 bg-err/10 text-err",
  info: "border-line2 bg-panel text-ink2",
  success: "border-ok/40 bg-ok/10 text-ok",
}

const kindIcon: Record<Kind, React.ReactNode> = {
  error: <IconX size={13} />,
  info: <IconInfo size={13} />,
  success: <IconCheck size={13} />,
}

export default function Toast({ toasts, onDismiss }: { toasts: Array<{ id: number; key: TranslationKey; kind: Kind; message?: string }>; onDismiss: (id: number) => void }) {
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())
  const lang = useUiStore((s) => s.lang)

  useEffect(() => {
    const active = new Set(toasts.map((t) => t.id))
    for (const [id, timer] of timersRef.current) {
      if (!active.has(id)) {
        clearTimeout(timer)
        timersRef.current.delete(id)
      }
    }
    for (const t of toasts) {
      if (!timersRef.current.has(t.id)) {
        const timer = setTimeout(() => {
          timersRef.current.delete(t.id)
          onDismiss(t.id)
        }, 3600)
        timersRef.current.set(t.id, timer)
      }
    }
  }, [toasts, onDismiss])

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      for (const timer of timers.values()) clearTimeout(timer)
      timers.clear()
    }
  }, [])

  if (toasts.length === 0) return null
  return (
    <div role="status" aria-live="polite" className="pointer-events-none fixed top-3 right-3 z-[60] flex w-[min(92vw,360px)] flex-col gap-2">
      {toasts.map((toast) => (
        <button
          key={toast.id}
          type="button"
          onClick={() => onDismiss(toast.id)}
          className={cls(
            "anim-toast pointer-events-auto flex w-full items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-left text-xs leading-relaxed shadow-lg backdrop-blur transition-opacity hover:opacity-85",
            kindStyle[toast.kind],
          )}
        >
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ink3/10">{kindIcon[toast.kind]}</span>
          <span className="flex-1">{toast.message || t(toast.key, lang)}</span>
        </button>
      ))}
    </div>
  )
}
