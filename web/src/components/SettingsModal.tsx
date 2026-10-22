import { useCallback, useEffect, useRef, useState } from "react"
import type { FullConfig, HookEntry, McpServer, ModelProfile, ProbeResult, SettingsPayload, Status } from "../types"
import { fetchConfig, fetchAgentsMd, saveAgentsMd, saveSettings, testConnection, listModelProfiles, saveModelProfile, deleteModelProfile, activateModelProfile, fetchDiagnostics, runSelfHeal, type HealthRow } from "../api"
import MemorySection from "./MemorySection"
import { McpRegistryModal } from "./McpRegistryModal"
import type { McpRegistryItem } from "../api"
import type { Theme } from "../theme"
import { Badge, Button, Field, IconButton, Select, Spinner, TextArea, TextInput, Toggle, cls } from "./ui"
import { IconBrain, IconCheck, IconGlobe, IconKey, IconLayers, IconMoon, IconPlus, IconRefresh, IconSettings, IconStethoscope, IconSun, IconTerminal, IconTrash, IconWrench, IconX } from "./icons"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { t, type TranslationKey } from "../i18n"

// Mirrors spark.hooks.HookRegistry.EVENTS (backend validates against it).
/** Split a hook argument line, honouring "double" and 'single' quotes. */
function splitArgs(raw: string): string[] {
  const out: string[] = []
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(raw)) !== null) out.push(m[1] ?? m[2] ?? m[3])
  return out
}

const HOOK_EVENTS = [
  "turn_start", "pre_tool", "post_tool", "turn_end",
  "app_start", "app_stop", "session_create", "session_load", "session_switch",
  "compaction", "memory_extract", "context_threshold",
  "approval_needed", "tool_error", "plan_update",
  "turn_error", "provider_error",
]

type Tab = "model" | "agent" | "security" | "display" | "memory" | "mcp" | "project"

// 预装模型预设：国内可用优先，用户选一个自动填 provider/base_url/model，只需填 API Key
const MODEL_PRESETS: { labelKey: TranslationKey; provider: string; base_url: string; model: string }[] = [
  { labelKey: "preset.deepseek", provider: "openai_compat", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { labelKey: "preset.qwen", provider: "openai_compat", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { labelKey: "preset.kimi", provider: "openai_compat", base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  { labelKey: "preset.glm", provider: "openai_compat", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash" },
  { labelKey: "preset.doubao", provider: "openai_compat", base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-pro-32k" },
  { labelKey: "preset.agnes", provider: "openai_compat", base_url: "https://api.agnes-ai.cn/v1", model: "agnes-2.5-flash" },
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

/**
 * 环境体检 + 一键修复（Project 页底部）。
 *
 * 和 CLI 的 `spark doctor` 共用同一份后端报告（/api/diagnostics），
 * 所以界面不会再出现「CLI 说有问题、界面说一切正常」的分裂。
 */
function HealthSection({ lang }: { lang: "zh" | "en" }) {
  const [rows, setRows] = useState<HealthRow[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const pushToast = useUiStore((s) => s.pushToast)

  const load = useCallback(async () => {
    try {
      const data = await fetchDiagnostics()
      setRows(data.rows)
    } catch {
      setRows([])
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const blockers = rows?.filter((r) => r.level === "bad") ?? []
  const warnings = rows?.filter((r) => r.level === "warn") ?? []
  const autoFixable = [...blockers, ...warnings].filter((r) => r.auto_fixable)
  const visible = expanded ? (rows ?? []) : [...blockers, ...warnings].slice(0, 4)

  async function repair() {
    setBusy(true)
    try {
      const report = await runSelfHeal()
      setRows(report.rows)
      pushToast("toast.selfhealDone", "success")
      if (report.remaining.length > 0) setExpanded(true)
    } catch {
      pushToast("toast.selfhealFailed", "error")
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink">
          <IconStethoscope size={14} className="text-accent" />
          {t("health.title", lang)}
        </h3>
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => void load()} disabled={busy}>
            <IconRefresh size={12} /> {t("health.refresh", lang)}
          </Button>
          {autoFixable.length > 0 && (
            <Button size="sm" variant="primary" onClick={() => void repair()} disabled={busy}>
              {busy ? <Spinner size={11} /> : <IconWrench size={12} />} {t("health.fix", lang, { n: autoFixable.length })}
            </Button>
          )}
        </div>
      </div>
      <p className="text-xs leading-relaxed text-ink3">{t("health.desc", lang)}</p>
      {rows === null && <p className="py-2 text-xs text-ink3">{t("common.loading", lang)}</p>}
      {rows !== null && blockers.length === 0 && warnings.length === 0 && (
        <p className="flex items-center gap-1.5 py-1 text-xs text-ok">
          <IconCheck size={12} /> {t("health.allGood", lang)}
        </p>
      )}
      <ul className="flex flex-col gap-1">
        {visible.map((row, i) => (
          <li
            key={`${row.kind}-${i}`}
            className={cls(
              "rounded-lg border px-2.5 py-1.5 text-[11.5px]",
              row.level === "bad"
                ? "border-err/30 bg-err/5 text-err"
                : row.level === "warn"
                  ? "border-warn/30 bg-warn/5 text-warn"
                  : "border-line bg-panel2 text-ink2",
            )}
          >
            <div className="flex items-start gap-1.5">
              <span className="mt-0.5 shrink-0">{row.level === "bad" ? "!" : row.level === "warn" ? "?" : "✓"}</span>
              <div className="min-w-0">
                <p className="break-words">{row.message}</p>
                {row.fix && <p className="mt-0.5 break-words text-ink3">{row.fix}</p>}
              </div>
            </div>
          </li>
        ))}
      </ul>
      {(rows?.length ?? 0) > visible.length && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="self-start text-[11px] text-accent hover:underline"
        >
          {t("health.showAll", lang)}
        </button>
      )}
    </section>
  )
}
export default function SettingsDrawer({ onClose, onSaved }: Props) {
  const lang = useUiStore((s) => s.lang)
  const setLang = useUiStore((s) => s.setLang)
  const [tab, setTab] = useState<Tab>("model")
  const [cfg, setCfg] = useState<FullConfig | null>(null)
  const [mcpRows, setMcpRows] = useState<McpServer[]>([])
  const [agentsMd, setAgentsMd] = useState<{ filename: string; content: string; chars: number; max_fragment_chars: number } | null>(null)
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState<"save" | "test" | "profile" | null>(null)
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [showProfileForm, setShowProfileForm] = useState(false)
  const [profileForm, setProfileForm] = useState({ id: "", name: "", provider: "openai_compat", base_url: "", model: "", api_key: "" })
  const [protectedText, setProtectedText] = useState("")
  const [hookRows, setHookRows] = useState<HookEntry[]>([])
  // Raw argument text per row, so quotes survive typing; rows are re-split on
  // every change but the input keeps what the user actually typed.
  const [argsText, setArgsText] = useState<Record<number, string>>({})
  // Single source of truth is the UI store: a local copy left the command
  // palette computing "next theme" from a stale value.
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)
  const [showRegistry, setShowRegistry] = useState(false)
  const dirtyRef = useRef({ cfg: false, mcp: false, md: false })

  function handleRegistryPick(item: McpRegistryItem) {
    if (!item.suggested_command) return
    dirtyRef.current.mcp = true
    const derived = (item.name || item.title || "mcp").split("/").pop() || "mcp"
    // Seed required env vars as empty placeholders so the card shows what the
    // server needs; an empty value is dropped at save time instead of starting
    // a server that is guaranteed to fail.
    const env: Record<string, string> = {}
    for (const field of item.required_env ?? []) {
      if (field.required) env[field.name] = ""
    }
    setMcpRows((rows) => [
      ...rows,
      {
        name: derived.replace(/[^a-zA-Z0-9_-]/g, "-"),
        command: item.suggested_command!,
        args: [...item.suggested_args],
        env,
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
          // Empty means "keep the stored key": the backend falls back to
          // cfg.provider.api_key / the env var when this is blank.
          api_key: apiKeyInput.trim(),
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
        // Project tab edits land here; without this field every context change
        // was silently discarded on save.
        context: {
          compact_threshold: cfg.context.compact_threshold,
          max_context_tokens: cfg.context.max_context_tokens,
          keep_recent_messages: cfg.context.keep_recent_messages,
          history_budget_chars: cfg.context.history_budget_chars,
          repo_map_enabled: cfg.context.repo_map_enabled,
        },
        mcp_servers: mcpRows.map((row) => ({
          ...row,
          env: Object.fromEntries(
            Object.entries(row.env ?? {}).filter(([, value]) => String(value).trim() !== ""),
          ),
        })),
        hooks: hookRows,
      }
      const data = await saveSettings(payload)
      // Surface MCP boot failures instead of a blanket "saved": a server that
      // could not start is a saved-but-broken state the user must see.
      if (data.mcp_errors && data.mcp_errors.length > 0) {
        setNotice(t("settings.savedWithMcpErrors", lang, { errors: data.mcp_errors.join("; ") }))
      } else {
        setNotice(t("settings.saved", lang))
      }
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
      const res = await testConnection({
        base_url: cfg!.provider.base_url,
        model: cfg!.provider.model,
        api_key: apiKeyInput.trim(),
      })
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
                  aria-current={tab === tb.key ? "page" : undefined}
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

            {/* 移动端：横向可滚动 tab。左导航在 <sm 时隐藏，没有这一条就只剩首个 tab 可达 */}
            <div className="flex gap-1 overflow-x-auto border-b border-line px-2 py-1.5 sm:hidden">
              {TABS.map((tb) => (
                <button
                  key={tb.key}
                  type="button"
                  onClick={() => setTab(tb.key)}
                  aria-current={tab === tb.key ? "page" : undefined}
                  className={cls(
                    "flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
                    tab === tb.key ? "bg-selected text-ink" : "text-ink2 hover:bg-hover",
                  )}
                >
                  <span className={tab === tb.key ? "text-accent" : "text-ink3"}>{tb.icon}</span>
                  {t(tb.labelKey, lang)}
                </button>
              ))}
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
                <Field label={t("model.apiKey", lang)} hint={t("model.apiKeyHint", lang)}>
                  <TextInput
                    type="password"
                    autoComplete="off"
                    value={apiKeyInput}
                    onChange={(e) => setApiKeyInput(e.target.value)}
                    placeholder={cfg.provider.has_api_key ? t("model.apiKeyKeep", lang, { masked: cfg.provider.api_key_masked }) : t("model.apiKeyPhNew", lang)}
                  />
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
                  <span className="flex flex-col">
                    <span className="text-[13px] text-ink2">{t("agent.workdirOnly", lang)}</span>
                    <span className="text-[10.5px] text-ink3">{t("agent.workdirOnlyHint", lang)}</span>
                  </span>
                  {/* The legacy workdir_only flag was never read by the sandbox,
                      so this switch wrote a value nothing obeyed. It now drives
                      sandbox_mode directly — the one field enforcement reads. */}
                  <Toggle
                    checked={cfg.agent.sandbox_mode === "workspace" || cfg.agent.sandbox_mode === "sandbox-only"}
                    onChange={(v) =>
                      patchAgent({
                        workdir_only: v,
                        sandbox_mode: v ? "workspace" : "full-access",
                        ...(v ? {} : { approval: cfg.agent.approval === "full-auto" ? "full-auto" : cfg.agent.approval }),
                      })
                    }
                  />
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
                <div className="flex flex-col gap-3">
                  {hookRows.length === 0 && (
                    <p className="text-xs text-ink3">{t("security.noHooks", lang)}</p>
                  )}
                  {hookRows.map((h, i) => (
                    <div key={i} className="flex flex-col gap-2 rounded-lg border border-line bg-panel2 px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <Select
                          value={h.event}
                          onChange={(e) => { dirtyRef.current.cfg = true; setHookRows((rows) => rows.map((r, idx) => idx === i ? { ...r, event: e.target.value } : r)) }}
                          aria-label={t("security.hookEvent", lang)}
                        >
                          {HOOK_EVENTS.map((ev) => <option key={ev} value={ev}>{ev}</option>)}
                        </Select>
                        <IconButton title={t("security.deleteHook", lang)} onClick={() => { dirtyRef.current.cfg = true; setHookRows((rows) => rows.filter((_, idx) => idx !== i)) }} className="h-7 w-7 shrink-0 text-ink3 hover:text-err">
                          <IconTrash size={12} />
                        </IconButton>
                      </div>
                      <TextInput
                        value={h.command}
                        onChange={(e) => { dirtyRef.current.cfg = true; setHookRows((rows) => rows.map((r, idx) => idx === i ? { ...r, command: e.target.value } : r)) }}
                        placeholder={t("security.hookCommandPh", lang)}
                        className="font-mono"
                      />
                      <TextInput
                        value={argsText[i] ?? h.args.join(" ")}
                        onChange={(e) => {
                          const raw = e.target.value
                          // Keep the raw text while typing so grouped quotes stay
                          // intact; split only on save (splitting per keystroke
                          // made "hello world" impossible to enter).
                          dirtyRef.current.cfg = true
                          setArgsText((m) => ({ ...m, [i]: raw }))
                          setHookRows((rows) => rows.map((r, idx) => idx === i ? { ...r, args: splitArgs(raw) } : r))
                        }}
                        placeholder={t("security.hookArgsPh", lang)}
                        className="font-mono"
                      />
                    </div>
                  ))}
                  <Button size="sm" variant="soft" onClick={() => { dirtyRef.current.cfg = true; setHookRows((rows) => [...rows, { event: "pre_tool", command: "", args: [], name: "", timeout_sec: 10 }]) }}>
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
                      onClick={() => setTheme(th)}
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
                      {s.env && Object.keys(s.env).length > 0 && (
                        <div className="flex flex-col gap-2 rounded-lg border border-warn/30 bg-warn/5 p-2.5">
                          <p className="text-[11px] text-warn">{t("mcp.envHint", lang)}</p>
                          {Object.keys(s.env).map((key) => (
                            <Field key={key} label={key}>
                              <TextInput
                                type="password"
                                autoComplete="off"
                                value={s.env?.[key] ?? ""}
                                onChange={(e) => patchMcp(i, { env: { ...(s.env ?? {}), [key]: e.target.value } })}
                                placeholder={t("mcp.envPh", lang)}
                              />
                            </Field>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="soft" onClick={() => setMcpRows((rows) => [...rows, { name: `mcp-${rows.length + 1}`, command: "", args: [], env: {}, readonly_tools: [] }])}>
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
                  {/* Stored as a fraction (0.1–0.99) but shown as a percentage:
                      typing 85 must mean 85%, and clearing the box must keep the
                      old value instead of injecting one the backend rejects. */}
                  <TextInput
                    type="number"
                    min={10}
                    max={99}
                    step={1}
                    value={Math.round((cfg.context.compact_threshold ?? 0.85) * 100)}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") return
                      const pct = Math.min(99, Math.max(10, Number(raw)))
                      dirtyRef.current.cfg = true
                      setCfg((c) => (c ? { ...c, context: { ...c.context, compact_threshold: pct / 100 } } : c))
                    }}
                  />
                </Field>
                <Field label={t("project.contextBudget", lang)}>
                  <TextInput
                    type="number"
                    min={1024}
                    value={cfg.context.max_context_tokens ?? 32768}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") return
                      dirtyRef.current.cfg = true
                      setCfg((c) => (c ? { ...c, context: { ...c.context, max_context_tokens: Math.max(1024, Number(raw)) } } : c))
                    }}
                  />
                </Field>
                <Field label={t("project.keepRecent", lang)}>
                  <TextInput
                    type="number"
                    min={2}
                    max={200}
                    value={cfg.context.keep_recent_messages ?? 8}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") return
                      const keep = Math.min(200, Math.max(2, Number(raw)))
                      dirtyRef.current.cfg = true
                      setCfg((c) => (c ? { ...c, context: { ...c.context, keep_recent_messages: keep } } : c))
                    }}
                  />
                </Field>
              </section>

              <section className="rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("project.workdir", lang)}</h3>
                <p className="mt-1 font-mono text-xs text-ink2">{cfg.workdir}</p>
              </section>

              <HealthSection lang={lang} />
            </div>
          )}
        </div>

        {/* 底部保存：memory 页有自己的即时操作，其余页（含 project）都可保存 */}
        {tab !== "memory" && (
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
