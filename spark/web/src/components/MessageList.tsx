import { useEffect, useRef, useState } from "react";
import { useApp, type ChatMsg } from "../state";
import { mdToHtml } from "../lib/markdown";

function MessageItem({
  msg,
  isLast,
  isRunning,
  copied,
  onCopy,
}: {
  msg: ChatMsg;
  isLast: boolean;
  isRunning: boolean;
  copied: boolean;
  onCopy: (text: string) => void;
}) {
  if (msg.role === "error") {
    return (
      <div
        className="rounded-xl border p-3 text-sm"
        style={{
          background: "color-mix(in srgb, var(--red, #d64545) 8%, transparent)",
          borderColor: "color-mix(in srgb, var(--red, #d64545) 35%, transparent)",
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

  return (
    <div
      className="group/msg w-full rounded-2xl rounded-bl-sm border p-4"
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
        <span
          className="mt-2 inline-block h-3 w-3 animate-pulse rounded-full"
          style={{ background: "var(--accent)" }}
        />
      )}
      <div
        className="mt-2 flex items-center justify-end gap-1 sm:opacity-0 sm:transition-opacity sm:group-hover/msg:opacity-100"
        style={{ color: "var(--ink-muted)" }}
      >
        <button
          type="button"
          className="rounded px-2 py-1 text-[11px] sm:py-0.5"
          style={{ border: "1px solid var(--border)" }}
          onClick={() => onCopy(msg.raw || msg.content || "")}
        >
          {copied ? "已复制" : "复制"}
        </button>
      </div>
    </div>
  );
}

export function MessageList() {
  const { sid, runningSids, messages, ctxUsed, ctxMax, toast } = useApp();
  const listRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const isRunning = sid ? !!runningSids[sid] : false;
  const ctxPct = Math.min(100, Math.round((ctxUsed / (ctxMax || 32000)) * 100));

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages]);

  const handleCopy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast("已复制");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast("复制失败");
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="relative flex-none px-3" style={{ height: 14 }}>
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

      <div
        ref={listRef}
        className="flex-1 overflow-y-auto px-4 py-4 scrollbar-thin"
      >
        <div className="mx-auto flex max-w-[860px] flex-col gap-4">
          {messages.map((msg, i) => (
            <MessageItem
              key={msg.id || i}
              msg={msg}
              isLast={i === messages.length - 1}
              isRunning={isRunning}
              copied={copied}
              onCopy={handleCopy}
            />
          ))}
          {messages.length === 0 && (
            <div
              className="flex flex-col items-center justify-center py-16 text-sm"
              style={{ color: "var(--ink-muted)" }}
            >
              开始对话吧
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
