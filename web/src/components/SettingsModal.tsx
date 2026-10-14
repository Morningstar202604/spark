import { useEffect, useRef, useState } from "react"
import type { FullConfig, HookEntry, McpServer, ModelProfile, ProbeResult, SettingsPayload, Status } from "../types"
import { fetchConfig, fetchAgentsMd, saveAgentsMd, saveSettings, testConnection, listModelProfiles, saveModelProfile, deleteModelProfile, activateModelProfile } from "../api"
import MemorySection from "./MemorySection"
import { McpRegistryModal } from "./McpRegistryModal"
import type { McpRegistryItem } from "../api"
import { applyTheme, getStoredTheme, type Theme } from "../theme"
import { Badge, Button, Field, IconButton, Select, Spinner, TextArea, TextInput, Toggle, cls } from "./ui"
import { IconBrain, IconCheck, IconGlobe, IconKey, IconLayers, IconMoon, IconPlus, IconSettings, IconSun, IconTerminal, IconTrash, IconX } from "./icons"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { t, type TranslationKey } from "../i18n"

type Tab = "model" | "agent" | "security" | "display" | "memory" | "mcp" | "project"

// 预装模型预设：国内可用优先，用户选一个自动填 provider/base_url/model，只需填 API Key
const MODEL_PRESETS: { labelKey: TranslationKey; provider: string; base_url: string; model: string }[] = [
  { labelKey: "preset.deepseek", provider: "openai_compat", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { labelKey: "preset.qwen", provider: "openai_compat", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { labelKey: "preset.kimi", provider: "openai_compat", base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  { labelKey: "preset.glm", provider: "openai_compat", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash" },
  { labelKey: "preset.doubao", provider: "openai_compat", base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-pro-32k" },
  { labelKey: "preset.agnes", provider: "openai_compat", base_url: "https://apihub.agnes-ai.com/v1", model: "agnes-2.5-flash" },
  { labelKey: "preset.ollama", provider: "ollama", base_url: "http://localhost:11434/v1", model: "qwen2.5-coder" },
  { labelKey: "preset.mock", provider: "mock", base_url: "http://localhost", model: "mock-model" },
]

const TABS: { key: Tab; labelKey: TranslationKey; icon: React.ReactNode }[] = [
  { key: "model", labelKey: "tab.model", icon: <IconLayers size={14} /> },
  { key: "agent", labelKey: "tab.agent", icon: <IconTerminal size={14} /> },
  { key: "security", labelKey: "tab.security", icon: <IconKey size={14} /> },
  { key: "display", labelKey: "tab.display", icon: <IconSun size={14} /> },
  { key: "memory", labelKey: "tab.memory", icon: <IconBrain size={14} /> },
  { key: "mcp", labelKey: "tab.mcp", icon: <IconGlobe size={14} /> },
  { key: "project", labelKey: "tab.project", icon: <IconSettings size={14} /> },
]

interface Props {
  onClose: () => void
  onSaved: (s: Status) => void
}
export default function SettingsDrawer({ onClose, onSaved }: Props) {
  const lang = useUiStore((s) => s.lang)
  const setLang = useUiStore((s) => s.setLang)
  const [tab, setTab] = useState<Tab>("model")
  const [cfg, setCfg] = useState<FullConfig | null>(null)
  const [mcpRows, setMcpRows] = useState<McpServer[]>([])
  const [agentsMd, setAgentsMd] = useState<{ filename: string; content: string; chars: number; max_fragment_chars: number } | null>(null)
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState<"save" | "test" | "profile" | null>(null)
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [showProfileForm, setShowProfileForm] = useState(false)
  const [profileForm, setProfileForm] = useState({ id: "", name: "", provider: "openai_compat", base_url: "", model: "", api_key: "" })
  const [protectedText, setProtectedText] = useState("")
  const [hookRows, setHookRows] = useState<HookEntry[]>([])
  const [theme, setTheme] = useState<Theme>(() => getStoredTheme())
  const [showRegistry, setShowRegistry] = useState(false)
  const dirtyRef = useRef({ cfg: false, mcp: false, md: false })

  function handleRegistryPick(item: McpRegistryItem) {
    if (!item.suggested_command) return
    dirtyRef.current.mcp = true
    const derived = (item.name || item.title || "mcp").split("/").pop() || "mcp"
    setMcpRows((rows) => [
      ...rows,
      {
        name: derived.replace(/[^a-zA-Z0-9_-]/g, "-"),
        command: item.suggested_command!,
        args: [...item.suggested_args],
        readonly_tools: [],
      },
    ])
    setShowRegistry(false)
  }

  function patchAgent(patch: Partial<FullConfig["agent"]>) {
    dirtyRef.current.cfg = true
    setCfg((c) => (c ? { ...c, agent: { ...c.agent, ...patch } } : c))
  }

  function patchMcp(i: number, patch: Partial<McpServer>) {
    dirtyRef.current.mcp = true
    setMcpRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }

  async function refreshProfiles() {
    try {
      const data = await listModelProfiles()
      setProfiles(data.profiles)
    } catch (e) {
      setNotice(t("model.loadFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    }
  }

  useEffect(() => {
    refreshProfiles()
    fetchConfig()
      .then((data) => {
        if (!dirtyRef.current.cfg) setCfg(data)
        if (!dirtyRef.current.mcp) setMcpRows(data.mcp_servers.map((s) => ({ ...s, args: [...s.args], readonly_tools: [...s.readonly_tools] })))
        setProtectedText(data.agent.protected_paths?.join("\n") ?? "")
        setHookRows((data.hooks || []).map((h) => ({ ...h, args: [...(h.args || [])] })))
      })
      .catch((e) => setNotice(t("settings.loadFailed", lang, { msg: e instanceof Error ? e.message : String(e) })))
    fetchAgentsMd()
      .then((d) => {
        if (!dirtyRef.current.md) setAgentsMd(d)
      })
      .catch((e) => setNotice(t("settings.memoryLoadFailed", lang, { msg: e instanceof Error ? e.message : String(e) })))
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  async function handleSave() {
    if (!cfg) return
    setBusy("save")
    setNotice("")
    try {
      const payload: SettingsPayload = {
        provider: {
          name: cfg.provider.name,
          base_url: cfg.provider.base_url,
          model: cfg.provider.model,
          api_key: cfg.provider.api_key_env ? "" : undefined,
        },
        agent: {
          approval: cfg.agent.approval,
          workdir_only: cfg.agent.workdir_only,
          sandbox_mode: cfg.agent.sandbox_mode,
          protected_paths: protectedText.split("\n").map((s) => s.trim()).filter(Boolean),
          shell_timeout_sec: cfg.agent.shell_timeout_sec,
          max_tool_rounds: cfg.agent.max_tool_rounds,
          max_repeat_calls: cfg.agent.max_repeat_calls,
          max_turn_tokens: cfg.agent.max_turn_tokens,
          max_output_chars: cfg.agent.max_output_chars,
          show_thinking: cfg.agent.show_thinking,
          show_tools: cfg.agent.show_tools,
          show_plan: cfg.agent.show_plan,
          show_context: cfg.agent.show_context,
          show_keywords: cfg.agent.show_keywords,
          show_notices: cfg.agent.show_notices,
          effort: cfg.agent.effort,
          system_prompt: cfg.agent.system_prompt ?? "",
        },
        mcp_servers: mcpRows,
        hooks: hookRows,
      }
      const data = await saveSettings(payload)
      setNotice(t("settings.saved", lang))
      dirtyRef.current.cfg = false
      dirtyRef.current.mcp = false
      onSaved(data.status)
      useSessionStore.getState().refreshSessions()
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function handleTest() {
    setBusy("test")
    setProbe(null)
    setNotice("")
    try {
      const res = await testConnection({ base_url: cfg!.provider.base_url, model: cfg!.provider.model, api_key: "" })
      setProbe(res)
    } catch (e) {
      setNotice(t("settings.testFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function handleSaveProfile() {
    setBusy("profile")
    setNotice("")
    try {
      const res = await saveModelProfile({
        id: profileForm.id || undefined,
        name: profileForm.name,
        provider: profileForm.provider,
        base_url: profileForm.base_url,
        model: profileForm.model,
        api_key: profileForm.api_key || undefined,
      })
      await refreshProfiles()
      const act = await activateModelProfile(res.id)
      onSaved(act.status)
      setShowProfileForm(false)
      setProfileForm({ id: "", name: "", provider: "openai_compat", base_url: "", model: "", api_key: "" })
      setNotice(t("model.saved", lang))
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function handleActivate(p: ModelProfile) {
    setBusy("profile")
    try {
      const act = await activateModelProfile(p.id)
      onSaved(act.status)
      await refreshProfiles()
      setNotice(t("model.switched", lang, { name: p.name }))
    } catch (e) {
      setNotice(t("settings.switchFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function handleDeleteProfile(id: string) {
    setBusy("profile")
    try {
      await deleteModelProfile(id)
      await refreshProfiles()
    } catch (e) {
      setNotice(t("settings.deleteFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function handleSaveMd() {
    if (!agentsMd) return
    setBusy("save")
    try {
      const res = await saveAgentsMd(agentsMd.content)
      setAgentsMd((a) => (a ? { ...a, chars: res.chars, max_fragment_chars: res.max_fragment_chars } : a))
      dirtyRef.current.md = false
      setNotice(t("project.memorySaved", lang))
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  if (!cfg) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={onClose}>
        <div className="flex h-[min(92vh,680px)] w-[min(96vw,920px)] items-center justify-center rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]">
          <Spinner size={20} className="text-accent" />
        </div>
      </div>
    )
  }

  const displayKeys: { key: keyof FullConfig["agent"] & `show_${string}`; label: string }[] = [
    { key: "show_thinking", label: t("display.thinking", lang) },
    { key: "show_tools", label: t("display.tools", lang) },
    { key: "show_plan", label: t("display.plan", lang) },
    { key: "show_context", label: t("display.context", lang) },
    { key: "show_keywords", label: t("display.keywords", lang) },
    { key: "show_notices", label: t("display.notices", lang) },
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={onClose}>
      <aside
        className="anim-fade flex h-[min(92vh,680px)] w-[min(96vw,920px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={t("settings.title", lang)}
      >
        <div className="flex h-full">
          {/* 左导航（桌面） */}
          <nav className="hidden w-[200px] shrink-0 flex-col border-r border-line bg-sidebar sm:flex">
            <div className="flex items-center gap-2 px-4 py-3.5">
              <IconSettings size={16} className="text-accent" />
              <span className="text-[14px] font-semibold text-ink">{t("settings.title", lang)}</span>
            </div>
            <div className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 py-1">
              {TABS.map((tb) => (
                <button
                  key={tb.key}
                  type="button"
                  onClick={() => setTab(tb.key)}
                  className={cls(
                    "flex items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] font-medium transition-colors",
                    tab === tb.key ? "bg-selected text-ink" : "text-ink2 hover:bg-hover",
                  )}
                >
                  <span className={cls("shrink-0", tab === tb.key ? "text-accent" : "text-ink3")}>{tb.icon}</span>
                  {t(tb.labelKey, lang)}
                  {tab === tb.key && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-accent" />}
                </button>
              ))}
            </div>
            <div className="px-3 pb-3 pt-1">
              <div className="rounded-lg border border-line bg-panel2 px-3 py-2">
                <p className="text-[10px] font-bold tracking-wide text-ink3">SPARK</p>
                <p className="mt-0.5 text-[11px] leading-snug text-ink2">{t("settings.brandDesc", lang)}</p>
              </div>
            </div>
          </nav>

          {/* 右栏 */}
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b border-line px-4 py-3">
              <span className="text-sm font-semibold text-ink">{t("settings.title", lang)}</span>
              <IconButton title={t("common.close", lang)} onClick={onClose} className="ml-auto">
                <IconX size={15} />
              </IconButton>
            </div>

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto p-4">
          {notice && (
            <div className="mb-3 flex items-start justify-between gap-2 rounded-lg border border-line bg-panel2 px-3 py-2 text-xs text-ink2">
              <span className="break-words">{notice}</span>
              <button type="button" onClick={() => setNotice("")} className="shrink-0 text-ink3 hover:text-ink">
                <IconX size={12} />
              </button>
            </div>
          )}

          {tab === "model" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">{t("settings.currentModel", lang)}</h3>
                  <Button size="sm" variant="outline" onClick={handleTest} disabled={busy === "test"}>
                    {busy === "test" ? <Spinner size={11} /> : t("model.testConnection", lang)}
                  </Button>
                </div>
                <Field label={t("model.provider", lang)}>
                  <Select value={cfg.provider.name} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, provider: { ...c.provider, name: e.target.value } } : c)) }}>
                    <option value="openai_compat">openai_compat</option>
                    <option value="ollama">ollama</option>
                  </Select>
                </Field>
                <Field label={t("settings.presets", lang)}>
                  <Select
                    value=""
                    onChange={(e) => {
                      const p = MODEL_PRESETS.find((x) => x.labelKey === e.target.value)
                      if (!p) return
                      dirtyRef.current.cfg = true
                      setCfg((c) => (c ? { ...c, provider: { ...c.provider, name: p.provider, base_url: p.base_url, model: p.model } } : c))
                      setNotice(t("settings.presetFilled", lang, { name: t(p.labelKey, lang) }))
                    }}
                  >
                    <option value="">{t("settings.selectPreset", lang)}</option>
                    {MODEL_PRESETS.map((p) => (
                      <option key={p.labelKey} value={p.labelKey}>{t(p.labelKey, lang)}</option>
                    ))}
                  </Select>
                </Field>
                <Field label={t("model.baseUrl", lang)}>
                  <TextInput value={cfg.provider.base_url} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, provider: { ...c.provider, base_url: e.target.value } } : c)) }} placeholder={t("model.baseUrlPh", lang)} />
                </Field>
                <Field label={t("model.model", lang)}>
                  <TextInput value={cfg.provider.model} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, provider: { ...c.provider, model: e.target.value } } : c)) }} placeholder={t("model.modelPh", lang)} />
                </Field>
                <Field label={t("settings.effort", lang)} hint={t("settings.effortDesc", lang)}>
                  <Select value={cfg.agent.effort ?? "medium"} onChange={(e) => patchAgent({ effort: e.target.value as "low" | "medium" | "high" })}>
                    <option value="low">{t("settings.effortLow", lang)}</option>
                    <option value="medium">{t("settings.effortMedium", lang)}</option>
                    <option value="high">{t("settings.effortHigh", lang)}</option>
                  </Select>
                </Field>
                {probe && (
                  <div className={cls("rounded-lg border px-3 py-2 text-xs", probe.ok ? "border-ok/40 bg-ok/8 text-ok" : "border-err/40 bg-err/8 text-err")}>
                    {probe.ok ? t("settings.testOk", lang, { latency: probe.latency_ms ?? 0, model: probe.model ?? "" }) : t("settings.testFail", lang, { error: probe.error ?? "" })}
                  </div>
                )}
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">{t("model.profiles", lang)}</h3>
                  <Button size="sm" variant="soft" onClick={() => setShowProfileForm((v) => !v)}>
                    <IconPlus size={12} /> {t("common.add", lang)}
                  </Button>
                </div>

                {showProfileForm && (
                  <div className="flex flex-col gap-2.5 rounded-lg border border-line bg-panel2 p-3">
                    <Field label={t("model.displayName", lang)}>
                      <TextInput value={profileForm.name} onChange={(e) => setProfileForm((p) => ({ ...p, name: e.target.value }))} placeholder={t("model.displayNamePh", lang)} />
                    </Field>
                    <Field label={t("model.provider", lang)}>
                      <Select value={profileForm.provider} onChange={(e) => setProfileForm((p) => ({ ...p, provider: e.target.value }))}>
                        <option value="openai_compat">openai_compat</option>
                        <option value="ollama">ollama</option>
                      </Select>
                    </Field>
                    <Field label={t("model.baseUrl", lang)}>
                      <TextInput value={profileForm.base_url} onChange={(e) => setProfileForm((p) => ({ ...p, base_url: e.target.value }))} placeholder={t("model.baseUrlPh", lang)} />
                    </Field>
                    <Field label={t("model.model", lang)}>
                      <TextInput value={profileForm.model} onChange={(e) => setProfileForm((p) => ({ ...p, model: e.target.value }))} placeholder={t("model.modelPh", lang)} />
                    </Field>
                    <Field label={t("model.apiKey", lang)}>
                      <TextInput value={profileForm.api_key} onChange={(e) => setProfileForm((p) => ({ ...p, api_key: e.target.value }))} placeholder={t("model.apiKeyPhNew", lang)} type="password" />
                    </Field>
                    <div className="flex gap-2">
                      <Button variant="primary" full onClick={handleSaveProfile} disabled={busy === "profile" || !profileForm.name || !profileForm.base_url || !profileForm.model}>
                        {busy === "profile" ? <Spinner size={12} /> : t("settings.saveAndActivate", lang)}
                      </Button>
                      <Button variant="ghost" onClick={() => setShowProfileForm(false)}>{t("common.cancel", lang)}</Button>
                    </div>
                  </div>
                )}

                <div className="flex max-h-64 flex-col gap-1.5 overflow-y-auto">
                  {profiles.length === 0 && <p className="py-2 text-xs text-ink3">{t("model.noProfiles", lang)}</p>}
                  {profiles.map((p) => (
                    <div key={p.id} className={cls("flex items-center gap-2.5 rounded-lg border px-3 py-2", p.active ? "border-accent/50 bg-accent-soft/50" : "border-line bg-panel2")}>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[13px] font-medium text-ink">
                          {p.name}
                          {p.active && <Badge tone="accent" className="ml-1.5">{t("model.active", lang)}</Badge>}
                        </p>
                        <p className="truncate font-mono text-[10.5px] text-ink3">{p.model} · {p.base_url}</p>
                      </div>
                      {!p.active && (
                        <Button size="sm" variant="soft" onClick={() => handleActivate(p)} disabled={busy === "profile"}>
                          {t("model.use", lang)}
                        </Button>
                      )}
                      <IconButton title={t("settings.deleteProfile", lang)} onClick={() => handleDeleteProfile(p.id)} className="h-7 w-7 text-ink3 hover:text-err">
                        <IconTrash size={12} />
                      </IconButton>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          )}

          {tab === "agent" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("agent.approval", lang)}</h3>
                {(["full-auto", "approve-plan", "auto-edit", "suggest", "plan-only", "manual"] as const).map((m) => (
                  <label key={m} className="flex cursor-pointer items-center gap-2.5">
                    <input type="radio" name="approval" className="accent-accent" checked={cfg.agent.approval === m} onChange={() => patchAgent({ approval: m })} />
                    <span className="text-[13px] text-ink2">
                      {m === "full-auto" ? t("agent.fullAuto", lang) :
                       m === "approve-plan" ? t("agent.approvePlan", lang) :
                       m === "auto-edit" ? t("agent.autoEdit", lang) :
                       m === "suggest" ? t("agent.suggest", lang) :
                       m === "plan-only" ? t("agent.planOnly", lang) :
                       t("agent.manual", lang)}
                    </span>
                  </label>
                ))}
                <label className="flex items-center justify-between gap-2 rounded-lg bg-panel2 px-3 py-2.5">
                  <span className="text-[13px] text-ink2">{t("agent.workdirOnly", lang)}</span>
                  <Toggle checked={cfg.agent.workdir_only} onChange={(v) => patchAgent({ workdir_only: v })} />
                </label>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("agent.execParams", lang)}</h3>
                <Field label={t("agent.maxToolRounds", lang)}>
                  <TextInput type="number" value={cfg.agent.max_tool_rounds} onChange={(e) => patchAgent({ max_tool_rounds: Number(e.target.value) || 0 })} />
                </Field>
                <Field label={t("agent.shellTimeout", lang)}>
                  <TextInput type="number" value={cfg.agent.shell_timeout_sec} onChange={(e) => patchAgent({ shell_timeout_sec: Number(e.target.value) || 0 })} />
                </Field>
                <Field label={t("agent.maxOutput", lang)}>
                  <TextInput type="number" value={cfg.agent.max_output_chars} onChange={(e) => patchAgent({ max_output_chars: Number(e.target.value) || 0 })} />
                </Field>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("agent.guardrails", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("agent.guardrailsHint", lang)}</p>
                <Field label={t("agent.repeatLimit", lang)}>
                  <TextInput type="number" value={cfg.agent.max_repeat_calls} onChange={(e) => patchAgent({ max_repeat_calls: Number(e.target.value) || 0 })} />
                </Field>
                <Field label={t("agent.tokenBudget", lang)}>
                  <TextInput type="number" value={cfg.agent.max_turn_tokens} onChange={(e) => patchAgent({ max_turn_tokens: Number(e.target.value) || 0 })} />
                </Field>
              </section>
            </div>
          )}

          {tab === "security" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("security.accessLevel", lang)}</h3>
                {([
                  ["sandbox-only", "security.sandboxOnly", "security.sandboxOnlyDesc"],
                  ["workspace", "security.workspace", "security.workspaceDesc"],
                  ["full-access", "security.fullAccess", "security.fullAccessDesc"],
                  ["unrestricted", "security.unrestrictedDanger", "security.unrestrictedDesc"],
                ] as const).map(([v, labelKey, descKey]) => (
                  <label key={v} className={cls("flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 transition-colors", cfg.agent.sandbox_mode === v ? "border-accent/50 bg-accent-soft/40" : "border-line bg-panel2 hover:bg-hover")}>
                    <input type="radio" name="sandbox" className="mt-0.5 accent-accent" checked={cfg.agent.sandbox_mode === v} onChange={() => patchAgent({ sandbox_mode: v })} />
                    <span className="min-w-0">
                      <span className="block text-[13px] font-medium text-ink">{t(labelKey, lang)}</span>
                      <span className="block text-xs text-ink3">{t(descKey, lang)}</span>
                    </span>
                  </label>
                ))}
              </section>

              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("security.protectedPaths", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("security.protectedPathsDesc", lang)}</p>
                <TextArea rows={4} value={protectedText} onChange={(e) => { dirtyRef.current.cfg = true; setProtectedText(e.target.value) }} placeholder={"/etc\n/root/.spark"} />
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("security.hooks", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("security.hooksDesc", lang)}</p>
                <div className="flex flex-col gap-2">
                  {hookRows.map((h, i) => (
                    <div key={i} className="flex items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2">
                      <span className="shrink-0 rounded-md bg-hover px-1.5 py-0.5 font-mono text-[10px] text-ink2">{h.event}</span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink2">{h.command} {h.args.join(" ")}</span>
                      <IconButton title={t("security.deleteHook", lang)} onClick={() => setHookRows((rows) => rows.filter((_, idx) => idx !== i))} className="h-6 w-6 text-ink3 hover:text-err">
                        <IconTrash size={11} />
                      </IconButton>
                    </div>
                  ))}
                  <Button size="sm" variant="soft" onClick={() => setHookRows((rows) => [...rows, { event: "pre_tool", command: "", args: [], name: "", timeout_sec: 10 }])}>
                    <IconPlus size={12} /> {t("security.addHook", lang)}
                  </Button>
                </div>
              </section>
            </div>
          )}

          {tab === "display" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("display.theme", lang)}</h3>
                <div className="grid grid-cols-3 gap-2">
                  {(["light", "dark", "system"] as Theme[]).map((th) => (
                    <button
                      key={th}
                      type="button"
                      onClick={() => { setTheme(th); applyTheme(th) }}
                      className={cls("flex flex-col items-center gap-1.5 rounded-lg border px-2 py-3 transition-colors", theme === th ? "border-accent bg-accent-soft/40" : "border-line bg-panel2 hover:bg-hover")}
                    >
                      {th === "light" ? <IconSun size={16} /> : th === "dark" ? <IconMoon size={16} /> : <IconLayers size={16} />}
                      <span className="text-xs font-medium text-ink">{th === "light" ? t("display.light", lang) : th === "dark" ? t("display.dark", lang) : t("display.system", lang)}</span>
                      {theme === th && <IconCheck size={12} className="text-accent" />}
                    </button>
                  ))}
                </div>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("display.language", lang)}</h3>
                <div className="flex gap-2">
                  {(["zh", "en"] as const).map((l) => (
                    <Button key={l} variant={lang === l ? "primary" : "outline"} size="sm" onClick={() => setLang(l)}>
                      {l === "zh" ? "中文" : "English"}
                    </Button>
                  ))}
                </div>
              </section>

              <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("display.toggles", lang)}</h3>
                {displayKeys.map(({ key, label }) => (
                  <label key={key} className="flex items-center justify-between rounded-lg px-2 py-2">
                    <span className="text-[13px] text-ink2">{label}</span>
                    <Toggle checked={Boolean(cfg.agent[key])} onChange={(v) => patchAgent({ [key]: v })} />
                  </label>
                ))}
              </section>

              <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">{t("settings.systemPrompt", lang)}</h3>
                  <Button
                    size="sm"
                    variant="soft"
                    onClick={() => {
                      patchAgent({ system_prompt: "" })
                      setNotice(t("settings.systemPromptReset", lang))
                    }}
                  >
                    {t("settings.restoreDefault", lang)}
                  </Button>
                </div>
                <p className="text-xs leading-relaxed text-ink3">{t("settings.systemPromptDesc", lang)}</p>
                <TextArea
                  value={cfg.agent.system_prompt ?? ""}
                  onChange={(e) => patchAgent({ system_prompt: e.target.value })}
                  placeholder={t("settings.systemPromptPh", lang)}
                  rows={8}
                  className="font-mono text-xs"
                />
              </section>
            </div>
          )}

          {tab === "memory" && <MemorySection />}

          {tab === "mcp" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("mcp.title", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("mcp.desc", lang)}</p>
                <div className="flex flex-col gap-2">
                  {mcpRows.length === 0 && <p className="py-2 text-xs text-ink3">{t("mcp.empty", lang)}</p>}
                  {mcpRows.map((s, i) => (
                    <div key={i} className="flex flex-col gap-2 rounded-lg border border-line bg-panel2 p-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-hover text-ink2">
                          <IconGlobe size={13} />
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">{s.name}</span>
                        <IconButton title={t("common.delete", lang)} onClick={() => setMcpRows((rows) => rows.filter((_, idx) => idx !== i))} className="h-7 w-7 text-ink3 hover:text-err">
                          <IconTrash size={12} />
                        </IconButton>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <Field label={t("common.command", lang)}>
                          <TextInput value={s.command} onChange={(e) => patchMcp(i, { command: e.target.value })} placeholder={t("mcp.commandPh", lang)} />
                        </Field>
                        <Field label={t("common.args", lang)}>
                          <TextInput value={s.args.join(" ")} onChange={(e) => patchMcp(i, { args: e.target.value.split(/\s+/).filter(Boolean) })} placeholder={t("mcp.argsPh", lang)} />
                        </Field>
                      </div>
                      <Field label={t("mcp.readonly", lang)}>
                        <TextInput value={s.readonly_tools.join(" ")} onChange={(e) => patchMcp(i, { readonly_tools: e.target.value.split(/\s+/).filter(Boolean) })} placeholder={t("mcp.readonlyPh", lang)} />
                      </Field>
                    </div>
                  ))}
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="soft" onClick={() => setMcpRows((rows) => [...rows, { name: `mcp-${rows.length + 1}`, command: "", args: [], readonly_tools: [] }])}>
                      <IconPlus size={12} /> {t("mcp.add", lang)}
                    </Button>
                    <Button size="sm" variant="soft" onClick={() => setShowRegistry(true)}>
                      <IconGlobe size={12} /> {t("mcp.browseRegistry", lang)}
                    </Button>
                  </div>
                </div>
              </section>
            </div>
          )}

          {tab === "project" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">{t("project.memoryTitle", lang)}</h3>
                  <Button size="sm" variant="soft" onClick={handleSaveMd} disabled={busy === "save"}>
                    {busy === "save" ? <Spinner size={11} /> : t("project.saveMemory", lang)}
                  </Button>
                </div>
                <p className="text-xs leading-relaxed text-ink3">{t("project.memoryDesc", lang)}</p>
                {agentsMd ? (
                  <>
                    <TextArea
                      rows={8}
                      value={agentsMd.content}
                      onChange={(e) => {
                        dirtyRef.current.md = true
                        setAgentsMd((a) => (a ? { ...a, content: e.target.value, chars: e.target.value.length } : a))
                      }}
                      placeholder={t("project.memoryPh", lang)}
                      className="font-mono text-[12.5px]"
                    />
                    <span className="text-right text-[10.5px] text-ink3">
                      {t("project.charCount", lang, { current: agentsMd.chars, max: agentsMd.max_fragment_chars })}
                    </span>
                  </>
                ) : (
                  <p className="text-xs text-ink3">{t("common.loading", lang)}</p>
                )}
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("project.contextMgmt", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("project.contextDesc", lang)}</p>
                <Field label={t("project.compactThreshold", lang)}>
                  <TextInput type="number" value={cfg.context.compact_threshold ?? 85} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, context: { ...c.context, compact_threshold: Number(e.target.value) || 85 } } : c)) }} />
                </Field>
                <Field label={t("project.contextBudget", lang)}>
                  <TextInput type="number" value={cfg.context.max_context_tokens ?? 0} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, context: { ...c.context, max_context_tokens: Number(e.target.value) || 0 } } : c)) }} />
                </Field>
                <Field label={t("project.keepRecent", lang)}>
                  <TextInput type="number" value={cfg.context.keep_recent_messages ?? 20} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, context: { ...c.context, keep_recent_messages: Number(e.target.value) || 0 } } : c)) }} />
                </Field>
              </section>

              <section className="rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("project.workdir", lang)}</h3>
                <p className="mt-1 font-mono text-xs text-ink2">{cfg.workdir}</p>
              </section>
            </div>
          )}
        </div>

        {/* 底部保存 */}
        {tab !== "memory" && tab !== "project" && (
          <div className="border-t border-line bg-panel2 px-4 py-3">
            <Button variant="primary" full onClick={handleSave} disabled={busy === "save"}>
              {busy === "save" ? (
                <span className="flex items-center gap-1.5"><Spinner size={12} /> {t("settings.saving", lang)}</span>
              ) : (
                t("common.save", lang)
              )}
            </Button>
          </div>
          )}
          </div>
        </div>
      </aside>
      {showRegistry && <McpRegistryModal onClose={() => setShowRegistry(false)} onPick={handleRegistryPick} />}
    </div>
  )
}
