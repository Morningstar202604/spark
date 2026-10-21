import { memo } from "react"
import type { SessionRow } from "../types"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { ContextMeter } from "./ContextMeter"
import { IconButton } from "./ui"
import { IconCommand, IconFolder, IconFork, IconGlobe, IconMenu, IconSpark, IconTerminal } from "./icons"
import { t, type Lang } from "../i18n"

export const Header = memo(function Header() {
  const status = useSessionStore((s) => s.status)
  const handleCancel = useSessionStore((s) => s.handleCancel)
  const streaming = useChatStoreStreaming()
  const lang = useUiStore((s) => s.lang)
  const toggleSidebar = useUiStore((s) => s.toggleSidebar)
  const setPaletteOpen = useUiStore((s) => s.setPaletteOpen)
  const openFiles = useUiStore((s) => s.openFiles)
  const openBrowser = useUiStore((s) => s.openBrowser)
  const openTerminal = useUiStore((s) => s.openTerminal)
  const openGraph = useUiStore((s) => s.openGraph)

  const sessions = useSessionStore((s) => s.sessions)
  const title = (status && status.session_id && sessionTitle(lang, sessions, status.session_id)) || t("common.newChat", lang)

  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b border-line bg-canvas/90 px-2 backdrop-blur-sm sm:px-3">
      <IconButton title={t("header.toggleSidebar", lang)} onClick={toggleSidebar} className="lg:hidden">
        <IconMenu size={17} />
      </IconButton>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <p className="truncate text-[13px] font-semibold text-ink">{title}</p>
          {status?.demo_mode && (
            <span
              title={status.demo_reason}
              className="shrink-0 rounded-full border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[10px] font-medium text-warn"
            >
              {t("header.demo", lang)}
            </span>
          )}
        </div>
        {status?.workdir && (
          <p className="hidden truncate text-[10.5px] text-ink3 sm:block" title={status.workdir}>
            {status.workdir}
          </p>
        )}
      </div>

      {streaming && (
        <button
          type="button"
          onClick={handleCancel}
          className="mr-1 flex items-center gap-1.5 rounded-full bg-hover px-2.5 py-1 text-[11px] font-medium text-ink2 transition-colors hover:bg-selected"
        >
          <span className="h-2 w-2 rounded-full bg-err" />
          <span className="hidden sm:inline">{t("common.stop", lang)}</span>
        </button>
      )}

      <ContextMeter />
      <IconButton title={t("files.title", lang)} onClick={openFiles}>
        <IconFolder size={16} />
      </IconButton>
      <IconButton title={t("browser.title", lang)} onClick={openBrowser} className="hidden sm:inline-flex">
        <IconGlobe size={16} />
      </IconButton>
      <IconButton title={t("graph.title", lang)} onClick={openGraph} className="hidden md:inline-flex">
        <IconSpark size={16} />
      </IconButton>
      <IconButton title={t("sidebar.fork", lang)} onClick={() => void useSessionStore.getState().handleFork()} className="hidden md:inline-flex">
        <IconFork size={16} />
      </IconButton>
      <IconButton title={`${t("terminal.title", lang)} (Ctrl+\`)`} onClick={openTerminal} className="hidden sm:inline-flex">
        <IconTerminal size={16} />
      </IconButton>
      <IconButton title={`${t("palette.title", lang)} (Ctrl+K)`} onClick={() => setPaletteOpen(true)}>
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
/** 会话标题：必须由组件订阅 sessions 后传入，否则侧栏改名后标题不刷新。 */
function sessionTitle(lang: Lang, sessions: SessionRow[], currentId: string): string {
  const row = sessions.find((r) => r.id === currentId)
  return row && row.title && row.title !== "untitled" ? row.title : t("common.newChat", lang)
}
