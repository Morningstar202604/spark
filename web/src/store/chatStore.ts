import { create } from "zustand"
import type { ChatEvent } from "../types"
import type { ChatItem } from "../components/ChatMessage"
import { streamChat, fetchHistory } from "../api"
import { useSessionStore } from "./sessionStore"
import { useUiStore } from "./uiStore"
import { t } from "../i18n"

interface ChatState {
  items: ChatItem[]
  input: string
  streaming: boolean
  thinking: { text: string; active: boolean } | null
  showNotices: boolean
  loading: boolean
  abortController: AbortController | null
  streamStatus: "idle" | "streaming" | "error" | "reconnecting"
  setInput: (v: string) => void
  setStreaming: (v: boolean) => void
  setThinking: (v: { text: string; active: boolean } | null) => void
  setItems: (items: ChatItem[]) => void
  addErrorItem: (text: string) => void
  send: (images?: string[]) => Promise<void>
  loadInitialHistory: () => Promise<void>
  cancel: () => void
}

function patchLast(items: ChatItem[], mutate: (item: ChatItem) => ChatItem): ChatItem[] {
  if (items.length === 0) return items
  return [...items.slice(0, -1), mutate(items[items.length - 1])]
}

export const useChatStore = create<ChatState>((set, get) => ({
  items: [],
  input: "",
  streaming: false,
  thinking: null,
  showNotices: true,
  loading: false,
  abortController: null,
  streamStatus: "idle" as const,

  setInput: (v) => set({ input: v }),
  setStreaming: (v) => set({ streaming: v }),
  setThinking: (v) => set({ thinking: v }),
  setItems: (items) => set({ items }),
  addErrorItem: (text) => set((s) => ({ items: [...s.items, { kind: "message", role: "error", text }] })),

  cancel: () => {
    const { abortController } = get()
    if (abortController) {
      abortController.abort()
      set({ abortController: null, streaming: false })
    }
  },

  loadInitialHistory: async () => {
    set({ loading: true })
    try {
      const h = await fetchHistory()
      const items = h.messages.map((m) =>
        m.role === "tool"
          ? { kind: "tool" as const, role: "assistant" as const, text: "", call: { id: "", name: m.name || "tool", arguments: {} }, result: m.content, ok: true }
          : { kind: "message" as const, role: m.role, text: m.content, images: m.images },
      )
      set({ items, loading: false })
    } catch {
      set({ loading: false })
      useUiStore.getState().pushToast(t("toast.historyLoadFailed", useUiStore.getState().lang))
    }
  },

  send: async (images: string[] = []) => {
    const { input, streaming, abortController } = get()
    const prompt = input.trim()
    if ((!prompt && images.length === 0) || streaming) return

    // Cancel any previous in-flight stream before starting a new one
    if (abortController) {
      abortController.abort()
    }
    const ac = new AbortController()
    set({ input: "", streaming: true, thinking: null, abortController: ac })
    set((s) => ({
      items: [
        ...s.items,
        { kind: "message", role: "user", text: prompt, images },
        { kind: "message", role: "assistant", text: "", streaming: true },
      ],
    }))

    const buf = { text: "", think: "", gotText: false }
    let rafPending = false

    function flush() {
      rafPending = false
      if (buf.text) {
        const add = buf.text
        buf.text = ""
        set((s) => ({ items: patchLast(s.items, (it) => (it.kind === "message" ? { ...it, text: it.text + add } : it)) }))
      }
      const chat = useChatStore.getState()
      chat.setThinking(buf.think ? { text: buf.think, active: !buf.gotText } : null)
    }

    function schedule() {
      if (!rafPending) {
        rafPending = true
        requestAnimationFrame(flush)
      }
    }

    const onEvent = (ev: ChatEvent) => {
      const session = useSessionStore.getState()
      const showNotices = session.status?.display?.show_notices ?? true

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
        case "tool_start": {
          flush()
          set((s) => ({ items: patchLast(s.items, (it) => (it.kind === "message" ? { ...it, streaming: false } : it)) }))
          set((s) => ({ items: [...s.items, { kind: "tool", role: "assistant", text: "", call: ev.tool }] }))
          break
        }
        case "tool_end": {
          flush()
          set((s) => {
            const next = [...s.items]
            for (let i = next.length - 1; i >= 0; i -= 1) {
              if (next[i].kind === "tool" && next[i].call?.id === ev.tool.id) {
                next[i] = { ...next[i], result: ev.result, ok: ev.ok }
                break
              }
            }
            return { items: next }
          })
          set((s) => ({
            items: s.items.some((it) => it.kind === "message" && it.streaming)
              ? s.items
              : [...s.items, { kind: "message", role: "assistant", text: "", streaming: true }],
          }))
          break
        }
        case "approval_needed":
          session.setApproval(ev.approval)
          break
        case "compaction": {
          if (ev.data?.usage) {
            const s = useSessionStore.getState()
            s.setStatus(s.status ? { ...s.status, context: ev.data.usage } : s.status)
          }
          if (ev.data?.before_tokens && showNotices) {
            flush()
            const text = t("context.compacted", useUiStore.getState().lang, { before: ev.data.before_tokens ?? 0, after: ev.data.after_tokens ?? 0, summarized: ev.data.summarized_messages ?? 0 })
            set((s) => {
              const idx = s.items.findIndex((it) => it.kind === "summary")
              if (idx >= 0) {
                const next = [...s.items]
                next[idx] = { ...next[idx], text }
                return { items: next }
              }
              return {
                items: [...s.items, { kind: "summary" as const, role: "assistant" as const, text }],
              }
            })
          }
          break
        }
        case "context": {
          if (ev.data?.usage) {
            const s = useSessionStore.getState()
            s.setStatus(s.status ? { ...s.status, context: ev.data.usage } : s.status)
          }
          break
        }
        case "plan":
          if (ev.data?.steps) useSessionStore.getState().setPlan(ev.data.steps)
          break
        case "turn_end":
          flush()
          set((s) => ({
            items: patchLast(s.items, (it) =>
              it.kind === "message" ? { ...it, streaming: false, text: ev.text && it.text ? it.text : ev.text || it.text } : it,
            ),
          }))
          break
        case "turn_error":
          flush()
          set((s) => ({ items: [...s.items, { kind: "message", role: "error", text: ev.text || "turn error" }] }))
          break
        case "done":
          flush()
          set((s) => ({ items: patchLast(s.items, (it) => (it.kind === "message" ? { ...it, streaming: false } : it)) }))
          break
      }
    }

    try {
      await streamChat(prompt, onEvent, ac.signal, images)
      flush()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      flush()
      set((s) => ({ items: s.items.filter((it) => !(it.kind === "message" && it.role === "assistant" && !it.text)) }))
      get().addErrorItem(msg)
      useUiStore.getState().pushToast(t("toast.sendFailed", useUiStore.getState().lang))
    } finally {
      set({ streaming: false, abortController: null })
      useSessionStore.getState().setApproval(null)
      set((s) => ({ thinking: s.thinking ? { ...s.thinking, active: false } : s.thinking }))
      await useSessionStore.getState().refreshSessions()
    }
  },
}))
