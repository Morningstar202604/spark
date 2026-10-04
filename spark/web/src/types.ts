export interface Preset {
  label: string;
  base_url?: string;
  model?: string;
}

export interface Cfg {
  provider?: string;
  base_url?: string;
  proxy?: string;
  model?: string;
  model_fast?: string;
  fallback_model?: string;
  api_key?: string;
  workdir?: string;
  approval_mode?: string;
  max_context_tokens?: number;
  memory_embedding?: string;
  memory_embed_model?: string;
  embed_base_url?: string;
  embed_api_key?: string;
  token_set?: boolean;
  system_prompt?: string;
  protected_paths?: string[];
  max_turns?: number;
  tool_timeout?: number;
  auto_verify?: boolean;
  temperature?: string | number;
  max_tokens?: string | number;
  route_enabled?: boolean;
  route_keywords?: string;
  usage_pricing?: Record<string, unknown>;
  demo_mode?: boolean;
  version?: string;
}

export interface McpServer {
  name: string;
  transport?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headersJson?: string;
}

export interface SessionMeta {
  id: string;
  title?: string;
  workdir?: string;
  model?: string;
  messages?: number;
  updated?: string;
  created?: string;
  running?: boolean;
  match?: { kind: string; role?: string; snippet?: string };
}

export interface MsgContent {
  type: "text" | "image_url";
  text?: string;
  image_url?: { url: string };
}

export interface Msg {
  role: "user" | "assistant" | "system";
  content: string | MsgContent[];
  id?: string;
}

export interface UsageResponse {
  totals: {
    total_tokens?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    calls?: number;
    est_cost?: number;
  };
  top_sessions: { session_id: string; total_tokens?: number; est_cost?: number }[];
  by_model: { model?: string; total_tokens?: number; est_cost?: number; calls?: number }[];
  days?: number;
}

export interface GitCheckpoint {
  hash?: string;
  time?: string;
  message?: string;
}

export interface GitStatus {
  repo?: boolean;
  branch?: string;
  changes?: number;
  reason?: string;
  checkpoints: GitCheckpoint[];
}

export interface MemoryItem {
  id: string;
  key: string;
  value: string;
  created_at?: string;
}

export interface PluginItem {
  name: string;
  tools?: string[];
  error?: string;
}

export type ThemeMode = "light" | "dark";
