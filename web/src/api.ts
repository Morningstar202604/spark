import type {
  ChatEvent,
  Status,
  ProbeResult,
  SessionRow,
  HistoryMessage,
  FullConfig,
  SettingsPayload,
  ModelProfile,
} from "./types"
import { useUiStore } from "./store/uiStore"

const DEFAULT_TIMEOUT_MS = 30000

export async function fetchWithTimeout(
  url: string,
  options?: RequestInit,
  timeout = DEFAULT_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController()
  const timeoutId = setTimeout(
    () => controller.abort(new DOMException("Request timeout", "TimeoutError")),
    timeout,
  )
  if (options?.signal) {
    options.signal.addEventListener("abort", () => controller.abort(), { once: true })
  }
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } finally {
    clearTimeout(timeoutId)
  }
}

async function readJson<T>(res: Response): Promise<T> {
  const data = await res.json()
  if (!res.ok) {
    const msg = (data as { error?: string }).error || `HTTP ${res.status}`
    throw new Error(msg)
  }
  return data as T
}

export async function fetchStatus(): Promise<Status> {
  return readJson<Status>(await fetchWithTimeout("/api/status"))
}

export async function fetchConfig(): Promise<FullConfig> {
  return readJson<FullConfig>(await fetchWithTimeout("/api/config"))
}

export async function fetchAgentsMd(): Promise<{
  filename: string
  exists: boolean
  content: string
  chars: number
  max_fragment_chars: number
}> {
  return readJson(await fetchWithTimeout("/api/agents_md"))
}

export async function saveAgentsMd(
  content: string,
): Promise<{ ok: boolean; chars: number; max_fragment_chars: number }> {
  return readJson(
    await fetchWithTimeout("/api/agents_md", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
  )
}

export interface MemoryItem {
  id: number
  type: string
  content: string
  importance: number
  status: string
  access_count: number
  created_at: number
  updated_at: number
  score?: number
}

export interface MemoryStats {
  active: number
  archived: number
  by_type: Record<string, number>
  embedding_available: boolean | null
  embedding_model: string
}

export async function fetchMemories(query: string): Promise<{
  items: MemoryItem[]
  search: MemoryItem[]
  stats: MemoryStats
}> {
  const url = query ? `/api/memory?q=${encodeURIComponent(query)}` : "/api/memory"
  return readJson(await fetchWithTimeout(url))
}

export async function memoryAdd(payload: {
  content: string
  type: string
  importance: number
}): Promise<{ ok: boolean; id: number; stats: MemoryStats }> {
  return readJson(
    await fetchWithTimeout("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "add", ...payload }),
    }),
  )
}

export async function memoryDelete(id: number): Promise<{ ok: boolean; stats: MemoryStats }> {
  return readJson(
    await fetchWithTimeout("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", id }),
    }),
  )
}

export async function memoryOptimize(): Promise<{
  ok: boolean
  report: { consolidated_groups: number; archived: number; errors: string[] }
}> {
  return readJson(
    await fetchWithTimeout("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "optimize" }),
    }),
  )
}

export async function saveSettings(
  payload: SettingsPayload,
): Promise<{ ok: boolean; status: Status; mcp_errors: string[]; config: FullConfig }> {
  return readJson(
    await fetchWithTimeout("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function listSessions(): Promise<{ sessions: SessionRow[]; current: string }> {
  return readJson(await fetchWithTimeout("/api/sessions"))
}

export async function fetchHistory(): Promise<{ session_id: string; messages: HistoryMessage[] }> {
  return readJson(await fetchWithTimeout("/api/history"))
}

export async function newSession(): Promise<Status> {
  const data = await readJson<{ status: Status }>(
    await fetchWithTimeout("/api/sessions/new", { method: "POST" }),
  )
  return data.status
}

export async function switchSession(
  sessionId: string,
): Promise<{ status: Status; messages: HistoryMessage[] }> {
  return readJson(
    await fetchWithTimeout("/api/sessions/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  )
}

export async function cancelTurn(): Promise<void> {
  await readJson(await fetchWithTimeout("/api/cancel", { method: "POST" }))
}

export async function deleteSession(sessionId: string): Promise<{ ok: boolean; status?: Status }> {
  return readJson(
    await fetchWithTimeout("/api/sessions/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  )
}

export async function respondApproval(decision: "allow" | "allow_always" | "deny"): Promise<void> {
  await readJson(
    await fetchWithTimeout("/api/approval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    }),
  )
}

export async function listModelProfiles(): Promise<{ profiles: ModelProfile[] }> {
  return readJson(await fetchWithTimeout("/api/models"))
}

export async function saveModelProfile(payload: {
  id?: string
  name: string
  provider: string
  base_url: string
  model: string
  api_key?: string
}): Promise<{ ok: boolean; id: string }> {
  return readJson(
    await fetchWithTimeout("/api/models/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function deleteModelProfile(id: string): Promise<void> {
  await readJson(
    await fetchWithTimeout("/api/models/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }),
  )
}

export async function activateModelProfile(id: string): Promise<{ status: Status }> {
  return readJson(
    await fetchWithTimeout("/api/models/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }),
  )
}

export async function testConnection(payload: {
  base_url: string
  model: string
  api_key: string
}): Promise<ProbeResult> {
  const res = await fetchWithTimeout("/api/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  const data = await res.json()
  return data as ProbeResult
}

/* ------------------------------------------------------------------ */
/* 内置终端（PTY）                                                     */
/* 架构说明：后端服务器基于标准库 http.server，不支持 WebSocket 升级，  */
/* 实时通道采用 SSE（GET 长连接逐帧推送），与 /api/chat/stream 同构。   */
/* ------------------------------------------------------------------ */

export interface PtyOpenInfo {
  tab_id: string
  cwd: string
  alive: boolean
  rows: number
  cols: number
}

export async function ptyOpen(tabId = "main", cwd?: string): Promise<PtyOpenInfo> {
  return readJson<PtyOpenInfo>(
    await fetchWithTimeout("/api/pty/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tab_id: tabId, cwd }),
    }),
  )
}

export async function ptyWrite(tabId: string, data: string): Promise<boolean> {
  const r = await readJson<{ ok: boolean }>(
    await fetchWithTimeout("/api/pty/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tab_id: tabId, data }),
    }),
  )
  return r.ok
}

export async function ptyResize(tabId: string, rows: number, cols: number): Promise<void> {
  await readJson(
    await fetchWithTimeout("/api/pty/resize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tab_id: tabId, rows, cols }),
    }),
  )
}

export async function ptyClose(tabId: string): Promise<void> {
  await readJson(
    await fetchWithTimeout("/api/pty/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tab_id: tabId }),
    }),
  )
}

export type PtyEvent =
  | { type: "pty_open"; tab_id: string; cwd: string }
  | { type: "pty_data"; data: string }
  | { type: "pty_exit"; tab_id: string }

/** 订阅 PTY 输出 SSE 流（GET 长连接）。返回的 Promise 仅在流结束时 resolve。 */
export async function streamPty(
  tabId: string,
  onEvent: (ev: PtyEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetchWithTimeout(
    `/api/pty/stream?tab_id=${encodeURIComponent(tabId)}`,
    { method: "GET", signal },
    120000,
  )
  if (!res.ok || !res.body) {
    throw new Error(`pty stream HTTP ${res.status}`)
  }
  const reader = res.body.getReader()
  try {
    const decoder = new TextDecoder()
    let buffer = ""
    for (;;) {
      const { done, value } = await reader.read()
      if (done) return
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data: ")) continue
          const payload = line.slice(6)
          if (!payload) continue
          try {
            onEvent(JSON.parse(payload) as PtyEvent)
          } catch {
            /* skip malformed frame */
          }
        }
      }
    }
  } finally {
    try { reader.cancel() } catch { /* ignore */ }
  }
}

/* ------------------------------------------------------------------ */
/* MCP registry 聚合搜索（P1-7）                                        */
/* ------------------------------------------------------------------ */

export interface McpRegistryItem {
  name: string
  title: string
  description: string
  version: string
  repository_url: string
  transport: "stdio" | "remote" | "unknown"
  remote_url: string | null
  suggested_command: string | null
  suggested_args: string[]
  required_env: { name: string; required: boolean; secret: boolean }[]
  glama: { score: number; count: number; source: string } | null
  china: { verdict: "available" | "risk" | "unreachable"; reasons: string[] }
}

export interface McpRegistryResult {
  items: McpRegistryItem[]
  source: "live" | "offline"
  error: string | null
  filtered_out: number
}

export async function searchMcpRegistry(
  query: string,
  includeUnavailable = false,
): Promise<McpRegistryResult> {
  const q = encodeURIComponent(query)
  const inc = includeUnavailable ? "1" : "0"
  return readJson<McpRegistryResult>(
    await fetchWithTimeout(`/api/mcp/registry/search?q=${q}&include_unavailable=${inc}`, undefined, 45000),
  )
}

export interface CheckpointRow {
  id: number
  label: string
  message_id: number
  created_at: number
}

export async function listCheckpoints(): Promise<{ checkpoints: CheckpointRow[] }> {
  return readJson(await fetchWithTimeout("/api/checkpoints"))
}

export async function rollbackCheckpoint(checkpointId: number): Promise<{
  ok: boolean
  report: { checkpoint_id: number; label: string; files: { restored_files: number; snapshot_id: string }; removed_messages: number }
  status: Status
  messages: HistoryMessage[]
}> {
  return readJson(
    await fetchWithTimeout("/api/checkpoints/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ checkpoint_id: checkpointId }),
    }),
  )
}

/* ------------------------------------------------------------------ */
/* 只读工具：文件查看 / 网页阅读（与 agent 工具共用同一套安全边界）      */
/* ------------------------------------------------------------------ */

export interface FsEntry {
  name: string
  type: "dir" | "file"
  size?: number
  mtime?: number
}

export async function fetchFsTree(path: string): Promise<{ path?: string; entries?: FsEntry[]; error?: string }> {
  return readJson(await fetchWithTimeout(`/api/fs/tree?path=${encodeURIComponent(path)}`))
}

export async function fetchFsRead(
  path: string,
): Promise<{ name?: string; path?: string; content?: string; size?: number; truncated?: boolean; binary?: boolean; error?: string }> {
  return readJson(await fetchWithTimeout(`/api/fs/read?path=${encodeURIComponent(path)}`))
}

export async function browseUrl(
  url: string,
): Promise<{ title?: string; url?: string; content?: string; error?: string }> {
  return readJson(await fetchWithTimeout(`/api/browse?url=${encodeURIComponent(url)}`))
}

export async function streamChat(
  prompt: string,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
  images: string[] = [],
): Promise<void> {
  if (signal?.aborted) {
    throw new DOMException("Aborted", "AbortError")
  }

  const MAX_RETRIES = 3
  const BASE_DELAY = 1000
  let lastEventId: string | undefined
  let attempt = 0

  for (;;) {
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" }
      if (lastEventId) headers["Last-Event-ID"] = lastEventId
      const res = await fetchWithTimeout(
        "/api/chat/stream",
        {
          method: "POST",
          headers,
          body: JSON.stringify({ prompt, images }),
          signal,
        },
        60000,
      )
      if (!res.ok || !res.body) {
        let msg = `HTTP ${res.status}`
        try {
          const data = await res.json()
          if (data.error) msg = data.error
        } catch {
          /* ignore */
        }
        throw new Error(msg)
      }
      const reader = res.body.getReader()
      try {
        const decoder = new TextDecoder()
        let buffer = ""
        for (;;) {
          const { done, value } = await reader.read()
          if (done) return
          buffer += decoder.decode(value, { stream: true })
          let idx: number
          while ((idx = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            for (const line of frame.split("\n")) {
              if (line.startsWith("id: ")) { lastEventId = line.slice(4).trim(); continue }
              if (!line.startsWith("data: ")) continue
              const payload = line.slice(6)
              if (!payload || payload === "[DONE]") continue
              try {
                onEvent(JSON.parse(payload) as ChatEvent)
              } catch {
                /* skip malformed frame */
              }
            }
          }
        }
      } finally {
        try { reader.cancel() } catch { /* ignore */ }
      }
    } catch (e) {
      if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
        throw e
      }
      attempt++
      if (attempt > MAX_RETRIES) {
        useUiStore.getState().pushToast("toast.sendFailed", "error")
        throw e
      }
      useUiStore.getState().pushToast("toast.reconnecting", "info")
      const delay = Math.min(BASE_DELAY * Math.pow(2, attempt - 1), 32000)
      await new Promise((r) => setTimeout(r, delay))
      if (signal?.aborted) {
        throw new DOMException("Aborted", "AbortError")
      }
    }
  }
}
