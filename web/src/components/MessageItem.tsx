import { lazy, memo, Suspense, useCallback, useState } from "react"
import type { ChatMsg, Segment } from "../store/chatStore"
// Markdown + highlight.js are only needed once an assistant message with text
// exists, so they are not part of the first paint (saves ~112 KB gzip).
const Markdown = lazy(() => import("./Markdown"))
import ToolBlock from "./ToolBlock"
import { ThinkingBlock } from "./ThinkingBlock"
import { PlanCard } from "./PlanCard"
import { IconCheck, IconCopy, IconSpark } from "./icons"
import { cls } from "./ui"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

function useCopy(text: string) {
  const [copied, setCopied] = useState(false)
  const copy = useCallback(() => {
    navigator.clipboard?.writeText(text).catch(() => {})
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1400)
  }, [text])
  return { copied, copy }
}

/* ---------------- 用户消息：右侧气泡 ---------------- */
const UserMessage = memo(function UserMessage({ msg }: { msg: ChatMsg }) {
  const lang = useUiStore((s) => s.lang)
  return (
    <div className="flex justify-end gap-3 py-3">
      <div className="max-w-[86%] sm:max-w-[70%]">
        {msg.images && msg.images.length > 0 && (
          <div className="mb-2 flex flex-wrap justify-end gap-2">
            {msg.images.map((src, i) => (
              <img
                key={i}
                src={src}
                alt={`attached-${i + 1}`}
                className="max-h-44 max-w-full rounded-lg border border-line object-cover"
              />
            ))}
          </div>
        )}
        <div className="anim-msg rounded-2xl rounded-br-md bg-[var(--user-bubble)] px-4 py-2.5 text-[14px] leading-relaxed whitespace-pre-wrap break-words text-[var(--user-bubble-text)]">
          {msg.text}
        </div>
      </div>
      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-hover text-[12px] font-semibold text-ink2">
        {t("chat.you", lang)}
      </div>
    </div>
  )
})

/* ---------------- 助手消息 ---------------- */
function SegmentRenderer({ seg, streaming }: { seg: Segment; streaming?: boolean }) {
  switch (seg.kind) {
    case "thinking":
      return <ThinkingBlock text={seg.text} streaming={streaming} />
    case "plan":
      return <PlanCard steps={seg.steps} />
    case "tool":
      return <ToolBlock tool={seg.tool} />
    case "text":
      if (!seg.text) return null
      return (
        <div className={cls("md-body-wrap", streaming && "caret")}>
          <Suspense fallback={<span className="whitespace-pre-wrap">{seg.text}</span>}>
            <Markdown text={seg.text} />
          </Suspense>
        </div>
      )
  }
}

const AssistantMessage = memo(function AssistantMessage({ msg }: { msg: ChatMsg }) {
  const { copied, copy } = useCopy(msg.text)
  const lang = useUiStore((s) => s.lang)
  const lastTextStreaming = msg.streaming && msg.segments.some((s) => s.kind === "text" && s.text.length > 0)
  const d = useSessionStore((s) => s.status?.display)
  const show = {
    thinking: d?.show_thinking ?? true,
    tools: d?.show_tools ?? true,
    plan: d?.show_plan ?? true,
    notices: d?.show_notices ?? true,
  }
  const segments = show.tools && show.plan && show.thinking ? msg.segments : msg.segments.filter((s) => (s.kind === "tool" && !show.tools) || (s.kind === "plan" && !show.plan) || (s.kind === "thinking" && !show.thinking) ? false : true)

  return (
    <div className="group flex gap-3 py-3">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-soft">
        <IconSpark size={15} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[12px] font-semibold text-ink">Spark</span>
          {msg.streaming && !lastTextStreaming && (
            <span className="flex items-center gap-1 text-[11px] text-accent">
              <span className="flex gap-0.5">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="h-1 w-1 rounded-full bg-accent"
                    style={{ animation: `pulse-dot 1.2s ease-in-out ${i * 0.2}s infinite` }}
                  />
                ))}
              </span>
              {t("chat.working", lang)}
            </span>
          )}
          {msg.text && !msg.streaming && (
            <button
              type="button"
              onClick={copy}
              title={t("chat.copyAll", lang)}
              className="ml-auto flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-ink3 opacity-0 transition-opacity hover:bg-hover hover:text-ink group-hover:opacity-100 focus-visible:opacity-100"
            >
              {copied ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
              {copied ? t("common.copied", lang) : t("common.copy", lang)}
            </button>
          )}
        </div>
        <div className="min-w-0 space-y-1">
          {segments.map((seg, i) => (
            <SegmentRenderer key={i} seg={seg} streaming={msg.streaming && i === segments.length - 1} />
          ))}
          {msg.streaming && segments.length === 0 && (
            <div className="flex items-center gap-2 py-1">
              <span className="flex gap-0.5">
                {[0, 1, 2].map((i) => (
                  <span key={i} className="h-1.5 w-1.5 rounded-full bg-accent" style={{ animation: `pulse-dot 1.2s ease-in-out ${i * 0.2}s infinite` }} />
                ))}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
})

/* ---------------- 错误 / 通知 / 摘要 ---------------- */
const ErrorMessage = memo(function ErrorMessage({ text }: { text: string }) {
  const lang = useUiStore((s) => s.lang)
  return (
    <div className="flex justify-center py-3">
      <div className="anim-msg w-full max-w-2xl rounded-xl border border-err/35 bg-err/8 px-4 py-2.5 text-[13px] text-err">
        <div className="mb-0.5 font-semibold">{t("common.error", lang)}</div>
        <p className="whitespace-pre-wrap break-words text-err/90">{text}</p>
      </div>
    </div>
  )
})

const NoticeMessage = memo(function NoticeMessage({ msg }: { msg: ChatMsg }) {
  const show = useSessionStore((s) => s.status?.display?.show_notices ?? true)
  if (!show) return null
  return (
    <div className="flex justify-center py-2">
      <div className="anim-msg flex items-center gap-2 rounded-full border border-line bg-panel2 px-3.5 py-1.5 text-[11.5px] text-ink3">
        {msg.meta?.compacted ? <span className="text-warn">⟳</span> : <span>·</span>}
        <span className="break-words">{msg.text}</span>
      </div>
    </div>
  )
})

export default function MessageItem({ msg }: { msg: ChatMsg }) {
  switch (msg.role) {
    case "user":
      return <UserMessage msg={msg} />
    case "assistant":
      return <AssistantMessage msg={msg} />
    case "error":
      return <ErrorMessage text={msg.text} />
    case "notice":
    case "summary":
      return <NoticeMessage msg={msg} />
  }
}
