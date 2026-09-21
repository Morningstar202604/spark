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
  readonly_tools: string[]
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
    max_output_chars: number
    show_thinking: boolean
    show_tools: boolean
    show_plan: boolean
    show_context: boolean
    show_keywords: boolean
    show_notices: boolean
  }
  context: {
    agents_md: string
    max_fragment_chars: number
    history_budget_chars: number
    max_context_tokens?: number
    compact_threshold?: number
    keep_recent_messages?: number
  }
  mcp_servers: McpServer[]
  mcp_errors: string[]
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
    max_output_chars: number
    show_thinking?: boolean
    show_tools?: boolean
    show_plan?: boolean
    show_context?: boolean
    show_keywords?: boolean
    show_notices?: boolean
  }
  mcp_servers: McpServer[]
}

export interface ToolCall {
  id: string
  name: string
  arguments: unknown
}

export type ChatEvent =
  | { type: "text_delta"; text: string }
  | { type: "reasoning_delta"; text: string }
  | { type: "tool_start"; tool: ToolCall }
  | { type: "tool_end"; tool: ToolCall; ok: boolean; result: unknown }
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
  model?: string
  latency_ms?: number
  rounds?: number
  content?: string
  stream_content?: string
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
}

export interface ApprovalInfo {
  summary: string
  diff?: string | null
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
