import { useMemo } from "react";
import { useApp } from "../state";
import {
  ThreadPrimitive,
  MessagePrimitive,
  ActionBarPrimitive,
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  useMessagePartText,
} from "@assistant-ui/react";
import type { ThreadMessageLike, ThreadAssistantMessagePart } from "@assistant-ui/react";
import type { ChatMsg } from "../types";
import { mdToHtml } from "../lib/markdown";

// ===========================================================================
// Convert Spark's ChatMsg into assistant-ui's ThreadMessageLike
// ===========================================================================
function convertMessage(
  msg: ChatMsg,
  idx: number
): ThreadMessageLike {
  // User message – plain text
  if (msg.role === "user") {
    return {
      role: "user",
      content: msg.content || "",
      id: msg.id || `msg-${idx}`,
    };
  }

  // Error message – map to assistant role with error status for styling
  if (msg.role === "error") {
    return {
      role: "assistant",
      content: [
        {
          type: "text",
          text: msg.content || "",
        } satisfies ThreadAssistantMessagePart,
      ],
      id: msg.id || `msg-${idx}`,
      status: { type: "incomplete", reason: "error" },
      metadata: { custom: { isError: true } },
    };
  }

  // Assistant message – pass raw markdown text; the custom Text renderer
  // (below) converts it to HTML via Spark's mdToHtml.
  const isRunning = !!msg.isStreaming;
  return {
    role: "assistant",
    content: [
      {
        type: "text",
        text: msg.raw || msg.content || "",
      } satisfies ThreadAssistantMessagePart,
    ],
    id: msg.id || `msg-${idx}`,
    status: isRunning
      ? { type: "running" }
      : { type: "complete", reason: "stop" },
  };
}

// ===========================================================================
// Custom Text part renderer — handles markdown, reasoning blocks,
// code blocks with copy, tables, etc. via Spark's mdToHtml.
// Replaces assistant-ui's default plain-text <p> renderer so that
// assistant messages keep Spark's rich markdown rendering.
// ===========================================================================
function MarkdownTextPart() {
  const part = useMessagePartText();
  const text = "text" in part ? part.text : "";
  return (
    <div
      className="text-sm leading-relaxed"
      dangerouslySetInnerHTML={{ __html: mdToHtml(text) }}
    />
  );
}

// ===========================================================================
// Assistant message bubble (right-sided card with markdown rendering)
// ===========================================================================
function AssistantMessage() {
  return (
    <MessagePrimitive.Root
      className="group/msg w-full rounded-2xl rounded-bl-sm border p-4"
      style={{
        background: "var(--card)",
        borderColor: "var(--border)",
        color: "var(--ink)",
      }}
    >
      <MessagePrimitive.Parts
        components={{
          Text: MarkdownTextPart,
        }}
      />
      <ActionBarPrimitive.Root
        autohide="never"
        hideWhenRunning={false}
        className="mt-2 flex items-center justify-end gap-1 sm:opacity-0 sm:transition-opacity sm:group-hover/msg:opacity-100"
        style={{ color: "var(--ink-muted)" }}
      >
        <ActionBarPrimitive.Copy
          copiedDuration={1500}
          className="rounded px-2 py-1 text-[11px]"
          style={{
            border: "1px solid var(--border)",
          }}
        >
          复制
        </ActionBarPrimitive.Copy>
      </ActionBarPrimitive.Root>
    </MessagePrimitive.Root>
  );
}

// ===========================================================================
// User message bubble (right-aligned, accent-tinted)
// Uses plain-text (not markdown) with pre-wrap to preserve user formatting
// ===========================================================================
function UserTextPart() {
  const part = useMessagePartText();
  const text = "text" in part ? part.text : "";
  return <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{text}</span>;
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-end">
      <div
        className="max-w-[86%] rounded-2xl rounded-br-sm border p-3 text-sm text-[var(--ink)]"
        style={{
          background: "color-mix(in srgb, var(--accent) 9%, var(--card))",
          borderColor: "color-mix(in srgb, var(--accent) 30%, transparent)",
        }}
      >
        <MessagePrimitive.Parts
          components={{
            Text: UserTextPart,
          }}
        />
      </div>
    </MessagePrimitive.Root>
  );
}

// ===========================================================================
// Context window usage bar (app-specific domain UI – kept as-is)
// ===========================================================================
function ContextBar({
  pct,
  used,
  max,
}: {
  pct: number;
  used: number;
  max: number;
}) {
  return (
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
          width: `calc(${pct}% * 0.94)`,
          background:
            pct > 80
              ? "var(--amber, #b26a00)"
              : "color-mix(in srgb, var(--accent) 24%, transparent)",
        }}
      />
      {used > 0 && (
        <span
          className="absolute bottom-0.5 right-3 z-10 text-[10px]"
          style={{ color: "var(--ink-muted)" }}
        >
          {pct}% · {used} / {max}
        </span>
      )}
    </div>
  );
}

// ===========================================================================
// Exported component – zero-prop interface preserved
// ===========================================================================
export function MessageList() {
  const { sid, runningSids, messages, ctxUsed, ctxMax } = useApp();
  const isRunning = sid ? !!runningSids[sid] : false;
  const ctxPct = Math.min(100, Math.round((ctxUsed / (ctxMax || 32000)) * 100));

  // Build the assistant-ui runtime from Spark's Zustand state.
  const runtime = useExternalStoreRuntime(
    useMemo(
      () => ({
        messages,
        convertMessage,
        isRunning,
        onNew: async () => {
          /* Spark sends via its own Composer – no-op here */
        },
        onCancel: async () => {
          /* Spark handles cancel via App.tsx stop button */
        },
      }),
      [messages, isRunning]
    )
  );

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <ContextBar pct={ctxPct} used={ctxUsed} max={ctxMax || 32000} />

        <ThreadPrimitive.Root className="min-h-0 flex-1">
          <ThreadPrimitive.Viewport className="scrollbar-thin">
            <ThreadPrimitive.Empty>
              <div
                className="flex flex-col items-center justify-center py-16 text-sm"
                style={{ color: "var(--ink-muted)" }}
              >
                开始对话吧
              </div>
            </ThreadPrimitive.Empty>

            <ThreadPrimitive.Messages
              components={{
                UserMessage,
                AssistantMessage,
              }}
            />
          </ThreadPrimitive.Viewport>
        </ThreadPrimitive.Root>
      </div>
    </AssistantRuntimeProvider>
  );
}
