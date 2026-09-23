import { useState } from "react"

interface Props {
  text: string
  active: boolean
}

export default function ThinkingBlock({ text, active }: Props) {
  const [open, setOpen] = useState(false)

  if (!text) return null
  return (
    <div className="w-full max-w-4xl overflow-hidden rounded-xl border border-spark-user/35 bg-spark-user/8 text-xs">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-spark-user"
      >
        <span className={`text-[10px] transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
        {active ? (
          <span className="animate-pulse font-bold">思考中…</span>
        ) : (
          <span className="font-bold">思考过程</span>
        )}
        <span className="ml-auto text-[10px] text-spark-muted">{text.length} 字</span>
      </button>
      {open && (
        <div className="max-h-56 overflow-auto border-t border-spark-user/25 px-3 py-2 whitespace-pre-wrap break-words text-spark-text/75">
          {text}
        </div>
      )}
    </div>
  )
}
