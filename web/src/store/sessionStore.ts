import { create } from "zustand"
import type { ApprovalInfo, PlanStep, SessionRow, Status, HistoryMessage } from "../types"
import {
  listSessions,
  newSession,
  switchSession,
  deleteSession,
  cancelTurn,
  respondApproval,
} from "../api"
import { useUiStore } from "./uiStore"
import { useChatStore } from "./chatStore"
import { t } from "../i18n"

interface SessionState {
  status: Status | null
  sessions: SessionRow[]
  approval: ApprovalInfo | null
  approvalBusy: boolean
  plan: PlanStep[]
  setStatus: (s: Status | null) => void
  setApproval: (a: ApprovalInfo | null) => void
  setPlan: (p: PlanStep[]) => void
  refreshSessions: () => Promise<void>
  handleNewSession: () => Promise<void>
  handleSwitch: (id: string) => Promise<void>
  handleDelete: (id: string) => Promise<void>
  handleCancel: () => Promise<void>
  handleApproval: (decision: "allow" | "allow_always" | "deny") => Promise<void>
}

function historyToItems(messages: HistoryMessage[]) {
  return messages.map((m) =>
    m.role === "tool"
      ? { kind: "tool" as const, role: "assistant" as const, text: "", call: { id: "", name: m.name || "tool", arguments: {} }, result: m.content, ok: true }
      : { kind: "message" as const, role: m.role, text: m.content, images: m.images },
  )
}

export const useSessionStore = create<SessionState>((set, get) => {
  let approvalBusyRef = false
  return {
    status: null,
    sessions: [],
    approval: null,
    approvalBusy: false,
    plan: [],

    setStatus: (s) => set({ status: s }),
    setApproval: (a) => set({ approval: a }),
    setPlan: (p) => set({ plan: p }),

    refreshSessions: async () => {
      try {
        const data = await listSessions()
        set({ sessions: data.sessions })
      } catch (e) {
        useUiStore.getState().pushToast(t("toast.sessionLoadFailed", useUiStore.getState().lang, { msg: e instanceof Error ? e.message : String(e) }))
      }
    },

    handleNewSession: async () => {
      try {
        const st = await newSession()
        set({ status: st, plan: [] })
        const chat = useChatStore.getState()
        chat.setItems([])
        chat.setThinking(null)
        await get().refreshSessions()
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        useChatStore.getState().addErrorItem(msg)
        useUiStore.getState().pushToast(t("toast.newSessionFailed", useUiStore.getState().lang))
      }
    },

    handleSwitch: async (id: string) => {
      const { status: currentStatus } = get()
      if (!currentStatus || id === currentStatus.session_id) return
      try {
        const data = await switchSession(id)
        set({ status: data.status, plan: data.status.plan || [] })
        const items = historyToItems(data.messages)
        const chat = useChatStore.getState()
        chat.setItems(items)
        chat.setThinking(null)
        await get().refreshSessions()
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        useChatStore.getState().addErrorItem(msg)
        useUiStore.getState().pushToast(t("toast.switchSessionFailed", useUiStore.getState().lang))
      }
    },

    handleDelete: async (id: string) => {
      const { status: currentStatus } = get()
      if (!currentStatus) return
      try {
        const data = await deleteSession(id)
        if (data.status) set({ status: data.status })
        if (id === currentStatus.session_id) {
          const chat = useChatStore.getState()
          chat.setItems([])
          chat.setThinking(null)
          set({ plan: data.status?.plan || [] })
        }
        await get().refreshSessions()
      } catch (e) {
        const msg = t("toast.deleteFailed", useUiStore.getState().lang, { msg: e instanceof Error ? e.message : String(e) })
        useChatStore.getState().addErrorItem(msg)
        useUiStore.getState().pushToast(t("toast.deleteSessionFailed", useUiStore.getState().lang))
      }
    },

    handleCancel: async () => {
      try {
        await cancelTurn()
      } catch {
        useUiStore.getState().pushToast(t("toast.cancelFailed", useUiStore.getState().lang))
      }
    },

    handleApproval: async (decision) => {
      if (approvalBusyRef) return
      approvalBusyRef = true
      set({ approvalBusy: true })
      try {
        await respondApproval(decision)
        set({ approval: null })
      } catch {
        useUiStore.getState().pushToast(t("toast.approvalFailed", useUiStore.getState().lang))
      } finally {
        approvalBusyRef = false
        set({ approvalBusy: false })
      }
    },
  }
})
