import { useState } from "react"
import { t } from "../i18n"
import { useUiStore } from "../store/uiStore"

interface Props {
  text: string
  active: boolean
}

export default function ThinkingBlock({ text, active }: Props) {
  const lang = useUiStore((s) => s.lang)
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
          <span className="animate-pulse font-bold">{t("common.thinking", lang)}</span>
        ) : (
          <span className="font-bold">{t("common.thinkingProcess", lang)}</span>
        )}
        <span className="ml-auto text-[10px] text-spark-muted">{t("common.charCount", lang, { n: text.length })}</span>
      </button>
      {open && (
        <div className="max-h-56 overflow-auto border-t border-spark-user/25 px-3 py-2 whitespace-pre-wrap break-words text-spark-text/75">
          {text}
        </div>
      )}
    </div>
  )
}
