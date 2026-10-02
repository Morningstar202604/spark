import { useState } from "react";
import { useApp } from "../state";
import { shortPath } from "../lib/utils";

const SUGGESTIONS = [
  "帮我分析这个项目的结构",
  "写一个 Python 脚本处理 CSV 文件",
  "解释这段代码的作用",
  "帮我调试这个报错",
];

export function WelcomeView() {
  const { cfg, toast, newSession, requestSend } = useApp();
  const [loading, setLoading] = useState(false);

  const missing: string[] = [];
  if (cfg) {
    if (!(cfg.workdir || "").trim()) missing.push("workdir");
    const hasModel =
      (cfg.base_url && cfg.model) || (cfg.api_key && cfg.model);
    if (!hasModel && !cfg.demo_mode) missing.push("model");
  }

  const modelStatus = !cfg
    ? "loading"
    : missing.includes("model")
      ? "missing"
      : cfg.demo_mode
        ? "demo"
        : "ok";
  const workdirStatus = !cfg ? "loading" : missing.includes("workdir") ? "missing" : "ok";

  const modelDesc = !cfg
    ? "正在读取本地配置…"
    : cfg.demo_mode
      ? "演示模式"
      : cfg.model || "未配置模型";
  const subText = !cfg
    ? "正在读取本地配置…"
    : [modelDesc, cfg.workdir ? shortPath(cfg.workdir) : "工作目录未设置"].join(
        " · "
      );

  async function start() {
    if (missing.includes("workdir")) {
      toast("先指定工作目录（Agent 的活动范围）");
      window.dispatchEvent(new CustomEvent("spark:open-settings", { detail: { pane: "paneWorkspace" } }));
      return;
    }
    if (missing.includes("model")) {
      toast("先选模型服务并填 Key（也可用演示模式）");
      window.dispatchEvent(new CustomEvent("spark:open-settings", { detail: { pane: "paneModel" } }));
      return;
    }
    setLoading(true);
    try {
      await newSession();
    } finally {
      setLoading(false);
    }
  }

  function chipClick(q: string) {
    if (missing.length) {
      start();
      return;
    }
    requestSend(q);
  }

  function StatusBadge({ status, label }: { status: string; label: string }) {
    if (status === "loading")
      return (
        <span className="text-xs" style={{ color: "var(--ink-muted)" }}>
          <i className="inline-block animate-pulse">…</i> {label}
        </span>
      );
    if (status === "missing")
      return (
        <span className="text-xs font-medium" style={{ color: "var(--red, #d64545)" }}>
          ✗ 未配置
        </span>
      );
    if (status === "demo")
      return (
        <span className="text-xs font-medium" style={{ color: "var(--amber, #b26a00)" }}>
          演示模式
        </span>
      );
    return (
      <span className="text-xs font-medium" style={{ color: "var(--green, #0e9d6e)" }}>
        ✓ 已配置
      </span>
    );
  }

  return (
    <div
      className="flex flex-1 flex-col items-center justify-center overflow-y-auto p-8"
      style={{ background: "var(--surface)" }}
    >
      {/* Logo */}
      <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl"
        style={{ background: "var(--accent)" }}
      >
        <svg viewBox="0 0 64 64" width="36" height="36">
          <path d="M20 40l6-16h3l-4 10h10l-3 6z" fill="#fff" />
        </svg>
      </div>

      <h1 className="mb-2 text-2xl font-bold">Spark 编程助手</h1>
      <p className="mb-8 text-sm" style={{ color: "var(--ink-muted)" }}>
        {subText}
      </p>

      {/* Steps */}
      <div className="mb-6 w-full max-w-md space-y-3">
        {[
          {
            title: "模型服务",
            status: modelStatus,
            desc: modelStatus === "ok" ? "已配置，可以开始对话" : modelStatus === "demo" ? "演示模式运行中" : "需要配置模型或开启演示",
            action: () =>
              window.dispatchEvent(
                new CustomEvent("spark:open-settings", { detail: { pane: "paneModel" } })
              ),
          },
          {
            title: "工作目录",
            status: workdirStatus,
            desc: workdirStatus === "ok" ? "Agent 将在指定目录内活动" : "需要指定项目路径",
            action: () =>
              window.dispatchEvent(
                new CustomEvent("spark:open-settings", { detail: { pane: "paneWorkspace" } })
              ),
          },
          {
            title: "新建会话",
            status: missing.length ? "missing" : "ok",
            desc: missing.length ? "完成前两步后可开始" : "点击「开始使用」新建会话",
            action: start,
          },
        ].map((step, i) => (
          <div
            key={i}
            className="flex items-center gap-4 rounded-xl border p-4"
            style={{
              background: "var(--card)",
              borderColor: "var(--border)",
            }}
          >
            <div
              className="flex h-8 w-8 flex-none items-center justify-center rounded-full text-xs font-bold"
              style={{
                background:
                  step.status === "ok"
                    ? "rgba(14, 157, 110, 0.15)"
                    : step.status === "demo"
                      ? "rgba(178, 106, 0, 0.15)"
                      : "var(--surface-2)",
                color:
                  step.status === "ok"
                    ? "var(--green, #0e9d6e)"
                    : step.status === "demo"
                      ? "var(--amber, #b26a00)"
                      : "var(--ink-muted)",
              }}
            >
              {i + 1}
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{step.title}</span>
                <StatusBadge status={step.status} label="" />
              </div>
              <p className="mt-0.5 text-xs" style={{ color: "var(--ink-muted)" }}>
                {step.desc}
              </p>
            </div>
            <button
              className="text-xs underline opacity-60 hover:opacity-100"
              style={{ color: "var(--accent)" }}
              onClick={step.action}
            >
              去设置
            </button>
          </div>
        ))}
      </div>

      {/* Suggestions */}
      <div className="flex flex-wrap justify-center gap-2">
        {SUGGESTIONS.map((q, i) => (
          <button
            key={i}
            className="rounded-full border px-3 py-1.5 text-xs transition-colors"
            style={{
              background: "var(--card)",
              borderColor: "var(--border)",
              color: "var(--ink)",
            }}
            onClick={() => chipClick(q)}
          >
            {q}
          </button>
        ))}
      </div>

      <button
        className="mt-6 rounded-xl px-6 py-2.5 text-sm font-medium transition-opacity disabled:opacity-50"
        style={{ background: "var(--accent)", color: "#fff" }}
        disabled={loading}
        onClick={start}
      >
        {loading ? "创建中…" : "开始使用"}
      </button>
    </div>
  );
}
