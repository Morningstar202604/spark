import { memo, useEffect, useRef, type ReactNode } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import type { ChatMsg } from "../store/chatStore"

/* ------------------------------------------------------------------ */
/* 长会话虚拟列表（P1-5）：@tanstack/react-virtual 替代手写偏移窗口。  */
/* 只渲染可视窗口内的消息，长会话（数百条）不卡。                     */
/* ------------------------------------------------------------------ */

interface Props {
  items: ChatMsg[]
  estimateHeight: (m: ChatMsg) => number
  renderItem: (m: ChatMsg) => ReactNode
  /** 贴底模式：消息/流式变化时自动滚到底部（用户上翻则取消贴底）。 */
  stickToBottom?: boolean
  footer?: ReactNode
}

export const VirtualMessageList = memo(function VirtualMessageList({
  items,
  estimateHeight,
  renderItem,
  stickToBottom = true,
  footer,
}: Props) {
  const parentRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(stickToBottom)

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (i) => estimateHeight(items[i]),
    overscan: 10,
  })

  // 贴底跟随：items / 流式变化时滚到底
  useEffect(() => {
    if (!stickRef.current) return
    if (items.length === 0) return
    virtualizer.scrollToIndex(items.length - 1, { align: "end" })
  }, [items, virtualizer])

  return (
    <div
      ref={parentRef}
      onScroll={(e) => {
        const el = e.currentTarget
        stickRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 120
      }}
      className="flex-1 overflow-y-auto overscroll-contain"
    >
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
          }}
        >
          {virtualizer.getVirtualItems().map((vi) => (
            <div
              key={items[vi.index].id}
              data-index={vi.index}
              ref={virtualizer.measureElement}
              style={{
                transform: `translateY(${vi.start}px)`,
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
              }}
            >
              {renderItem(items[vi.index])}
            </div>
          ))}
        </div>
      </div>
      {footer}
    </div>
  )
})
