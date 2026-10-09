import { create } from "zustand"
import { getDefaultLang, setDefaultLang, type Lang } from "../i18n"
import type { ToastItem } from "../components/Toast"
import type { CheckpointRow } from "../api"

interface UiState {
  settingsOpen: boolean
  sidebarOpen: boolean
  cpsOpen: boolean
  checkpoints: CheckpointRow[]
  cpsBusy: boolean
  toasts: ToastItem[]
  openSettings: () => void
  closeSettings: () => void
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  openCheckpoints: () => void
  closeCheckpoints: () => void
  setCheckpoints: (rows: CheckpointRow[]) => void
  setCpsBusy: (busy: boolean) => void
  pushToast: (text: string, kind?: ToastItem["kind"]) => void
  lang: Lang
  setLang: (lang: Lang) => void
  dismissToast: (id: number) => void
}

let toastCounter = 0

export const useUiStore = create<UiState>((set) => ({
  settingsOpen: false,
  sidebarOpen: window.innerWidth >= 768,
  cpsOpen: false,
  checkpoints: [],
  cpsBusy: false,
  toasts: [],

  openSettings: () => set({ settingsOpen: true }),
  closeSettings: () => set({ settingsOpen: false }),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),

  openCheckpoints: () => set({ cpsOpen: true, cpsBusy: true }),
  closeCheckpoints: () => set({ cpsOpen: false }),
  setCheckpoints: (rows) => set({ checkpoints: rows }),
  setCpsBusy: (busy) => set({ cpsBusy: busy }),

  lang: getDefaultLang(),
  setLang: (lang) => {
    setDefaultLang(lang)
    set({ lang })
  },

  pushToast: (text, kind = "error") => {
    toastCounter += 1
    const id = toastCounter
    set((s) => ({ toasts: [...s.toasts.slice(-2), { id, text, kind }] }))
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
