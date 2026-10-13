import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { ChatMsg } from "../store/chatStore"
import MessageItem from "./MessageItem"
import { EmptyState } from "./EmptyState"
import { useChatStore } from "../store/chatStore"
import { useUiStore } from "../store/uiStore"

/* ------------------------------------------------------------------ */
/* 虚拟化：消息过多时只渲染可视窗口                                     */
/* ------------------------------------------------------------------ */

const VIRTUAL_THRESHOLD = 40
const WINDOW = 22
const BUFFER = 9

function estimateHeight(m: ChatMsg): number {
  if (m.role === "user") {
    return 60 + (m.images?.length ?? 0) * 100 + Math.min(m.text.length / 70, 10) * 22
  }
  if (m.role === "assistant") {
    let h = 44
    for (const s of m.segments) {
      if (s.kind === "text") h += 22 + Math.min((s.text.length / 52) * 22, 260)
      else if (s.kind === "tool") h += 46
      else if (s.kind === "thinking") h += 38
      else if (s.kind === "plan") h += 46 + s.steps.length * 30
    }
    return h + (m.streaming ? 20 : 0)
  }
  if (m.role === "error") return 80
  return 44
}

function findIndex(offsets: number[], top: number): number {
  let lo = 0
  let hi = offsets.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (offsets[mid] <= top) lo = mid + 1
    else hi = mid - 1
  }
  return lo
}

function SkeletonList() {
  return (
    <div className="mx-auto max-w-[780px] space-y-4 px-4 py-6">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex gap-3">
          <div className="skeleton h-7 w-7 shrink-0 rounded-full" />
          <div className="flex-1 space-y-2">
            <div className="skeleton h-4 w-24" />
            <div className="skeleton h-4 w-full" />
            <div className="skeleton h-4 w-3/4" />
          </div>
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* ChatView：滚动容器 + 虚拟化 + stick-to-bottom + 骨架屏              */
/* ------------------------------------------------------------------ */

export const ChatView = memo(function ChatView() {
  const items = useChatStore((s) => s.items)
  const loading = useChatStore((s) => s.loading)
  const streaming = useChatStore((s) => s.streaming)
  const setInput = useChatStore((s) => s.setInput)
  const lang = useUiStore((s) => s.lang)

  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)

  const offsets = useMemo(() => {
    let acc = 0
    const arr: number[] = [0]
    for (const m of items) {
      acc += estimateHeight(m)
      arr.push(acc)
    }
    return arr
  }, [items])

  const total = offsets[offsets.length - 1] ?? 0
  const virtual = items.length > VIRTUAL_THRESHOLD
  const [range, setRange] = useState<{ start: number; end: number }>({ start: 0, end: Math.min(items.length, WINDOW + BUFFER * 2) })

  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      const el = e.currentTarget
      stickRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 160
      if (virtual && offsets.length) {
        const idx = findIndex(offsets, el.scrollTop)
        setRange({
          start: Math.max(0, idx - BUFFER),
          end: Math.min(items.length, idx + WINDOW),
        })
      }
    },
    [virtual, offsets, items.length],
  )

  // stick-to-bottom：流式 / 消息变化时自动跟随底部
  useEffect(() => {
    if (!stickRef.current) return
    const el = scrollRef.current
    if (!el) return
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight
    })
  }, [items, streaming, loading])

  // 首屏直接到底
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const sliceStart = virtual ? range.start : 0
  const sliceEnd = virtual ? range.end : items.length
  const topPad = virtual ? offsets[sliceStart] : 0
  const bottomPad = virtual ? Math.max(0, total - (offsets[sliceEnd] ?? total)) : 0

  if (loading) {
    return (
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <SkeletonList />
      </div>
    )
  }

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto overscroll-contain">
      {items.length === 0 ? (
        <EmptyState onPick={(p: string) => setInput(p)} />
      ) : (
        <div className="mx-auto max-w-[780px] px-3 sm:px-4" lang={lang}>
          {virtual && topPad > 0 && <div style={{ height: topPad }} aria-hidden="true" />}
          {items.slice(sliceStart, sliceEnd).map((m) => (
            <MessageItem key={m.id} msg={m} />
          ))}
          {virtual && bottomPad > 0 && <div style={{ height: bottomPad }} aria-hidden="true" />}
          {streaming && (
            <div className="pb-2 text-center text-[11px] text-ink3">正在生成…</div>
          )}
        </div>
      )}
    </div>
  )
})
