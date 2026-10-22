import { memo, useEffect, useRef, useState } from "react"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { Button, cls } from "./ui"
import { IconInfo } from "./icons"
import { t } from "../i18n"

/**
 * ask_user 卡片：模型主动提问时弹出，回答后解除 agent 的阻塞。
 *
 * 与审批卡是两条独立通路：审批决定“是否执行”，提问获取“信息”。
 * 没有这张卡片时模型只能猜，或者在最终答复里反问（多一轮往返）。
 */
export const QuestionCard = memo(function QuestionCard() {
  const question = useSessionStore((s) => s.question)
  const handleAnswer = useSessionStore((s) => s.handleAnswer)
  const lang = useUiStore((s) => s.lang)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setText("")
    if (question) window.setTimeout(() => inputRef.current?.focus(), 50)
  }, [question])

  if (!question) return null

  async function submit(answer: string) {
    if (!answer.trim() || busy) return
    setBusy(true)
    try {
      await handleAnswer(answer.trim())
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-3 backdrop-blur-[2px] sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label={t("question.title", lang)}
    >
      <div className="anim-fade-up w-full max-w-lg overflow-hidden rounded-2xl border border-line bg-panel shadow-[var(--shadow)]">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-info/15 text-info">
            <IconInfo size={14} />
          </span>
          <span className="text-sm font-semibold text-ink">{t("question.title", lang)}</span>
        </div>
        <div className="px-4 py-3">
          <p className="whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-ink">
            {question.question}
          </p>
          {question.options.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {question.options.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  disabled={busy}
                  onClick={() => void submit(opt)}
                  className={cls(
                    "rounded-lg border border-line bg-panel2 px-3 py-1.5 text-[12.5px] text-ink transition-colors",
                    "hover:border-accent hover:bg-accent-soft hover:text-accent disabled:opacity-50",
                  )}
                >
                  {opt}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2 border-t border-line bg-panel2 px-3 py-3 sm:flex-row sm:items-center sm:px-4">
          <input
            ref={inputRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                void submit(text)
              }
            }}
            placeholder={t("question.placeholder", lang)}
            className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-canvas px-3 text-[13px] text-ink outline-none focus:border-accent"
          />
          <Button variant="primary" onClick={() => void submit(text)} disabled={busy || !text.trim()}>
            {t("question.send", lang)}
          </Button>
        </div>
      </div>
    </div>
  )
})
