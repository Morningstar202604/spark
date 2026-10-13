import { lazy, Suspense, useCallback, useEffect } from "react"
import { Sidebar } from "./components/Sidebar"
import { Header } from "./components/Header"
import { ChatView } from "./components/ChatView"
import { Composer } from "./components/Composer"
import { ApprovalModal } from "./components/ApprovalModal"
import { CommandPalette } from "./components/CommandPalette"
import { CheckpointModal } from "./components/CheckpointModal"
import { FileViewer } from "./components/FileViewer"
import { BrowserModal } from "./components/BrowserModal"
import { Terminal } from "./components/Terminal"
import { ExecutionGraph } from "./components/ExecutionGraph"
import Toast from "./components/Toast"
import { fetchStatus } from "./api"
import type { Status } from "./types"
import { useChatStore } from "./store/chatStore"
import { useSessionStore } from "./store/sessionStore"
import { useUiStore } from "./store/uiStore"

const SettingsModal = lazy(() => import("./components/SettingsModal"))

export default function App() {
  const { setStatus, setPlan, refreshSessions } = useSessionStore()
  const { loadInitialHistory } = useChatStore()
  const { settingsOpen, closeSettings, toasts, dismissToast } = useUiStore()

  useEffect(() => {
    fetchStatus()
      .then((s) => {
        setStatus(s)
        setPlan(s.plan || [])
      })
      .catch(() => useUiStore.getState().pushToast("toast.backendError", "error"))
    refreshSessions()
    loadInitialHistory()
  }, [setStatus, setPlan, refreshSessions, loadInitialHistory])

  // Ctrl+N 新建会话 / Ctrl+\ 切换侧边栏
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") {
        e.preventDefault()
        if (!useChatStore.getState().streaming) void useSessionStore.getState().handleNewSession()
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "\\") {
        e.preventDefault()
        useUiStore.getState().toggleSidebar()
      }
      // Ctrl+` 切换内置终端（P1-5）
      if ((e.metaKey || e.ctrlKey) && e.key === "`") {
        e.preventDefault()
        const s = useUiStore.getState()
        if (s.terminalOpen) s.closeTerminal()
        else s.openTerminal()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const handleSaved = useCallback(
    (s: Status) => {
      setStatus(s)
      setPlan(s.plan || [])
    },
    [setStatus, setPlan],
  )

  return (
    <div className="flex h-full overflow-hidden bg-canvas text-ink">
      <Toast toasts={toasts} onDismiss={dismissToast} />
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <ChatView />
        <Composer />
      </div>
      <ApprovalModal />
      <CommandPalette />
      <CheckpointModal />
      <FileViewer />
      <BrowserModal />
      <Terminal />
      <ExecutionGraph />
      {settingsOpen && (
        <Suspense fallback={null}>
          <SettingsModal onClose={closeSettings} onSaved={handleSaved} />
        </Suspense>
      )}
    </div>
  )
}
