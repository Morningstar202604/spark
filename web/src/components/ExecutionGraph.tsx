import { memo, useMemo } from "react"
import { ReactFlow, Background, Controls, type Node, type Edge } from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { IconButton } from "./ui"
import { IconX, IconSpark } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t, type Lang, type TranslationKey } from "../i18n"

/* ------------------------------------------------------------------ */
/* 执行图（P1-5）：agent 执行图 / 子代理树可视化。                     */
/*                                                                     */
/* 接入点说明（诚实底线）：当前 loop/task 事件流尚未携带结构化节点/边， */
/* 本组件先以静态数据渲染，验证 @xyflow/react v12 的布局、交互与主题。 */
/* 后续接实时数据时：把 useExecutionGraphData() 的返回替换为从          */
/* chatStore / SSE 事件累积出的 nodes/edges 即可，组件本身无需改动。   */
/* ------------------------------------------------------------------ */

export interface ExecutionNode extends Record<string, unknown> {
  id: string
  label: string
  kind: "root" | "agent" | "tool" | "result"
  status: "running" | "done" | "error" | "pending"
}

export interface ExecutionEdge {
  from: string
  to: string
  label?: string
}

const STATUS_COLOR: Record<ExecutionNode["status"], string> = {
  running: "var(--accent)",
  done: "#3fb950",
  error: "#f85149",
  pending: "#8b949e",
}

const KIND_LABEL: Record<ExecutionNode["kind"], TranslationKey> = {
  root: "graph.kind.root",
  agent: "graph.kind.agent",
  tool: "graph.kind.tool",
  result: "graph.kind.result",
}

function nodeColor(kind: ExecutionNode["kind"]): string {
  if (kind === "root") return "#1f6feb"
  if (kind === "agent") return "#8957e5"
  if (kind === "tool") return "#2d76a4"
  return "#238636"
}

/**
 * 静态样例数据 —— 真实接入时替换为事件流累积结果。
 * 形态：主代理 -> 两个并行子代理 -> 各自工具 -> 汇总产出。
 */
const SAMPLE_NODES: ExecutionNode[] = [
  { id: "root", label: "graph.sample.root", kind: "root", status: "running" },
  { id: "agent.research", label: "graph.sample.research", kind: "agent", status: "done" },
  { id: "agent.coder", label: "graph.sample.coder", kind: "agent", status: "running" },
  { id: "tool.search", label: "graph.sample.search", kind: "tool", status: "done" },
  { id: "tool.read", label: "read_file", kind: "tool", status: "done" },
  { id: "tool.edit", label: "edit_file", kind: "tool", status: "running" },
  { id: "result", label: "graph.sample.result", kind: "result", status: "pending" },
]

const SAMPLE_EDGES: ExecutionEdge[] = [
  { from: "root", to: "agent.research", label: "graph.edge.delegate" },
  { from: "root", to: "agent.coder", label: "graph.edge.delegate" },
  { from: "agent.research", to: "tool.search" },
  { from: "agent.research", to: "tool.read" },
  { from: "agent.coder", to: "tool.edit" },
  { from: "tool.search", to: "result" },
  { from: "tool.read", to: "result" },
  { from: "tool.edit", to: "result" },
]

export function buildGraph(nodes: ExecutionNode[], edges: ExecutionEdge[], lang: Lang): {
  nodes: Node[]
  edges: Edge[]
} {
  // 简单分层布局：按 kind 确定行，同层水平排列
  const rows: Record<string, number> = { root: 0, agent: 1, tool: 2, result: 3 }
  const colCount: Record<number, number> = {}
  const pos: Record<string, { x: number; y: number }> = {}
  for (const n of nodes) {
    const row = rows[n.kind] ?? 1
    const col = colCount[row] ?? 0
    colCount[row] = col + 1
    pos[n.id] = { x: col * 260, y: row * 110 }
  }
  const rfNodes: Node[] = nodes.map((n) => ({
    id: n.id,
    position: pos[n.id] ?? { x: 0, y: 0 },
    data: {
      label: (
        <div style={{ borderLeft: `3px solid ${nodeColor(n.kind)}`, paddingLeft: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 600 }}>{t(n.label as TranslationKey, lang)}</div>
          <div style={{ fontSize: 10, opacity: 0.75 }}>
            {t(KIND_LABEL[n.kind], lang)} · <span style={{ color: STATUS_COLOR[n.status] }}>{n.status}</span>
          </div>
        </div>
      ),
    },
    style: {
      background: "var(--panel)",
      color: "var(--ink)",
      border: "1px solid var(--line)",
      borderRadius: 8,
      padding: "6px 10px",
      width: 200,
    },
  }))
  const rfEdges: Edge[] = edges.map((e, i) => ({
    id: `e${i}`,
    source: e.from,
    target: e.to,
    label: e.label ? t(e.label as TranslationKey, lang) : undefined,
    animated: true,
    style: { stroke: "var(--ink3)" },
    markerEnd: { type: "arrowclosed" },
  }))
  return { nodes: rfNodes, edges: rfEdges }
}

export const ExecutionGraph = memo(function ExecutionGraph() {
  const open = useUiStore((s) => s.graphOpen)
  const close = useUiStore((s) => s.closeGraph)
  const lang = useUiStore((s) => s.lang)

  const { nodes, edges } = useMemo(() => buildGraph(SAMPLE_NODES, SAMPLE_EDGES, lang), [lang])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={close}>
      <div
        className="anim-fade flex h-[min(82vh,640px)] w-[min(94vw,960px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={t("graph.title", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconSpark size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("graph.title", lang)}</span>
          <span className="ml-1 text-[10.5px] text-ink3">{t("graph.sampleNote", lang)}</span>
          <IconButton title={t("common.close", lang)} onClick={close} className="ml-auto">
            <IconX size={15} />
          </IconButton>
        </div>
        <div className="min-h-0 flex-1">
          <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
            <Background gap={20} color="var(--line)" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </div>
    </div>
  )
})
