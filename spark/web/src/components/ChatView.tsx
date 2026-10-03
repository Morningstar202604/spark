import { useEffect, useRef, useState, useCallback } from "react";
import { useApp } from "../state";
import {
  getSession,
  cancelSession,
  forkSession,
  renameSession,
  deleteSession,
  createSession,
  streamChat,
  getSessionContext,
} from "../lib/api";
import { mdToHtml } from "../lib/markdown";
import type { Msg } from "../types";

interface ChatMsg {
  role: "user" | "assistant" | "error";
  content: string;
  raw?: string;
  id?: string;
  isStreaming?: boolean;
  meta?: { images?: number };
}

export function ChatView() {
  const {
    sid,
    setSid,
    setRunning,
    runningSids,
    cfg,
    toast,
    loadConfig,
    refreshSessions,
    setApproval,
    approval,
  } = useApp();

  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [ctxUsed, setCtxUsed] = useState(0);
  const [ctxMax, setCtxMax] = useState(32000);
  const [view, setView] = useState<"welcome" | "session">("welcome");

  const isRunning = sid ? !!runningSids[sid] : false;

  const messagesRef = useRef<ChatMsg[]>([]);
  messagesRef.current = messages;

  // Select session: load messages
  useEffect(() => {
    if (!sid) {
      setMessages([]);
      setView("welcome");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = await getSession(sid);
        if (cancelled) return;
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
              role: "user",
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
        setView(msgs.length > 0 ? "session" : "welcome");
        // load context
        try {
          const ctx = await getSessionContext(sid);
          if (!cancelled) {
            setCtxUsed(ctx.used);
            setCtxMax(ctx.max);
          }
        } catch { /* ignore */ }
      } catch {
        toast("加载会话失败");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sid, refreshSessions, toast]);

  // Send message
  async function doSend(
    prompt: string,
    images: { data: string; mime: string }[]
  ) {
    if (!sid) {
      toast("请先新建或选择一个会话");
      return;
    }
    if (runningSids[sid]) return;

    const finalPrompt = prompt || "（图片）请分析这张图片。";
    setRunning(sid, true);

    // Add user message locally
    const userMsg: ChatMsg = {
      role: "user",
      content: finalPrompt,
      raw: finalPrompt,
      meta: images.length ? { images: images.length } : undefined,
    };
    setMessages((prev) => [...prev, userMsg]);
    setView("session");

    // Start assistant streaming
    let assistantRaw = "";
    const asstMsgId = `stream-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        role: "assistant",
        content: "",
        raw: "",
        id: asstMsgId,
        isStreaming: true,
      },
    ]);

    try {
      await streamChat(sid, finalPrompt, images, {
        onEvent: (ev: any) => {
          if (ev.type === "text") {
            assistantRaw += ev.delta || "";
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.isStreaming) {
                last.raw = assistantRaw;
                last.content = mdToHtml(assistantRaw);
              }
              return next;
            });
          } else if (ev.type === "reasoning") {
            assistantRaw += "\n[thinking] " + (ev.delta || "") + "\n";
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.isStreaming) {
                last.raw = assistantRaw;
                last.content = mdToHtml(assistantRaw);
              }
              return next;
            });
          } else if (ev.type === "error") {
            setMessages((prev) => [
              ...prev,
              { role: "error", content: ev.message || "出错了", raw: ev.message || "" },
            ]);
          } else if (ev.type === "usage") {
            if (ev.estimated) setCtxUsed(ev.estimated);
          } else if (ev.type === "done") {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.isStreaming) {
                last.isStreaming = false;
                last.id = ev.assistant_msg_id || last.id;
              }
              if (ev.reason === "max_turns") {
                next.push({ role: "error", content: "已达到最大轮次，请分步提问。", raw: "" });
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
        onError: (err) => {
          setMessages((prev) => [
            ...prev,
            { role: "error", content: `连接中断：${err.message}`, raw: "" },
          ]);
        },
        onDone: () => {
          setRunning(sid, false);
          refreshSessions();
          getSessionContext(sid)
            .then((ctx) => {
              setCtxUsed(ctx.used);
              setCtxMax(ctx.max);
            })
            .catch(() => {});
        },
      });
    } catch (e: any) {
      setRunning(sid, false);
      setMessages((prev) => [
        ...prev,
        { role: "error", content: e.message || "发送失败", raw: "" },
      ]);
    }
  }

  // Approval modal
  async function doApprove(action: "allow" | "deny" | "always") {
    if (!approval) return;
    setApproval(null);
    try {
      await fetch("/api/approval", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(useAppToken() ? { "X-Spark-Token": useAppToken() } : {}),
        },
        body: JSON.stringify({
          request_id: approval.request_id,
          action,
          tool: approval.tool,
        }),
      });
    } catch {
      toast("审批提交失败");
    }
  }

  function useAppToken(): string {
    return localStorage.getItem("spark_token") || "";
  }

  const ctxPct = Math.min(100, Math.round((ctxUsed / (ctxMax || 32000)) * 100));
  const hasMessages = view === "session" || messages.length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {/* Context meter (only in session view) */}
      {hasMessages && (
        <div className="relative flex-none" style={{ height: 14 }}>
          <div
            className="absolute bottom-0 left-3 right-3"
            style={{
              height: 2,
              borderRadius: 999,
              background: "color-mix(in srgb, var(--border) 55%, transparent)",
            }}
          />
          <div
            className="absolute bottom-0 left-3"
            style={{
              height: 2,
              borderRadius: 999,
              width: `calc(${ctxPct}% * 0.94)`,
              background:
                ctxPct > 80
                  ? "var(--amber, #b26a00)"
                  : "color-mix(in srgb, var(--accent) 24%, transparent)",
            }}
          />
          {ctxUsed > 0 && (
            <span
              className="absolute bottom-0.5 right-3 z-10 text-[10px]"
              style={{ color: "var(--ink-muted)" }}
            >
              {ctxPct}% · {ctxUsed} / {ctxMax || 32000}
            </span>
          )}
        </div>
      )}

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-4 scrollbar-thin">
        <div className="mx-auto flex max-w-[860px] flex-col gap-4">
          {messages.map((msg, i) => (
            <MessageItem
              key={msg.id || i}
              msg={msg}
              isLast={i === messages.length - 1}
              isRunning={isRunning}
            />
          ))}
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 text-sm" style={{ color: "var(--ink-muted)" }}>
              开始对话吧
            </div>
          )}
        </div>
      </div>

      {/* Approval modal */}
      {approval && <ApprovalModal onAction={doApprove} diff={approval.diff} summary={approval.summary} reason={approval.reason} tool={approval.tool} />}
    </div>
  );
}

function MessageItem({
  msg,
  isLast,
  isRunning,
}: {
  msg: ChatMsg;
  isLast: boolean;
  isRunning: boolean;
}) {
  if (msg.role === "error") {
    return (
      <div
        className="rounded-xl border p-3 text-sm"
        style={{
          background: "color-mix(in srgb, var(--red) 8%, transparent)",
          borderColor: "color-mix(in srgb, var(--red) 35%, transparent)",
          color: "var(--red, #d64545)",
        }}
      >
        {msg.content}
      </div>
    );
  }

  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div
          className="max-w-[86%] rounded-2xl rounded-br-sm border p-3 text-sm whitespace-pre-wrap break-words"
          style={{
            background: "color-mix(in srgb, var(--accent) 9%, var(--card))",
            borderColor: "color-mix(in srgb, var(--accent) 30%, transparent)",
            color: "var(--ink)",
          }}
        >
          {msg.content}
          {msg.meta?.images ? (
            <span className="mt-1 block text-xs" style={{ color: "var(--ink-muted)" }}>
              （附 {msg.meta.images} 张图片）
            </span>
          ) : null}
        </div>
      </div>
    );
  }

  // assistant
  return (
    <div
      className="w-full rounded-2xl rounded-bl-sm border p-4"
      style={{
        background: "var(--card)",
        borderColor: "var(--border)",
        color: "var(--ink)",
      }}
    >
      <div
        className="text-sm leading-relaxed"
        dangerouslySetInnerHTML={{ __html: msg.content || msg.raw || "" }}
      />
      {isLast && isRunning && (
        <span className="mt-2 inline-block h-3 w-3 animate-pulse rounded-full" style={{ background: "var(--accent)" }} />
      )}
    </div>
  );
}

function ApprovalModal({
  onAction,
  diff,
  summary,
  reason,
  tool,
}: {
  onAction: (action: "allow" | "deny" | "always") => void;
  diff?: string;
  summary?: string;
  reason?: string;
  tool?: string;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "color-mix(in srgb, #101828 55%, transparent)" }}>
      <div
        className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-2xl border p-5 shadow-2xl scrollbar-thin"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}
      >
        <div className="mb-3 text-sm font-semibold">工具审批</div>
        {tool && (
          <div className="mb-2 text-xs" style={{ color: "var(--ink-muted)" }}>
            工具：{tool}
          </div>
        )}
        {summary && <div className="mb-2 text-xs">{summary}</div>}
        {reason && (
          <div className="mb-3 text-xs" style={{ color: "var(--ink-muted)" }}>
            原因：{reason}
          </div>
        )}
        {diff && (
          <pre
            className="mb-4 max-h-60 overflow-auto rounded-lg border p-3 text-xs"
            style={{ background: "var(--surface-2)", borderColor: "var(--border)", whiteSpace: "pre-wrap" }}
          >
            {diff}
          </pre>
        )}
        <div className="flex gap-2">
          <button
            className="flex-1 rounded-lg px-3 py-2 text-sm font-medium"
            style={{ background: "var(--green, #0e9d6e)", color: "#fff" }}
            onClick={() => onAction("allow")}
          >
            允许 <span className="kbd ml-1 rounded border px-1 text-[10px]">A</span>
          </button>
          <button
            className="flex-1 rounded-lg px-3 py-2 text-sm font-medium"
            style={{ background: "var(--red, #d64545)", color: "#fff" }}
            onClick={() => onAction("deny")}
          >
            拒绝 <span className="kbd ml-1 rounded border px-1 text-[10px]">D</span>
          </button>
          <button
            className="flex-1 rounded-lg border px-3 py-2 text-sm font-medium"
            style={{ borderColor: "var(--border)", color: "var(--ink)" }}
            onClick={() => onAction("always")}
          >
            始终允许 <span className="kbd ml-1 rounded border px-1 text-[10px]">S</span>
          </button>
        </div>
      </div>
    </div>
  );
}
