import { memo } from "react"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { ContextMeter } from "./ContextMeter"
import { IconButton } from "./ui"
import { IconCommand, IconFolder, IconGlobe, IconMenu } from "./icons"

export const Header = memo(function Header() {
  const status = useSessionStore((s) => s.status)
  const handleCancel = useSessionStore((s) => s.handleCancel)
  const streaming = useChatStoreStreaming()
  const toggleSidebar = useUiStore((s) => s.toggleSidebar)
  const setPaletteOpen = useUiStore((s) => s.setPaletteOpen)
  const openFiles = useUiStore((s) => s.openFiles)
  const openBrowser = useUiStore((s) => s.openBrowser)

  const title = (status && status.session_id && sessionTitle()) || "新对话"

  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b border-line bg-canvas/90 px-2 backdrop-blur-sm sm:px-3">
      <IconButton title="切换侧边栏" onClick={toggleSidebar} className="lg:hidden">
        <IconMenu size={17} />
      </IconButton>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-semibold text-ink">{title}</p>
      </div>

      {streaming && (
        <button
          type="button"
          onClick={handleCancel}
          className="mr-1 hidden items-center gap-1.5 rounded-full bg-hover px-2.5 py-1 text-[11px] font-medium text-ink2 transition-colors hover:bg-selected sm:flex"
        >
          <span className="h-2 w-2 rounded-full bg-err" />
          停止
        </button>
      )}

      <ContextMeter />
      <IconButton title="文件查看器" onClick={openFiles}>
        <IconFolder size={16} />
      </IconButton>
      <IconButton title="网页阅读" onClick={openBrowser}>
        <IconGlobe size={16} />
      </IconButton>
      <IconButton title="命令面板 (Ctrl+K)" onClick={() => setPaletteOpen(true)}>
        <IconCommand size={16} />
      </IconButton>
    </header>
  )
})

import { useChatStore } from "../store/chatStore"
function useChatStoreStreaming() {
  return useChatStore((s) => s.streaming)
}

/** 会话标题：取当前会话标题（sessionStore.sessions 中匹配） */
function sessionTitle(): string {
  const s = useSessionStore.getState()
  const row = s.sessions.find((r) => r.id === s.status?.session_id)
  return row && row.title && row.title !== "untitled" ? row.title : "新对话"
}
