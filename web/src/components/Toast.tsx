import { useEffect, useRef } from "react"

export interface ToastItem {
  id: number
  text: string
  kind: "error" | "info" | "ok"
}

const kindStyle: Record<ToastItem["kind"], string> = {
  error: "border-spark-err/45 bg-spark-err/10 text-spark-err",
  info: "border-spark-accent/45 bg-spark-panel text-spark-accent",
  ok: "border-spark-ok/45 bg-spark-ok/10 text-spark-ok",
}

const kindIcon: Record<ToastItem["kind"], string> = {
  error: "❌",
  info: "ℹ",
  ok: "✓",
}

function durationFor(text: string): number {
  return Math.min(3000 + Math.floor(text.length / 20) * 1000, 10000)
}

export default function Toast({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: number) => void }) {
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

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
        }, durationFor(t.text))
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
    <div role="status" aria-live="polite" className="pointer-events-none fixed top-3 left-1/2 z-50 flex w-[92%] max-w-sm -translate-x-1/2 flex-col gap-2">
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onDismiss(t.id)}
          className={`animate-in pointer-events-auto flex items-center gap-2 w-full rounded-xl border px-4 py-2.5 text-left text-xs leading-relaxed shadow-lg transition-opacity hover:opacity-80 ${kindStyle[t.kind]}`}
        >
          <span className="text-sm shrink-0" aria-hidden>{kindIcon[t.kind]}</span>
          <span className="flex-1">{t.text}</span>
        </button>
      ))}
    </div>
  )
}
