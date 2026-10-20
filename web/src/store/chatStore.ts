import { create } from "zustand"
import type { ChatEvent, PlanStep } from "../types"
import { streamChat, fetchHistory } from "../api"
import { useSessionStore } from "./sessionStore"
import { useUiStore } from "./uiStore"
import { t } from "../i18n"

export interface ToolCallState {
  id: string
  name: string
  arguments: unknown
  status: "running" | "ok" | "error"
  startedAt: number
  durationMs?: number
  result?: unknown
}

/** 助手消息内部的顺序片段，忠实还原"思考→计划→工具→文本"交错顺序 */
export type Segment =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "plan"; steps: PlanStep[] }
  | { kind: "tool"; tool: ToolCallState }

export interface ChatMsg {
  id: string
  role: "user" | "assistant" | "error" | "notice" | "summary"
  /** 全文（复制用），与 segments 中的文本一致 */
  text: string
  images?: string[]
  streaming?: boolean
  segments: Segment[]
  meta?: {
    compacted?: { before: number; after: number; summarized: number }
  }
}

export function uid(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `m-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
  }
}

interface ChatState {
  items: ChatMsg[]
  input: string
  streaming: boolean
  loading: boolean
  abortController: AbortController | null
  setInput: (v: string) => void
  setStreaming: (v: boolean) => void
  setItems: (items: ChatMsg[]) => void
  setLoading: (v: boolean) => void
  addErrorItem: (text: string) => void
  send: (images?: string[]) => Promise<void>
  loadInitialHistory: () => Promise<void>
  cancel: () => void
}

export const useChatStore = create<ChatState>((set, get) => ({
  items: [],
  input: "",
  streaming: false,
  loading: false,
  abortController: null,

  setInput: (v) => set({ input: v }),
  setStreaming: (v) => set({ streaming: v }),
  setItems: (items) => set({ items }),
  setLoading: (v) => set({ loading: v }),

  addErrorItem: (text) => set((s) => ({ items: [...s.items, { id: uid(), role: "error", text, segments: [] }] })),

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
      const items = historyToItems(h.messages)
      set({ items, loading: false })
      useSessionStore.getState().syncPlanFromItems(items)
    } catch {
      set({ loading: false })
      useUiStore.getState().pushToast("toast.historyLoadFailed", "error")
    }
  },

  send: async (images: string[] = []) => {
    const { input, streaming, abortController } = get()
    const prompt = input.trim()
    if ((!prompt && images.length === 0) || streaming) return

    if (abortController) abortController.abort()
    const ac = new AbortController()
    set({ input: "", streaming: true, abortController: ac })
    const assistantId = uid()
    set((s) => ({
      items: [
        ...s.items,
        { id: uid(), role: "user", text: prompt, images, segments: [] },
        { id: assistantId, role: "assistant", text: "", streaming: true, segments: [] },
      ],
    }))

    const buf = { text: "", think: "" }
    let rafPending = false

    /** 修改当前助手消息；无则 no-op */
    function patchLast(fn: (it: ChatMsg) => ChatMsg) {
      set((s) => {
        const idx = s.items.findIndex((m) => m.id === assistantId)
        if (idx < 0) return s
        const next = [...s.items]
        next[idx] = fn(next[idx])
        return { items: next }
      })
    }

    /** 追加文本片段：若末段已是 text 则合并，否则新建 */
    function appendText(it: ChatMsg, add: string): ChatMsg {
      const segs = [...it.segments]
      const last = segs[segs.length - 1]
      if (last && last.kind === "text") {
        segs[segs.length - 1] = { kind: "text", text: last.text + add }
      } else {
        segs.push({ kind: "text", text: add })
      }
      return { ...it, segments: segs, text: it.text + add }
    }

    /** 追加思考片段 */
    function appendThinking(it: ChatMsg, add: string): ChatMsg {
      const segs = [...it.segments]
      const last = segs[segs.length - 1]
      if (last && last.kind === "thinking") {
        segs[segs.length - 1] = { kind: "thinking", text: last.text + add }
      } else {
        segs.push({ kind: "thinking", text: add })
      }
      return { ...it, segments: segs }
    }

    function flush() {
      rafPending = false
      if (buf.text) {
        const add = buf.text
        buf.text = ""
        patchLast((it) => appendText(it, add))
      }
      if (buf.think) {
        const add = buf.think
        buf.think = ""
        patchLast((it) => appendThinking(it, add))
      }
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
          buf.text += ev.text
          schedule()
          break
        case "reasoning_delta":
          buf.think += ev.text
          schedule()
          break
        case "tool_start": {
          flush()
          patchLast((it) => ({
            ...it,
            segments: [
              ...it.segments,
              {
                kind: "tool",
                tool: { id: ev.tool.id, name: ev.tool.name, arguments: ev.tool.arguments, status: "running", startedAt: Date.now() },
              },
            ],
          }))
          break
        }
        case "tool_end": {
          flush()
          patchLast((it) => ({
            ...it,
            segments: it.segments.map((seg) =>
              seg.kind === "tool" && seg.tool.id === ev.tool.id
                ? {
                    ...seg,
                    tool: {
                      ...seg.tool,
                      status: ev.ok ? "ok" : "error",
                      result: ev.result,
                      durationMs: seg.tool.startedAt ? Date.now() - seg.tool.startedAt : undefined,
                    },
                  }
                : seg,
            ),
          }))
          break
        }
        case "approval_needed":
          useSessionStore.getState().setApproval({ info: ev.approval, tool: ev.tool })
          break
        case "compaction": {
          if (ev.data?.usage) {
            useSessionStore.getState().patchStatus({ context: ev.data.usage })
          }
          if (ev.data?.before_tokens) {
            flush()
            const lang = useUiStore.getState().lang
            const text = t("context.compacted", lang, {
              before: ev.data.before_tokens ?? 0,
              after: ev.data.after_tokens ?? 0,
              summarized: ev.data.summarized_messages ?? 0,
            })
            set((s) => ({
              items: [
                ...s.items,
                {
                  id: uid(),
                  role: "notice",
                  text,
                  segments: [],
                  meta: {
                    compacted: {
                      before: ev.data.before_tokens ?? 0,
                      after: ev.data.after_tokens ?? 0,
                      summarized: ev.data.summarized_messages ?? 0,
                    },
                  },
                },
              ],
            }))
          }
          break
        }
        case "context": {
          if (ev.data?.usage) {
            useSessionStore.getState().patchStatus({ context: ev.data.usage })
          }
          break
        }
        case "plan": {
          if (ev.data?.steps) {
            const steps = ev.data.steps
            useSessionStore.getState().setPlan(steps)
            patchLast((it) => {
              const segs = [...it.segments]
              const last = segs[segs.length - 1]
              if (last && last.kind === "plan") {
                segs[segs.length - 1] = { kind: "plan", steps }
              } else {
                segs.push({ kind: "plan", steps })
              }
              return { ...it, segments: segs }
            })
          }
          break
        }
        case "turn_end":
          flush()
          patchLast((it) => ({
            ...it,
            streaming: false,
            text: ev.text && !it.text ? ev.text : it.text,
          }))
          break
        case "turn_error":
          flush()
          set((s) => ({ items: [...s.items, { id: uid(), role: "error", text: ev.text || "turn error", segments: [] }] }))
          break
        case "done":
          flush()
          patchLast((it) => ({ ...it, streaming: false }))
          break
      }
    }

    try {
      await streamChat(prompt, onEvent, ac.signal, images)
      flush()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      flush()
      const isAbort = e instanceof DOMException && e.name === "AbortError"
      if (!isAbort) {
        set((s) => ({
          items: s.items.filter((m) => !(m.id === assistantId && !m.text && !m.segments.length)),
        }))
        get().addErrorItem(msg)
        // api.ts already surfaced a toast for transport failures; only the
        // higher-level "send failed" toast is added here for other errors.
      }
    } finally {
      // Any termination path ends the stream: an aborted or failed turn used to
      // leave the bubble blinking "generating" forever because only turn_end /
      // done cleared the flag.
      patchLast((it) => (it.streaming ? { ...it, streaming: false } : it))
      // Guard against a superseded turn: if the user already started a new one,
      // this late-completing stream must not clear the new turn's controller or
      // streaming flag (that made Stop disappear and allowed a third concurrent
      // send).
      if (get().abortController === ac) {
        set({ streaming: false, abortController: null })
        useSessionStore.getState().setApproval(null)
      }
      await useSessionStore.getState().refreshSessions()
    }
  },
}))

/** 后端 history（user/assistant/tool 平铺）→ 前端分组 + 顺序片段模型 */
export function historyToItems(msgs: { role: string; content: string; name?: string; images?: string[] }[]): ChatMsg[] {
  const out: ChatMsg[] = []
  for (const m of msgs) {
    if (m.role === "tool") {
      const tool: ToolCallState = {
        id: `h-${out.length}-${Date.now()}`,
        name: m.name || "tool",
        arguments: {},
        status: "ok",
        startedAt: 0,
        result: m.content,
      }
      const last = out[out.length - 1]
      if (last && last.role === "assistant") {
        last.segments.push({ kind: "tool", tool })
      } else {
        out.push({ id: uid(), role: "assistant", text: "", segments: [{ kind: "tool", tool }] })
      }
    } else if (m.role === "user") {
      out.push({ id: uid(), role: "user", text: m.content, images: m.images, segments: [] })
    } else {
      out.push({ id: uid(), role: "assistant", text: m.content, segments: [{ kind: "text", text: m.content }] })
    }
  }
  return out
}
