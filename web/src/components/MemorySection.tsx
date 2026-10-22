import { useCallback, useEffect, useState } from "react"
import { fetchMemories, memoryAdd, memoryDelete, memoryOptimize, type MemoryItem, type MemoryStats } from "../api"
import { Badge, Button, Field, Select, TextInput, cls } from "./ui"
import { IconSearch, IconTrash } from "./icons"
import { t, type Lang } from "../i18n"
import { useUiStore } from "../store/uiStore"

const typeTone: Record<string, "accent" | "ok" | "warn" | "neutral"> = {
  preference: "accent",
  project: "ok",
  lesson: "warn",
  general: "neutral",
}

function fmtTime(ts: number, lang: Lang): string {
  return new Date(ts * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
}

function ItemRow({ item, onDelete }: { item: MemoryItem; onDelete: (id: number) => void }) {
  const lang = useUiStore((s) => s.lang)
  const score = typeof item.score === "number" ? item.score.toFixed(3) : null
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-line bg-panel2 px-3 py-2.5">
      <span className="mt-0.5 shrink-0">
        <Badge tone={typeTone[item.type] ?? "neutral"}>{item.type}</Badge>
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[12.5px] break-words leading-relaxed text-ink">{item.content}</p>
        <p className="mt-1 text-[10.5px] text-ink3">
          {t("memory.importance", lang, { score: item.importance })} · {t("memory.hits", lang, { count: item.access_count })} · {fmtTime(item.updated_at, lang)}
          {score !== null && <span className="text-accent"> · score {score}</span>}
          {item.status === "archived" && <span className="text-ink3"> · {t("memory.archivedFlag", lang)}</span>}
        </p>
      </div>
      <button
        type="button"
        onClick={() => onDelete(item.id)}
        title={t("common.delete", lang)}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink3 transition-colors hover:bg-err/10 hover:text-err"
      >
        <IconTrash size={12} />
      </button>
    </div>
  )
}

export default function MemorySection() {
  const [stats, setStats] = useState<MemoryStats | null>(null)
  const [items, setItems] = useState<MemoryItem[]>([])
  const [query, setQuery] = useState("")
  const [hits, setHits] = useState<MemoryItem[] | null>(null)
  const [newContent, setNewContent] = useState("")
  const [newType, setNewType] = useState("preference")
  const [report, setReport] = useState("")
  const [busy, setBusy] = useState(false)
  const lang = useUiStore((s) => s.lang)

  const refresh = useCallback(async (q: string) => {
    try {
      const data = await fetchMemories(q)
      setStats(data.stats)
      setItems(data.items)
      setHits(q ? data.search : null)
    } catch (e) {
      setReport(t("memory.loadFailed", useUiStore.getState().lang, { msg: e instanceof Error ? e.message : String(e) }))
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
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-ink">{t("memory.longTerm", lang)}</div>
        {stats && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-ink3">
            <span>{t("memory.active", lang)} <b className="text-ink">{stats.active}</b></span>
            <span>{t("memory.archived", lang)} <b className="text-ink">{stats.archived}</b></span>
            {Object.entries(stats.by_type).map(([t, n]) => (
              <span key={t}>
                {t} <b className="text-ink">{n}</b>
              </span>
            ))}
            <span>
              {t("memory.vectorSearch", lang)}{" "}
              <b className={stats.embedding_available ? "text-ok" : stats.embedding_available === false ? "text-warn" : "text-ink3"}>
                {stats.embedding_available ? t("memory.available", lang) : stats.embedding_available === false ? t("memory.degraded", lang) : t("memory.undetected", lang)}
              </b>
            </span>
          </div>
        )}
      </div>

      <p className="text-xs leading-relaxed text-ink3">
        {t("memory.description", lang)}
      </p>

      <div className="flex gap-2">
        <TextInput
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              void handleSearch()
            }
          }}
          placeholder={t("memory.searchTest", lang)}
        />
        <Button variant="soft" onClick={handleSearch} disabled={busy} className="shrink-0">
          <IconSearch size={13} /> {t("common.search", lang)}
        </Button>
      </div>

      {hits !== null && (
        <div className="flex flex-col gap-2">
          <div className="text-[11px] font-medium text-ink2">{t("memory.searchResults", lang)}</div>
          {hits.length === 0 && <p className="text-xs text-ink3">{t("memory.noHits", lang)}</p>}
          {hits.map((h) => (
            <ItemRow key={`h-${h.id}`} item={h} onDelete={handleDelete} />
          ))}
        </div>
      )}

      <div className={cls("flex max-h-64 flex-col gap-2 overflow-y-auto border-t border-line pt-3")}>
        {items.length === 0 && <p className="py-2 text-xs text-ink3">{t("memory.noItems", lang)}</p>}
        {items.map((item) => (
          <ItemRow key={item.id} item={item} onDelete={handleDelete} />
        ))}
      </div>

      <div className="flex flex-col gap-2 border-t border-line pt-3">
        <Field label={t("memory.add", lang)}>
          <div className="flex gap-2">
            <TextInput
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  void handleAdd()
                }
              }}
              placeholder={t("memory.addPlaceholder", lang)}
              className="flex-1"
            />
            <Select value={newType} onChange={(e) => setNewType(e.target.value)} className="w-32 shrink-0">
              <option value="preference">{t("memory.type.preference", lang)}</option>
              <option value="project">{t("memory.type.project", lang)}</option>
              <option value="lesson">{t("memory.type.lesson", lang)}</option>
              <option value="general">{t("memory.type.general", lang)}</option>
            </Select>
          </div>
        </Field>
        <div className="flex items-center gap-2">
          <Button variant="primary" className="flex-1" onClick={handleAdd} disabled={busy || !newContent.trim()}>
            {t("common.add", lang)}
          </Button>
          <Button variant="outline" className="shrink-0 whitespace-nowrap" onClick={handleOptimize} disabled={busy}>
            {t("memory.optimize", lang)}
          </Button>
        </div>
      </div>

      {report && <div className="rounded-lg border border-line bg-panel2 px-3 py-2 text-xs text-ink2">{report}</div>}
    </div>
  )
}
