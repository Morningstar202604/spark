import { useEffect, useRef, useState } from "react"
import ChatMessage, { type ChatItem } from "./components/ChatMessage"
import SettingsPanel from "./components/SettingsPanel"
import SessionSidebar from "./components/SessionSidebar"
import ApprovalCard from "./components/ApprovalCard"
import InputBox from "./components/InputBox"
import ThinkingBlock from "./components/ThinkingBlock"
import Toast, { type ToastItem } from "./components/Toast"
import ContextMeter from "./components/ContextMeter"
import PlanCard from "./components/PlanCard"
import Logo from "./components/Logo"
import {
  fetchStatus,
  streamChat,
  listSessions,
  newSession,
  switchSession,
  deleteSession,
  cancelTurn,
  respondApproval,
  listCheckpoints,
  rollbackCheckpoint,
  type CheckpointRow,
} from "./api"
import type { ApprovalInfo, ChatEvent, PlanStep, SessionRow, Status } from "./types"

const EXAMPLES = [
  "看看这个项目的结构，总结入口文件",
  "运行 pytest，告诉我失败原因",
  "把 README 里的安装步骤改成中文",
]

function historyToItems(messages: { role: "user" | "assistant" | "tool"; content: string; name?: string; images?: string[] }[]): ChatItem[] {
  return messages.map((m) =>
    m.role === "tool"
      ? { kind: "tool", role: "assistant", text: "", call: { id: "", name: m.name || "tool", arguments: {} }, result: m.content, ok: true }
      : { kind: "message", role: m.role, text: m.content, images: m.images },
  )
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [items, setItems] = useState<ChatItem[]>([])
  const [input, setInput] = useState("")
  const [streaming, setStreaming] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(window.innerWidth >= 768)
  const [sessions, setSessions] = useState<SessionRow[]>([])
  const [approval, setApproval] = useState<ApprovalInfo | null>(null)
  const [thinking, setThinking] = useState<{ text: string; active: boolean } | null>(null)
  const [plan, setPlan] = useState<PlanStep[]>([])
  const [cpsOpen, setCpsOpen] = useState(false)
  const [checkpoints, setCheckpoints] = useState<CheckpointRow[]>([])
  const [cpsBusy, setCpsBusy] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)
  const showNoticesRef = useRef(true)
  showNoticesRef.current = status?.display?.show_notices ?? true
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
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const toastId = useRef(0)
  function pushToast(text: string, kind: ToastItem["kind"] = "error") {
    toastId.current += 1
    const id = toastId.current
    setToasts((prev) => [...prev.slice(-2), { id, text, kind }])
  }
  const dismissToast = (id: number) => setToasts((prev) => prev.filter((t) => t.id !== id))

  function addErrorItem(text: string) {
    setItems((prev) => [...prev, { kind: "message", role: "error", text }])
  }

  useEffect(() => {
    fetchStatus()
      .then((s) => {
        setStatus(s)
        setPlan(s.plan || [])
      })
      .catch(() => pushToast("无法连接后端服务，请稍后重试或刷新页面"))
    refreshSessions()
  }, [])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [items, approval, thinking])

  function patchLast(mutate: (item: ChatItem) => ChatItem) {
    setItems((prev) => (prev.length === 0 ? prev : [...prev.slice(0, -1), mutate(prev[prev.length - 1])]))
  }

  async function refreshSessions() {
    try {
      const data = await listSessions()
      setSessions(data.sessions)
    } catch {
      /* ignore */
    }
  }

  async function openCheckpoints() {
    setCpsOpen(true)
    setCpsBusy(true)
    try {
      const data = await listCheckpoints()
      setCheckpoints(data.checkpoints)
    } catch {
      setCheckpoints([])
    } finally {
      setCpsBusy(false)
    }
  }

  async function handleRollback(id: number) {
    if (streaming || cpsBusy) return
    if (!window.confirm("回滚到该检查点？文件与会话都会恢复到当时状态，之后的对话将被移除。")) return
    setCpsBusy(true)
    try {
      const data = await rollbackCheckpoint(id)
      setStatus(data.status)
      setItems(historyToItems(data.messages))
      setThinking(null)
      setPlan(data.status.plan || [])
      setCpsOpen(false)
      await refreshSessions()
    } catch (e) {
      addErrorItem(`回滚失败: ${e instanceof Error ? e.message : String(e)}`)
      pushToast("回滚失败，请重试")
    } finally {
      setCpsBusy(false)
    }
  }

  async function handleNewSession() {
    if (streaming) return
    try {
      const st = await newSession()
      setStatus(st)
      setItems([])
      setThinking(null)
      setPlan([])
      await refreshSessions()
    } catch (e) {
      addErrorItem(e instanceof Error ? e.message : String(e))
      pushToast("新建会话失败")
    }
  }

  async function handleSwitch(id: string) {
    if (streaming || id === status?.session_id) return
    try {
      const data = await switchSession(id)
      setStatus(data.status)
      setItems(historyToItems(data.messages))
      setThinking(null)
      setPlan(data.status.plan || [])
      await refreshSessions()
    } catch (e) {
      addErrorItem(e instanceof Error ? e.message : String(e))
      pushToast("切换会话失败")
    }
  }

  async function handleDelete(id: string) {
    if (streaming) return
    try {
      const data = await deleteSession(id)
      if (data.status) setStatus(data.status)
      if (id === status?.session_id) {
        setItems([])
        setThinking(null)
        setPlan(data.status?.plan || [])
      }
      await refreshSessions()
    } catch (e) {
      addErrorItem(`删除会话失败：${e instanceof Error ? e.message : String(e)}`)
      pushToast("删除会话失败")
    }
  }

  async function handleCancel() {
    try {
      await cancelTurn()
    } catch {
      /* ignore */
    }
  }

  async function handleApproval(decision: "allow" | "allow_always" | "deny") {
    try {
      await respondApproval(decision)
      setApproval(null)
    } catch {
      setApproval(null)
    }
  }

  async function send(images: string[] = []) {
    const prompt = input.trim()
    if ((!prompt && images.length === 0) || streaming) return
    setInput("")
    setStreaming(true)
    setThinking(null)
    setItems((prev) => [
      ...prev,
      { kind: "message", role: "user", text: prompt, images },
      { kind: "message", role: "assistant", text: "", streaming: true },
    ])

    const buf = { text: "", think: "", gotText: false }
    let rafPending = false
    function flush() {
      rafPending = false
      if (buf.text) {
        const add = buf.text
        buf.text = ""
        patchLast((it) => (it.kind === "message" ? { ...it, text: it.text + add } : it))
      }
      setThinking(buf.think ? { text: buf.think, active: !buf.gotText } : null)
    }
    function schedule() {
      if (!rafPending) {
        rafPending = true
        requestAnimationFrame(flush)
      }
    }

    const onEvent = (ev: ChatEvent) => {
      switch (ev.type) {
        case "text_delta":
          buf.gotText = true
          buf.text += ev.text
          schedule()
          break
        case "reasoning_delta":
          buf.think += ev.text
          schedule()
          break
        case "tool_start":
          flush()
          patchLast((it) => (it.kind === "message" ? { ...it, streaming: false } : it))
          setItems((prev) => [...prev, { kind: "tool", role: "assistant", text: "", call: ev.tool }])
          break
        case "tool_end":
          flush()
          setItems((prev) => {
            const next = [...prev]
            for (let i = next.length - 1; i >= 0; i -= 1) {
              if (next[i].kind === "tool" && next[i].call?.id === ev.tool.id) {
                next[i] = { ...next[i], result: ev.result, ok: ev.ok }
                break
              }
            }
            return next
          })
          setItems((prev) => (prev.some((it) => it.kind === "message" && it.streaming) ? prev : [...prev, { kind: "message", role: "assistant", text: "", streaming: true }]))
          break
        case "approval_needed":
          setApproval(ev.approval)
          break
        case "compaction":
          if (ev.data?.usage) {
            setStatus((s) => (s ? { ...s, context: ev.data!.usage } : s))
          }
          if (ev.data?.before_tokens) {
            flush()
            setItems((prev) => [
              ...prev.filter((it) => !(it.kind === "message" && it.role === "assistant" && !it.text)),
              ...(showNoticesRef.current
                ? [
                    {
                      kind: "notice" as const,
                      role: "assistant" as const,
                      text: `上下文已自动压缩：约 ${ev.data!.before_tokens} → ${ev.data!.after_tokens} tokens（摘要合并了 ${ev.data!.summarized_messages} 条历史消息）`,
                    },
                  ]
                : []),
              { kind: "message", role: "assistant", text: "", streaming: true },
            ])
          }
          break
        case "context":
          if (ev.data?.usage) setStatus((s) => (s ? { ...s, context: ev.data!.usage } : s))
          break
        case "plan":
          if (ev.data?.steps) setPlan(ev.data.steps)
          break
        case "turn_end":
          flush()
          patchLast((it) => (it.kind === "message" ? { ...it, streaming: false, text: ev.text && it.text ? it.text : ev.text || it.text } : it))
          break
        case "turn_error":
          flush()
          setItems((prev) => [...prev, { kind: "message", role: "error", text: ev.text || "turn error" }])
          break
        case "done":
          flush()
          patchLast((it) => (it.kind === "message" ? { ...it, streaming: false } : it))
          break
      }
    }
    try {
      await streamChat(prompt, onEvent, undefined, images)
      flush()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      flush()
      setItems((prev) => prev.filter((it) => !(it.kind === "message" && it.role === "assistant" && !it.text)))
      addErrorItem(msg)
      pushToast("发送失败：后端连接中断或模型不可用")
    } finally {
      setStreaming(false)
      setApproval(null)
      setThinking((t) => (t ? { ...t, active: false } : t))
      refreshSessions()
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
        onSelect={handleSwitch}
        onNew={handleNewSession}
        onDelete={handleDelete}
      />
      <div className="grid min-w-0 flex-1 grid-rows-[auto_1fr_auto]">
        <header className="border-b border-spark-line bg-spark-panel px-2 py-2 sm:px-4">
          <div className="flex items-center justify-between gap-1.5">
            <div className="flex min-w-0 items-center gap-0.5">
              <button
                type="button"
                onClick={() => setSidebarOpen(!sidebarOpen)}
                title="切换会话栏"
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
                  onClick={handleCancel}
                  title="停止生成"
                  className="flex h-9 w-9 animate-in items-center justify-center rounded-lg bg-red-950 text-spark-err transition-colors hover:bg-red-900"
                >
                  <span className="block h-3 w-3 rounded-[2px] bg-current" />
                </button>
              )}
              <ContextMeter usage={show.context ? status?.context : undefined} />
              <button
                type="button"
                title="检查点回滚"
                onClick={() => void openCheckpoints()}
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
                onClick={() => setSettingsOpen(true)}
                className="flex h-9 w-9 items-center justify-center rounded-lg bg-spark-line text-spark-text transition-colors hover:bg-spark-line/70 hover:text-spark-accent"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <circle cx="12" cy="12" r="3" />
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                </svg>
              </button>
            </div>
          </div>
        </header>
        <div ref={logRef} className="overflow-x-hidden overflow-y-auto px-2.5 py-4 sm:px-4 sm:py-5">
          <div className="mx-auto flex min-w-0 max-w-4xl flex-col gap-3">
            {show.plan && plan.length > 0 && <PlanCard steps={plan} />}
            {items.length === 0 && !approval && !thinking && (
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
            {visibleItems.map((item, i) => (
              <div key={i} className="animate-in">
                <ChatMessage item={item} />
              </div>
            ))}
            {show.thinking && thinking && <ThinkingBlock text={thinking.text} active={thinking.active} />}
            {approval && <ApprovalCard approval={approval} onDecide={handleApproval} busy={false} />}
          </div>
        </div>
        <InputBox
          value={input}
          onChange={setInput}
          onSubmit={(imgs) => void send(imgs)}
          onStop={() => void handleCancel()}
          streaming={streaming}
          model={status?.model}
          onOpenSettings={() => setSettingsOpen(true)}
        />
      </div>
      {settingsOpen && (
        <SettingsPanel status={status} onClose={() => setSettingsOpen(false)} onSaved={(s) => setStatus(s)} />
      )}
      {cpsOpen && (
        <div className="animate-fade fixed inset-0 z-40">
          <div className="absolute inset-0 bg-black/55" onClick={() => setCpsOpen(false)} />
          <aside className="animate-in absolute top-0 right-0 flex h-full w-full flex-col border-l border-spark-line bg-spark-panel sm:w-[26rem]">
            <div className="flex items-center justify-between border-b border-spark-line px-4 py-3">
              <h2 className="text-base font-bold">检查点回滚</h2>
              <button type="button" onClick={() => setCpsOpen(false)} className="rounded-lg bg-spark-line px-3 py-1.5 text-sm text-spark-text transition-colors hover:text-spark-accent">
                关闭
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <p className="mb-3 text-xs text-spark-muted">
                每次涉及写文件或执行命令的回合结束后自动创建检查点。回滚会同时恢复工作区文件与对话历史。
              </p>
              {cpsBusy && <p className="text-xs text-spark-muted">加载中…</p>}
              {!cpsBusy && checkpoints.length === 0 && <p className="text-xs text-spark-muted">暂无检查点。</p>}
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
                        disabled={cpsBusy || streaming}
                        onClick={() => void handleRollback(cp.id)}
                        className="shrink-0 rounded-lg bg-spark-accent px-3 py-1.5 text-xs font-bold text-teal-950 transition-all hover:brightness-110 active:scale-95 disabled:opacity-50"
                      >
                        回滚
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
