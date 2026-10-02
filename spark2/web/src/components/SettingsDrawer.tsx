import { useState, useEffect, useRef } from "react";
import { useApp } from "../state";
import {
  getConfig,
  saveConfig,
  testConnection,
  getRecentDirs,
  listMemory,
  addMemory,
  deleteMemory,
  getPlugins,
  getUsage,
  getGitStatus,
  gitCheckpoint,
  gitReset,
} from "../lib/api";
import { fmtTokens, shortPath } from "../lib/utils";
import type { Cfg, McpServer, UsageResponse } from "../types";

const SETTINGS_PANES = [
  { id: "paneModel", label: "模型" },
  { id: "paneWorkspace", label: "工作区" },
  { id: "paneMemory", label: "记忆" },
  { id: "paneIntegrations", label: "集成" },
  { id: "paneAdvanced", label: "高级" },
  { id: "paneAbout", label: "关于" },
];

export function SettingsDrawer({
  open,
  onClose,
  initialPane,
}: {
  open: boolean;
  onClose: () => void;
  initialPane?: string;
}) {
  const {
    cfg,
    presets,
    approvalModes,
    mcpServers,
    setMcpServers,
    dirs,
    token,
    setToken,
    toast,
    loadConfig,
    refreshSessions,
    setUsage,
  } = useApp();

  const [activePane, setActivePane] = useState(initialPane || "paneModel");
  const [dirty, setDirty] = useState(false);
  const [saveStatus, setSaveStatus] = useState<{ text: string; ok?: boolean }>({ text: "" });

  // Local form state
  const [form, setForm] = useState<Partial<Cfg>>({});
  const [memKey, setMemKey] = useState("");
  const [memVal, setMemVal] = useState("");
  const [memoryItems, setMemoryItems] = useState<{ id: string; key: string; value: string }[]>([]);
  const [plugins, setPlugins] = useState<{ name: string; tools?: string[]; error?: string }[]>([]);
  const [pluginsDir, setPluginsDir] = useState("");
  const [recentDirs, setRecentDirsState] = useState<string[]>([]);
  const [usage, setUsageState] = useState<UsageResponse | null>(null);
  const [git, setGit] = useState<any>(null);
  const [cpMsg, setCpMsg] = useState("");

  // Sync form when cfg loads
  useEffect(() => {
    if (cfg) {
      setForm({
        ...cfg,
        protected_paths_str: (cfg.protected_paths || []).join("\n"),
        pricing_str: cfg.usage_pricing ? JSON.stringify(cfg.usage_pricing, null, 2) : "",
      } as any);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg]);

  // Load aux data when drawer opens
  useEffect(() => {
    if (!open) return;
    loadAuxData();
  }, [open]);

  async function loadAuxData() {
    const [recentDirsRes, pluginsRes, usageRes] = await Promise.allSettled([
      getRecentDirs(),
      getPlugins(),
      getUsage(),
    ]);
    if (recentDirsRes.status === "fulfilled") setRecentDirsState(recentDirsRes.value.dirs || []);
    if (pluginsRes.status === "fulfilled") {
      setPlugins(pluginsRes.value.plugins || []);
      setPluginsDir(pluginsRes.value.dir || "");
    }
    if (usageRes.status === "fulfilled") {
      setUsageState(usageRes.value);
      setUsage(usageRes.value);
    }
    if (cfg?.workdir) {
      const memRes = await getRecentDirsSafe(cfg.workdir);
      if (memRes) setMemoryItems(memRes.items || []);
    }
  }

  async function getRecentDirsSafe(workdir: string) {
    try {
      return await listMemory(workdir);
    } catch {
      return null;
    }
  }

  function markDirty() {
    setDirty(true);
    setSaveStatus({ text: "有未保存修改，记得点右下角「保存设置」" });
  }

  function updateForm<K extends keyof Cfg>(key: K, val: Cfg[K]) {
    setForm((prev) => ({ ...prev, [key]: val }));
    markDirty();
  }

  async function handleSave() {
    const f = form as any;
    const body: any = {
      provider: f.provider,
      base_url: (f.base_url || "").trim(),
      proxy: (f.proxy || "").trim(),
      model: (f.model || "").trim(),
      model_fast: (f.model_fast || "").trim(),
      fallback_model: (f.fallback_model || "").trim(),
      api_key: f.api_key,
      workdir: (f.workdir || "").trim(),
      approval_mode: f.approval_mode,
      max_context_tokens: parseInt(f.max_context_tokens, 10) || 32000,
      memory_embedding: f.memory_embedding,
      memory_embed_model: (f.memory_embed_model || "").trim(),
      mcp_servers: mcpServers.map((s) => ({
        name: s.name,
        transport: s.transport || "stdio",
        command: s.command || "",
        args: s.args || [],
        url: s.url || "",
        headers: safeParseHeaders(s.headersJson || ""),
      })),
      system_prompt: f.system_prompt || "",
      protected_paths: (f.protected_paths_str || "")
        .split("\n")
        .map((s: string) => s.trim())
        .filter(Boolean),
      max_turns: parseInt(f.max_turns, 10) || 25,
      tool_timeout: parseInt(f.tool_timeout, 10) || 180,
      auto_verify: f.auto_verify !== false,
      temperature: (f.temperature || "").trim(),
      max_tokens: (f.max_tokens || "").trim(),
      route_enabled: f.route_enabled !== false,
      route_keywords: (f.route_keywords || "").trim(),
    };

    const pt = (f.pricing_str || "").trim();
    if (pt) {
      try {
        const po = JSON.parse(pt);
        if (!po || typeof po !== "object" || Array.isArray(po)) {
          setSaveStatus({ text: "成本单价需是 JSON 对象（键 = 模型名）", ok: false });
          return;
        }
        body.usage_pricing = po;
      } catch {
        setSaveStatus({ text: "成本单价不是合法 JSON，未保存。", ok: false });
        toast("成本单价 JSON 解析失败");
        return;
      }
    } else {
      body.usage_pricing = {};
    }

    try {
      const data = await saveConfig(body);
      setSaveStatus({ text: "已保存。", ok: true });
      setDirty(false);
      toast("设置已保存");
      // reload
      await loadConfig();
      refreshSessions();
      // reload aux
      const [rdRes, memRes] = await Promise.allSettled([
        getRecentDirs(),
        cfg?.workdir ? listMemory(cfg.workdir) : Promise.resolve(null),
      ]);
      if (rdRes.status === "fulfilled") setRecentDirsState(rdRes.value.dirs || []);
      if (memRes.status === "fulfilled" && memRes.value) setMemoryItems(memRes.value.items || []);
    } catch (e: any) {
      setSaveStatus({ text: `保存失败：${e.message || e}`, ok: false });
    }
  }

  async function handleTestConn() {
    setSaveStatus({ text: "正在测试…" });
    try {
      const d = await testConnection({
        base_url: (form.base_url || "").trim(),
        proxy: (form.proxy || "").trim(),
        model: (form.model || "").trim(),
        api_key: form.api_key,
      });
      setSaveStatus({ text: d.ok ? "连接成功" : d.message || "连接失败", ok: d.ok });
    } catch (e: any) {
      setSaveStatus({ text: `请求失败：${e.message || e}`, ok: false });
    }
  }

  async function handleAddMem() {
    if (!form.workdir) {
      toast("先在设置里指定工作目录");
      return;
    }
    if (!memKey.trim() || !memVal.trim()) {
      toast("key 与内容都要填");
      return;
    }
    try {
      await addMemory(form.workdir, memKey.trim(), memVal.trim());
      setMemKey("");
      setMemVal("");
      toast(`已记住「${memKey.trim()}」`);
      const res = await listMemory(form.workdir);
      setMemoryItems(res.items || []);
    } catch {
      toast("保存失败");
    }
  }

  async function handleDeleteMem(id: string) {
    try {
      await deleteMemory(id);
      toast("已删除这条记忆");
      if (form.workdir) {
        const res = await listMemory(form.workdir);
        setMemoryItems(res.items || []);
      }
    } catch {
      toast("删除失败");
    }
  }

  async function handleGitLoad() {
    try {
      const d = await getGitStatus();
      setGit(d);
    } catch {
      setGit(null);
    }
  }

  async function handleCheckpoint() {
    if (!cfg) return;
    try {
      await gitCheckpoint("", cpMsg);
      setCpMsg("");
      toast("已存档");
      handleGitLoad();
    } catch (e: any) {
      toast(e.message || "存档失败");
    }
  }

  async function handleGitReset() {
    if (!confirm("回滚到最近一次存档？未存档的改动将丢失（不可撤销）。")) return;
    try {
      await gitReset();
      toast("已回滚到最近存档");
      handleGitLoad();
    } catch (e: any) {
      toast(e.message || "回滚失败");
    }
  }

  function safeParseHeaders(s: string): Record<string, string> {
    if (!s.trim()) return {};
    try {
      const o = JSON.parse(s);
      return o && typeof o === "object" && !Array.isArray(o) ? o : {};
    } catch {
      return {};
    }
  }

  function addMcp() {
    setMcpServers((prev) => [
      ...prev,
      { name: "", command: "", args: [], env: {}, transport: "stdio", url: "", headersJson: "" },
    ]);
    markDirty();
  }

  function removeMcp(idx: number) {
    setMcpServers((prev) => prev.filter((_, i) => i !== idx));
    markDirty();
  }

  function updateMcp(idx: number, patch: Partial<McpServer>) {
    setMcpServers((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], ...patch };
      return next;
    });
    markDirty();
  }

  const f = form as any;

  const content = (() => {
    switch (activePane) {
      case "paneModel":
        return (
          <PaneModel
            f={f}
            updateForm={updateForm}
            presets={presets}
            approvalModes={approvalModes}
            recentDirs={recentDirs}
            onTest={handleTestConn}
          />
        );
      case "paneWorkspace":
        return (
          <PaneWorkspace
            f={f}
            updateForm={updateForm}
            approvalModes={approvalModes}
          />
        );
      case "paneMemory":
        return (
          <PaneMemory
            f={f}
            updateForm={updateForm}
            memKey={memKey}
            setMemKey={setMemKey}
            memVal={memVal}
            setMemVal={setMemVal}
            onAddMem={handleAddMem}
            memoryItems={memoryItems}
            onDeleteMem={handleDeleteMem}
          />
        );
      case "paneIntegrations":
        return (
          <PaneIntegrations
            mcpServers={mcpServers}
            onAddMcp={addMcp}
            onRemoveMcp={removeMcp}
            onUpdateMcp={updateMcp}
            plugins={plugins}
            pluginsDir={pluginsDir}
          />
        );
      case "paneAdvanced":
        return (
          <PaneAdvanced
            f={f}
            updateForm={updateForm}
            token={token}
          />
        );
      case "paneAbout":
        return <PaneAbout cfg={cfg} version={(cfg as any)?.version} />;
      default:
        return null;
    }
  })();

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-40"
        style={{ background: "color-mix(in srgb, #101828 42%, transparent)" }}
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      />
      <div
        className="fixed z-50 flex h-full flex-col"
        style={{
          right: 0,
          top: 0,
          width: 520,
          maxWidth: "94vw",
          background: "var(--surface)",
          borderLeft: "1px solid var(--border)",
        }}
      >
        {/* Header */}
        <div
          className="flex flex-none items-center justify-between border-b px-4 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <h2 className="text-sm font-semibold">设置</h2>
          <button
            className="rounded-md p-1 text-sm"
            style={{ color: "var(--ink-muted)" }}
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div className="flex flex-1 overflow-hidden">
          {/* Left nav */}
          <div
            className="flex w-28 flex-none flex-col gap-0.5 border-r p-2"
            style={{ borderColor: "var(--border)" }}
          >
            {SETTINGS_PANES.map((p) => (
              <button
                key={p.id}
                className="rounded-md px-2 py-1.5 text-left text-xs transition-colors"
                style={{
                  background:
                    activePane === p.id
                      ? "color-mix(in srgb, var(--accent) 12%, transparent)"
                      : "transparent",
                  color: activePane === p.id ? "var(--accent)" : "var(--ink-muted)",
                  fontWeight: activePane === p.id ? 600 : 400,
                }}
                onClick={() => setActivePane(p.id)}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Pane content */}
          <div className="flex-1 overflow-y-auto p-4 scrollbar-thin">{content}</div>
        </div>

        {/* Footer */}
        <div
          className="flex flex-none items-center justify-between gap-3 border-t px-4 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <div
            className="flex-1 text-xs"
            style={{
              color: saveStatus.ok ? "var(--green, #0e9d6e)" : saveStatus.text ? "var(--amber, #b26a00)" : "var(--ink-muted)",
            }}
          >
            {saveStatus.text}
          </div>
          <button
            className="rounded-lg px-4 py-1.5 text-sm font-medium"
            style={{ background: "var(--accent)", color: "#fff" }}
            onClick={handleSave}
            disabled={!dirty && !saveStatus.text}
          >
            保存设置
          </button>
        </div>
      </div>
    </>
  );
}

// ---------- Sub-panes ----------

function PaneModel({
  f,
  updateForm,
  presets,
  recentDirs,
  onTest,
}: {
  f: any;
  updateForm: (key: any, val: any) => void;
  presets: Record<string, any>;
  approvalModes: any[];
  recentDirs: string[];
  onTest: () => void;
}) {
  return (
    <div className="space-y-4">
      <Field label="模型服务">
        <select
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.provider || ""}
          onChange={(e) => {
            const preset = presets[e.target.value];
            if (preset) {
              updateForm("provider", e.target.value);
              if (preset.base_url) updateForm("base_url", preset.base_url);
              if (preset.model) updateForm("model", preset.model);
            } else {
              updateForm("provider", e.target.value);
            }
          }}
        >
          {Object.entries(presets).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label || k}
            </option>
          ))}
        </select>
      </Field>

      <Field label="接口地址">
        <input
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.base_url || ""}
          onChange={(e) => updateForm("base_url", e.target.value)}
          placeholder="https://api.openai.com/v1"
        />
      </Field>

      <Field label="代理">
        <input
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.proxy || ""}
          onChange={(e) => updateForm("proxy", e.target.value)}
          placeholder="http://127.0.0.1:7890"
        />
      </Field>

      <Field label="主模型">
        <input
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.model || ""}
          onChange={(e) => updateForm("model", e.target.value)}
          placeholder="gpt-4o"
        />
      </Field>

      <Field label="快速模型（可选）">
        <input
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.model_fast || ""}
          onChange={(e) => updateForm("model_fast", e.target.value)}
        />
      </Field>

      <Field label="备用模型（可选）">
        <input
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.fallback_model || ""}
          onChange={(e) => updateForm("fallback_model", e.target.value)}
        />
      </Field>

      <Field label="API Key">
        <input
          type="password"
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.api_key || ""}
          onChange={(e) => updateForm("api_key", e.target.value)}
          placeholder="sk-…"
        />
      </Field>

      <Field label="上下文上限（tokens）">
        <input
          type="number"
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.max_context_tokens ?? 32000}
          onChange={(e) => updateForm("max_context_tokens", parseInt(e.target.value, 10) || 32000)}
        />
      </Field>

      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={f.route_enabled !== false}
            onChange={(e) => updateForm("route_enabled", e.target.checked)}
          />
          启用多模型路由
        </label>
        <button
          className="rounded-lg border px-3 py-1.5 text-xs"
          style={{ border: "1px solid var(--border)", color: "var(--ink-muted)", background: "var(--surface)" }}
          onClick={onTest}
        >
          测试连接
        </button>
      </div>

      {recentDirs.length > 0 && (
        <div>
          <div className="mb-1 text-xs" style={{ color: "var(--ink-muted)" }}>
            最近目录
          </div>
          <div className="flex flex-wrap gap-1.5">
            {recentDirs.map((d, i) => (
              <button
                key={i}
                className="rounded-full border px-2 py-0.5 text-[11px]"
                style={{
                  border: "1px solid var(--border)",
                  background: "var(--surface)",
                  color: "var(--ink-muted)",
                }}
                onClick={() => updateForm("workdir", d)}
              >
                {shortPath(d)}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PaneWorkspace({
  f,
  updateForm,
  approvalModes,
}: {
  f: any;
  updateForm: (key: any, val: any) => void;
  approvalModes: { value: string; label: string }[];
}) {
  return (
    <div className="space-y-4">
      <Field label="工作目录">
        <input
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.workdir || ""}
          onChange={(e) => updateForm("workdir", e.target.value)}
          placeholder="/path/to/project"
        />
      </Field>

      <Field label="审批模式">
        <select
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.approval_mode || "suggest"}
          onChange={(e) => updateForm("approval_mode", e.target.value)}
        >
          {approvalModes.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
      </Field>

      <Field label="最大轮次">
        <input
          type="number"
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.max_turns ?? 25}
          onChange={(e) => updateForm("max_turns", parseInt(e.target.value, 10) || 25)}
        />
      </Field>

      <Field label="工具超时（秒）">
        <input
          type="number"
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.tool_timeout ?? 180}
          onChange={(e) => updateForm("tool_timeout", parseInt(e.target.value, 10) || 180)}
        />
      </Field>

      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={f.auto_verify !== false}
          onChange={(e) => updateForm("auto_verify", e.target.checked)}
        />
        <label className="text-sm">自动验证</label>
      </div>

      <Field label="保护路径（每行一个）">
        <textarea
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          rows={3}
          value={(f as any).protected_paths_str || ""}
          onChange={(e) => (f as any).protected_paths_str = e.target.value}
          placeholder="/secrets\n/config"
        />
      </Field>
    </div>
  );
}

function PaneMemory({
  f,
  updateForm,
  memKey,
  setMemKey,
  memVal,
  setMemVal,
  onAddMem,
  memoryItems,
  onDeleteMem,
}: {
  f: any;
  updateForm: (key: any, val: any) => void;
  memKey: string;
  setMemKey: (v: string) => void;
  memVal: string;
  setMemVal: (v: string) => void;
  onAddMem: () => void;
  memoryItems: { id: string; key: string; value: string }[];
  onDeleteMem: (id: string) => void;
}) {
  return (
    <div className="space-y-4">
      <Field label="记忆检索方式">
        <select
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          value={f.memory_embedding || "off"}
          onChange={(e) => updateForm("memory_embedding", e.target.value)}
        >
          <option value="off">关闭</option>
          <option value="keyword">关键词检索</option>
          <option value="local">本地语义检索</option>
        </select>
      </Field>

      <div className="rounded-xl border p-3" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
        <div className="mb-2 text-xs font-medium">新增记忆</div>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-lg border px-2 py-1.5 text-xs"
            style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
            placeholder="key"
            value={memKey}
            onChange={(e) => setMemKey(e.target.value)}
          />
          <input
            className="flex-[2] rounded-lg border px-2 py-1.5 text-xs"
            style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
            placeholder="内容"
            value={memVal}
            onChange={(e) => setMemVal(e.target.value)}
          />
          <button
            className="rounded-lg px-3 py-1.5 text-xs font-medium"
            style={{ background: "var(--accent)", color: "#fff" }}
            onClick={onAddMem}
          >
            添加
          </button>
        </div>
      </div>

      {memoryItems.length === 0 ? (
        <div className="text-xs" style={{ color: "var(--ink-muted)" }}>
          还没有记忆。对话里说「记住 XX 是 YY」，Agent 就会记在这里。
        </div>
      ) : (
        <div className="space-y-2">
          {memoryItems.map((it) => (
            <div
              key={it.id}
              className="flex items-start gap-2 rounded-lg border p-2.5"
              style={{ border: "1px solid var(--border)", background: "var(--card)" }}
            >
              <div className="flex-1">
                <div className="text-xs font-medium" style={{ color: "var(--accent)" }}>{it.key}</div>
                <div className="mt-0.5 text-xs" style={{ color: "var(--ink-muted)" }}>{it.value}</div>
              </div>
              <button
                className="text-xs"
                style={{ color: "var(--red, #d64545)" }}
                onClick={() => onDeleteMem(it.id)}
              >
                删除
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PaneIntegrations({
  mcpServers,
  onAddMcp,
  onRemoveMcp,
  onUpdateMcp,
  plugins,
  pluginsDir,
}: {
  mcpServers: McpServer[];
  onAddMcp: () => void;
  onRemoveMcp: (idx: number) => void;
  onUpdateMcp: (idx: number, patch: Partial<McpServer>) => void;
  plugins: { name: string; tools?: string[]; error?: string }[];
  pluginsDir: string;
}) {
  return (
    <div className="space-y-5">
      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-medium">MCP 服务器</h3>
          <button
            className="rounded-lg border px-2.5 py-1 text-xs"
            style={{ border: "1px solid var(--border)", color: "var(--accent)" }}
            onClick={onAddMcp}
          >
            ＋ 添加
          </button>
        </div>
        {mcpServers.length === 0 ? (
          <div className="text-xs" style={{ color: "var(--ink-muted)" }}>
            暂无 MCP 服务器
          </div>
        ) : (
          <div className="space-y-2">
            {mcpServers.map((s, i) => (
              <div
                key={i}
                className="rounded-xl border p-3"
                style={{ border: "1px solid var(--border)", background: "var(--card)" }}
              >
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-medium">{s.name || `server-${i + 1}`}</span>
                  <button
                    className="text-xs"
                    style={{ color: "var(--red, #d64545)" }}
                    onClick={() => onRemoveMcp(i)}
                  >
                    删除
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    className="rounded-lg border px-2 py-1.5 text-xs"
                    style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
                    placeholder="名称"
                    value={s.name}
                    onChange={(e) => onUpdateMcp(i, { name: e.target.value })}
                  />
                  <select
                    className="rounded-lg border px-2 py-1.5 text-xs"
                    style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
                    value={s.transport || "stdio"}
                    onChange={(e) => onUpdateMcp(i, { transport: e.target.value })}
                  >
                    <option value="stdio">stdio</option>
                    <option value="http">http</option>
                  </select>
                </div>
                {(s.transport || "stdio") === "stdio" ? (
                  <>
                    <input
                      className="mt-2 w-full rounded-lg border px-2 py-1.5 text-xs"
                      style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
                      placeholder="命令（如 npx）"
                      value={s.command || ""}
                      onChange={(e) => onUpdateMcp(i, { command: e.target.value })}
                    />
                    <input
                      className="mt-1.5 w-full rounded-lg border px-2 py-1.5 text-xs"
                      style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
                      placeholder="参数（空格分隔）"
                      value={(s.args || []).join(" ")}
                      onChange={(e) =>
                        onUpdateMcp(i, { args: e.target.value.trim() ? e.target.value.trim().split(/\s+/) : [] })
                      }
                    />
                  </>
                ) : (
                  <input
                    className="mt-2 w-full rounded-lg border px-2 py-1.5 text-xs"
                    style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
                    placeholder="URL"
                    value={s.url || ""}
                    onChange={(e) => onUpdateMcp(i, { url: e.target.value })}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-sm font-medium">插件</h3>
        {plugins.length === 0 ? (
          <div className="text-xs" style={{ color: "var(--ink-muted)" }}>
            暂无插件。在 {pluginsDir || "~/.spark2/plugins"} 放一个 .py，重启生效。
          </div>
        ) : (
          <div className="space-y-1.5">
            {plugins.map((p, i) => (
              <div
                key={i}
                className="flex items-center justify-between rounded-lg border px-3 py-2 text-xs"
                style={{ border: "1px solid var(--border)", background: "var(--card)" }}
              >
                <span className="font-medium">{p.name}</span>
                {p.error ? (
                  <span style={{ color: "var(--red, #d64545)" }}>加载失败：{p.error}</span>
                ) : (
                  <span style={{ color: "var(--ink-muted)" }}>
                    工具：{p.tools?.join("、") || "（无）"}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PaneAdvanced({
  f,
  updateForm,
  token,
}: {
  f: any;
  updateForm: (key: any, val: any) => void;
  token: string;
}) {
  return (
    <div className="space-y-4">
      <Field label="访问令牌">
        <input
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          placeholder={token ? "已设置（输入新值可更换）" : "留空 = 本机免登录"}
          type="password"
          value=""
          onChange={(e) => updateForm("token_new", e.target.value)}
        />
      </Field>

      <Field label="系统提示词">
        <textarea
          className="w-full rounded-lg border px-3 py-2 text-sm"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          rows={3}
          value={f.system_prompt || ""}
          onChange={(e) => updateForm("system_prompt", e.target.value)}
          placeholder="自定义 Agent 人设…"
        />
      </Field>

      <Field label="成本单价 JSON（可选，覆盖默认价）">
        <textarea
          className="w-full rounded-lg border px-3 py-2 font-mono text-xs"
          style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
          rows={4}
          value={(f as any).pricing_str || ""}
          onChange={(e) => ((f as any).pricing_str = e.target.value)}
          placeholder='{"gpt-4o": {"prompt": 0.005, "completion": 0.015}}'
        />
      </Field>
    </div>
  );
}

function PaneAbout({ cfg, version }: { cfg: Cfg | null; version?: string }) {
  return (
    <div className="space-y-4">
      <div className="rounded-xl border p-4" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
        <div className="text-sm font-medium">Spark Agent</div>
        <div className="mt-1 text-xs" style={{ color: "var(--ink-muted)" }}>
          {version ? `v${version}` : "版本未知"}
        </div>
        <div className="mt-3 text-xs" style={{ color: "var(--ink-muted)" }}>
          全部数据在本地，仅你配置的模型服务发生网络调用。
        </div>
      </div>
      <div className="text-xs leading-relaxed" style={{ color: "var(--ink-muted)" }}>
        <p className="mb-1 font-medium" style={{ color: "var(--ink)" }}>特性</p>
        <ul className="ml-4 list-disc space-y-0.5">
          <li>多模型支持（OpenAI 兼容接口）</li>
          <li>工具调用与审批机制</li>
          <li>内置终端（PTY）</li>
          <li>MCP 协议集成</li>
          <li>长期记忆与跨会话知识</li>
          <li>Git 检查点与回滚</li>
        </ul>
      </div>
    </div>
  );
}

// ---------- Git / Usage drawers ----------

export function GitDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { sid, toast } = useApp();
  const [git, setGit] = useState<any>(null);
  const [cpMsg, setCpMsg] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (open) loadGit();
  }, [open]);

  async function loadGit() {
    setLoading(true);
    try {
      const d = await getGitStatus(sid || "");
      setGit(d);
    } catch {
      setGit({ repo: false, reason: "加载失败", checkpoints: [] });
    } finally {
      setLoading(false);
    }
  }

  async function doCheckpoint() {
    try {
      await gitCheckpoint(sid || "", cpMsg);
      setCpMsg("");
      toast("已存档");
      loadGit();
    } catch (e: any) {
      toast(e.message || "存档失败");
    }
  }

  async function doReset() {
    if (!confirm("回滚到最近一次存档？未存档的改动将丢失（不可撤销）。")) return;
    try {
      await gitReset(sid || "");
      toast("已回滚到最近存档");
      loadGit();
    } catch (e: any) {
      toast(e.message || "回滚失败");
    }
  }

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-40"
        style={{ background: "color-mix(in srgb, #101828 42%, transparent)" }}
        onClick={(e) => e.target === e.currentTarget && onClose()}
      />
      <div
        className="fixed z-50 flex h-full flex-col"
        style={{
          right: 0,
          top: 0,
          width: 400,
          maxWidth: "94vw",
          background: "var(--surface)",
          borderLeft: "1px solid var(--border)",
        }}
      >
        <div
          className="flex flex-none items-center justify-between border-b px-4 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <h2 className="text-sm font-semibold">检查点</h2>
          <button className="rounded-md p-1 text-sm" style={{ color: "var(--ink-muted)" }} onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 scrollbar-thin">
          {loading ? (
            <div className="text-xs" style={{ color: "var(--ink-muted)" }}>加载中…</div>
          ) : git?.repo ? (
            <>
              <div className="mb-3 flex flex-wrap gap-2">
                <span
                  className="rounded-full border px-2.5 py-0.5 text-[11px]"
                  style={{ border: "1px solid var(--border)", color: "var(--ink-muted)" }}
                >
                  分支 {git.branch}
                </span>
                <span
                  className="rounded-full border px-2.5 py-0.5 text-[11px]"
                  style={{
                    border: "1px solid var(--border)",
                    color: git.changes ? "var(--amber, #b26a00)" : "var(--green, #0e9d6e)",
                  }}
                >
                  {git.changes ? `${git.changes} 处未存档改动` : "工作区干净"}
                </span>
                <span className="text-[11px]" style={{ color: "var(--ink-muted)" }}>
                  {git.checkpoints?.length || 0} 个检查点
                </span>
              </div>

              <div className="mb-4 flex gap-2">
                <input
                  className="flex-1 rounded-lg border px-2 py-1.5 text-xs"
                  style={{ border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
                  placeholder="存档说明（可选）"
                  value={cpMsg}
                  onChange={(e) => setCpMsg(e.target.value)}
                />
                <button
                  className="rounded-lg px-3 py-1.5 text-xs font-medium"
                  style={{ background: "var(--accent)", color: "#fff" }}
                  onClick={doCheckpoint}
                >
                  存档
                </button>
              </div>

              {git.checkpoints?.length ? (
                <div className="space-y-1.5">
                  {git.checkpoints.map((c: any, i: number) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 rounded-lg border px-3 py-2 text-xs"
                      style={{
                        border: i === 0 ? "1px solid color-mix(in srgb, var(--accent) 40%, transparent)" : "1px solid var(--border)",
                        background: i === 0 ? "color-mix(in srgb, var(--accent) 6%, var(--card))" : "var(--card)",
                      }}
                    >
                      <div className="flex-1">
                        <div className="font-mono" style={{ color: "var(--accent)" }}>{(c.hash || "").slice(0, 8)}</div>
                        <div style={{ color: "var(--ink-muted)" }}>{c.message}</div>
                      </div>
                      {i === 0 && (
                        <button
                          className="text-xs"
                          style={{ color: "var(--red, #d64545)" }}
                          onClick={doReset}
                        >
                          回滚至此
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-xs" style={{ color: "var(--ink-muted)" }}>
                  还没有检查点，点上面「存档」。
                </div>
              )}
            </>
          ) : (
            <div className="text-xs leading-relaxed" style={{ color: "var(--ink-muted)" }}>
              {git?.reason || "工作目录不是 git 仓库，检查点未启用（在项目里执行 git init 即可开启）。"}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export function UsageDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { toast } = useApp();
  const [usage, setUsageState] = useState<UsageResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (open) loadUsage();
  }, [open]);

  async function loadUsage() {
    setLoading(true);
    try {
      const d = await getUsage();
      setUsageState(d);
    } catch {
      setUsageState(null);
    } finally {
      setLoading(false);
    }
  }

  if (!open) return null;

  const t = usage?.totals;
  const rows = usage?.top_sessions || [];

  return (
    <>
      <div
        className="fixed inset-0 z-40"
        style={{ background: "color-mix(in srgb, #101828 42%, transparent)" }}
        onClick={(e) => e.target === e.currentTarget && onClose()}
      />
      <div
        className="fixed z-50 flex h-full flex-col"
        style={{
          right: 0,
          top: 0,
          width: 380,
          maxWidth: "94vw",
          background: "var(--surface)",
          borderLeft: "1px solid var(--border)",
        }}
      >
        <div
          className="flex flex-none items-center justify-between border-b px-4 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <h2 className="text-sm font-semibold">用量统计</h2>
          <button className="rounded-md p-1 text-sm" style={{ color: "var(--ink-muted)" }} onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 scrollbar-thin">
          {loading ? (
            <div className="text-xs" style={{ color: "var(--ink-muted)" }}>加载中…</div>
          ) : !t?.calls ? (
            <div className="text-xs" style={{ color: "var(--ink-muted)" }}>
              还没有用量记录。每轮模型调用都会统计 tokens 与估算费用。
            </div>
          ) : (
            <>
              <div className="mb-4 space-y-1 text-sm">
                <div>
                  <b>{fmtTokens(t.total_tokens || 0)}</b> tokens
                  <span className="ml-2 text-xs" style={{ color: "var(--ink-muted)" }}>
                    （输入 {fmtTokens(t.prompt_tokens || 0)} / 输出 {fmtTokens(t.completion_tokens || 0)}）·{" "}
                    {t.calls} 次调用
                  </span>
                </div>
                <div>
                  估算费用：<b style={{ color: "var(--accent)" }}>¥{(t.est_cost || 0).toFixed(4)}</b>
                  <span className="ml-1 text-xs" style={{ color: "var(--ink-muted)" }}>
                    （近 {usage?.days || 30} 天）
                  </span>
                </div>
              </div>

              {rows.length > 0 && (
                <>
                  <div className="mb-2 text-xs font-medium" style={{ color: "var(--ink-muted)" }}>
                    会话排行
                  </div>
                  <div className="space-y-2">
                    {rows.map((r, i) => {
                      const max = Math.max(...rows.map((x) => x.est_cost || 0), 0.0001);
                      const w = Math.max(4, Math.round(((r.est_cost || 0) / max) * 100));
                      return (
                        <div key={i}>
                          <div className="mb-0.5 flex items-center justify-between text-[11px]">
                            <span>{(r.session_id || "").slice(0, 8)}</span>
                            <span>
                              {fmtTokens(r.total_tokens || 0)} · ¥{(r.est_cost || 0).toFixed(4)}
                            </span>
                          </div>
                          <div
                            className="h-1.5 rounded-full"
                            style={{ background: "var(--surface-2)" }}
                          >
                            <div
                              className="h-full rounded-full"
                              style={{ width: `${w}%`, background: "var(--accent)" }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

// ---------- Shared helpers ----------

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium" style={{ color: "var(--ink-muted)" }}>
        {label}
      </label>
      {children}
    </div>
  );
}
