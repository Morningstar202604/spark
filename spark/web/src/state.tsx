import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type Dispatch,
  type SetStateAction,
} from "react";
import type {
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
  truncateSession,
} from "./lib/api";
import { mdToHtml } from "./lib/markdown";

export interface ChatMsg {
  role: "user" | "assistant" | "error";
  content: string;
  raw?: string;
  id?: string;
  isStreaming?: boolean;
  meta?: { images?: number };
}

export interface ToastMsg {
  id: string;
  text: string;
  type?: "info" | "error" | "success";
}

export interface AppState {
  token: string;
  setToken: (t: string) => void;

  cfg: Cfg | null;
  setCfg: Dispatch<SetStateAction<Cfg | null>>;
  presets: Record<string, Preset>;
  approvalModes: { value: string; label: string }[];
  mcpServers: McpServer[];
  setMcpServers: Dispatch<SetStateAction<McpServer[]>>;
  usage: UsageResponse | null;
  setUsage: Dispatch<SetStateAction<UsageResponse | null>>;
  dirs: Record<string, string>;

  sessions: SessionMeta[];
  setSessions: Dispatch<SetStateAction<SessionMeta[]>>;
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
  setPendingImages: Dispatch<
    SetStateAction<{ data: string; mime: string; name?: string }[]>
  >;

  messages: ChatMsg[];
  ctxUsed: number;
  ctxMax: number;
  pendingPrompt: string;
  requestSend: (prompt: string) => void;

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

const Ctx = createContext<AppState>(null!);

export function useApp(): AppState {
  return useContext(Ctx);
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokState] = useState(
    () =>
      new URLSearchParams(location.search).get("token") ||
      localStorage.getItem("spark_token") ||
      ""
  );
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [presets, setPresets] = useState<Record<string, Preset>>({});
  const [approvalModes, setApprovalModes] = useState<AppState["approvalModes"]>(
    []
  );
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [dirs, setDirs] = useState<Record<string, string>>({});

  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [sid, setSidState] = useState<string | null>(null);
  const [runningSids, setRunningSids] = useState<Record<string, boolean>>({});
  const [approval, setApproval] = useState<AppState["approval"]>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [ctxUsed, setCtxUsed] = useState(0);
  const [ctxMax, setCtxMax] = useState(32000);
  const [pendingPrompt, setPendingPrompt] = useState("");

  const [theme, setThemeState] = useState<ThemeMode>(() => {
    const saved = localStorage.getItem("spark_theme");
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  });

  const [toasts, setToasts] = useState<ToastMsg[]>([]);
  const toastIdRef = useRef(0);

  const [pendingImages, setPendingImages] = useState<AppState["pendingImages"]>(
    []
  );
  const streamCtrlRef = useRef<AbortController | null>(null);
  const sendTargetRef = useRef<{
    sid: string;
    prompt: string;
    images: { data: string; mime: string }[];
  } | null>(null);
  const sidRef = useRef<string | null>(null);
  const runningSidsRef = useRef<Record<string, boolean>>({});
  const cfgRef = useRef<Cfg | null>(null);
  const pendingPromptRef = useRef("");

  sidRef.current = sid;
  runningSidsRef.current = runningSids;
  cfgRef.current = cfg;
  pendingPromptRef.current = pendingPrompt;

  const setToken = useCallback((t: string) => {
    apiSetToken(t);
    setTokState(t);
  }, []);

  const setSid = useCallback((s: string | null) => setSidState(s), []);

  const setTheme = useCallback((t: ThemeMode) => {
    setThemeState(t);
    localStorage.setItem("spark_theme", t);
    document.documentElement.dataset.theme = t;
  }, []);

  const toast = useCallback((text: string, type?: ToastMsg["type"]) => {
    const id = `t${++toastIdRef.current}`;
    setToasts((prev) => [...prev, { id, text, type }]);
    setTimeout(
      () => setToasts((prev) => prev.filter((x) => x.id !== id)),
      2600
    );
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((x) => x.id !== id));
  }, []);

  const setRunning = useCallback((s: string, on: boolean) => {
    setRunningSids((prev) => {
      const next = { ...prev };
      if (on) next[s] = true;
      else delete next[s];
      return next;
    });
  }, []);

  const loadConfig = useCallback(async () => {
    try {
      const data = await getConfig();
      setCfg(data.current);
      setPresets(data.presets);
      setApprovalModes(data.approval_modes);
      setMcpServers(data.mcp?.servers || []);
      setDirs(data.dirs || {});
      return true;
    } catch {
      return false;
    }
  }, []);

  const refreshSessions = useCallback(async (q?: string) => {
    try {
      const rows = await listSessions(q);
      setSessions(rows);
    } catch {
      /* ignore */
    }
  }, []);

  const startStream = useCallback(
    (
      targetSid: string,
      prompt: string,
      images: { data: string; mime: string }[]
    ) => {
      streamCtrlRef.current?.abort();
      const ctrl = streamChat(targetSid, prompt, images, {
        onEvent: (ev: any) => {
          if (ev.type === "text" || ev.type === "reasoning") {
            const delta = ev.type === "reasoning" ? `\n[thinking] ${ev.delta || ""}\n` : ev.delta || "";
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.isStreaming) {
                last.raw = (last.raw || "") + delta;
                last.content = mdToHtml(last.raw || "");
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
                last.isStreaming = false;
                last.id = ev.assistant_msg_id || last.id;
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
      streamCtrlRef.current = ctrl;
    },
    [refreshSessions, setRunning]
  );

  const openSession = useCallback(
    async (targetSid: string) => {
      setSidState(targetSid);
      setMessages([]);
      setCtxUsed(0);
      setCtxMax(32000);
      try {
        const data = await getSession(targetSid);
        const msgs: ChatMsg[] = data.messages.map((m) => {
          if (m.role === "user") {
            const c = m.content;
            const text = Array.isArray(c)
              ? (c as any[])
                  .filter((p) => p.type === "text")
                  .map((p) => p.text)
                  .join("\n")
              : (c as string);
            const imgCount = Array.isArray(c)
              ? (c as any[]).filter((p) => p.type === "image_url").length
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
            content: (m.content as string) || "",
            id: m.id,
            raw: (m.content as string) || "",
          };
        });
        setMessages(msgs);
        try {
          const ctx = await getSessionContext(targetSid);
          setCtxUsed(ctx.used);
          setCtxMax(ctx.max);
        } catch {
          /* ignore */
        }
      } catch {
        toast("加载会话失败");
      }
    },
    [toast]
  );

  const sendCurrentSession = useCallback(
    (prompt: string) => {
      const targetSid = sidRef.current;
      if (!targetSid) {
        toast("请先新建或选择一个会话");
        return;
      }
      if (runningSidsRef.current[targetSid]) return;

      const finalPrompt = prompt || "（图片）请分析这张图片。";
      setRunning(targetSid, true);
      setPendingPrompt("");

      const userMsg: ChatMsg = {
        role: "user",
        content: finalPrompt,
        raw: finalPrompt,
      };
      setMessages((prev) => [...prev, userMsg, {
        role: "assistant",
        content: "",
        raw: "",
        id: `stream-${Date.now()}`,
        isStreaming: true,
      }]);

      const images = pendingImages.length
        ? pendingImages.map((img) => ({ data: img.data, mime: img.mime }))
        : [];
      setPendingImages([]);
      sendTargetRef.current = { sid: targetSid, prompt: finalPrompt, images };
      startStream(targetSid, finalPrompt, images);
    },
    [pendingImages, setPendingImages, setRunning, startStream, toast]
  );

  const newSession = useCallback(async () => {
    const currentCfg = cfgRef.current;
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
      if (pendingPromptRef.current) {
        sendCurrentSession(pendingPromptRef.current);
      }
    } catch (e: any) {
      toast(e.message || "创建会话失败");
    }
  }, [openSession, refreshSessions, sendCurrentSession, toast]);

  const requestSend = useCallback(
    (prompt: string) => {
      if (sidRef.current) {
        setPendingPrompt(prompt);
        sendCurrentSession(prompt);
      } else {
        setPendingPrompt(prompt);
        newSession();
      }
    },
    [newSession, sendCurrentSession]
  );

  const cancelCurrentSession = useCallback(() => {
    streamCtrlRef.current?.abort();
    const targetSid = sidRef.current;
    if (targetSid) setRunning(targetSid, false);
  }, [setRunning]);

  const clearCurrentSession = useCallback(async () => {
    const targetSid = sidRef.current;
    if (!targetSid) {
      toast("请先选择或新建一个会话");
      return;
    }
    try {
      await clearSession(targetSid);
      setMessages([]);
      setCtxUsed(0);
      setCtxMax(32000);
      setPendingPrompt("");
      toast("已清空当前会话");
      await refreshSessions();
    } catch {
      toast("清空会话失败");
    }
  }, [refreshSessions, toast]);

  const respondApproval = useCallback(
    async (action: "allow" | "deny" | "always") => {
      const current = approval;
      if (!current) return;
      setApproval(null);
      try {
        await answerApproval({
          request_id: current.request_id,
          action,
          tool: current.tool,
        });
      } catch {
        toast("审批提交失败");
      }
    },
    [approval, toast]
  );

  useEffect(() => {
    loadConfig().then((ok) => {
      if (!ok) return;
      refreshSessions();
    });
    document.documentElement.dataset.theme = theme;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handler = () => {
      window.dispatchEvent(new CustomEvent("spark:show-token-modal"));
    };
    window.addEventListener("spark:auth-required", handler);
    return () =>
      window.removeEventListener("spark:auth-required", handler);
  }, []);

  const value: AppState = {
    token,
    setToken,
    cfg,
    setCfg,
    presets,
    approvalModes,
    mcpServers,
    setMcpServers,
    usage,
    setUsage,
    dirs,
    sessions,
    setSessions,
    sid,
    setSid,
    runningSids,
    setRunning,
    approval,
    setApproval,
    theme,
    setTheme,
    toasts,
    toast,
    dismissToast,
    pendingImages,
    setPendingImages,
    messages,
    ctxUsed,
    ctxMax,
    pendingPrompt,
    requestSend,
    loadConfig,
    refreshSessions,
    newSession,
    openSession,
    sendCurrentSession,
    cancelCurrentSession,
    clearCurrentSession,
    respondApproval,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
