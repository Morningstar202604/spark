import { useEffect, useRef, useState } from "react";
import { useApp } from "../state";
import { fmtTime, shortPath } from "../lib/utils";
import {
  cancelSession,
  deleteSession,
  forkSession,
  getSession,
  listSessions,
  renameSession,
} from "../lib/api";
import type { SessionMeta } from "../types";

function sessionSub(s: SessionMeta): string {
  if (s.match && s.match.kind === "content") {
    const role = s.match.role === "user" ? "你" : "Spark";
    return `匹配：${role} · ${s.match.snippet || ""}`;
  }
  return `${fmtTime(s.updated || "")} · ${s.messages || 0} 条消息`;
}

export function Sidebar({
  open,
  onToggle,
}: {
  open?: boolean;
  onToggle?: () => void;
}) {
  const {
    sessions,
    sid,
    runningSids,
    setRunning,
    setSessions,
    toast,
    refreshSessions,
    openSession,
    newSession,
    setSid,
  } = useApp();

  const [collapsed, setCollapsed] = useState(
    () => window.innerWidth <= 700
  );
  const [searchQ, setSearchQ] = useState("");
  const searchTimer = useRef<ReturnType<typeof setTimeout>>(0);

  useEffect(() => {
    const t = setTimeout(() => {
      const q = searchQ.trim();
      listSessions(q || undefined)
        .then(setSessions)
        .catch(() => {});
    }, 300);
    return () => clearTimeout(t);
  }, [searchQ, setSessions]);

  useEffect(() => {
    if (sessions.length && !sid) {
      openSession(sessions[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const q = searchQ.trim().toLowerCase();
  let list = sessions;
  if (q && !sessions.some((s) => s.match)) {
    list = list.filter(
      (s) =>
        (s.title || "").toLowerCase().includes(q) ||
        (s.workdir || "").toLowerCase().includes(q)
    );
  }

  const groups = new Map<string, SessionMeta[]>();
  for (const s of [...list].sort(
    (a, b) => (b.updated || "").localeCompare(a.updated || "")
  )) {
    const wd = s.workdir || "（未指定目录）";
    if (!groups.has(wd)) groups.set(wd, []);
    groups.get(wd)!.push(s);
  }

  async function selectSession(targetSid: string) {
    if (targetSid === sid) return;
    setSid(targetSid);
    await openSession(targetSid);
    refreshSessions();
    onToggle?.();
  }

  async function doRename(s: SessionMeta) {
    const title = prompt("会话新标题（留空取消）", s.title);
    if (!title) return;
    try {
      await renameSession(s.id, title.trim().slice(0, 60));
      toast("已重命名");
      await refreshSessions();
    } catch {
      toast("重命名失败");
    }
  }

  async function doExport(s: SessionMeta) {
    try {
      const data = await getSession(s.id);
      let md = `# ${data.meta.title || "会话"}\n\n`;
      for (const m of data.messages) {
        if (m.role === "user") {
          const text = Array.isArray(m.content)
            ? (m.content as any[]).filter((p) => p.type === "text").map((p) => p.text).join("\n")
            : (m.content as string);
          md += `## 你\n\n${text}\n\n`;
        } else if (m.role === "assistant") {
          md += `## Spark\n\n${m.content as string}\n\n`;
        }
      }
      const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (data.meta.title || "会话").replace(/[\\/:*?"<>|]/g, "_").slice(0, 40) + ".md";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(a.href);
      toast("已导出 Markdown");
    } catch {
      toast("导出失败");
    }
  }

  async function doFork(s: SessionMeta) {
    try {
      const meta = await forkSession(s.id);
      toast("已分叉出新会话");
      await refreshSessions();
      await selectSession(meta.id);
    } catch {
      toast("分叉失败");
    }
  }

  async function doDelete(s: SessionMeta) {
    if (!confirm(`删除会话「${s.title}」？此操作不可恢复。`)) return;
    try {
      await deleteSession(s.id);
      toast("会话已删除");
      if (sid === s.id) {
        setSid(null);
      }
      await refreshSessions();
    } catch {
      toast("删除失败");
    }
  }

  async function doCancel(id: string) {
    try {
      await cancelSession(id);
      setRunning(id, false);
      toast("已请求停止会话");
    } catch {
      toast("停止失败");
    }
  }

  const isMobile = typeof window !== "undefined" && window.innerWidth <= 700;
  const effectiveCollapsed = open !== undefined ? !open : collapsed;

  return (
    <div
      className={`flex flex-none flex-col overflow-hidden transition-all ${
        effectiveCollapsed ? "w-0" : "w-[260px]"
      } ${isMobile && open ? "absolute z-50 h-full shadow-xl" : ""}`}
      style={{
        borderRight: effectiveCollapsed
          ? "none"
          : "1px solid var(--border)",
        background: "var(--card)",
        top: isMobile ? 0 : undefined,
        left: isMobile ? 0 : undefined,
      }}
    >
      {/* Top: new session + search */}
      <div className="flex-none px-3 pt-3">
        <button
          className="mb-2 w-full rounded-lg px-3 py-2 text-sm font-medium transition-colors"
          style={{ background: "var(--accent)", color: "#fff" }}
          onClick={() => {
            newSession();
            onToggle?.();
          }}
        >
          ＋ 新建会话
        </button>
        <input
          className="w-full rounded-lg border px-3 py-1.5 text-sm outline-none"
          style={{
            border: "1px solid var(--border)",
            background: "var(--surface)",
            color: "var(--ink)",
          }}
          placeholder="搜索会话…"
          value={searchQ}
          onChange={(e) => setSearchQ(e.target.value)}
        />
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto px-3 py-2 scrollbar-thin">
        {sessions.length === 0 ? (
          <div
            className="py-6 text-center text-xs"
            style={{ color: "var(--ink-muted)" }}
          >
            还没有会话，点上面新建
          </div>
        ) : list.length === 0 ? (
          <div
            className="py-6 text-center text-xs"
            style={{ color: "var(--ink-muted)" }}
          >
            没有匹配「{searchQ}」的会话
          </div>
        ) : (
          Array.from(groups.entries()).map(([wd, sessList]) => (
            <div key={wd} className="mb-3">
              <div
                className="flex items-center justify-between px-2 py-1 text-[10px] uppercase tracking-wider"
                style={{ color: "var(--ink-muted)" }}
              >
                <span className="truncate">{shortPath(wd)}</span>
                <span>{sessList.length}</span>
              </div>
              {sessList.map((s) => {
                const isRunning = runningSids[s.id];
                const isActive = s.id === sid;
                return (
                  <div
                    key={s.id}
                    className="group mb-1 cursor-pointer rounded-lg border p-2.5 transition-colors"
                    style={{
                      background: isActive
                        ? "color-mix(in srgb, var(--accent) 8%, var(--card))"
                        : "var(--card)",
                      borderColor: isActive
                        ? "color-mix(in srgb, var(--accent) 40%, transparent)"
                        : "var(--border)",
                    }}
                    onClick={() => selectSession(s.id)}
                  >
                    <div className="flex items-center gap-1.5">
                      <span
                        className="flex-1 truncate text-xs font-medium"
                        style={{ color: "var(--ink)" }}
                      >
                        {s.title || "(未命名)"}
                      </span>
                      {isRunning && (
                        <span
                          className="flex-none rounded-full px-1.5 text-[10px] font-medium"
                          style={{
                            background:
                              "color-mix(in srgb, var(--accent) 15%, transparent)",
                            color: "var(--accent)",
                          }}
                        >
                          运行中
                        </span>
                      )}
                    </div>
                    <div
                      className="mt-0.5 truncate text-[11px]"
                      style={{ color: "var(--ink-muted)" }}
                    >
                      {sessionSub(s)}
                    </div>
                    {s.workdir && (
                      <div
                        className="mt-0.5 truncate text-[10px]"
                        style={{
                          color: "var(--ink-muted)",
                          opacity: 0.7,
                        }}
                      >
                        {shortPath(s.workdir)}
                      </div>
                    )}
                    {/* Action row - always visible on mobile, hover on desktop */}
                    <div
                      className={`mt-1.5 flex flex-wrap gap-1 ${
                        isMobile ? "" : "opacity-0 transition-opacity group-hover:opacity-100"
                      }`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {isRunning && (
                        <button
                          className="rounded px-1.5 py-0.5 text-[10px]"
                          style={{ color: "var(--red, #d64545)" }}
                          onClick={() => doCancel(s.id)}
                        >
                          ■ 停止
                        </button>
                      )}
                      <button
                        className="rounded px-1.5 py-0.5 text-[10px]"
                        style={{ color: "var(--ink-muted)" }}
                        onClick={() => doRename(s)}
                      >
                        重命名
                      </button>
                      <button
                        className="rounded px-1.5 py-0.5 text-[10px]"
                        style={{ color: "var(--ink-muted)" }}
                        onClick={() => doFork(s)}
                      >
                        分叉
                      </button>
                      <button
                        className="rounded px-1.5 py-0.5 text-[10px]"
                        style={{ color: "var(--ink-muted)" }}
                        onClick={() => doExport(s)}
                      >
                        导出
                      </button>
                      <button
                        className="rounded px-1.5 py-0.5 text-[10px]"
                        style={{ color: "var(--red, #d64545)" }}
                        onClick={() => doDelete(s)}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
