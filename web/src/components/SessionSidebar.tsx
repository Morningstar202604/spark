import { useState } from "react"
import type { SessionRow } from "../types"

interface Props {
  open: boolean
  sessions: SessionRow[]
  current: string
  showKeywords?: boolean
  onClose: () => void
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}

function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
}

export default function SessionSidebar({ open, sessions, current, showKeywords = true, onClose, onSelect, onNew, onDelete }: Props) {
  const [confirmId, setConfirmId] = useState<string | null>(null)
  return (
    <>
      {/* mobile overlay */}
      {open && <div className="fixed inset-0 z-20 bg-black/55 md:hidden" onClick={onClose} />}
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-64 flex-col border-r border-spark-line bg-[#0c1117] transition-transform duration-200 md:static md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full md:w-0 md:overflow-hidden md:border-r-0"
        }`}
      >
        <div className="flex items-center justify-between border-b border-spark-line px-4 py-2.5">
          <span className="text-xs font-bold tracking-widest text-spark-muted uppercase">会话历史</span>
          <button
            type="button"
            onClick={onClose}
            title="关闭侧栏"
            aria-label="关闭侧栏"
            className="-mr-1 flex h-9 w-9 items-center justify-center rounded-lg text-spark-muted transition-colors hover:bg-spark-line hover:text-spark-text md:hidden"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden>
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="p-3">
          <button
            type="button"
            onClick={onNew}
            className="w-full rounded-lg border border-dashed border-spark-line px-4 py-2 text-sm text-spark-muted transition-colors hover:border-spark-accent hover:text-spark-accent active:scale-[0.98]"
          >
            + 新建会话
          </button>
        </div>
        <div className="flex-1 overflow-auto px-3 pb-3">
          {sessions.length === 0 && <p className="px-1 text-xs text-spark-muted">暂无历史会话。</p>}
          <ul className="flex flex-col gap-1.5">
            {sessions.map((s) => (
              <li key={s.id} className="group relative">
                <button
                  type="button"
                  onClick={() => onSelect(s.id)}
                  className={`w-full rounded-lg border px-3 py-2 pr-9 text-left text-sm transition-colors ${
                    s.id === current
                      ? "border-spark-accent/50 bg-spark-panel text-spark-text"
                      : "border-transparent text-spark-muted hover:bg-spark-panel hover:text-spark-text"
                  }`}
                >
                  <span className="block truncate font-medium">
                    {s.title && s.title !== "untitled" ? s.title : "未命名会话"}
                  </span>
                  {showKeywords && s.keywords && (
                    <span className="mt-1 flex flex-wrap gap-1">
                      {s.keywords
                        .split(/\s+/)
                        .filter(Boolean)
                        .slice(0, 3)
                        .map((kw) => (
                          <span key={kw} className="rounded bg-spark-line px-1.5 py-0.5 text-[9px] text-spark-muted">
                            {kw}
                          </span>
                        ))}
                    </span>
                  )}
                  <span className="mt-0.5 block text-[10px] text-spark-muted/70">
                    {timeLabel(s.updated_at)} · {s.model}
                  </span>
                </button>
                {confirmId === s.id ? (
                  <span className="absolute top-1.5 right-1.5 flex items-center gap-1 rounded-lg border border-red-900 bg-spark-bg px-1.5 py-1">
                    <button
                      type="button"
                      title="确认删除"
                      onClick={(e) => {
                        e.stopPropagation()
                        setConfirmId(null)
                        onDelete(s.id)
                      }}
                      className="rounded p-1 text-[10px] font-bold text-spark-err transition-colors hover:bg-red-950"
                    >
                      删除
                    </button>
                    <button
                      type="button"
                      title="取消"
                      onClick={(e) => {
                        e.stopPropagation()
                        setConfirmId(null)
                      }}
                      className="rounded p-1 text-[10px] text-spark-muted transition-colors hover:bg-spark-line"
                    >
                      取消
                      </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      setConfirmId(s.id)
                    }}
                    title="删除会话"
                    aria-label="删除会话"
                    className="absolute top-1.5 right-1.5 flex h-7 w-7 items-center justify-center rounded-lg text-spark-muted transition-all hover:bg-red-950 hover:text-spark-err active:scale-90 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100"
                  >
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden>
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </>
  )
}
