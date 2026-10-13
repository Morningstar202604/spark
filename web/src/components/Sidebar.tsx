import { memo, useMemo, useState } from "react"
import type { SessionRow } from "../types"
import { IconHistory, IconMonitor, IconMoon, IconPlus, IconSearch, IconSettings, IconSpark, IconSun, IconTrash, IconX } from "./icons"
import { cls, IconButton } from "./ui"
import { useUiStore } from "../store/uiStore"
import { useSessionStore } from "../store/sessionStore"

function ThemeGlyph({ theme }: { theme: "light" | "dark" | "system" }) {
  if (theme === "light") return <IconSun size={14} />
  if (theme === "dark") return <IconMoon size={14} />
  return <IconMonitor size={14} />
}

function timeLabel(ts: number): string {
  const d = new Date(ts * 1000)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) {
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
  }
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })
}

function groupKey(ts: number): string {
  const d = new Date(ts * 1000)
  const now = new Date()
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diffDays = Math.floor((startOfDay(now) - startOfDay(d)) / 86400000)
  if (diffDays <= 0) return "今天"
  if (diffDays === 1) return "昨天"
  if (diffDays < 7) return "近 7 天"
  return "更早"
}

const GROUP_ORDER = ["今天", "昨天", "近 7 天", "更早"]

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

  return (
    <>
      {sidebarOpen && (
        <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => setSidebarOpen(false)} aria-hidden="true" />
      )}
      <aside
        id="session-sidebar"
        aria-hidden={!sidebarOpen}
        inert={!sidebarOpen}
        className={cls(
          "fixed inset-y-0 left-0 z-40 flex w-[270px] shrink-0 flex-col border-r border-line bg-sidebar transition-transform duration-200 lg:static lg:translate-x-0 lg:z-auto",
          sidebarOpen ? "translate-x-0" : "-translate-x-full lg:w-0 lg:border-r-0 lg:overflow-hidden",
        )}
      >
        {/* 头部 */}
        <div className="flex items-center gap-2 px-3 pt-3 pb-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-accent-soft">
            <IconSpark size={17} />
          </div>
          <span className="text-[15px] font-bold tracking-tight text-ink">Spark</span>
          <div className="ml-auto flex items-center gap-0.5">
            <IconButton title="收起侧边栏" onClick={() => setSidebarOpen(false)} className="lg:hidden">
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
            新建会话
          </button>
          <div className="relative">
            <IconSearch size={14} className="pointer-events-none absolute top-2.5 left-2.5 text-ink3" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索会话…"
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
              <p className="text-xs text-ink3">{query ? "未找到匹配会话" : "暂无会话，开始第一次对话吧"}</p>
            </div>
          )}
          {groups.map((g) => (
            <div key={g.key} className="mt-3">
              <div className="mb-1 px-1 text-[10.5px] font-semibold tracking-wider text-ink3 uppercase">{g.key}</div>
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
                          {s.title && s.title !== "untitled" ? s.title : "新对话"}
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
                          {timeLabel(s.updated_at)} · {s.model}
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
                              删除
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                setConfirmId(null)
                              }}
                              className="rounded px-1.5 py-0.5 text-[10px] text-ink2 hover:bg-hover"
                            >
                              取消
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            title="删除会话"
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
              <p className="truncate text-[12px] font-semibold text-ink">{status?.model ?? "未配置模型"}</p>
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
              设置
            </button>
            <button
              type="button"
              title="检查点回滚"
              onClick={openCheckpoints}
              className="flex h-8 items-center justify-center rounded-lg bg-hover text-ink2 transition-colors hover:bg-selected active:scale-[0.99]"
            >
              <IconHistory size={14} />
            </button>
            <button
              type="button"
              title={`主题：${theme === "light" ? "亮色" : theme === "dark" ? "暗色" : "跟随系统"}（点击切换）`}
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
