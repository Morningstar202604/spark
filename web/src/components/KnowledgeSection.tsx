import { useCallback, useEffect, useState } from "react"
import { Badge, Button, Field, IconButton, Spinner, TextInput, cls } from "./ui"
import { IconPlus, IconSearch, IconTrash } from "./icons"
import { knowledgeAction, type KnowledgeDoc, type KnowledgeHit } from "../api"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/**
 * 知识库：用户显式导入的项目文档，每轮按输入检索后注入。
 *
 * 与「记忆」的区别在这里明确写出来，否则用户会分不清该把规范放哪：
 * 记忆是模型自己总结的偏好，知识库是人放进去的原文。
 */
export default function KnowledgeSection() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [items, setItems] = useState<KnowledgeDoc[]>([])
  const [enabled, setEnabled] = useState(true)
  const [topK, setTopK] = useState(6)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [pathInput, setPathInput] = useState("")
  const [query, setQuery] = useState("")
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null)
  const [notice, setNotice] = useState("")

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await knowledgeAction({ action: "list" })
      setItems(data.items ?? [])
      setEnabled(data.enabled ?? true)
      setTopK(data.top_k ?? 6)
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    void load()
  }, [load])

  async function addDocs() {
    const paths = pathInput
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean)
    if (!paths.length) return
    setBusy(true)
    try {
      const data = await knowledgeAction({ action: "add", paths })
      setItems(data.items ?? [])
      setNotice(data.errors && data.errors.length ? data.errors.join("; ") : "")
      setPathInput("")
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    setBusy(true)
    try {
      const data = await knowledgeAction({ action: "delete", id })
      setItems(data.items ?? [])
      setHits(null)
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function runSearch() {
    if (!query.trim()) return
    setBusy(true)
    try {
      const data = await knowledgeAction({ action: "search", query })
      setHits(data.hits ?? [])
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function saveSettings(patch: { enabled?: boolean; top_k?: number }) {
    try {
      const data = await knowledgeAction({ action: "settings", ...patch })
      setEnabled(data.enabled ?? enabled)
      setTopK(data.top_k ?? topK)
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-xs text-ink3">
        <Spinner size={13} /> {t("common.loading", lang)}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("knowledge.title", lang)}</h3>
          <Badge tone="accent">{items.length}</Badge>
          <button
            type="button"
            className={cls(
              "ml-auto shrink-0 rounded-lg px-2.5 py-1 text-[11.5px] font-medium transition-colors",
              enabled ? "bg-ok/15 text-ok" : "bg-hover text-ink3",
            )}
            onClick={() => void saveSettings({ enabled: !enabled })}
          >
            {enabled ? t("knowledge.on", lang) : t("knowledge.off", lang)}
          </button>
        </div>
        <p className="text-xs leading-relaxed text-ink3">{t("knowledge.desc", lang)}</p>

        <Field label={t("knowledge.addLabel", lang)}>
          <TextInput
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            placeholder="docs/ARCHITECTURE.md"
            spellCheck={false}
          />
        </Field>
        <div className="flex items-end gap-2">
          <Button variant="primary" className="flex-1" onClick={() => void addDocs()} disabled={busy || !pathInput.trim()}>
            {busy ? <Spinner size={12} /> : <IconPlus size={13} />} {t("knowledge.add", lang)}
          </Button>
          <div className="flex w-32 shrink-0 flex-col gap-1.5">
            <span className="text-[12px] font-medium text-ink2">{t("knowledge.topK", lang)}</span>
            <TextInput
              type="number"
              min={1}
              max={50}
              value={topK}
              onChange={(e) => {
                const v = Math.min(50, Math.max(1, Number(e.target.value) || 6))
                setTopK(v)
                void saveSettings({ top_k: v })
              }}
              className="w-full"
            />
          </div>
        </div>
        <p className="text-[10.5px] leading-relaxed text-ink3">{t("knowledge.pathHint", lang)}</p>
        {notice && <div className="rounded-lg border border-err/30 bg-err/5 px-3 py-2 text-[11.5px] text-err">{notice}</div>}
      </section>

      <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
        <h3 className="text-sm font-semibold text-ink">{t("knowledge.docsTitle", lang)}</h3>
        {items.length === 0 ? (
          <p className="py-3 text-xs text-ink3">{t("knowledge.noDocs", lang)}</p>
        ) : (
          <div className="flex max-h-64 flex-col gap-1.5 overflow-y-auto">
            {items.map((d) => (
              <div key={d.id} className="flex items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2">
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">{d.name}</span>
                <span className="shrink-0 text-[10.5px] text-ink3">
                  {d.chunks} {t("knowledge.chunks", lang)} · {fmtSize(d.size)}
                </span>
                <IconButton
                  title={t("common.delete", lang)}
                  onClick={() => void remove(d.id)}
                  className="h-7 w-7 text-ink3 hover:text-err"
                >
                  <IconTrash size={12} />
                </IconButton>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
        <h3 className="text-sm font-semibold text-ink">{t("knowledge.searchTitle", lang)}</h3>
        <div className="flex gap-2">
          <TextInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                void runSearch()
              }
            }}
            placeholder={t("knowledge.searchPh", lang)}
          />
          <Button variant="soft" className="shrink-0" onClick={() => void runSearch()} disabled={busy}>
            <IconSearch size={13} /> {t("common.search", lang)}
          </Button>
        </div>
        {hits !== null && (
          <div className="flex flex-col gap-2">
            {hits.length === 0 ? (
              <p className="text-xs text-ink3">{t("knowledge.noHits", lang)}</p>
            ) : (
              hits.map((h, i) => (
                <div key={i} className="flex flex-col gap-1 rounded-lg bg-panel2 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[10.5px] text-ink3">
                      {h.doc_name} #{h.index}
                    </span>
                    <span className="ml-auto text-[10.5px] text-accent">{h.score.toFixed(2)}</span>
                  </div>
                  <p className="line-clamp-3 whitespace-pre-wrap text-[11.5px] leading-relaxed text-ink2">{h.text}</p>
                </div>
              ))
            )}
          </div>
        )}
      </section>
    </div>
  )
}
