import { lazy, Suspense, useCallback, useEffect, useRef } from "react"
import ChatMessage from "./components/ChatMessage"
import Modal from "./components/Modal"
import SessionSidebar from "./components/SessionSidebar"
import ApprovalCard from "./components/ApprovalCard"
import InputBox from "./components/InputBox"
import ThinkingBlock from "./components/ThinkingBlock"
import Toast from "./components/Toast"
import ContextMeter from "./components/ContextMeter"
import PlanCard from "./components/PlanCard"
import Logo from "./components/Logo"
import {
  fetchStatus,
  listCheckpoints,
  rollbackCheckpoint,
  type CheckpointRow,
} from "./api"
import type { HistoryMessage } from "./types"
import { useChatStore } from "./store/chatStore"
import { useSessionStore } from "./store/sessionStore"
import { useUiStore } from "./store/uiStore"
import { t } from "./i18n"

const EXAMPLES = [
  "看看这个项目的结构，总结入口文件",
  "运行 pytest，告诉我失败原因",
  "把 README 里的安装步骤改成中文",
]

const SettingsPanel = lazy(() => import("./components/SettingsPanel"))

function historyToItems(messages: HistoryMessage[]) {
  return messages.map((m) =>
    m.role === "tool"
      ? { kind: "tool" as const, role: "assistant" as const, text: "", call: { id: "", name: m.name || "tool", arguments: {} as unknown }, result: m.content, ok: true }
      : { kind: "message" as const, role: m.role, text: m.content, images: m.images },
  )
}

function CheckpointDialog({ checkpoints, busy, streaming, onClose, onRollback }: {
  checkpoints: CheckpointRow[]
  busy: boolean
  streaming: boolean
  onClose: () => void
  onRollback: (id: number) => void
}) {
  return (
    <Modal labelledBy="checkpoint-dialog-title" onClose={onClose} className="animate-fade">
      <aside className="animate-in absolute top-0 right-0 flex h-full w-full flex-col border-l border-spark-line bg-spark-panel sm:w-[26rem]">
        <div className="flex items-center justify-between border-b border-spark-line px-4 py-3">
          <h2 id="checkpoint-dialog-title" className="text-base font-bold">检查点回滚</h2>
          <button
            data-modal-initial-focus
            type="button"
            onClick={onClose}
            className="rounded-lg bg-spark-line px-3 py-1.5 text-sm text-spark-text transition-colors hover:text-spark-accent"
          >
            关闭
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          <p className="mb-3 text-xs text-spark-muted">
            每次涉及写文件或执行命令的回合结束后自动创建检查点。回滚会同时恢复工作区文件与对话历史。
          </p>
          {busy && <p className="text-xs text-spark-muted">加载中…</p>}
          {!busy && checkpoints.length === 0 && <p className="text-xs text-spark-muted">暂无检查点。</p>}
          <ul className="flex flex-col gap-2">
            {checkpoints.map((cp) => (
              <li key={cp.id} className="rounded-lg border border-spark-line bg-spark-bg p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-bold text-spark-text">{cp.label || "checkpoint"}</div>
                    <div className="text-[10px] text-spark-muted">
                      #{cp.id} · {new Date(cp.created_at * 1000).toLocaleString("zh-CN")}
                    </div>
                  </div>
                  <button
                    type="button"
                    disabled={busy || streaming}
                    onClick={() => onRollback(cp.id)}
                    className="shrink-0 rounded-lg bg-spark-accent px-3 py-1.5 text-xs font-bold text-spark-on-accent transition-all hover:brightness-110 active:scale-95 disabled:opacity-50"
                  >
                    回滚
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </Modal>
  )
}

export default function App() {
  const { items, input, streaming, thinking, loading, setInput, send, loadInitialHistory, setItems, setThinking, addErrorItem } = useChatStore()
  const { status, sessions, plan, approval, approvalBusy, setStatus, setPlan, refreshSessions, handleNewSession, handleSwitch, handleDelete, handleCancel, handleApproval } = useSessionStore()
  const { settingsOpen, sidebarOpen, toasts, checkpoints, cpsOpen, cpsBusy, openSettings, closeSettings, toggleSidebar, setSidebarOpen, pushToast, dismissToast, openCheckpoints, closeCheckpoints, setCheckpoints, setCpsBusy } = useUiStore()
  const lang = useUiStore((s) => s.lang)

  const logRef = useRef<HTMLDivElement>(null)
  const interactedRef = useRef(false)

  const d = status?.display
  const show = {
    thinking: d?.show_thinking ?? true,
    tools: d?.show_tools ?? true,
    plan: d?.show_plan ?? true,
    context: d?.show_context ?? true,
    keywords: d?.show_keywords ?? true,
    notices: d?.show_notices ?? true,
  }
  const visibleItems = show.tools ? items : items.filter((it) => it.kind !== "tool")

  useEffect(() => {
    fetchStatus()
      .then((s) => {
        setStatus(s)
        setPlan(s.plan || [])
      })
      .catch(() => pushToast(t('common.error', lang) + ': Backend'))
    refreshSessions()
    loadInitialHistory()
  }, [])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [items, approval, thinking])

  const wrappedHandleNewSession = useCallback(async () => {
    if (useChatStore.getState().streaming) return
    interactedRef.current = true
    await handleNewSession()
  }, [handleNewSession])

  const wrappedHandleSwitch = useCallback(async (id: string) => {
    if (useChatStore.getState().streaming || id === useSessionStore.getState().status?.session_id) return
    interactedRef.current = true
    await handleSwitch(id)
  }, [handleSwitch])

  const wrappedHandleDelete = useCallback(async (id: string) => {
    if (useChatStore.getState().streaming) return
    interactedRef.current = true
    await handleDelete(id)
  }, [handleDelete])

  async function handleOpenCheckpoints() {
    openCheckpoints()
    try {
      const data = await listCheckpoints()
      setCheckpoints(data.checkpoints)
    } catch {
      setCheckpoints([])
      pushToast(t('sidebar.checkpoint', lang) + ' ' + t('common.error', lang))
    } finally {
      setCpsBusy(false)
    }
  }

  async function handleRollback(id: number) {
    if (streaming || cpsBusy) return
    if (!window.confirm(t('sidebar.checkpoint', lang) + ' | ' + t('common.confirm', lang))) return
    interactedRef.current = true
    setCpsBusy(true)
    try {
      const data = await rollbackCheckpoint(id)
      setStatus(data.status)
      setItems(historyToItems(data.messages))
      setThinking(null)
      setPlan(data.status.plan || [])
      closeCheckpoints()
      await refreshSessions()
    } catch (e) {
      addErrorItem(t('sidebar.checkpoint', lang) + ' ' + t('common.error', lang) + `: ${e instanceof Error ? e.message : String(e)}`)
      pushToast(t('sidebar.checkpoint', lang) + ' ' + t('common.error', lang))
    } finally {
      setCpsBusy(false)
    }
  }

  return (
    <div className="flex h-full overflow-x-hidden">
      <Toast toasts={toasts} onDismiss={dismissToast} />
      <SessionSidebar
        open={sidebarOpen}
        sessions={sessions}
        current={status?.session_id || ""}
        showKeywords={show.keywords}
        onClose={() => setSidebarOpen(false)}
        onSelect={wrappedHandleSwitch}
        onNew={wrappedHandleNewSession}
        onDelete={wrappedHandleDelete}
      />
      <div className="grid min-w-0 flex-1 grid-rows-[auto_1fr_auto]">
        <header className="border-b border-spark-line bg-spark-panel px-2 py-2 sm:px-4">
          <div className="flex items-center justify-between gap-1.5">
            <div className="flex min-w-0 items-center gap-0.5">
              <button
                type="button"
                onClick={toggleSidebar}
                title="切换会话栏"
                aria-label="切换会话栏"
                aria-expanded={sidebarOpen}
                aria-controls="session-sidebar"
                className="rounded-lg p-2 text-spark-muted transition-colors hover:bg-spark-line hover:text-spark-text"
              >
                <span className="block h-0.5 w-4 bg-current shadow-[0_5px_0_currentColor,0_-5px_0_currentColor]" />
              </button>
              <Logo />
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {streaming && (
                <button
                  type="button"
                  onClick={() => void handleCancel()}
                  title="停止生成"
                  className="flex h-9 w-9 animate-in items-center justify-center rounded-lg bg-spark-err/15 text-spark-err transition-colors hover:bg-spark-err/25"
                >
                  <span className="block h-3 w-3 rounded-[2px] bg-current" />
                </button>
              )}
              <ContextMeter usage={show.context ? status?.context : undefined} />
              <button
                type="button"
                title="检查点回滚"
                onClick={() => void handleOpenCheckpoints()}
                className="flex h-9 w-9 items-center justify-center rounded-lg bg-spark-line text-spark-text transition-colors hover:bg-spark-line/70 hover:text-spark-accent"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M3 3v5h5" />
                  <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
                  <path d="M12 7v5l4 2" />
                </svg>
              </button>
              <button
                type="button"
                title="设置"
                onClick={openSettings}
                className="flex h-9 w-9 items-center justify-center rounded-lg bg-spark-line text-spark-text transition-colors hover:bg-spark-line/70 hover:text-spark-accent"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <circle cx="12" cy="12" r="3" />
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                </svg>
              </button>
            </div>
          </div>
        </header>
        <div ref={logRef} className="overflow-x-hidden overflow-y-auto px-2.5 py-4 sm:px-4 sm:py-5">
          <div className="mx-auto flex min-w-0 max-w-4xl flex-col gap-3">
            {show.plan && plan.length > 0 && <PlanCard steps={plan} />}
            {loading && items.length === 0 && (
              <div className="space-y-4 p-4 animate-pulse">
                <div className="h-4 bg-spark-line rounded w-1/4"></div>
                <div className="h-4 bg-spark-line rounded w-1/2"></div>
                <div className="h-4 bg-spark-line rounded w-2/3"></div>
              </div>
            )}
            {!loading && items.length === 0 && !approval && !thinking && (
              <div className="mt-10 text-center sm:mt-20">
                <div className="flex justify-center">
                  <Logo size={44} withWordmark={false} />
                </div>
                <p className="mt-3 text-xl font-bold">有什么可以帮你？</p>
                <p className="mt-1 text-sm text-spark-muted">本地代码 · 联网检索 · 工具执行 · 长期记忆</p>
                <div className="mx-auto mt-6 grid max-w-2xl gap-2 sm:grid-cols-3">
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      type="button"
                      onClick={() => setInput(ex)}
                      className="rounded-xl border border-spark-line bg-spark-panel px-3 py-3 text-left text-xs leading-relaxed text-spark-muted hover:border-spark-accent/50 hover:text-spark-text"
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {visibleItems.map((item, i) => {
              const key = item.kind === "tool" && item.call?.id
                ? `tool-${item.call.id}`
                : `${item.kind}-${item.role}-${i}`
              return (
                <div key={key} className="animate-in">
                  <ChatMessage item={item} />
                </div>
              )
            })}
            {show.thinking && thinking && <ThinkingBlock text={thinking.text} active={thinking.active} />}
            {approval && <ApprovalCard approval={approval} onDecide={handleApproval} busy={approvalBusy} />}
          </div>
        </div>
        <InputBox
          value={input}
          onChange={setInput}
          onSubmit={(imgs) => void send(imgs)}
          onStop={() => void handleCancel()}
          streaming={streaming}
          model={status?.model}
          onOpenSettings={openSettings}
        />
      </div>
      {settingsOpen && (
        <Suspense fallback={null}>
          <SettingsPanel status={status} onClose={closeSettings} onSaved={(s) => setStatus(s)} />
        </Suspense>
      )}
      {cpsOpen && (
        <CheckpointDialog
          checkpoints={checkpoints}
          busy={cpsBusy}
          streaming={streaming}
          onClose={closeCheckpoints}
          onRollback={(id) => void handleRollback(id)}
        />
      )}
    </div>
  )
}
