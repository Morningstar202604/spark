import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { SessionRow } from "../types"
import { IconHistory, IconMonitor, IconMoon, IconPlus, IconSearch, IconSettings, IconSun, IconTrash, IconX } from "./icons"
import { cls, IconButton } from "./ui"
import { BrandMark } from "./BrandMark"
import { useUiStore } from "../store/uiStore"
import { useSessionStore } from "../store/sessionStore"
import { t, type Lang, type TranslationKey } from "../i18n"

function ThemeGlyph({ theme }: { theme: "light" | "dark" | "system" }) {
  if (theme === "light") return <IconSun size={14} />
  if (theme === "dark") return <IconMoon size={14} />
  return <IconMonitor size={14} />
}

function timeLabel(ts: number, lang: Lang): string {
  const d = new Date(ts * 1000)
  const now = new Date()
  const locale = lang === "zh" ? "zh-CN" : "en-US"
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) {
    return d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
  }
  return d.toLocaleDateString(locale, { month: "2-digit", day: "2-digit" })
}

function groupKey(ts: number): TranslationKey {
  const d = new Date(ts * 1000)
  const now = new Date()
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diffDays = Math.floor((startOfDay(now) - startOfDay(d)) / 86400000)
  if (diffDays <= 0) return "sidebar.today"
  if (diffDays === 1) return "sidebar.yesterday"
  if (diffDays < 7) return "sidebar.last7"
  return "sidebar.earlier"
}

const GROUP_ORDER: TranslationKey[] = ["sidebar.today", "sidebar.yesterday", "sidebar.last7", "sidebar.earlier"]

const SIDEBAR_MIN = 220
const SIDEBAR_MAX = 420
const SIDEBAR_KEY = "spark.sidebarWidth"

/**
 * Drag-to-resize for the desktop sidebar.
 *
 * The width lives in a CSS variable on the <aside> so dragging never
 * re-renders the session list; only pointer moves touch the DOM. Persisted to
 * localStorage so the layout survives reloads, double-click resets it, and
 * arrow keys move it for keyboard users.
 */
function useSidebarWidth() {
  const [value, setValue] = useState(() => {
    try {
      const stored = Number(localStorage.getItem(SIDEBAR_KEY))
      return Number.isFinite(stored) && stored >= SIDEBAR_MIN && stored <= SIDEBAR_MAX ? stored : 270
    } catch {
      return 270 // storage blocked (private mode / policy): fall back to default
    }
  })
  const drag = useRef<{ startX: number; startW: number; el: HTMLElement | null; pointerId: number } | null>(null)
  const pending = useRef(0)
  const frame = useRef(0)

  // During a drag only the CSS variable is written (no React state), so a
  // pointermove never re-renders the memoised session list. State is synced
  // once when the gesture ends.
  const writeWidth = useCallback((next: number, el: HTMLElement | null) => {
    const clamped = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(next)))
    el?.style.setProperty("--sidebar-w", `${clamped}px`)
    pending.current = clamped
    return clamped
  }, [])

  const commit = useCallback(() => {
    const clamped = pending.current
    setValue(clamped)
    try {
      localStorage.setItem(SIDEBAR_KEY, String(clamped))
    } catch {
      /* storage blocked: the width still applies for this session */
    }
  }, [])

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const container = e.currentTarget.parentElement as HTMLElement | null
    drag.current = {
      startX: e.clientX,
      startW: container?.offsetWidth ?? 270,
      el: container,
      pointerId: e.pointerId,
    }
    pending.current = container?.offsetWidth ?? 270
    e.currentTarget.setPointerCapture(e.pointerId)
  }, [])

  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = drag.current
      if (!d) return
      // A release outside the window can skip pointerup; a move with no buttons
      // means the gesture is over, so stop instead of rubber-banding.
      if (e.buttons === 0 || e.pointerId !== d.pointerId) {
        drag.current = null
        commit()
        return
      }
      if (frame.current) return
      frame.current = requestAnimationFrame(() => {
        frame.current = 0
        const cur = drag.current
        if (cur) writeWidth(cur.startW + (e.clientX - cur.startX), cur.el)
      })
    }
    const up = () => {
      if (!drag.current) return
      drag.current = null
      if (frame.current) {
        cancelAnimationFrame(frame.current)
        frame.current = 0
      }
      commit()
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up)
    window.addEventListener("pointercancel", up)
    return () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
      window.removeEventListener("pointercancel", up)
      if (frame.current) cancelAnimationFrame(frame.current)
    }
  }, [writeWidth, commit])

  const apply = useCallback(
    (next: number) => {
      const el = document.getElementById("session-sidebar")
      writeWidth(next, el)
      commit()
    },
    [writeWidth, commit],
  )

  const reset = useCallback(() => apply(270), [apply])

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return
      e.preventDefault()
      apply(value + (e.key === "ArrowRight" ? 16 : -16))
    },
    [apply, value],
  )

  return { value, onPointerDown, onKeyDown, reset }
}

export const Sidebar = memo(function Sidebar() {
  const sessions = useSessionStore((s) => s.sessions)
  const status = useSessionStore((s) => s.status)
  const handleNewSession = useSessionStore((s) => s.handleNewSession)
  const handleSwitch = useSessionStore((s) => s.handleSwitch)
  const handleDelete = useSessionStore((s) => s.handleDelete)
  const sidebarOpen = useUiStore((s) => s.sidebarOpen)
  const setSidebarOpen = useUiStore((s) => s.setSidebarOpen)
  const openSettings = useUiStore((s) => s.openSettings)
  const openCheckpoints = useUiStore((s) => s.openCheckpoints)
  const cycleTheme = useUiStore((s) => s.cycleTheme)
  const theme = useUiStore((s) => s.theme)
  const lang = useUiStore((s) => s.lang)
  const showKeywords = useSessionStore((s) => s.status?.display?.show_keywords ?? true)

  const [query, setQuery] = useState("")
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = q
      ? sessions.filter((s) => (s.title + " " + (s.keywords ?? "") + " " + (s.model ?? "")).toLowerCase().includes(q))
      : sessions
    const map = new Map<string, SessionRow[]>()
    for (const s of filtered) {
      const k = groupKey(s.updated_at)
      map.set(k, [...(map.get(k) ?? []), s])
    }
    return GROUP_ORDER.filter((k) => map.has(k)).map((k) => ({ key: k, items: map.get(k)! }))
  }, [sessions, query])

  const currentId = status?.session_id
  const themeLabel = t(theme === "light" ? "display.light" : theme === "dark" ? "display.dark" : "display.system", lang)
  const width = useSidebarWidth()

  return (
    <>
      {sidebarOpen && (
        <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => setSidebarOpen(false)} aria-hidden="true" />
      )}
      <aside
        id="session-sidebar"
        aria-hidden={!sidebarOpen}
        inert={!sidebarOpen}
        style={{ "--sidebar-w": `${width.value}px` } as React.CSSProperties}
        className={cls(
          // The width utility lives in the open branch only: emitting the same
          // lg:w-[var(--sidebar-w)] at base level kept it winning over
          // lg:w-0 in the closed branch (same media block, later source order),
          // so collapsing left a 270px dead strip on desktop.
          "fixed inset-y-0 left-0 z-40 flex w-[270px] shrink-0 flex-col border-r border-line bg-sidebar transition-transform duration-200 lg:static lg:translate-x-0 lg:z-auto",
          sidebarOpen
            ? "translate-x-0 lg:w-[var(--sidebar-w)]"
            : "-translate-x-full lg:w-0 lg:border-r-0 lg:overflow-hidden",
        )}
      >
        {/* 桌面端拖拽调宽：手机/平板隐藏 */}
        {sidebarOpen && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label={t("sidebar.resize", lang)}
            aria-valuenow={width.value}
            aria-valuemin={SIDEBAR_MIN}
            aria-valuemax={SIDEBAR_MAX}
            tabIndex={0}
            onPointerDown={width.onPointerDown}
            onDoubleClick={width.reset}
            onKeyDown={width.onKeyDown}
            className="absolute inset-y-0 -right-1 z-10 hidden w-2 cursor-col-resize touch-none lg:block hover:bg-accent/25 active:bg-accent/40"
          />
        )}
        {/* 头部 */}
        <div className="flex items-center gap-2 px-3 pt-3 pb-2">
          <BrandMark size={28} />
          <span className="text-[15px] font-bold tracking-tight text-ink">Spark</span>
          <div className="ml-auto flex items-center gap-0.5">
            <IconButton title={t("sidebar.collapse", lang)} onClick={() => setSidebarOpen(false)} className="lg:hidden">
              <IconX size={16} />
            </IconButton>
          </div>
        </div>

        {/* 新建 + 搜索 */}
        <div className="space-y-2 px-3 pb-2">
          <button
            type="button"
            onClick={handleNewSession}
            className="flex h-9 w-full items-center justify-center gap-1.5 rounded-lg bg-hover text-sm font-medium text-ink transition-colors hover:bg-selected active:scale-[0.99]"
          >
            <IconPlus size={14} />
            {t("common.newSession", lang)}
          </button>
          <div className="relative">
            <IconSearch size={14} className="pointer-events-none absolute top-2.5 left-2.5 text-ink3" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("sidebar.searchPh", lang)}
              className="h-9 w-full rounded-lg border border-line bg-panel pr-7 pl-8 text-[13px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                className="absolute top-2 right-2 flex h-5 w-5 items-center justify-center rounded text-ink3 hover:bg-hover"
              >
                <IconX size={12} />
              </button>
            )}
          </div>
        </div>

        {/* 会话列表 */}
        <div className="flex-1 overflow-y-auto px-3 pb-3">
          {groups.length === 0 && (
            <div className="flex flex-col items-center gap-2 py-10 text-center">
              <IconHistory size={22} className="text-ink3" />
              <p className="text-xs text-ink3">{query ? t("sidebar.noMatch", lang) : t("sidebar.empty", lang)}</p>
            </div>
          )}
          {groups.map((g) => (
            <div key={g.key} className="mt-3">
              <div className="mb-1 px-1 text-[10.5px] font-semibold tracking-wider text-ink3 uppercase">{t(g.key, lang)}</div>
              <ul className="flex flex-col gap-0.5">
                {g.items.map((s) => {
                  const active = s.id === currentId
                  return (
                    <li key={s.id} className="group relative">
                      <button
                        type="button"
                        onClick={() => {
                          if (s.id !== currentId) handleSwitch(s.id)
                          setSidebarOpen(false)
                        }}
                        className={cls(
                          "w-full rounded-lg px-3 py-2 pr-8 text-left transition-colors",
                          active ? "bg-selected text-ink" : "text-ink2 hover:bg-hover",
                        )}
                      >
                        <span className="block truncate text-[13px] font-medium">
                          {s.title && s.title !== "untitled" ? s.title : t("common.newChat", lang)}
                        </span>
                        {showKeywords && s.keywords && (
                          <span className="mt-1 flex flex-wrap gap-1">
                            {s.keywords
                              .split(/\s+/)
                              .filter(Boolean)
                              .slice(0, 3)
                              .map((kw) => (
                                <span key={kw} className="rounded bg-hover px-1.5 py-0.5 text-[9.5px] text-ink3">
                                  {kw}
                                </span>
                              ))}
                          </span>
                        )}
                        <span className="mt-0.5 block text-[10px] text-ink3">
                          {timeLabel(s.updated_at, lang)} · {s.model}
                        </span>
                      </button>
                      <div className="absolute top-1.5 right-1.5">
                        {confirmId === s.id ? (
                          <span className="anim-fade flex items-center gap-1 rounded-md border border-err/40 bg-panel p-0.5 shadow-sm">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDelete(s.id)
                                setConfirmId(null)
                              }}
                              className="rounded px-1.5 py-0.5 text-[10px] font-bold text-err hover:bg-err/10"
                            >
                              {t("common.delete", lang)}
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                setConfirmId(null)
                              }}
                              className="rounded px-1.5 py-0.5 text-[10px] text-ink2 hover:bg-hover"
                            >
                              {t("common.cancel", lang)}
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            title={t("sidebar.deleteSession", lang)}
                            onClick={(e) => {
                              e.stopPropagation()
                              setConfirmId(s.id)
                            }}
                            className={cls(
                              "flex h-6 w-6 items-center justify-center rounded-md text-ink3 opacity-0 transition-all hover:bg-err/10 hover:text-err focus-visible:opacity-100",
                              active && "opacity-100",
                              "group-hover:opacity-100",
                            )}
                          >
                            <IconTrash size={12} />
                          </button>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>

        {/* 底部：模型 + 工作目录 + 设置入口 */}
        <div className="border-t border-line p-3">
          <div className="flex items-center gap-2 rounded-lg bg-panel px-3 py-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-hover text-[10px] font-bold text-accent">
              {status?.model?.slice(0, 2).toUpperCase() ?? "AI"}
            </span>
            <div className="min-w-0">
              <p className="truncate text-[12px] font-semibold text-ink">{status?.model ?? t("sidebar.noModel", lang)}</p>
              <p className="truncate text-[10.5px] text-ink3" title={status?.workdir}>
                {status?.workdir ?? ""}
              </p>
            </div>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1.5">
            <button
              type="button"
              onClick={openSettings}
              className="flex h-8 items-center justify-center gap-1.5 rounded-lg bg-hover text-[12px] font-medium text-ink transition-colors hover:bg-selected active:scale-[0.99]"
            >
              <IconSettings size={13} />
              {t("settings.title", lang)}
            </button>
            <button
              type="button"
              title={t("sidebar.checkpoint", lang)}
              onClick={openCheckpoints}
              className="flex h-8 items-center justify-center rounded-lg bg-hover text-ink2 transition-colors hover:bg-selected active:scale-[0.99]"
            >
              <IconHistory size={14} />
            </button>
            <button
              type="button"
              title={t("sidebar.themeTitle", lang, { theme: themeLabel })}
              onClick={cycleTheme}
              className="flex h-8 items-center justify-center rounded-lg bg-hover text-ink2 transition-colors hover:bg-selected active:scale-[0.99]"
            >
              <ThemeGlyph theme={theme} />
            </button>
          </div>
        </div>
      </aside>
    </>
  )
})
