import type {
  Cfg,
  Preset,
  McpServer,
  UsageResponse,
  SessionMeta,
  MemoryItem,
  PluginItem,
  GitStatus,
} from "./types";

const TOKEN_KEY = "spark2_token";

function getToken(): string {
  const urlToken = new URLSearchParams(location.search).get("token") || "";
  if (urlToken) localStorage.setItem(TOKEN_KEY, urlToken);
  return urlToken || localStorage.getItem(TOKEN_KEY) || "";
}

let currentToken = getToken();

export function setToken(t: string) {
  currentToken = t;
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getTokenValue(): string {
  return currentToken;
}

interface ConfigResponse {
  current: Cfg;
  version?: string;
  presets: Record<string, Preset>;
  approval_modes: { value: string; label: string }[];
  dirs?: Record<string, string>;
  mcp?: { servers: McpServer[] };
}

interface FsEntry {
  name: string;
  dir: boolean;
}

interface FsResponse {
  entries: FsEntry[];
}

interface SessionDetail {
  meta: SessionMeta;
  messages: { role: string; content: unknown; id?: string }[];
}

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string>),
  };
  if (currentToken) headers["X-Spark-Token"] = currentToken;

  const res = await fetch(path, { ...opts, headers });

  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent("spark:auth-required"));
    throw new Error("auth");
  }
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`;
    try {
      const d = await res.json();
      detail = d.detail || detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// Config

export function getConfig(): Promise<ConfigResponse> {
  return request<ConfigResponse>("/api/config");
}

export function saveConfig(
  body: Partial<Cfg> & { mcp_servers?: McpServer[]; token?: string }
): Promise<ConfigResponse> {
  return request<ConfigResponse>("/api/config", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function testConnection(body: {
  base_url?: string;
  proxy?: string;
  model?: string;
  api_key?: string;
}): Promise<{ ok: boolean; message?: string }> {
  return request("/api/test-connection", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getRecentDirs(): Promise<{ dirs: string[] }> {
  return request("/api/recent-dirs");
}

export function getUsage(): Promise<UsageResponse> {
  return request<UsageResponse>("/api/usage");
}

export interface SlashCommandInfo {
  name: string;
  label: string;
  description: string;
}

export function getSlashCommands(): Promise<{ commands: SlashCommandInfo[] }> {
  return request("/api/slash-commands");
}

export function getPlugins(): Promise<{ plugins: PluginItem[]; dir?: string }> {
  return request("/api/plugins");
}

// Sessions

export function listSessions(q?: string): Promise<SessionMeta[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return request<SessionMeta[]>(`/api/sessions${qs}`);
}

export function createSession(workdir?: string, model?: string): Promise<SessionMeta> {
  return request<SessionMeta>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ workdir, model }),
  });
}

export function getSession(sid: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${sid}`);
}

export function renameSession(sid: string, title: string): Promise<{ ok: boolean; title: string }> {
  return request(`/api/sessions/${sid}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function deleteSession(sid: string): Promise<{ ok: boolean }> {
  return request(`/api/sessions/${sid}`, { method: "DELETE" });
}

export function forkSession(sid: string): Promise<SessionMeta> {
  return request<SessionMeta>(`/api/sessions/${sid}/fork`, { method: "POST" });
}

export function truncateSession(
  sid: string,
  messageId: string
): Promise<{ ok: boolean; messages: unknown[] }> {
  return request(`/api/sessions/${sid}/truncate`, {
    method: "POST",
    body: JSON.stringify({ message_id: messageId }),
  });
}

export function clearSession(sid: string): Promise<{ ok: boolean; messages: unknown[] }> {
  return request(`/api/sessions/${sid}/truncate`, {
    method: "POST",
    body: JSON.stringify({ message_id: "" }),
  });
}

export function deleteMessage(
  sid: string,
  messageId: string
): Promise<{ ok: boolean; messages: unknown[] }> {
  return request(`/api/sessions/${sid}/messages/${messageId}`, { method: "DELETE" });
}

export function cancelSession(sid: string): Promise<{ ok: boolean }> {
  return request("/api/cancel", {
    method: "POST",
    body: JSON.stringify({ session_id: sid }),
  });
}

export function getSessionContext(
  sid: string
): Promise<{ used: number; max: number; compact: boolean }> {
  return request(`/api/sessions/${sid}/context`);
}

// Filesystem

export function browseFs(sid: string, path = ""): Promise<FsResponse> {
  return request(`/api/fs?sid=${encodeURIComponent(sid)}&path=${encodeURIComponent(path)}`);
}

// Memory

export function listMemory(workdir: string): Promise<{ items: MemoryItem[] }> {
  return request(`/api/memory?workdir=${encodeURIComponent(workdir)}`);
}

export function addMemory(
  workdir: string,
  key: string,
  value: string
): Promise<MemoryItem> {
  return request("/api/memory", {
    method: "POST",
    body: JSON.stringify({ workdir, key, value }),
  });
}

export function deleteMemory(id: string): Promise<{ ok: boolean }> {
  return request(`/api/memory/${id}`, { method: "DELETE" });
}

// Git

export function getGitStatus(sid?: string): Promise<GitStatus> {
  return request(`/api/git?sid=${encodeURIComponent(sid || "")}`);
}

export function gitCheckpoint(
  sid: string,
  message?: string
): Promise<{ ok: boolean; detail?: string }> {
  return request("/api/git/checkpoint", {
    method: "POST",
    body: JSON.stringify({ sid, message }),
  });
}

export function gitReset(
  sid?: string
): Promise<{ ok: boolean; detail?: string }> {
  return request("/api/git/reset", {
    method: "POST",
    body: JSON.stringify({ sid: sid || "", confirm: "yes" }),
  });
}

// Approval

export function answerApproval(body: {
  request_id: string;
  action: "allow" | "deny" | "always";
  tool?: string;
  files?: string[] | null;
}): Promise<{ ok: boolean }> {
  return request("/api/approval", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// SSE Stream

export interface StreamCallbacks {
  onEvent: (ev: any) => void;
  onError?: (err: Error) => void;
  onDone?: () => void;
}

export function streamChat(
  sid: string,
  prompt: string,
  images: { data: string; mime: string }[],
  callbacks: StreamCallbacks
): AbortController {
  const ctrl = new AbortController();

  const doStream = async () => {
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (currentToken) headers["X-Spark-Token"] = currentToken;

      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers,
        body: JSON.stringify({ session_id: sid, prompt, images }),
        signal: ctrl.signal,
      });

      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent("spark:auth-required"));
        callbacks.onDone?.();
        return;
      }

      if (!res.ok) {
        let msg = `请求失败（${res.status}）`;
        try {
          const d = await res.json();
          msg = d.detail || msg;
        } catch { /* ignore */ }
        callbacks.onError?.(new Error(msg));
        callbacks.onDone?.();
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) return;

      const dec = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });

        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = chunk.trim();
          if (!line.startsWith("data:")) continue;
          try {
            const ev = JSON.parse(line.slice(5).trim());
            callbacks.onEvent(ev);
          } catch { /* skip malformed frame */ }
        }
      }

      callbacks.onDone?.();
    } catch (e: unknown) {
      if ((e as Error).name !== "AbortError") {
        callbacks.onError?.(e as Error);
      }
      callbacks.onDone?.();
    }
  };

  doStream();
  return ctrl;
}

// PTY WebSocket

export function createPtySocket(
  sid: string,
  tabId: string,
  token: string
): WebSocket {
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  const url = `${proto}${location.host}/ws/pty?token=${encodeURIComponent(token)}&sid=${encodeURIComponent(sid)}&tab=${encodeURIComponent(tabId)}`;
  return new WebSocket(url);
}
