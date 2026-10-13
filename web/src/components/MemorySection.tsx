import { useCallback, useEffect, useState } from "react"
import { fetchMemories, memoryAdd, memoryDelete, memoryOptimize, type MemoryItem, type MemoryStats } from "../api"
import { Badge, Button, Field, Select, TextInput, cls } from "./ui"
import { IconSearch, IconTrash } from "./icons"

const typeTone: Record<string, "accent" | "ok" | "warn" | "neutral"> = {
  preference: "accent",
  project: "ok",
  lesson: "warn",
  general: "neutral",
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
}

function ItemRow({ item, onDelete }: { item: MemoryItem; onDelete: (id: number) => void }) {
  const score = typeof item.score === "number" ? item.score.toFixed(3) : null
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-line bg-panel2 px-3 py-2.5">
      <span className="mt-0.5 shrink-0">
        <Badge tone={typeTone[item.type] ?? "neutral"}>{item.type}</Badge>
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[12.5px] break-words leading-relaxed text-ink">{item.content}</p>
        <p className="mt-1 text-[10.5px] text-ink3">
          重要度 {item.importance} · 命中 {item.access_count} 次 · {fmtTime(item.updated_at)}
          {score !== null && <span className="text-accent"> · score {score}</span>}
          {item.status === "archived" && <span className="text-ink3"> · 已归档</span>}
        </p>
      </div>
      <button
        type="button"
        onClick={() => onDelete(item.id)}
        title="删除"
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

  const refresh = useCallback(async (q: string) => {
    try {
      const data = await fetchMemories(q)
      setStats(data.stats)
      setItems(data.items)
      setHits(q ? data.search : null)
    } catch (e) {
      setReport(`记忆加载失败：${e instanceof Error ? e.message : String(e)}`)
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
      setReport(`检索失败：${e instanceof Error ? e.message : String(e)}`)
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
      setReport(`添加失败：${e instanceof Error ? e.message : String(e)}`)
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
      setReport(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  async function handleOptimize() {
    setBusy(true)
    setReport("优化中：相似合并 + 容量淘汰…")
    try {
      const { report: r } = await memoryOptimize()
      const parts = [`合并簇 ${r.consolidated_groups} 组`, `归档 ${r.archived} 条`]
      if (r.errors?.length) parts.push(`错误 ${r.errors.length}`)
      setReport(`完成：${parts.join(", ")}`)
      await refresh("")
    } catch (e) {
      setReport(`优化失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-ink">长期记忆</div>
        {stats && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-ink3">
            <span>活跃 <b className="text-ink">{stats.active}</b></span>
            <span>归档 <b className="text-ink">{stats.archived}</b></span>
            {Object.entries(stats.by_type).map(([t, n]) => (
              <span key={t}>
                {t} <b className="text-ink">{n}</b>
              </span>
            ))}
            <span>
              向量检索{" "}
              <b className={stats.embedding_available ? "text-ok" : stats.embedding_available === false ? "text-warn" : "text-ink3"}>
                {stats.embedding_available ? "可用" : stats.embedding_available === false ? "降级为关键词" : "未探测"}
              </b>
            </span>
          </div>
        )}
      </div>

      <p className="text-xs leading-relaxed text-ink3">
        模型自动从对话中抽取事实，检索后注入每轮上下文。支持去重合并、相似巩固、容量淘汰与访问强化。
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
          placeholder="检索测试：输入问题看会命中哪些记忆"
        />
        <Button variant="soft" onClick={handleSearch} disabled={busy} className="shrink-0">
          <IconSearch size={13} /> 检索
        </Button>
      </div>

      {hits !== null && (
        <div className="flex flex-col gap-2">
          <div className="text-[11px] font-medium text-ink2">命中结果（含打分）：</div>
          {hits.length === 0 && <p className="text-xs text-ink3">无命中。</p>}
          {hits.map((h) => (
            <ItemRow key={`h-${h.id}`} item={h} onDelete={handleDelete} />
          ))}
        </div>
      )}

      <div className={cls("flex max-h-64 flex-col gap-2 overflow-y-auto border-t border-line pt-3")}>
        {items.length === 0 && <p className="py-2 text-xs text-ink3">还没有记忆。对话后自动积累，或在下方手动添加。</p>}
        {items.map((item) => (
          <ItemRow key={item.id} item={item} onDelete={handleDelete} />
        ))}
      </div>

      <div className="flex flex-col gap-2 border-t border-line pt-3">
        <Field label="新增记忆">
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
              placeholder="手动添加一条记忆"
              className="flex-1"
            />
            <Select value={newType} onChange={(e) => setNewType(e.target.value)} className="w-32 shrink-0">
              <option value="preference">preference</option>
              <option value="project">project</option>
              <option value="lesson">lesson</option>
              <option value="general">general</option>
            </Select>
          </div>
        </Field>
        <div className="flex gap-2">
          <Button variant="primary" full onClick={handleAdd} disabled={busy || !newContent.trim()}>
            添加
          </Button>
          <Button variant="outline" onClick={handleOptimize} disabled={busy}>
            立即优化
          </Button>
        </div>
      </div>

      {report && <div className="rounded-lg border border-line bg-panel2 px-3 py-2 text-xs text-ink2">{report}</div>}
    </div>
  )
}
