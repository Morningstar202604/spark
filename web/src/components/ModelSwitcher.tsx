import { useEffect, useRef, useState } from "react"
import type { ModelProfile, Status } from "../types"
import { activateModelProfile, listModelProfiles } from "../api"

interface Props {
  status: Status | null
  busy: boolean
  onSwitched: (s: Status) => void
  className?: string
  fullWidth?: boolean
}

export default function ModelSwitcher({ status, busy, onSwitched, className = "", fullWidth = false }: Props) {
  const [open, setOpen] = useState(false)
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [switching, setSwitching] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  async function refresh() {
    try {
      const data = await listModelProfiles()
      setProfiles(data.profiles)
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    if (open) refresh()
  }, [open])

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [])

  return (
    <div ref={ref} className={`relative shrink-0 ${fullWidth ? "w-full" : ""} ${className}`}>
      <button
        type="button"
        disabled={busy || switching}
        onClick={() => setOpen((v) => !v)}
        className={`flex max-w-36 items-center gap-1.5 rounded-lg bg-spark-line px-3 py-1.5 text-xs font-bold text-spark-text hover:opacity-80 disabled:opacity-50 sm:max-w-52 ${
          fullWidth ? "w-full justify-between" : ""
        }`}
      >
        <span className="truncate">{switching ? "切换中…" : (status?.model || "模型")}</span>
        <span className={`shrink-0 text-[9px] text-spark-muted transition-transform ${open ? "rotate-180" : ""}`}>▼</span>
      </button>
      {open && (
        <div className={`absolute top-full z-30 mt-1.5 max-h-72 w-64 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-spark-line bg-spark-panel py-1.5 shadow-xl ${fullWidth ? "left-0" : "right-0"}`}>
          {profiles.length === 0 && <div className="px-4 py-3 text-xs text-spark-muted">暂无模型档案，请在设置中添加。</div>}
          {profiles.map((p) => (
            <button
              key={p.id}
              type="button"
              disabled={switching}
              onClick={async () => {
                if (p.active) {
                  setOpen(false)
                  return
                }
                setSwitching(true)
                try {
                  const r = await activateModelProfile(p.id)
                  onSwitched(r.status)
                  setOpen(false)
                } finally {
                  setSwitching(false)
                }
              }}
              className={`flex w-full flex-col items-start gap-0.5 px-4 py-2 text-left hover:bg-spark-line disabled:opacity-50 ${
                p.active ? "bg-spark-accent/10" : ""
              }`}
            >
              <span className="flex w-full items-center gap-2 text-xs font-bold text-spark-text">
                <span className="truncate">{p.name}</span>
                {p.active && <span className="ml-auto shrink-0 text-[10px] text-spark-accent">●</span>}
              </span>
              <span className="w-full truncate text-[11px] text-spark-muted">
                {p.model} · {p.provider}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
