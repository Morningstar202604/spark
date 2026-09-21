import { useCallback, useEffect, useState } from "react"
import { fetchMemories, memoryAdd, memoryDelete, memoryOptimize, type MemoryItem, type MemoryStats } from "../api"

const typeBadge: Record<string, string> = {
  preference: "bg-sky-950 text-sky-300",
  project: "bg-emerald-950 text-emerald-300",
  lesson: "bg-amber-950 text-amber-300",
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
  return (
    <div className="flex items-start gap-2 rounded-lg border border-spark-line bg-spark-bg px-3 py-2">
      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${typeBadge[item.type] || typeBadge.general}`}>
        {item.type}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-xs break-words text-spark-text">{item.content}</p>
        <p className="mt-0.5 text-[10px] text-spark-muted">
          重要度 {item.importance} · 命中 {item.access_count} 次 · {fmtTime(item.updated_at)}
          {item.status === "archived" && " · 已归档"}
        </p>
      </div>
      <button
        type="button"
        onClick={() => onDelete(item.id)}
        className="shrink-0 rounded px-2 py-1 text-[10px] font-bold text-spark-err hover:bg-red-950"
      >
        删除
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
  const [report, setReport] = useState<string>("")
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async (q: string) => {
    try {
      const data = await fetchMemories(q)
      setStats(data.stats)
      setItems(data.items)
      setHits(q ? data.search : null)
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    refresh("")
  }, [refresh])

  async function handleSearch() {
    setBusy(true)
    await refresh(query.trim())
    setBusy(false)
  }

  async function handleAdd() {
    if (!newContent.trim()) return
    setBusy(true)
    try {
      await memoryAdd({ content: newContent.trim(), type: newType, importance: 8 })
      setNewContent("")
      await refresh(query.trim())
    } catch {
      /* ignore */
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(id: number) {
    await memoryDelete(id)
    await refresh(query.trim())
  }

  async function handleOptimize() {
    setBusy(true)
    setReport("优化中：相似合并 + 容量淘汰…")
    try {
      const { report: r } = await memoryOptimize()
      const parts = [`合并簇 ${r.consolidated_groups} 组`, `归档 ${r.archived} 条`]
      if (r.errors?.length) parts.push(`错误 ${r.errors.length}`)
      setReport(`完成：${parts.join("，")}。`)
      await refresh("")
    } catch (e) {
      setReport(`优化失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-xl border border-spark-line p-4 flex flex-col gap-3">
      <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">长期记忆</div>
      <p className="text-xs text-spark-muted">
        模型自动从对话中抽取事实，检索后注入每轮上下文。支持去重合并（UPDATE/NOOP）、相似巩固、容量淘汰与访问强化。
      </p>
      {stats && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-spark-muted">
          <span>活跃 <b className="text-spark-text">{stats.active}</b></span>
          <span>归档 <b className="text-spark-text">{stats.archived}</b></span>
          {Object.entries(stats.by_type).map(([t, n]) => (
            <span key={t}>
              {t} <b className="text-spark-text">{n}</b>
            </span>
          ))}
          <span>
            向量检索{" "}
            <b className={stats.embedding_available ? "text-spark-ok" : "text-spark-tool"}>
              {stats.embedding_available ? "可用" : stats.embedding_available === false ? "降级为关键词" : "未探测"}
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
          placeholder="检索测试：输入问题看会命中哪些记忆"
          className="rounded-lg border border-spark-line bg-spark-bg px-3 py-2 text-sm text-spark-text outline-none focus:border-spark-accent"
        />
        <button
          type="button"
          onClick={handleSearch}
          disabled={busy}
          className="rounded-lg bg-spark-line px-3 py-2 text-xs font-bold text-spark-text hover:opacity-80 disabled:opacity-50"
        >
          检索
        </button>
      </div>

      {hits !== null && (
        <div className="flex flex-col gap-2">
          <div className="text-[11px] text-spark-muted">命中结果（含打分）：</div>
          {hits.length === 0 && <p className="text-xs text-spark-muted">无命中。</p>}
          {hits.map((h) => (
            <ItemRow key={`h-${h.id}`} item={h} onDelete={handleDelete} />
          ))}
        </div>
      )}

      <div className="max-h-64 overflow-auto flex flex-col gap-2 border-t border-spark-line pt-3">
        {items.length === 0 && <p className="text-xs text-spark-muted">还没有记忆。对话后自动积累，或在下方手动添加。</p>}
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
          placeholder="手动添加一条记忆"
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
            className="rounded-lg bg-spark-accent px-3 py-2 text-xs font-bold text-teal-950 hover:opacity-90 disabled:opacity-50"
          >
            添加
          </button>
        </div>
        <button
          type="button"
          onClick={handleOptimize}
          disabled={busy}
          className="rounded-lg bg-spark-line px-3 py-2 text-xs font-bold text-spark-text hover:opacity-80 disabled:opacity-50"
        >
          立即优化
        </button>
      </div>
      {report && <div className="rounded-lg border border-spark-line px-3 py-2 text-xs text-spark-muted">{report}</div>}
    </div>
  )
}
