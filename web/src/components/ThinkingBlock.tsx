import { useState } from "react"

interface Props {
  text: string
  active: boolean
}

export default function ThinkingBlock({ text, active }: Props) {
  const [open, setOpen] = useState(false)

  if (!text) return null
  return (
    <div className="w-full max-w-4xl overflow-hidden rounded-xl border border-sky-900/50 bg-sky-950/20 text-xs">      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sky-300"
      >
        <span className={`text-[10px] transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
        {active ? (
          <span className="animate-pulse font-bold">思考中…</span>
        ) : (
          <span className="font-bold">思考过程</span>
        )}
        <span className="ml-auto text-[10px] text-sky-400/60">{text.length} 字</span>
      </button>
      {open && (
        <div className="max-h-56 overflow-auto border-t border-sky-900/40 px-3 py-2 whitespace-pre-wrap break-words text-sky-200/70">
          {text}
        </div>
      )}
    </div>
  )
}
