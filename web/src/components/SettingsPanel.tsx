import { useEffect, useRef, useState } from "react"
import type { FullConfig, HookEntry, ModelProfile, ProbeResult, Status, McpServer, SettingsPayload } from "../types"
import {
  fetchConfig,
  fetchAgentsMd,
  saveAgentsMd,
  saveSettings,
  testConnection,
  listModelProfiles,
  saveModelProfile,
  deleteModelProfile,
  activateModelProfile,
} from "../api"
import MemorySection from "./MemorySection"
import Modal from "./Modal"
import { applyTheme, getStoredTheme, type Theme } from "../theme"
import { t, type TranslationKey } from "../i18n"
import { useUiStore } from "../store/uiStore"

interface Props {
  status: Status | null
  onClose: () => void
  onSaved: (s: Status) => void
}

const inputCls =
  "rounded-lg border border-spark-line bg-spark-bg px-3 py-2 text-sm text-spark-text outline-none focus:border-spark-accent"
const labelCls = "flex flex-col gap-1.5 text-[11px] tracking-wider text-spark-muted uppercase"
const sectionCls = "rounded-xl border border-spark-line p-4 flex flex-col gap-3"

type Tab = "model" | "agent" | "security" | "display" | "mcp" | "memory" | "project"

const tabKeys: { key: Tab; i18n: TranslationKey }[] = [
  { key: "model", i18n: "tab.model" },
  { key: "agent", i18n: "tab.agent" },
  { key: "security", i18n: "tab.security" },
  { key: "display", i18n: "tab.display" },
  { key: "memory", i18n: "tab.memory" },
  { key: "mcp", i18n: "tab.mcp" },
  { key: "project", i18n: "tab.project" },
]

const hookEventOptions: { key: string; label: string }[] = [
  { key: "pre_tool", label: "pre_tool — 工具调用前" },
  { key: "post_tool", label: "post_tool — 工具执行后" },
  { key: "turn_start", label: "turn_start — 每轮开始" },
  { key: "turn_end", label: "turn_end — 每轮结束" },
]

const displayOptions: { key: keyof FullConfig["agent"] & `show_${string}`; labelKey: TranslationKey; descKey: TranslationKey }[] = [
  { key: "show_thinking", labelKey: "display.thinking", descKey: "display.thinkingDesc" },
  { key: "show_tools", labelKey: "display.tools", descKey: "display.toolsDesc" },
  { key: "show_plan", labelKey: "display.plan", descKey: "display.planDesc" },
  { key: "show_context", labelKey: "display.context", descKey: "display.contextDesc" },
  { key: "show_keywords", labelKey: "display.keywords", descKey: "display.keywordsDesc" },
  { key: "show_notices", labelKey: "display.notices", descKey: "display.noticesDesc" },
]

export default function SettingsPanel({ status, onClose, onSaved }: Props) {
  const lang = useUiStore((s) => s.lang)
  const setLang = useUiStore((s) => s.setLang)
  const [tab, setTab] = useState<Tab>("model")
  const [cfg, setCfg] = useState<FullConfig | null>(null)
  const [mcpRows, setMcpRows] = useState<McpServer[]>([])
  const [agentsMd, setAgentsMd] = useState<{ filename: string; content: string; chars: number; max_fragment_chars: number } | null>(null)
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const [notice, setNotice] = useState("")
  const [mcpErrors, setMcpErrors] = useState<string[]>([])
  const [busy, setBusy] = useState<"save" | "test" | "profile" | null>(null)
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [showProfileForm, setShowProfileForm] = useState(false)
  const [profileForm, setProfileForm] = useState({ id: "", name: "", provider: "openai_compat", base_url: "", model: "", api_key: "" })
  const [protectedText, setProtectedText] = useState("")
  const [hookRows, setHookRows] = useState<HookEntry[]>([])
  const [theme, setTheme] = useState<Theme>(() => getStoredTheme())
  const dirtyRef = useRef({ cfg: false, mcp: false, md: false })

  function changeTheme(next: Theme) {
    setTheme(next)
    applyTheme(next)
  }

  async function refreshProfiles() {
    try {
      const data = await listModelProfiles()
      setProfiles(data.profiles)
    } catch (e) {
      setNotice(t('model.loadFailed', lang, { msg: e instanceof Error ? e.message : String(e) }))
    }
  }

  useEffect(() => {
    refreshProfiles()
    fetchConfig()
      .then((data) => {
        if (!dirtyRef.current.cfg) {
          setCfg(data)
          dirtyRef.current.mcp = dirtyRef.current.mcp || false
        }
        if (!dirtyRef.current.mcp) {
          setMcpRows(data.mcp_servers.map((s) => ({ ...s, args: [...s.args], readonly_tools: [...s.readonly_tools] })))
        }
        setProtectedText(data.agent.protected_paths?.join("\n") ?? "")
        setHookRows((data.hooks || []).map((h) => ({ ...h, args: [...(h.args || [])] })))
      })
      .catch((e) => setNotice(t('settings.loadFailed', lang, { msg: e instanceof Error ? e.message : String(e) })))
    fetchAgentsMd().then((d) => {
      if (!dirtyRef.current.md) setAgentsMd(d)
    }).catch((e) => setNotice(t('settings.memoryLoadFailed', lang, { msg: e instanceof Error ? e.message : String(e) })))
  }, [])

  function patchAgent(patch: Partial<FullConfig["agent"]>) {
    dirtyRef.current.cfg = true
    setCfg((c) => (c ? { ...c, agent: { ...c.agent, ...patch } } : c))
  }

  function patchMcp(i: number, patch: Partial<McpServer>) {
    dirtyRef.current.mcp = true
    setMcpRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }

  function patchHook(i: number, patch: Partial<HookEntry>) {
    dirtyRef.current.mcp = true
    setHookRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }

  function resetForm() {
    if (busy) return
    dirtyRef.current = { cfg: false, mcp: false, md: false }
    setProbe(null)
    setNotice("")
    fetchConfig()
      .then((data) => {
        setCfg(data)
        setProtectedText(data.agent.protected_paths?.join("\n") ?? "")
        setMcpRows(data.mcp_servers.map((s) => ({ ...s, args: [...s.args], readonly_tools: [...s.readonly_tools] })))
        setHookRows((data.hooks || []).map((h) => ({ ...h, args: [...(h.args || [])] })))
        setMcpErrors([])
      })
      .catch((e) => setNotice(t('settings.loadFailed', lang, { msg: e instanceof Error ? e.message : String(e) })))
    fetchAgentsMd().then(setAgentsMd).catch((e) => setNotice(t('settings.memoryLoadFailed', lang, { msg: e instanceof Error ? e.message : String(e) })))
  }

  async function handleSave() {
    if (!cfg) return
    setBusy("save")
    setProbe(null)
    setNotice("")
    setMcpErrors([])
    const payload: SettingsPayload = {
      provider: {
        name: cfg.provider.name,
        base_url: cfg.provider.base_url.trim(),
        model: cfg.provider.model.trim(),
      },
      agent: {
        approval: cfg.agent.approval,
        workdir_only: cfg.agent.workdir_only,
        sandbox_mode: cfg.agent.sandbox_mode || "workspace",
        protected_paths: protectedText.split("\n").map((s) => s.trim()).filter(Boolean),
        shell_timeout_sec: Number(cfg.agent.shell_timeout_sec) || 60,
        max_tool_rounds: Number(cfg.agent.max_tool_rounds) || 30,
        max_repeat_calls: Math.max(0, Number(cfg.agent.max_repeat_calls) || 0),
        max_turn_tokens: Math.max(0, Number(cfg.agent.max_turn_tokens) || 0),
        max_output_chars: Number(cfg.agent.max_output_chars) || 8000,
        show_thinking: cfg.agent.show_thinking,
        show_tools: cfg.agent.show_tools,
        show_plan: cfg.agent.show_plan,
        show_context: cfg.agent.show_context,
        show_keywords: cfg.agent.show_keywords,
        show_notices: cfg.agent.show_notices,
      },
      mcp_servers: mcpRows
        .filter((r) => r.name.trim() && r.command.trim())
        .map((r) => ({
          name: r.name.trim(),
          command: r.command.trim(),
          args: r.args.filter(Boolean),
          readonly_tools: r.readonly_tools.filter(Boolean),
        })),
      hooks: hookRows
        .filter((h) => h.event.trim() && h.command.trim())
        .map((h) => ({
          event: h.event.trim(),
          command: h.command.trim(),
          args: h.args.join(" ").trim() ? h.args.join(" ").trim().split(/\s+/) : [],
          name: h.name.trim(),
          timeout_sec: Math.max(1, Number(h.timeout_sec) || 15),
        })),
    }
    try {
      const data = await saveSettings(payload)
      onSaved(data.status)
      setCfg(data.config)
      setMcpErrors(data.mcp_errors || [])
      setMcpRows(data.config.mcp_servers.map((s) => ({ ...s, args: [...s.args], readonly_tools: [...s.readonly_tools] })))
      setNotice(t('settings.saved', lang))
    } catch (e) {
      setProbe({ ok: false, error: String(e instanceof Error ? e.message : e) })
    } finally {
      setBusy(null)
    }
  }

  async function handleSaveMemory() {
    if (!agentsMd) return
    try {
      const r = await saveAgentsMd(agentsMd.content)
      setAgentsMd({ ...agentsMd, chars: r.chars, max_fragment_chars: r.max_fragment_chars })
      setNotice(t('project.memorySaved', lang))
    } catch (e) {
      setProbe({ ok: false, error: String(e instanceof Error ? e.message : e) })
    }
  }

  return (
    <Modal labelledBy="settings-panel-title" onClose={onClose} className="animate-fade">
      <aside className="animate-in absolute top-0 right-0 flex h-full w-full flex-col border-l border-spark-line bg-spark-panel sm:w-[38rem]">
        <div className="flex items-center justify-between border-b border-spark-line px-4 py-3 sm:px-5">
          <h2 id="settings-panel-title" className="text-base font-bold">{t('settings.title', lang)}</h2>
          <div className="flex items-center gap-2">
            <button type="button" onClick={resetForm} title="放弃修改，恢复当前配置" className="rounded-lg bg-spark-line px-3 py-1.5 text-xs text-spark-muted transition-colors hover:text-spark-text">
              {t('common.reset', lang)}
            </button>
            <button data-modal-initial-focus type="button" onClick={onClose} className="rounded-lg bg-spark-line px-3 py-1.5 text-sm text-spark-text transition-colors hover:text-spark-accent">
              {t('common.close', lang)}
            </button>
          </div>
        </div>
        <div className="flex gap-1 overflow-x-auto border-b border-spark-line px-3 py-2 sm:px-4">
          {tabKeys.map((tk) => (
            <button
              key={tk.key}
              type="button"
              onClick={() => setTab(tk.key)}
              className={`shrink-0 rounded-lg px-3 py-1.5 text-xs font-bold transition-colors ${
                tab === tk.key ? "bg-spark-accent text-spark-on-accent" : "text-spark-muted hover:bg-spark-line hover:text-spark-text"
              }`}
            >
              {t(tk.i18n, lang)}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-x-hidden overflow-y-auto p-4 sm:p-5">
          {tab === "model" && (
            <div className="flex flex-col gap-4">
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('model.profiles', lang)}</div>
                <p className="text-xs text-spark-muted">{t('model.profilesDesc', lang)}</p>
                {profiles.length === 0 && <p className="text-xs text-spark-muted">{t('model.noProfiles', lang)}</p>}
                <div className="flex flex-col gap-2">
                  {profiles.map((p) => (
                    <div
                      key={p.id}
                      className={`flex flex-wrap items-center gap-2 rounded-lg border p-3 ${
                        p.active ? "border-spark-accent bg-spark-accent/10" : "border-spark-line bg-spark-bg"
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 text-sm font-bold text-spark-text">
                          <span className="truncate">{p.name}</span>
                          {p.active && <span className="shrink-0 rounded bg-spark-accent px-1.5 py-0.5 text-[10px] text-spark-on-accent">{t('model.active', lang)}</span>}
                        </div>
                        <div className="truncate text-xs text-spark-text">{p.model}</div>
                        <div className="truncate text-[11px] text-spark-muted">
                          {p.provider} · {p.base_url}
                          {p.has_api_key && ` · ${p.api_key_masked}`}
                        </div>
                      </div>
                      <div className="flex w-full shrink-0 gap-1.5 sm:w-auto">
                        {!p.active && (
                          <button
                            type="button"
                            disabled={busy !== null}
                            onClick={async () => {
                              setBusy("profile")
                              setNotice("")
                              try {
                                const r = await activateModelProfile(p.id)
                                onSaved(r.status)
                                await refreshProfiles()
                                setNotice(t('model.switched', lang, { name: p.name }))
                              } catch (e) {
                                setProbe({ ok: false, error: String(e instanceof Error ? e.message : e) })
                              } finally {
                                setBusy(null)
                              }
                            }}
                            className="flex-1 rounded-lg bg-spark-accent px-3 py-1.5 text-xs font-bold text-spark-on-accent hover:opacity-90 disabled:opacity-50 sm:flex-none"
                          >
                            {t('model.use', lang)}
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => {
                            setShowProfileForm(true)
                            setProfileForm({
                              id: p.id,
                              name: p.name,
                              provider: p.provider,
                              base_url: p.base_url,
                              model: p.model,
                              api_key: "",
                            })
                            setProbe(null)
                          }}
                          className="flex-1 rounded-lg bg-spark-line px-3 py-1.5 text-xs font-bold text-spark-text hover:opacity-80 sm:flex-none"
                        >
                          {t('common.edit', lang)}
                        </button>
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={async () => {
                            setBusy("profile")
                            try {
                              await deleteModelProfile(p.id)
                              await refreshProfiles()
                            } catch (e) {
                              setProbe({ ok: false, error: String(e instanceof Error ? e.message : e) })
                            } finally {
                              setBusy(null)
                            }
                          }}
                          className="flex-1 rounded-lg bg-spark-err/12 px-3 py-1.5 text-xs font-bold text-spark-err hover:opacity-80 disabled:opacity-50 sm:flex-none"
                        >
                          {t('common.delete', lang)}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                {!showProfileForm && (
                  <button
                    type="button"
                    onClick={() => {
                      setShowProfileForm(true)
                      setProfileForm({ id: "", name: "", provider: "openai_compat", base_url: "", model: "", api_key: "" })
                      setProbe(null)
                    }}
                    className="self-start rounded-lg bg-spark-line px-3 py-1.5 text-xs font-bold text-spark-text hover:opacity-80"
                  >
                    {t('model.add', lang)}
                  </button>
                )}
              </div>

              {showProfileForm && (
                <div className={sectionCls}>
                  <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">
                    {profileForm.id ? t('model.edit', lang) : t('model.new', lang)}
                  </div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <label className={labelCls}>
                      {t('model.displayName', lang)}
                      <input
                        value={profileForm.name}
                        onChange={(e) => setProfileForm((f) => ({ ...f, name: e.target.value }))}
                        placeholder={t('model.displayNamePh', lang)}
                        className={inputCls}
                      />
                    </label>
                    <label className={labelCls}>
                      {t('model.provider', lang)}
                      <select
                        value={profileForm.provider}
                        onChange={(e) => setProfileForm((f) => ({ ...f, provider: e.target.value }))}
                        className={inputCls}
                      >
                        <option value="openai_compat">openai_compat</option>
                        <option value="ollama">ollama</option>
                        <option value="mock">mock</option>
                      </select>
                    </label>
                    <label className={`${labelCls} sm:col-span-2`}>
                      {t('model.baseUrl', lang)}
                      <input
                        value={profileForm.base_url}
                        onChange={(e) => setProfileForm((f) => ({ ...f, base_url: e.target.value }))}
                        placeholder={t('model.baseUrlPh', lang)}
                        className={inputCls}
                      />
                    </label>
                    <label className={`${labelCls} sm:col-span-2`}>
                      {t('model.model', lang)}
                      <input
                        value={profileForm.model}
                        onChange={(e) => setProfileForm((f) => ({ ...f, model: e.target.value }))}
                        placeholder={t('model.modelPh', lang)}
                        className={inputCls}
                      />
                    </label>
                    <label className={`${labelCls} sm:col-span-2`}>
                      {t('model.apiKey', lang)}
                      <input
                        value={profileForm.api_key}
                        onChange={(e) => setProfileForm((f) => ({ ...f, api_key: e.target.value }))}
                        type="password"
                        placeholder={
                          profileForm.id
                            ? profiles.find((p) => p.id === profileForm.id)?.has_api_key
                              ? t('model.apiKeyPhEdit', lang, { masked: profiles.find((p) => p.id === profileForm.id)?.api_key_masked ?? "" })
                              : "your-api-key-here"
                            : t('model.apiKeyPhNew', lang)
                        }
                        className={inputCls}
                      />
                    </label>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <button
                      type="button"
                      disabled={busy !== null || !profileForm.base_url.trim() || !profileForm.model.trim()}
                      onClick={async () => {
                        setBusy("profile")
                        setNotice("")
                        try {
                          await saveModelProfile({
                            id: profileForm.id || undefined,
                            name: profileForm.name.trim(),
                            provider: profileForm.provider,
                            base_url: profileForm.base_url.trim(),
                            model: profileForm.model.trim(),
                            api_key: profileForm.api_key.trim() || undefined,
                          })
                          setShowProfileForm(false)
                          await refreshProfiles()
                          setNotice(t('model.saved', lang))
                        } catch (e) {
                          setProbe({ ok: false, error: String(e instanceof Error ? e.message : e) })
                        } finally {
                          setBusy(null)
                        }
                      }}
                      className="rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-spark-on-accent hover:opacity-90 disabled:opacity-50"
                    >
                      {t('model.saveProfile', lang)}
                    </button>
                    <button
                      type="button"
                      disabled={busy !== null || !profileForm.base_url.trim() || !profileForm.model.trim()}
                      onClick={async () => {
                        setBusy("test")
                        setProbe(null)
                        try {
                          const data = await testConnection({
                            base_url: profileForm.base_url.trim(),
                            model: profileForm.model.trim(),
                            api_key: profileForm.api_key.trim(),
                          })
                          setProbe(data)
                        } catch (e) {
                          setProbe({ ok: false, error: String(e instanceof Error ? e.message : e) })
                        } finally {
                          setBusy(null)
                        }
                      }}
                      className="rounded-lg bg-spark-line px-4 py-2 text-sm font-bold text-spark-text hover:opacity-80 disabled:opacity-50"
                    >
                      {busy === "test" ? t('model.testing', lang) : t('model.testConnection', lang)}
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowProfileForm(false)}
                      className="rounded-lg bg-spark-line px-4 py-2 text-sm font-bold text-spark-text hover:opacity-80"
                    >
                      {t('common.cancel', lang)}
                    </button>
                  </div>
                </div>
              )}

              {probe === null ? (
                <p className="text-xs text-spark-muted">{t('model.testDesc', lang)}</p>
              ) : (
                <div
                  className={`rounded-lg border px-3 py-2 font-mono text-xs whitespace-pre-wrap ${
                    probe.ok ? "border-spark-ok/50 text-spark-ok" : "border-spark-err/50 text-spark-err"
                  }`}
                >
                  {probe.ok ? `OK  ${probe.model}  ${probe.latency_ms}ms\n${probe.content}` : `FAIL  ${probe.error || "probe failed"}`}
                </div>
              )}
            </div>
          )}

          {tab === "agent" && (
            <div className={sectionCls}>
              <label className={labelCls}>
                {t('agent.approval', lang)}
                <select value={cfg?.agent.approval || "suggest"} onChange={(e) => patchAgent({ approval: e.target.value })} className={inputCls}>
                  <option value="suggest">{t('agent.suggest', lang)}</option>
                  <option value="auto-edit">{t('agent.autoEdit', lang)}</option>
                  <option value="full-auto">{t('agent.fullAuto', lang)}</option>
                </select>
              </label>
              <label className="flex items-center gap-2 text-sm text-spark-text">
                <input type="checkbox" checked={cfg?.agent.workdir_only ?? true} onChange={(e) => patchAgent({ workdir_only: e.target.checked })} className="h-4 w-4 accent-teal-300" />
                {t('agent.workdirOnly', lang)}
              </label>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <label className={labelCls}>
                  {t('agent.maxToolRounds', lang)}
                  <input type="number" min={1} value={cfg?.agent.max_tool_rounds ?? 30} onChange={(e) => patchAgent({ max_tool_rounds: Number(e.target.value) })} className={inputCls} />
                </label>
                <label className={labelCls}>
                  {t('agent.shellTimeout', lang)}
                  <input type="number" min={1} value={cfg?.agent.shell_timeout_sec ?? 60} onChange={(e) => patchAgent({ shell_timeout_sec: Number(e.target.value) })} className={inputCls} />
                </label>
                <label className={labelCls}>
                  {t('agent.maxOutput', lang)}
                  <input type="number" min={200} value={cfg?.agent.max_output_chars ?? 8000} onChange={(e) => patchAgent({ max_output_chars: Number(e.target.value) })} className={inputCls} />
                </label>
              </div>
              <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('agent.guardrails', lang)}</div>
              <p className="text-xs text-spark-muted">{t('agent.guardrailsDesc', lang)}</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className={labelCls}>
                  {t('agent.repeatLimit', lang)}
                  <input type="number" min={0} value={cfg?.agent.max_repeat_calls ?? 4} onChange={(e) => patchAgent({ max_repeat_calls: Math.max(0, Number(e.target.value) || 0) })} className={inputCls} />
                </label>
                <label className={labelCls}>
                  {t('agent.tokenBudget', lang)}
                  <input type="number" min={0} step={1000} value={cfg?.agent.max_turn_tokens ?? 0} onChange={(e) => patchAgent({ max_turn_tokens: Math.max(0, Number(e.target.value) || 0) })} className={inputCls} />
                </label>
              </div>
              <button type="button" onClick={handleSave} disabled={busy !== null} className="self-start rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-spark-on-accent hover:opacity-90 disabled:opacity-50">
                {busy === "save" ? t('settings.saving', lang) : t('common.save', lang)}
              </button>
            </div>
          )}

          {tab === "security" && (
            <div className="flex flex-col gap-4">
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('security.accessLevel', lang)}</div>
                <p className="text-xs text-spark-muted">{t('security.accessDesc', lang)}</p>
                <div className="flex flex-col gap-2">
                  {([
                    { key: "sandbox-only", title: t('security.sandboxOnly', lang), desc: t('security.sandboxOnlyDesc', lang) },
                    { key: "workspace", title: t('security.workspace', lang), desc: t('security.workspaceDesc', lang) },
                    { key: "full-access", title: t('security.fullAccess', lang), desc: t('security.fullAccessDesc', lang) },
                    { key: "unrestricted", title: t('security.unrestricted', lang), desc: t('security.unrestrictedDesc', lang) },
                  ] as const).map((opt) => (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => patchAgent({ sandbox_mode: opt.key })}
                      className={`rounded-lg border p-3 text-left transition-colors ${
                        (cfg?.agent.sandbox_mode || "workspace") === opt.key
                          ? opt.key === "unrestricted"
                            ? "border-spark-err bg-spark-err/12"
                            : "border-spark-accent bg-spark-accent/10"
                          : "border-spark-line bg-spark-bg hover:border-spark-muted"
                      }`}
                    >
                      <div className="flex items-center gap-2 text-sm font-bold text-spark-text">
                        <span className={`h-2 w-2 rounded-full ${
                          (cfg?.agent.sandbox_mode || "workspace") === opt.key
                            ? opt.key === "unrestricted" ? "bg-red-500" : "bg-spark-accent"
                            : "bg-spark-line"
                        }`} />
                        {opt.title}
                        {opt.key === "unrestricted" && <span className="rounded bg-spark-err/20 px-1.5 py-0.5 text-[10px] text-spark-err">{t('security.danger', lang)}</span>}
                      </div>
                      <p className="mt-1 text-xs text-spark-muted">{opt.desc}</p>
                    </button>
                  ))}
                </div>
              </div>
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('security.protectedPaths', lang)}</div>
                <p className="text-xs text-spark-muted">
                  {t('security.protectedPathsDesc', lang)}
                </p>
                <textarea
                  value={protectedText}
                  onChange={(e) => setProtectedText(e.target.value)}
                  rows={5}
                  placeholder={"/opt/production-config\n/home/me/secrets"}
                  className={`${inputCls} resize-y font-mono text-xs leading-relaxed`}
                />
              </div>
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('security.envProbe', lang)}</div>
                <p className="text-xs text-spark-muted">{t('security.envProbeDesc', lang)}</p>
                {status?.env && Object.keys(status.env).length > 0 ? (
                  <dl className="flex flex-col gap-1.5 text-xs">
                    {Object.entries(status.env).map(([k, v]) => (
                      <div key={k} className="flex items-baseline justify-between gap-3">
                        <dt className="shrink-0 text-spark-muted">{k}</dt>
                        <dd className="truncate font-mono text-spark-text">{v}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="text-xs text-spark-muted">{t('security.noProbeData', lang)}</p>
                )}
              </div>
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('security.hooks', lang)}</div>
                <p className="text-xs text-spark-muted">
                  {t('security.hooksDesc', lang)}
                </p>
                <div className="flex flex-col gap-2">
                  {hookRows.map((h, i) => (
                    <div key={i} className="flex flex-col gap-2 rounded-lg border border-spark-line bg-spark-panel p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <select value={h.event} onChange={(e) => patchHook(i, { event: e.target.value })} className={`${inputCls} w-auto flex-1`}>
                          {hookEventOptions.map((opt) => (
                            <option key={opt.key} value={opt.key}>{opt.label}</option>
                          ))}
                        </select>
                        <input type="number" min={1} value={h.timeout_sec} onChange={(e) => patchHook(i, { timeout_sec: Math.max(1, Number(e.target.value) || 1) })} className={`${inputCls} w-24`} title={t('agent.shellTimeout', lang)} />
                        <button type="button" onClick={() => setHookRows((rows) => rows.filter((_, idx) => idx !== i))} className="rounded border border-spark-err/45 px-2 py-1 text-xs text-spark-err hover:bg-spark-err/12">{t('common.delete', lang)}</button>
                      </div>
                      <input value={h.command} onChange={(e) => patchHook(i, { command: e.target.value })} placeholder={t('mcp.commandPh', lang)} className={`${inputCls} font-mono text-xs`} />
                      <input value={h.args.join(" ")} onChange={(e) => patchHook(i, { args: e.target.value.trim() ? e.target.value.trim().split(/\s+/) : [] })} placeholder={t('mcp.argsPh', lang)} className={`${inputCls} font-mono text-xs`} />
                      <input value={h.name} onChange={(e) => patchHook(i, { name: e.target.value })} placeholder={t('common.name', lang)} className={`${inputCls} text-xs`} />
                    </div>
                  ))}
                  <button type="button" onClick={() => setHookRows((rows) => [...rows, { event: "pre_tool", command: "", args: [], name: "", timeout_sec: 15 }])} className="self-start rounded border border-spark-line px-3 py-1.5 text-xs text-spark-text hover:bg-spark-line">
                    {t('security.addHook', lang)}
                  </button>
                </div>
              </div>
              <button type="button" onClick={handleSave} disabled={busy !== null} className="self-start rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-spark-on-accent hover:opacity-90 disabled:opacity-50">
                {busy === "save" ? t('settings.saving', lang) : t('agent.saveApproval', lang)}
              </button>
            </div>
          )}

          {tab === "memory" && <MemorySection />}

          {tab === "display" && (
            <div className="flex flex-col gap-4">
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('display.theme', lang)}</div>
                <p className="text-xs text-spark-muted">{t('display.themeDesc', lang)}</p>
                <div className="grid grid-cols-2 gap-2">
                  {([
                    { key: "light" as const, label: t('display.light', lang), desc: t('display.lightDesc', lang) },
                    { key: "dark" as const, label: t('display.dark', lang), desc: t('display.darkDesc', lang) },
                  ]).map((opt) => (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => changeTheme(opt.key)}
                      className={`rounded-lg border p-3 text-left transition-colors ${
                        theme === opt.key
                          ? "border-spark-accent bg-spark-accent/10"
                          : "border-spark-line bg-spark-bg hover:border-spark-muted"
                      }`}
                    >
                      <div className="flex items-center gap-2 text-sm font-bold text-spark-text">
                        <span
                          className={`h-3 w-3 rounded-full border ${
                            theme === opt.key ? "border-spark-accent bg-spark-accent" : "border-spark-line bg-spark-panel"
                          }`}
                        />
                        {opt.label}
                      </div>
                      <p className="mt-1 text-xs text-spark-muted">{opt.desc}</p>
                    </button>
                  ))}
                </div>
              </div>
              <div className={sectionCls}>
                <div className="text-[11px] font-semibold text-spark-accent uppercase tracking-wider">
                  {t('display.language', lang)}
                </div>
                <div className="flex gap-2">
                  {(["zh", "en"] as const).map((l) => (
                    <button
                      key={l}
                      onClick={() => setLang(l)}
                      className={`rounded-lg px-3 py-1.5 text-sm transition ${
                        lang === l
                          ? "bg-spark-accent text-spark-on-accent"
                          : "border border-spark-line hover:border-spark-accent"
                      }`}
                    >
                      {l === "zh" ? "中文" : "English"}
                    </button>
                  ))}
                </div>
              </div>
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('display.toggles', lang)}</div>
                <p className="text-xs text-spark-muted">{t('display.togglesDesc', lang)}</p>
                <div className="flex flex-col gap-2">
                  {displayOptions.map((opt) => {
                    const checked = (cfg?.agent[opt.key] as boolean | undefined) ?? true
                    return (
                      <label
                        key={opt.key}
                        className="flex cursor-pointer items-start gap-3 rounded-lg border border-spark-line bg-spark-bg p-3 hover:border-spark-muted"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => patchAgent({ [opt.key]: e.target.checked })}
                          className="mt-0.5 h-4 w-4 shrink-0 accent-teal-300"
                        />
                        <span className="min-w-0">
                          <span className="block text-sm font-bold text-spark-text">{t(opt.labelKey, lang)}</span>
                          <span className="block text-xs text-spark-muted">{t(opt.descKey, lang)}</span>
                        </span>
                      </label>
                    )
                  })}
                </div>
              </div>
              <button type="button" onClick={handleSave} disabled={busy !== null} className="self-start rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-spark-on-accent hover:opacity-90 disabled:opacity-50">
                {busy === "save" ? t('settings.saving', lang) : t('display.save', lang)}
              </button>
            </div>
          )}

          {tab === "mcp" && (
            <div className="flex flex-col gap-3">
              <div className={sectionCls}>
                <p className="text-xs text-spark-muted">{t('mcp.desc', lang)}</p>
                {mcpRows.length === 0 && <p className="text-xs text-spark-muted">{t('mcp.empty', lang)}</p>}
                {mcpRows.map((row, i) => (
                  <div key={i} className="flex flex-col gap-2 rounded-lg border border-spark-line bg-spark-bg p-3">
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <input value={row.name} onChange={(e) => patchMcp(i, { name: e.target.value })} placeholder={t('common.name', lang)} className={inputCls} />
                      <input value={row.command} onChange={(e) => patchMcp(i, { command: e.target.value })} placeholder={t('mcp.commandPh', lang)} className={inputCls} />
                    </div>
                    <input value={row.args.join(" ")} onChange={(e) => patchMcp(i, { args: e.target.value.split(" ") })} placeholder={t('mcp.argsPh', lang)} className={inputCls} />
                    <input value={row.readonly_tools.join(" ")} onChange={(e) => patchMcp(i, { readonly_tools: e.target.value.split(" ") })} placeholder={t('mcp.readonlyPh', lang)} className={inputCls} />
                    <button type="button" onClick={() => setMcpRows((rows) => rows.filter((_, idx) => idx !== i))} className="self-start rounded-lg bg-spark-err/12 px-3 py-1.5 text-xs font-bold text-spark-err hover:opacity-80">
                      {t('common.remove', lang)}
                    </button>
                  </div>
                ))}
                <button type="button" onClick={() => setMcpRows((rows) => [...rows, { name: "", command: "", args: [], readonly_tools: [] }])} className="self-start rounded-lg bg-spark-line px-3 py-1.5 text-xs font-bold text-spark-text hover:opacity-80">
                  {t('mcp.add', lang)}
                </button>
                {mcpErrors.length > 0 && <div className="rounded-lg border border-spark-err/45 px-3 py-2 text-xs text-spark-err">{mcpErrors.join("\n")}</div>}
                <button type="button" onClick={handleSave} disabled={busy !== null} className="self-start rounded-lg bg-spark-line px-4 py-2 text-sm font-bold text-spark-text hover:opacity-80 disabled:opacity-50">
                  {busy === "save" ? t('settings.saving', lang) : t('common.save', lang)}
                </button>
              </div>
            </div>
          )}

          {tab === "project" && (
            <div className="flex flex-col gap-4">
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('project.memoryTitle', lang)}</div>
                <p className="text-xs text-spark-muted">{t('project.memoryDesc', lang)}</p>
                <textarea
                  value={agentsMd?.content ?? ""}
                  onChange={(e) => {
                    dirtyRef.current.md = true
                    setAgentsMd((m) => (m ? { ...m, content: e.target.value } : m))
                  }}
                  placeholder={t('project.memoryPh', lang)}
                  rows={10}
                  className={`${inputCls} resize-y font-mono text-xs leading-relaxed`}
                />
                <div className="flex items-center justify-between">
                  <span className={`text-xs ${(agentsMd?.chars || 0) > (agentsMd?.max_fragment_chars || 8000) ? "text-spark-err" : "text-spark-muted"}`}>
                    {t('project.charCount', lang, { current: agentsMd?.chars || 0, max: agentsMd?.max_fragment_chars || 8000 })}
                  </span>
                  <button type="button" onClick={handleSaveMemory} className="rounded-lg bg-spark-line px-3 py-1.5 text-xs font-bold text-spark-text hover:opacity-80">
                    {t('project.saveMemory', lang)}
                  </button>
                </div>
              </div>
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('project.contextMgmt', lang)}</div>
                <p className="text-xs text-spark-muted">{t('project.contextDesc', lang)}</p>
                <dl className="flex flex-col gap-1.5 text-xs">
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-spark-muted">{t('project.compactThreshold', lang)}</dt>
                    <dd className="text-spark-text">{cfg?.context.compact_threshold ? `${Math.round(cfg.context.compact_threshold * 100)}%` : "—"}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-spark-muted">{t('project.contextBudget', lang)}</dt>
                    <dd className="text-spark-text">{cfg?.context.max_context_tokens ?? "—"} tokens</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-spark-muted">{t('project.keepRecent', lang)}</dt>
                    <dd className="text-spark-text">{t('project.messages', lang, { n: cfg?.context.keep_recent_messages ?? 8 })}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-spark-muted">{t('project.fragmentLimit', lang)}</dt>
                    <dd className="text-spark-text">{t('project.chars', lang, { n: cfg?.context.max_fragment_chars ?? 0 })}</dd>
                  </div>
                </dl>
              </div>
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">{t('project.workdir', lang)}</div>
                <code className="self-start rounded bg-spark-bg px-2 py-1 text-xs break-all text-spark-text">{cfg?.workdir || status?.workdir}</code>
              </div>
            </div>
          )}
        </div>
        {notice && (
          <div className="mx-5 mb-3 rounded-lg border border-spark-ok/40 px-3 py-2 text-xs text-spark-ok">{notice}</div>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-spark-line px-5 py-2.5 text-[11px] text-spark-muted">
          <span className="truncate">
            {t('status.model', lang)} <b className="font-medium text-spark-text">{status?.model}</b>
          </span>
          <span>
            {t('status.approval', lang)} <b className="font-medium text-spark-text">{status?.approval}</b>
          </span>
          <span className="ml-auto hidden font-mono text-[10px] sm:inline">session {status?.session_id}</span>
        </div>
      </aside>
    </Modal>
  )
}
