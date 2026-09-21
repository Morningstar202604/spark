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

async function readJson<T>(res: Response): Promise<T> {
  const data = await res.json()
  if (!res.ok) {
    const msg = (data as { error?: string }).error || `HTTP ${res.status}`
    throw new Error(msg)
  }
  return data as T
}

export async function fetchStatus(): Promise<Status> {
  return readJson<Status>(await fetch("/api/status"))
}

export async function fetchConfig(): Promise<FullConfig> {
  return readJson<FullConfig>(await fetch("/api/config"))
}

export async function fetchAgentsMd(): Promise<{
  filename: string
  exists: boolean
  content: string
  chars: number
  max_fragment_chars: number
}> {
  return readJson(await fetch("/api/agents_md"))
}

export async function saveAgentsMd(
  content: string,
): Promise<{ ok: boolean; chars: number; max_fragment_chars: number }> {
  return readJson(
    await fetch("/api/agents_md", {
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
  return readJson(await fetch(url))
}

export async function memoryAdd(payload: {
  content: string
  type: string
  importance: number
}): Promise<{ ok: boolean; id: number; stats: MemoryStats }> {
  return readJson(
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "add", ...payload }),
    }),
  )
}

export async function memoryDelete(id: number): Promise<{ ok: boolean; stats: MemoryStats }> {
  return readJson(
    await fetch("/api/memory", {
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
    await fetch("/api/memory", {
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
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function listSessions(): Promise<{ sessions: SessionRow[]; current: string }> {
  return readJson(await fetch("/api/sessions"))
}

export async function newSession(): Promise<Status> {
  const data = await readJson<{ status: Status }>(
    await fetch("/api/sessions/new", { method: "POST" }),
  )
  return data.status
}

export async function switchSession(
  sessionId: string,
): Promise<{ status: Status; messages: HistoryMessage[] }> {
  return readJson(
    await fetch("/api/sessions/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  )
}

export async function cancelTurn(): Promise<void> {
  await readJson(await fetch("/api/cancel", { method: "POST" }))
}

export async function deleteSession(sessionId: string): Promise<{ ok: boolean; status?: Status }> {
  return readJson(
    await fetch("/api/sessions/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  )
}

export async function respondApproval(decision: "allow" | "allow_always" | "deny"): Promise<void> {
  await readJson(
    await fetch("/api/approval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    }),
  )
}

export async function listModelProfiles(): Promise<{ profiles: ModelProfile[] }> {
  return readJson(await fetch("/api/models"))
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
    await fetch("/api/models/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function deleteModelProfile(id: string): Promise<void> {
  await readJson(
    await fetch("/api/models/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }),
  )
}

export async function activateModelProfile(id: string): Promise<{ status: Status }> {
  return readJson(
    await fetch("/api/models/activate", {
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
  const res = await fetch("/api/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  const data = await res.json()
  return data as ProbeResult
}

export interface CheckpointRow {
  id: number
  label: string
  message_id: number
  created_at: number
}

export async function listCheckpoints(): Promise<{ checkpoints: CheckpointRow[] }> {
  return readJson(await fetch("/api/checkpoints"))
}

export async function rollbackCheckpoint(checkpointId: number): Promise<{
  ok: boolean
  report: { checkpoint_id: number; label: string; files: { restored_files: number; snapshot_id: string }; removed_messages: number }
  status: Status
  messages: HistoryMessage[]
}> {
  return readJson(
    await fetch("/api/checkpoints/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ checkpoint_id: checkpointId }),
    }),
  )
}

export async function streamChat(
  prompt: string,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
  images: string[] = [],
): Promise<void> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, images }),
    signal,
  })
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
  const decoder = new TextDecoder()
  let buffer = ""
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      for (const line of frame.split("\n")) {
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
}
