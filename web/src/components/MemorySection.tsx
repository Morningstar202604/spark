import { useCallback, useEffect, useState } from "react"
import { fetchMemories, memoryAdd, memoryDelete, memoryOptimize, type MemoryItem, type MemoryStats } from "../api"
import { t } from "../i18n"
import { useUiStore } from "../store/uiStore"

const typeBadge: Record<string, string> = {
  preference: "bg-spark-user/15 text-spark-user",
  project: "bg-spark-ok/15 text-spark-ok",
  lesson: "bg-spark-tool/15 text-spark-tool",
  general: "bg-spark-line text-spark-muted",
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function ItemRow({ item, onDelete }: { item: MemoryItem; onDelete: (id: number) => void }) {
  const lang = useUiStore((s) => s.lang)
  return (
    <div className="flex items-start gap-2 rounded-lg border border-spark-line bg-spark-bg px-3 py-2">
      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${typeBadge[item.type] || typeBadge.general}`}>
        {item.type}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-xs break-words text-spark-text">{item.content}</p>
        <p className="mt-0.5 text-[10px] text-spark-muted">
          {t("memory.importance", lang, { score: item.importance })} · {t("memory.hits", lang, { count: item.access_count })} · {fmtTime(item.updated_at)}
          {item.status === "archived" && ` · ${t("memory.archivedFlag", lang)}`}
        </p>
      </div>
      <button
        type="button"
        onClick={() => onDelete(item.id)}
        className="shrink-0 rounded px-2 py-1 text-[10px] font-bold text-spark-err hover:bg-spark-err/12"
      >
        {t("memory.delete", lang)}
      </button>
    </div>
  )
}

export default function MemorySection() {
  const lang = useUiStore((s) => s.lang)
  const [stats, setStats] = useState<MemoryStats | null>(null)
  const [items, setItems] = useState<MemoryItem[]>([])
  const [query, setQuery] = useState("")
  const [hits, setHits] = useState<MemoryItem[] | null>(null)
  const [newContent, setNewContent] = useState("")
  const [newType, setNewType] = useState("preference")
  const [report, setReport] = useState<string>("")
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async (q: string) => {
    try {
      const data = await fetchMemories(q)
      setStats(data.stats)
      setItems(data.items)
      setHits(q ? data.search : null)
    } catch (e) {
      setReport(t("memory.loadFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    }
  }, [])

  useEffect(() => {
    refresh("")
  }, [refresh])

  async function handleSearch() {
    setBusy(true)
    try {
      await refresh(query.trim())
    } catch (e) {
      setReport(t("memory.searchFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(false)
    }
  }

  async function handleAdd() {
    if (!newContent.trim()) return
    setBusy(true)
    try {
      await memoryAdd({ content: newContent.trim(), type: newType, importance: 8 })
      setNewContent("")
      await refresh(query.trim())
    } catch (e) {
      setReport(t("memory.addFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(id: number) {
    setBusy(true)
    try {
      await memoryDelete(id)
      await refresh(query.trim())
    } catch (e) {
      setReport(t("memory.deleteFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(false)
    }
  }

  async function handleOptimize() {
    setBusy(true)
    setReport(t("common.optimizing", lang))
    try {
      const { report: r } = await memoryOptimize()
      const parts = [t("memory.optimizeMerge", lang, { n: r.consolidated_groups }), t("memory.optimizeArchive", lang, { n: r.archived })]
      if (r.errors?.length) parts.push(t("memory.optimizeError", lang, { n: r.errors.length }))
      setReport(t("memory.optimizeDone", lang, { text: parts.join(", ") }))
      await refresh("")
    } catch (e) {
      setReport(t("memory.optimizeFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-xl border border-spark-line p-4 flex flex-col gap-3">
      <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t("memory.longTerm", lang)}</div>
      <p className="text-xs text-spark-muted">
        {t("memory.description", lang)}
      </p>
      {stats && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-spark-muted">
          <span>{t("memory.active", lang)} <b className="text-spark-text">{stats.active}</b></span>
          <span>{t("memory.archived", lang)} <b className="text-spark-text">{stats.archived}</b></span>
          {Object.entries(stats.by_type).map(([t, n]) => (
            <span key={t}>
              {t} <b className="text-spark-text">{n}</b>
            </span>
          ))}
          <span>
            {t("memory.vectorSearch", lang)}{" "}
            <b className={stats.embedding_available ? "text-spark-ok" : "text-spark-tool"}>
              {stats.embedding_available ? t("memory.available", lang) : stats.embedding_available === false ? t("memory.degraded", lang) : t("memory.undetected", lang)}
            </b>
          </span>
        </div>
      )}

      <div className="grid grid-cols-[1fr_auto] gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              void handleSearch()
            }
          }}
          placeholder={t("memory.searchTest", lang)}
          className="rounded-lg border border-spark-line bg-spark-bg px-3 py-2 text-sm text-spark-text outline-none focus:border-spark-accent"
        />
        <button
          type="button"
          onClick={handleSearch}
          disabled={busy}
          className="rounded-lg bg-spark-line px-3 py-2 text-xs font-bold text-spark-text hover:opacity-80 disabled:opacity-50"
        >
          {t("common.search", lang)}
        </button>
      </div>

      {hits !== null && (
        <div className="flex flex-col gap-2">
          <div className="text-[11px] text-spark-muted">{t("memory.searchResults", lang)}</div>
          {hits.length === 0 && <p className="text-xs text-spark-muted">{t("memory.noHits", lang)}</p>}
          {hits.map((h) => (
            <ItemRow key={`h-${h.id}`} item={h} onDelete={handleDelete} />
          ))}
        </div>
      )}

      <div className="max-h-64 overflow-auto flex flex-col gap-2 border-t border-spark-line pt-3">
        {items.length === 0 && <p className="text-xs text-spark-muted">{t("memory.noItems", lang)}</p>}
        {items.map((item) => (
          <ItemRow key={item.id} item={item} onDelete={handleDelete} />
        ))}
      </div>

      <div className="flex flex-col gap-2 border-t border-spark-line pt-3 sm:grid sm:grid-cols-[1fr_auto_auto] sm:items-center">
        <input
          value={newContent}
          onChange={(e) => setNewContent(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              void handleAdd()
            }
          }}
          placeholder={t("memory.addPlaceholder", lang)}
          className="rounded-lg border border-spark-line bg-spark-bg px-3 py-2 text-sm text-spark-text outline-none focus:border-spark-accent"
        />
        <div className="grid grid-cols-2 gap-2 sm:contents">
          <select
            value={newType}
            onChange={(e) => setNewType(e.target.value)}
            className="rounded-lg border border-spark-line bg-spark-bg px-2 py-2 text-xs text-spark-text"
          >
            <option value="preference">preference</option>
            <option value="project">project</option>
            <option value="lesson">lesson</option>
            <option value="general">general</option>
          </select>
          <button
            type="button"
            onClick={handleAdd}
            disabled={busy || !newContent.trim()}
            className="rounded-lg bg-spark-accent px-3 py-2 text-xs font-bold text-spark-on-accent hover:opacity-90 disabled:opacity-50"
          >
            {t("memory.add", lang)}
          </button>
        </div>
        <button
          type="button"
          onClick={handleOptimize}
          disabled={busy}
          className="rounded-lg bg-spark-line px-3 py-2 text-xs font-bold text-spark-text hover:opacity-80 disabled:opacity-50"
        >
          {t("memory.optimize", lang)}
        </button>
      </div>
      {report && <div className="rounded-lg border border-spark-line px-3 py-2 text-xs text-spark-muted">{report}</div>}
    </div>
  )
}
