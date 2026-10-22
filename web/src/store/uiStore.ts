import { create } from "zustand"
import { getDefaultLang, setDefaultLang, type Lang, type TranslationKey } from "../i18n"
import type { Theme } from "../theme"
import { applyTheme, getStoredTheme, initTheme } from "../theme"
import type { CheckpointRow } from "../api"

export interface ToastItem {
  id: number
  key: TranslationKey
  kind: "error" | "success" | "info"
}

interface UiState {
  theme: Theme
  settingsOpen: boolean
  sidebarOpen: boolean
  paletteOpen: boolean
  checkpointsOpen: boolean
  filesOpen: boolean
  browserOpen: boolean
  terminalOpen: boolean
  graphOpen: boolean
  replayOpen: boolean
  helpOpen: boolean
  checkpoints: CheckpointRow[]
  cpsBusy: boolean
  toasts: ToastItem[]
  lang: Lang
  setTheme: (t: Theme) => void
  cycleTheme: () => void
  openSettings: () => void
  closeSettings: () => void
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  setPaletteOpen: (open: boolean) => void
  openCheckpoints: () => void
  closeCheckpoints: () => void
  openFiles: () => void
  closeFiles: () => void
  openBrowser: () => void
  closeBrowser: () => void
  openTerminal: () => void
  closeTerminal: () => void
  openGraph: () => void
  closeGraph: () => void
  openReplay: () => void
  closeReplay: () => void
  openHelp: () => void
  closeHelp: () => void
  setCheckpoints: (rows: CheckpointRow[]) => void
  setCpsBusy: (busy: boolean) => void
  pushToast: (key: TranslationKey, kind?: ToastItem["kind"]) => void
  dismissToast: (id: number) => void
  setLang: (lang: Lang) => void
}

let toastCounter = 0

export const useUiStore = create<UiState>((set) => ({
  theme: initTheme(),
  settingsOpen: false,
  sidebarOpen: window.innerWidth >= 1024,
  paletteOpen: false,
  checkpointsOpen: false,
  filesOpen: false,
  browserOpen: false,
  terminalOpen: false,
  graphOpen: false,
  replayOpen: false,
  helpOpen: false,
  checkpoints: [],
  cpsBusy: false,
  toasts: [],
  lang: getDefaultLang(),

  setTheme: (t) => {
    applyTheme(t)
    set({ theme: t })
  },
  cycleTheme: () => {
    const order: Theme[] = ["light", "dark", "system"]
    const cur = getStoredTheme()
    const next = order[(order.indexOf(cur) + 1) % order.length]
    applyTheme(next)
    set({ theme: next })
  },

  openSettings: () => set({ settingsOpen: true, checkpointsOpen: false, replayOpen: false, graphOpen: false, terminalOpen: false }),
  closeSettings: () => set({ settingsOpen: false }),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setPaletteOpen: (open) => set({ paletteOpen: open }),

  openCheckpoints: () => set({ settingsOpen: false, checkpointsOpen: true, filesOpen: false, browserOpen: false, graphOpen: false, replayOpen: false, terminalOpen: false }),
  closeCheckpoints: () => set({ checkpointsOpen: false }),
  openFiles: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: true, browserOpen: false, graphOpen: false, replayOpen: false, terminalOpen: false }),
  closeFiles: () => set({ filesOpen: false }),
  openBrowser: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: false, browserOpen: true, graphOpen: false, replayOpen: false, terminalOpen: false }),
  closeBrowser: () => set({ browserOpen: false }),
  openTerminal: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: false, browserOpen: false, graphOpen: false, replayOpen: false, terminalOpen: true }),
  closeTerminal: () => set({ terminalOpen: false }),
  openGraph: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: false, browserOpen: false, graphOpen: true, replayOpen: false, terminalOpen: false }),
  closeGraph: () => set({ graphOpen: false }),
  openReplay: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: false, browserOpen: false, graphOpen: false, replayOpen: true, terminalOpen: false }),
  closeReplay: () => set({ replayOpen: false }),
  openHelp: () => set({ helpOpen: true }),
  closeHelp: () => set({ helpOpen: false }),
  setCheckpoints: (rows) => set({ checkpoints: rows }),
  setCpsBusy: (busy) => set({ cpsBusy: busy }),

  pushToast: (key, kind = "error") => {
    toastCounter += 1
    const id = toastCounter
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, key, kind }] }))
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  setLang: (lang) => {
    setDefaultLang(lang)
    set({ lang })
  },
}))
