import { useEffect, useRef, useState } from "react";
import { AppProvider, useApp } from "./state";
import { Sidebar } from "./components/Sidebar";
import { WelcomeView } from "./components/WelcomeView";
import { MessageList } from "./components/MessageList";
import {
  SettingsDrawer,
  GitDrawer,
  UsageDrawer,
} from "./components/SettingsDrawer";
import { highlightDiff } from "./lib/markdown";
import { cancelSession } from "./lib/api";
import {
  History,
  Menu,
  Moon,
  Paperclip,
  Send,
  Settings,
  Square,
  Sun,
  Trash2,
} from "lucide-react";

function Toasts() {
  const { toasts, dismissToast } = useApp();
  return (
    <div
      className="fixed left-1/2 top-3 z-[60] flex -translate-x-1/2 flex-col items-center gap-2"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          className="max-w-[92vw] rounded-full border px-4 py-2 text-xs shadow-sm"
          style={{
            background:
              t.type === "error"
                ? "color-mix(in srgb, var(--red, #d64545) 10%, var(--surface))"
                : "var(--surface)",
            borderColor: t.type === "error" ? "rgba(214,69,69,0.35)" : "var(--border)",
            color: "var(--ink)",
          }}
          onClick={() => dismissToast(t.id)}
        >
          {t.text}
        </button>
      ))}
    </div>
  );
}

function ApprovalModal() {
  const { approval, respondApproval } = useApp();
  if (!approval) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "color-mix(in srgb, #101828 55%, transparent)" }}
    >
      <div
        className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-2xl border p-5 shadow-2xl scrollbar-thin"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}
      >
        <div className="mb-3 text-sm font-semibold">工具审批</div>
        {approval.tool && (
          <div className="mb-2 text-xs" style={{ color: "var(--ink-muted)" }}>
            工具：{approval.tool}
          </div>
        )}
        {approval.summary && <div className="mb-2 text-xs">{approval.summary}</div>}
        {approval.reason && (
          <div className="mb-3 text-xs" style={{ color: "var(--ink-muted)" }}>
            原因：{approval.reason}
          </div>
        )}
        {approval.diff && (
          <pre
            className="mb-4 max-h-60 overflow-auto rounded-lg border p-3 text-xs"
            style={{
              background: "var(--surface-2)",
              borderColor: "var(--border)",
              whiteSpace: "pre-wrap",
            }}
            dangerouslySetInnerHTML={{ __html: highlightDiff(approval.diff) }}
          />
        )}
        <div className="flex gap-2">
          <button
            className="flex-1 rounded-lg px-3 py-2 text-sm font-medium"
            style={{ background: "var(--green, #0e9d6e)", color: "#fff" }}
            onClick={() => respondApproval("allow")}
          >
            允许
          </button>
          <button
            className="flex-1 rounded-lg px-3 py-2 text-sm font-medium"
            style={{ background: "var(--red, #d64545)", color: "#fff" }}
            onClick={() => respondApproval("deny")}
          >
            拒绝
          </button>
          <button
            className="flex-1 rounded-lg border px-3 py-2 text-sm font-medium"
            style={{ borderColor: "var(--border)", color: "var(--ink)" }}
            onClick={() => respondApproval("always")}
          >
            始终允许
          </button>
        </div>
      </div>
    </div>
  );
}

function Topbar({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const {
    sid,
    sessions,
    runningSids,
    theme,
    setTheme,
    cfg,
    cancelCurrentSession,
    clearCurrentSession,
  } = useApp();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsPane, setSettingsPane] = useState("");
  const [gitOpen, setGitOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);

  useEffect(() => {
    const open = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      setSettingsPane(detail.pane || "");
      setSettingsOpen(true);
    };
    window.addEventListener("spark:open-settings", open);
    return () => window.removeEventListener("spark:open-settings", open);
  }, []);

  const session = sessions.find((s) => s.id === sid);
  const isRunning = sid ? !!runningSids[sid] : false;

  return (
    <header
      className="flex flex-none items-center justify-between gap-2 px-3 py-2 sm:px-4"
      style={{
        borderBottom: "1px solid var(--border)",
        background: "var(--card)",
      }}
    >
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          className="rounded-lg border p-2 text-sm sm:hidden"
          style={{
            borderColor: "var(--border)",
            color: "var(--ink-muted)",
          }}
          onClick={onToggleSidebar}
          aria-label="切换会话列表"
        >
          <Menu size={16} />
        </button>
        <button
          type="button"
          className="hidden rounded-lg border p-2 sm:inline-flex"
          style={{
            borderColor: "var(--border)",
            color: "var(--ink-muted)",
          }}
          onClick={() => setGitOpen(true)}
          aria-label="历史记录"
          title="历史记录"
        >
          <History size={16} />
        </button>
        {session ? (
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">
              {session.title || "新会话"}
            </div>
            <div
              className="hidden truncate text-xs sm:block"
              style={{ color: "var(--ink-muted)" }}
            >
              {cfg?.model || "未配置模型"}
              {cfg?.workdir ? ` · ${cfg.workdir}` : ""}
            </div>
          </div>
        ) : (
          <div className="flex min-w-0 items-center gap-2">
            <div
              className="flex h-7 w-7 flex-none items-center justify-center rounded-lg"
              style={{ background: "var(--accent)" }}
            >
              <svg viewBox="0 0 64 64" width="14" height="14">
                <path d="M20 40l6-16h3l-4 10h10l-3 6z" fill="#fff" />
              </svg>
            </div>
            <span className="truncate text-sm font-semibold">Spark</span>
          </div>
        )}
      </div>

      <div className="flex flex-none items-center gap-1.5 sm:gap-2">
        {isRunning && (
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs"
            style={{
              border: "1px solid rgba(214,69,69,0.35)",
              color: "var(--red, #d64545)",
            }}
            onClick={cancelCurrentSession}
          >
            <Square size={12} /> 停止
          </button>
        )}
        <button
          type="button"
          className="rounded-lg p-2"
          style={{
            border: "1px solid var(--border)",
            color: "var(--ink-muted)",
          }}
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label={theme === "dark" ? "切换为浅色" : "切换为深色"}
          title={theme === "dark" ? "浅色" : "深色"}
        >
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
        <button
          type="button"
          className="rounded-lg p-2"
          style={{
            border: "1px solid var(--border)",
            color: "var(--ink-muted)",
          }}
          onClick={() => clearCurrentSession()}
          aria-label="清空当前会话"
          title="清空当前会话"
        >
          <Trash2 size={16} />
        </button>
        <button
          type="button"
          className="rounded-lg p-2"
          style={{
            border: "1px solid var(--border)",
            color: "var(--ink-muted)",
          }}
          onClick={() => setSettingsOpen(true)}
          aria-label="设置"
          title="设置"
        >
          <Settings size={16} />
        </button>
      </div>

      <SettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        initialPane={settingsPane}
      />
      <GitDrawer open={gitOpen} onClose={() => setGitOpen(false)} />
      <UsageDrawer open={usageOpen} onClose={() => setUsageOpen(false)} />
    </header>
  );
}

function Composer() {
  const { pendingPrompt, requestSend, runningSids, sid, setPendingImages, pendingImages, toast } =
    useApp();
  const [files, setFiles] = useState<File[]>([]);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // 输入框自动增高（现代聊天体验），到上限后出滚动条
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }, [pendingPrompt]);

  useEffect(() => {
    if (!pendingPrompt) return;
    const timer = setTimeout(() => {
      requestSend(pendingPrompt);
    }, 0);
    return () => clearTimeout(timer);
  }, [pendingPrompt, requestSend]);

  async function addFiles(list: FileList | null) {
    if (!list) return;
    const next = [...files];
    for (const f of Array.from(list)) {
      if (!f.type.startsWith("image/")) {
        toast("只支持图片附件");
        continue;
      }
      if (f.size > 2 * 1024 * 1024) {
        toast("单张图片请控制在 2MB 内");
        continue;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const data = String(reader.result || "")
          .split(",")
          .pop();
        setFiles((prev) => [...prev, f]);
        setPendingImages((prev) => [
          ...prev,
          { data: data || "", mime: f.type, name: f.name },
        ]);
      };
      reader.readAsDataURL(f);
      next.push(f);
    }
    setFiles(next);
  }

  return (
    <div
      className="flex flex-none items-end gap-1.5 border-t p-2 sm:gap-2 sm:p-3"
      style={{ borderColor: "var(--border)", background: "var(--card)" }}
    >
      <input
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        id="composer-images"
        onChange={(e) => addFiles(e.target.files)}
      />
      <button
        type="button"
        className="rounded-xl border p-2.5 sm:p-3"
        style={{
          borderColor: "var(--border)",
          background: "var(--surface)",
          color: "var(--ink-muted)",
        }}
        onClick={() => document.getElementById("composer-images")?.click()}
        title="添加图片"
        aria-label="添加图片"
      >
        <Paperclip size={18} />
      </button>
      <textarea
        ref={taRef}
        rows={1}
        className="max-h-40 min-h-[44px] flex-1 resize-none rounded-xl border px-3 py-2.5 text-sm leading-relaxed outline-none sm:px-3.5"
        style={{
          border: "1px solid var(--border)",
          background: "var(--surface)",
          color: "var(--ink)",
          overflowY: "auto",
        }}
        placeholder="给 Spark 发消息，Enter 发送，Shift+Enter 换行"
        value={pendingPrompt}
        onChange={(e) => requestSend(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (sid) requestSend(pendingPrompt);
          }
        }}
      />
      <button
        type="button"
        className="flex h-11 w-11 flex-none items-center justify-center rounded-full sm:h-12 sm:w-12"
        style={{
          background: "var(--accent)",
          color: "#fff",
          opacity: sid ? 1 : 0.5,
        }}
        onClick={() => {
          if (!sid) return;
          if (runningSids[sid]) cancelSession(sid);
          else requestSend(pendingPrompt);
        }}
        aria-label={runningSids[sid || ""] ? "停止生成" : "发送"}
        title={runningSids[sid || ""] ? "停止生成" : "发送"}
      >
        {runningSids[sid || ""] ? <Square size={18} /> : <Send size={18} />}
      </button>
    </div>
  );
}

function Shell() {
  const { sid } = useApp();
  const [sidebarOpen, setSidebarOpen] = useState(
    () => typeof window !== "undefined" && window.innerWidth > 700
  );

  return (
    <div className="flex h-full min-h-0">
      <Sidebar open={sidebarOpen} onToggle={() => setSidebarOpen(!sidebarOpen)} />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <Topbar onToggleSidebar={() => setSidebarOpen(!sidebarOpen)} />
        {sid ? <MessageList /> : <WelcomeView />}
        <Composer />
      </main>
      <ApprovalModal />
    </div>
  );
}

export function App() {
  return (
    <AppProvider>
      <div className="h-screen w-screen overflow-hidden">
        <Shell />
        <Toasts />
      </div>
    </AppProvider>
  );
}
