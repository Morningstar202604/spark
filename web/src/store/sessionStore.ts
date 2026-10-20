import { create } from "zustand"
import type { ApprovalInfo, PlanStep, SessionRow, Status, ToolCall } from "../types"
import { listSessions, newSession, switchSession, deleteSession, cancelTurn, respondApproval } from "../api"
import { useChatStore, historyToItems, type ChatMsg } from "./chatStore"
import { useUiStore } from "./uiStore"

export interface ApprovalState {
  info: ApprovalInfo
  tool: ToolCall
}

interface SessionState {
  status: Status | null
  sessions: SessionRow[]
  approval: ApprovalState | null
  approvalBusy: boolean
  plan: PlanStep[]
  setStatus: (s: Status | null) => void
  patchStatus: (patch: Partial<Status>) => void
  setApproval: (a: ApprovalState | null) => void
  setPlan: (p: PlanStep[]) => void
  syncPlanFromItems: (items: ChatMsg[]) => void
  refreshSessions: () => Promise<void>
  handleNewSession: () => Promise<void>
  handleSwitch: (id: string) => Promise<void>
  handleDelete: (id: string) => Promise<void>
  handleCancel: () => Promise<void>
  handleApproval: (decision: "allow" | "allow_always" | "deny") => Promise<void>
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
    patchStatus: (patch) => set((s) => ({ status: s.status ? { ...s.status, ...patch } : s.status })),
    setApproval: (a) => set({ approval: a }),
    setPlan: (p) => set({ plan: p }),

    syncPlanFromItems: (items) => {
      for (let i = items.length - 1; i >= 0; i -= 1) {
        for (let j = items[i].segments.length - 1; j >= 0; j -= 1) {
          const seg = items[i].segments[j]
          if (seg.kind === "plan" && seg.steps.length) {
            set({ plan: seg.steps })
            return
          }
        }
      }
    },

    refreshSessions: async () => {
      try {
        const data = await listSessions()
        set({ sessions: data.sessions })
      } catch (e) {
        useUiStore.getState().pushToast("toast.sessionLoadFailed", "error")
        void e
      }
    },

    handleNewSession: async () => {
      try {
        const st = await newSession()
        set({ status: st, plan: st.plan || [] })
        const chat = useChatStore.getState()
        chat.setItems([])
        await get().refreshSessions()
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        useChatStore.getState().addErrorItem(msg)
        useUiStore.getState().pushToast("toast.newSessionFailed", "error")
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
        await get().refreshSessions()
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        useChatStore.getState().addErrorItem(msg)
        useUiStore.getState().pushToast("toast.switchSessionFailed", "error")
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
          set({ plan: data.status?.plan || [] })
        }
        await get().refreshSessions()
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        void msg
        useUiStore.getState().pushToast("toast.deleteSessionFailed", "error")
      }
    },

    handleCancel: async () => {
      // Abort the local SSE request first so the UI stops immediately, then ask
      // the server to stop the turn. The header button used to post
      // /api/cancel only, leaving the stream open until the server closed it —
      // two "stop" affordances with different behaviour.
      useChatStore.getState().cancel()
      try {
        await cancelTurn()
      } catch {
        useUiStore.getState().pushToast("toast.cancelFailed", "error")
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
        useUiStore.getState().pushToast("toast.approvalFailed", "error")
      } finally {
        approvalBusyRef = false
        set({ approvalBusy: false })
      }
    },
  }
})
