import { useEffect } from "react"

export interface ToastItem {
  id: number
  text: string
  kind: "error" | "info" | "ok"
}

const kindStyle: Record<ToastItem["kind"], string> = {
  error: "border-red-900 bg-red-950/90 text-red-200",
  info: "border-spark-line bg-spark-panel text-spark-text",
  ok: "border-emerald-900 bg-emerald-950/90 text-emerald-200",
}

export default function Toast({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: number) => void }) {
  useEffect(() => {
    if (toasts.length === 0) return
    const timers = toasts.map((t) => setTimeout(() => onDismiss(t.id), 5000))
    return () => timers.forEach(clearTimeout)
  }, [toasts, onDismiss])
  if (toasts.length === 0) return null
  return (
    <div className="pointer-events-none fixed top-3 left-1/2 z-50 flex w-[92%] max-w-sm -translate-x-1/2 flex-col gap-2">
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onDismiss(t.id)}
          className={`animate-in pointer-events-auto w-full rounded-xl border px-4 py-2.5 text-left text-xs leading-relaxed shadow-lg transition-opacity hover:opacity-80 ${kindStyle[t.kind]}`}
        >
          {t.text}
        </button>
      ))}
    </div>
  )
}
