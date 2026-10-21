export interface Status {
  session_id: string
  workdir: string
  sandbox_mode?: string
  env?: Record<string, string>
  display?: DisplayFlags
  model: string
  provider: string
  approval: string
  base_url: string
  api_key_masked: string
  has_api_key: boolean
  mcp_servers?: string[]
  mcp_errors?: string[]
  context?: { used: number; limit: number; percent: number }
  plan?: PlanStep[]
  /** True when the configured provider degraded to the scripted mock. */
  demo_mode?: boolean
  demo_reason?: string
  session_tokens?: {
    input_tokens: number
    output_tokens: number
    reasoning_tokens: number
    total_tokens: number
    calls: number
    cost_usd: number | null
  }
}

export interface DisplayFlags {
  show_thinking: boolean
  show_tools: boolean
  show_plan: boolean
  show_context: boolean
  show_keywords: boolean
  show_notices: boolean
}

export interface PlanStep {
  title: string
  status: "pending" | "in_progress" | "completed"
}

export interface McpServer {
  name: string
  command: string
  args: string[]
  env?: Record<string, string>
  readonly_tools: string[]
  transport?: "stdio" | "sse" | "streamable_http"
  allowed_tools?: string[]
  http?: { url: string; headers: Record<string, string>; timeout_sec: number } | null
}

export interface FullConfig {
  workdir: string
  provider: {
    name: string
    base_url: string
    model: string
    api_key_env: string
    api_key_masked: string
    has_api_key: boolean
  }
  agent: {
    approval: string
    workdir_only: boolean
    sandbox_mode: string
    protected_paths: string[]
    shell_timeout_sec: number
    max_tool_rounds: number
    max_repeat_calls: number
    max_turn_tokens: number
    max_output_chars: number
    effort: "low" | "medium" | "high"
    show_thinking: boolean
    show_tools: boolean
    show_plan: boolean
    show_context: boolean
    show_keywords: boolean
    show_notices: boolean
    system_prompt: string
  }
  context: {
    agents_md: string
    max_fragment_chars: number
    history_budget_chars: number
    max_context_tokens?: number
    compact_threshold?: number
    keep_recent_messages?: number
    repo_map_enabled?: boolean
    repo_map_max_chars?: number
  }
  mcp_servers: McpServer[]
  mcp_errors: string[]
  hooks: HookEntry[]
}

export interface HookEntry {
  event: string
  command: string
  args: string[]
  name: string
  timeout_sec: number
}

export interface SettingsPayload {
  provider: {
    name: string
    base_url: string
    model: string
    api_key?: string
  }
  agent: {
    approval: string
    workdir_only: boolean
    sandbox_mode: string
    protected_paths: string[]
    shell_timeout_sec: number
    max_tool_rounds: number
    max_repeat_calls?: number
    max_turn_tokens?: number
    max_output_chars: number
    show_thinking?: boolean
    show_tools?: boolean
    show_plan?: boolean
    show_context?: boolean
    show_keywords?: boolean
    show_notices?: boolean
    effort?: "low" | "medium" | "high"
    system_prompt?: string
  }
  context?: {
    compact_threshold?: number
    max_context_tokens?: number
    keep_recent_messages?: number
    history_budget_chars?: number
    repo_map_enabled?: boolean
  }
  mcp_servers: McpServer[]
  hooks?: HookEntry[]
}

export interface ToolCall {
  id: string
  name: string
  arguments: unknown
}

export type ChatEvent =
  | { type: "text_delta"; text: string }
  | { type: "reasoning_delta"; text: string }
  | { type: "tool_start"; tool: ToolCall; data?: { subtask_index?: number } }
  | { type: "subtask_start"; data: { index: number; prompt: string; total: number } }
  | { type: "subtask_end"; text?: string; data: { index: number; summary: string } }
  | { type: "subtask_progress"; data: { index: number; phase: "thinking" | "answering"; chars: number } }
  | { type: "subtask_trace"; data: { index: number; thinking_chars: number; answer_chars: number; tools: number } }
  | { type: "tool_end"; tool: ToolCall; ok: boolean; result: unknown; data?: { subtask_index?: number } }
  | { type: "approval_needed"; approval: ApprovalInfo; tool: ToolCall }
  | { type: "compaction"; data: { before_tokens?: number; after_tokens?: number; limit?: number; summarized_messages?: number; usage?: ContextUsage } }
  | { type: "context"; data: { usage: ContextUsage } }
  | { type: "plan"; data: { steps: PlanStep[] } }
  | { type: "turn_end"; text?: string }
  | { type: "turn_error"; text?: string }
  | { type: "done" }

export interface ContextUsage {
  used: number
  limit: number
  percent: number
}

export interface ProbeResult {
  ok: boolean
  error?: string
  base_url?: string
  model?: string
  latency_ms?: number
  rounds?: number
  content?: string
  stream_content?: string
  details?: string[]
}

export interface SessionRow {
  id: string
  title: string
  keywords?: string | null
  workdir: string
  model: string
  created_at: number
  updated_at: number
}

export interface HistoryMessage {
  role: "user" | "assistant" | "tool"
  content: string
  name?: string
  images?: string[]
  /** Database row id; present when the backend could align rows with history. */
  id?: number
  tool_call_id?: string
  /** Present when an assistant turn only requested tools (no text). */
  tool_calls?: Array<{ id: string; name: string; arguments: unknown }>
}

export interface ApprovalInfo {
  summary: string
  diff?: string | null
  id?: string
  /** Tool name that requested approval (present on approval_needed). */
  tool?: string
  /** Exact arguments the tool will run with. */
  arguments?: Record<string, unknown>
}

export interface ModelProfile {
  id: string
  name: string
  provider: string
  base_url: string
  model: string
  api_key_masked: string
  has_api_key: boolean
  active: boolean
}
