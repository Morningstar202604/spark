import { create } from "zustand";
import type {
  ChatMsg,
  Cfg,
  Preset,
  McpServer,
  SessionMeta,
  ThemeMode,
  UsageResponse,
} from "./types";
import {
  answerApproval,
  clearSession,
  createSession,
  getConfig,
  getSession,
  getSessionContext,
  listSessions,
  setToken as apiSetToken,
  streamChat,
} from "./lib/api";
import { mdToHtml } from "./lib/markdown";

// ---------------------------------------------------------------------------
// Module-level mutable instances (not reactive state)
// ---------------------------------------------------------------------------
let streamCtrl: AbortController | null = null;
let sendTarget: {
  sid: string;
  prompt: string;
  images: { data: string; mime: string }[];
} | null = null;
let toastSeq = 0;

// ---------------------------------------------------------------------------
// Exported types
// ---------------------------------------------------------------------------
export type { ChatMsg };

export interface ToastMsg {
  id: string;
  text: string;
  type?: "info" | "error" | "success";
}

export interface AppState {
  token: string;
  setToken: (t: string) => void;

  cfg: Cfg | null;
  setCfg: (c: Cfg | null) => void;
  presets: Record<string, Preset>;
  approvalModes: { value: string; label: string }[];
  mcpServers: McpServer[];
  setMcpServers: (s: McpServer[] | ((prev: McpServer[]) => McpServer[])) => void;
  usage: UsageResponse | null;
  setUsage: (u: UsageResponse | null | ((prev: UsageResponse | null) => UsageResponse | null)) => void;
  dirs: Record<string, string>;

  sessions: SessionMeta[];
  setSessions: (s: SessionMeta[] | ((prev: SessionMeta[]) => SessionMeta[])) => void;
  sid: string | null;
  setSid: (s: string | null) => void;
  runningSids: Record<string, boolean>;
  setRunning: (sid: string, on: boolean) => void;

  approval: {
    request_id: string;
    tool?: string;
    summary?: string;
    reason?: string;
    diff?: string;
  } | null;
  setApproval: (a: AppState["approval"]) => void;

  theme: ThemeMode;
  setTheme: (t: ThemeMode) => void;

  toasts: ToastMsg[];
  toast: (text: string, type?: ToastMsg["type"]) => void;
  dismissToast: (id: string) => void;

  pendingImages: { data: string; mime: string; name?: string }[];
  setPendingImages: (
    s:
      | { data: string; mime: string; name?: string }[]
      | ((
          prev: { data: string; mime: string; name?: string }[]
        ) => { data: string; mime: string; name?: string }[])
  ) => void;

  messages: ChatMsg[];
  setMessages: (
    fn: ChatMsg[] | ((prev: ChatMsg[]) => ChatMsg[])
  ) => void;
  ctxUsed: number;
  setCtxUsed: (n: number) => void;
  ctxMax: number;
  setCtxMax: (n: number) => void;
  pendingPrompt: string;
  setPending: (v: string) => void;
  requestSend: (prompt: string) => void;
  _startStream: (
    targetSid: string,
    prompt: string,
    images: { data: string; mime: string }[]
  ) => void;

  loadConfig: () => Promise<boolean>;
  refreshSessions: (q?: string) => Promise<void>;
  newSession: () => Promise<void>;
  openSession: (targetSid: string) => Promise<void>;
  sendCurrentSession: (prompt: string) => void;
  cancelCurrentSession: () => void;
  clearCurrentSession: () => Promise<void>;
  respondApproval: (
    action: "allow" | "deny" | "always"
  ) => Promise<void>;
}

// ---------------------------------------------------------------------------
// Store creation
// ---------------------------------------------------------------------------
export const useApp = create<AppState>((set, get) => ({
  // --- token ---
  token:
    new URLSearchParams(location.search).get("token") ||
    localStorage.getItem("spark_token") ||
    "",
  setToken: (t: string) => {
    apiSetToken(t);
    set({ token: t });
  },

  // --- config ---
  cfg: null,
  setCfg: (c) => set({ cfg: c }),
  presets: {},
  approvalModes: [],
  mcpServers: [],
  setMcpServers: (s) =>
    set((prev) => ({
      mcpServers: typeof s === "function" ? s(prev.mcpServers) : s,
    })),
  usage: null,
  setUsage: (u) =>
    set((prev) => ({
      usage: typeof u === "function" ? u(prev.usage) : u,
    })),
  dirs: {},

  // --- sessions ---
  sessions: [],
  setSessions: (s) =>
    set((prev) => ({
      sessions: typeof s === "function" ? s(prev.sessions) : s,
    })),
  sid: null,
  setSid: (s) => set({ sid: s }),
  runningSids: {},
  setRunning: (sid, on) =>
    set((prev) => {
      const next = { ...prev.runningSids };
      if (on) next[sid] = true;
      else delete next[sid];
      return { runningSids: next };
    }),

  // --- approval ---
  approval: null,
  setApproval: (a) => set({ approval: a }),

  // --- theme ---
  theme: (() => {
    const saved = localStorage.getItem("spark_theme");
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  })(),
  setTheme: (t: ThemeMode) => {
    localStorage.setItem("spark_theme", t);
    document.documentElement.dataset.theme = t;
    set({ theme: t });
  },

  // --- toasts ---
  toasts: [],
  toast: (text, type) => {
    const id = `t${++toastSeq}`;
    set((prev) => ({ toasts: [...prev.toasts, { id, text, type }] }));
    setTimeout(
      () => set((prev) => ({ toasts: prev.toasts.filter((x) => x.id !== id) })),
      2600
    );
  },
  dismissToast: (id) =>
    set((prev) => ({ toasts: prev.toasts.filter((x) => x.id !== id) })),

  // --- composer ---
  pendingImages: [],
  setPendingImages: (s) =>
    set((prev) => ({
      pendingImages:
        typeof s === "function" ? s(prev.pendingImages) : s,
    })),

  // --- messages / context usage ---
  messages: [],
  setMessages: (fn) =>
    set((prev) => ({
      messages: typeof fn === "function" ? fn(prev.messages) : fn,
    })),
  ctxUsed: 0,
  setCtxUsed: (n) => set({ ctxUsed: n }),
  ctxMax: 32000,
  setCtxMax: (n) => set({ ctxMax: n }),
  pendingPrompt: "",

  // --- derived / async actions ---
  setPending: (v) => set({ pendingPrompt: v }),

  loadConfig: async () => {
    try {
      const data = await getConfig();
      set({
        cfg: data.current,
        presets: data.presets,
        approvalModes: data.approval_modes,
        mcpServers: data.mcp?.servers || [],
        dirs: data.dirs || {},
      });
      return true;
    } catch {
      return false;
    }
  },

  refreshSessions: async (q) => {
    try {
      const rows = await listSessions(q);
      set({ sessions: rows });
    } catch {
      /* ignore */
    }
  },

  openSession: async (targetSid) => {
    const { toast } = get();
    set({ sid: targetSid, messages: [], ctxUsed: 0, ctxMax: 32000 });
    try {
      const data = await getSession(targetSid);
      const msgs: ChatMsg[] = data.messages.map((m) => {
        if (m.role === "user") {
          const c = m.content;
          const text = Array.isArray(c)
            ? (c as unknown[])
                .filter((p) => (p as Record<string, string>).type === "text")
                .map((p) => (p as Record<string, string>).text)
                .join("\n")
            : (c as string);
          const imgCount = Array.isArray(c)
            ? (c as unknown[]).filter(
                (p) => (p as Record<string, string>).type === "image_url"
              ).length
            : 0;
          return {
            role: "user" as const,
            content: text,
            id: m.id,
            raw: text,
            meta: imgCount ? { images: imgCount } : undefined,
          };
        }
        return {
          role: "assistant" as const,
          content: mdToHtml((m.content as string) || ""),
          id: m.id,
          raw: (m.content as string) || "",
        };
      });
      set({ messages: msgs });
      try {
        const ctx = await getSessionContext(targetSid);
        set({ ctxUsed: ctx.used, ctxMax: ctx.max });
      } catch {
        /* ignore */
      }
    } catch {
      toast("加载会话失败");
    }
  },

  sendCurrentSession: (prompt) => {
    const { sid, runningSids, pendingImages, setRunning, toast } = get();
    const targetSid = sid;
    if (!targetSid) {
      toast("请先新建或选择一个会话");
      return;
    }
    if (runningSids[targetSid]) return;

    const finalPrompt = prompt || "（图片）请分析这张图片。";
    setRunning(targetSid, true);
    set({ pendingPrompt: "" });

    set((prev) => ({
      messages: [
        ...prev.messages,
        {
          role: "user",
          content: finalPrompt,
          raw: finalPrompt,
        },
        {
          role: "assistant",
          content: "",
          raw: "",
          id: `stream-${Date.now()}`,
          isStreaming: true,
        },
      ],
    }));

    const images = pendingImages.length
      ? pendingImages.map((img) => ({ data: img.data, mime: img.mime }))
      : [];
    set({ pendingImages: [] });
    sendTarget = { sid: targetSid, prompt: finalPrompt, images };
    get()._startStream(targetSid, finalPrompt, images);
  },

  cancelCurrentSession: () => {
    streamCtrl?.abort();
    const { sid, setRunning } = get();
    if (sid) setRunning(sid, false);
  },

  clearCurrentSession: async () => {
    const { sid, toast, refreshSessions } = get();
    if (!sid) {
      toast("请先选择或新建一个会话");
      return;
    }
    try {
      await clearSession(sid);
      set({ messages: [], ctxUsed: 0, ctxMax: 32000, pendingPrompt: "" });
      toast("已清空当前会话");
      await refreshSessions();
    } catch {
      toast("清空会话失败");
    }
  },

  respondApproval: async (action) => {
    const { approval, toast } = get();
    if (!approval) return;
    set({ approval: null });
    try {
      await answerApproval({
        request_id: approval.request_id,
        action,
        tool: approval.tool,
      });
    } catch {
      toast("审批提交失败");
    }
  },

  newSession: async () => {
    const { cfg, refreshSessions, openSession, sendCurrentSession, toast, pendingPrompt } = get();
    const currentCfg = cfg;
    if (!currentCfg) {
      toast("配置尚未加载，稍后再试");
      return;
    }
    const missing: string[] = [];
    if (!(currentCfg.workdir || "").trim()) missing.push("workdir");
    const hasModel =
      (currentCfg.base_url && currentCfg.model) ||
      (currentCfg.api_key && currentCfg.model);
    if (!hasModel && !currentCfg.demo_mode) missing.push("model");
    if (missing.length) {
      toast(
        missing.includes("workdir")
          ? "请先指定工作目录"
          : "请先配置模型服务并填 Key，或开启演示模式"
      );
      return;
    }
    try {
      const meta = await createSession(currentCfg.workdir, currentCfg.model);
      await refreshSessions();
      await openSession(meta.id);
      if (pendingPrompt) {
        sendCurrentSession(pendingPrompt);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "创建会话失败";
      toast(msg);
    }
  },

  requestSend: (prompt) => {
    const { sid, sendCurrentSession, newSession, setPending } = get();
    if (sid) {
      setPending(prompt);
      sendCurrentSession(prompt);
    } else {
      setPending(prompt);
      newSession();
    }
  },

  // --- internal helpers (not part of public AppState) ---
  _startStream: (
    targetSid: string,
    prompt: string,
    images: { data: string; mime: string }[]
  ) => {
    const { refreshSessions, setRunning, setMessages, setCtxUsed, setCtxMax, toast, setApproval } =
      get();
    streamCtrl?.abort();
    const ctrl = streamChat(targetSid, prompt, images, {
      onEvent: (ev: any) => {
        if (ev.type === "text" || ev.type === "reasoning") {
          const delta =
            ev.type === "reasoning"
              ? `\n[thinking] ${ev.delta || ""}\n`
              : ev.delta || "";
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.isStreaming) {
              next[next.length - 1] = {
                ...last,
                raw: (last.raw || "") + delta,
                content: mdToHtml((last.raw || "") + delta),
              };
            }
            return next;
          });
        } else if (ev.type === "error") {
          setMessages((prev) => [
            ...prev,
            {
              role: "error",
              content: ev.message || "出错了",
              raw: ev.message || "",
            },
          ]);
        } else if (ev.type === "usage" && ev.estimated) {
          setCtxUsed(ev.estimated);
        } else if (ev.type === "done") {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.isStreaming) {
              next[next.length - 1] = {
                ...last,
                isStreaming: false,
                id: ev.assistant_msg_id || last.id,
              };
            }
            if (ev.reason === "max_turns") {
              next.push({
                role: "error",
                content: "已达到最大轮次，请分步提问。",
                raw: "",
              });
            }
            return next;
          });
        } else if (ev.type === "approval") {
          setApproval({
            request_id: ev.request_id,
            tool: ev.tool,
            summary: ev.summary,
            reason: ev.reason,
            diff: ev.diff,
          });
        }
      },
      onError: (err: Error) => {
        setMessages((prev) => [
          ...prev,
          {
            role: "error",
            content: `连接中断：${err.message}`,
            raw: "",
          },
        ]);
      },
      onDone: async () => {
        setRunning(targetSid, false);
        await refreshSessions();
        try {
          const ctx = await getSessionContext(targetSid);
          setCtxUsed(ctx.used);
          setCtxMax(ctx.max);
        } catch {
          /* ignore */
        }
      },
    });
    streamCtrl = ctrl;
  },
}));

// ---------------------------------------------------------------------------
// Bootstrap effect (runs once on module load)
// ---------------------------------------------------------------------------
if (typeof window !== "undefined") {
  const store = useApp.getState();
  // Apply initial theme to document
  document.documentElement.dataset.theme = store.theme;

  // Initial data load
  store.loadConfig().then((ok) => {
    if (!ok) return;
    store.refreshSessions();
  });

  // Auth-required event listener
  const authHandler = () => {
    window.dispatchEvent(new CustomEvent("spark:show-token-modal"));
  };
  window.addEventListener("spark:auth-required", authHandler);
}
