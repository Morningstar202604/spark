import { memo, useEffect, useRef } from "react"
import type { ChatMsg } from "../store/chatStore"
import MessageItem from "./MessageItem"
import { EmptyState } from "./EmptyState"
import { VirtualMessageList } from "./VirtualMessageList"
import { useChatStore } from "../store/chatStore"
import { useUiStore } from "../store/uiStore"

/* ------------------------------------------------------------------ */
/* 虚拟化高度估算（P1-5 起由 @tanstack/react-virtual 驱动）             */
/* ------------------------------------------------------------------ */

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
/* ChatView：滚动容器已下沉到 VirtualMessageList（tanstack virtual）   */
/* ------------------------------------------------------------------ */

export const ChatView = memo(function ChatView() {
  const items = useChatStore((s) => s.items)
  const loading = useChatStore((s) => s.loading)
  const streaming = useChatStore((s) => s.streaming)
  const setInput = useChatStore((s) => s.setInput)
  const lang = useUiStore((s) => s.lang)
  const scrollRef = useRef<HTMLDivElement>(null)

  // 首屏直接到底（仅空列表/首屏场景）
  useEffect(() => {
    if (!loading && items.length > 0) return
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (loading) {
    return (
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <SkeletonList />
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto">
        <EmptyState onPick={(p: string) => setInput(p)} />
      </div>
    )
  }

  return (
    <VirtualMessageList
      items={items}
      estimateHeight={estimateHeight}
      renderItem={(m) => (
        <div className="mx-auto max-w-[780px] px-3 sm:px-4" lang={lang}>
          <MessageItem key={m.id} msg={m} />
        </div>
      )}
      footer={
        streaming ? (
          <div className="pb-2 pt-1 text-center text-[11px] text-ink3">正在生成…</div>
        ) : undefined
      }
    />
  )
})
