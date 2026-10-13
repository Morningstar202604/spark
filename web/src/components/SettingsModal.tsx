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

type Tab = "model" | "agent" | "security" | "display" | "memory" | "mcp" | "project"

const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
  { key: "model", label: "模型", icon: <IconLayers size={14} /> },
  { key: "agent", label: "Agent", icon: <IconTerminal size={14} /> },
  { key: "security", label: "权限", icon: <IconKey size={14} /> },
  { key: "display", label: "展示", icon: <IconSun size={14} /> },
  { key: "memory", label: "记忆", icon: <IconBrain size={14} /> },
  { key: "mcp", label: "MCP", icon: <IconGlobe size={14} /> },
  { key: "project", label: "项目", icon: <IconSettings size={14} /> },
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
      setNotice(`模型档案加载失败：${e instanceof Error ? e.message : String(e)}`)
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
      .catch((e) => setNotice(`配置加载失败：${e instanceof Error ? e.message : String(e)}`))
    fetchAgentsMd()
      .then((d) => {
        if (!dirtyRef.current.md) setAgentsMd(d)
      })
      .catch((e) => setNotice(`项目记忆加载失败：${e instanceof Error ? e.message : String(e)}`))
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
        },
        mcp_servers: mcpRows,
        hooks: hookRows,
      }
      const data = await saveSettings(payload)
      setNotice("已保存并生效。")
      dirtyRef.current.cfg = false
      dirtyRef.current.mcp = false
      onSaved(data.status)
      useSessionStore.getState().refreshSessions()
    } catch (e) {
      setNotice(`保存失败：${e instanceof Error ? e.message : String(e)}`)
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
      setNotice(`测试失败：${e instanceof Error ? e.message : String(e)}`)
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
      setNotice("模型档案已保存并切换。")
    } catch (e) {
      setNotice(`保存失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(null)
    }
  }

  async function handleActivate(id: string) {
    setBusy("profile")
    try {
      const act = await activateModelProfile(id)
      onSaved(act.status)
      await refreshProfiles()
      setNotice("已切换模型。")
    } catch (e) {
      setNotice(`切换失败：${e instanceof Error ? e.message : String(e)}`)
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
      setNotice(`删除失败：${e instanceof Error ? e.message : String(e)}`)
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
      setNotice("项目记忆已保存，下一轮对话生效。")
    } catch (e) {
      setNotice(`保存失败：${e instanceof Error ? e.message : String(e)}`)
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
    { key: "show_thinking", label: "思考过程" },
    { key: "show_tools", label: "工具调用" },
    { key: "show_plan", label: "计划卡片" },
    { key: "show_context", label: "上下文用量" },
    { key: "show_keywords", label: "会话关键词" },
    { key: "show_notices", label: "系统提示" },
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={onClose}>
      <aside
        className="anim-fade flex h-[min(92vh,680px)] w-[min(96vw,920px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="设置"
      >
        <div className="flex h-full">
          {/* 左导航（桌面） */}
          <nav className="hidden w-[200px] shrink-0 flex-col border-r border-line bg-sidebar sm:flex">
            <div className="flex items-center gap-2 px-4 py-3.5">
              <IconSettings size={16} className="text-accent" />
              <span className="text-[14px] font-semibold text-ink">设置</span>
            </div>
            <div className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 py-1">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => setTab(t.key)}
                  className={cls(
                    "flex items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] font-medium transition-colors",
                    tab === t.key ? "bg-selected text-ink" : "text-ink2 hover:bg-hover",
                  )}
                >
                  <span className={cls("shrink-0", tab === t.key ? "text-accent" : "text-ink3")}>{t.icon}</span>
                  {t.label}
                  {tab === t.key && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-accent" />}
                </button>
              ))}
            </div>
            <div className="px-3 pb-3 pt-1">
              <div className="rounded-lg border border-line bg-panel2 px-3 py-2">
                <p className="text-[10px] font-bold tracking-wide text-ink3">SPARK</p>
                <p className="mt-0.5 text-[11px] leading-snug text-ink2">本地自托管的 AI 编程智能体</p>
              </div>
            </div>
          </nav>

          {/* 右栏 */}
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b border-line px-4 py-3">
              <span className="text-sm font-semibold text-ink">设置</span>
              <IconButton title="关闭" onClick={onClose} className="ml-auto">
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
                  <h3 className="text-sm font-semibold text-ink">当前模型</h3>
                  <Button size="sm" variant="outline" onClick={handleTest} disabled={busy === "test"}>
                    {busy === "test" ? <Spinner size={11} /> : "测试连接"}
                  </Button>
                </div>
                <Field label="Provider">
                  <Select value={cfg.provider.name} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, provider: { ...c.provider, name: e.target.value } } : c)) }}>
                    <option value="openai_compat">openai_compat</option>
                    <option value="ollama">ollama</option>
                  </Select>
                </Field>
                <Field label="Base URL">
                  <TextInput value={cfg.provider.base_url} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, provider: { ...c.provider, base_url: e.target.value } } : c)) }} placeholder="https://api.example.com/v1" />
                </Field>
                <Field label="Model">
                  <TextInput value={cfg.provider.model} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, provider: { ...c.provider, model: e.target.value } } : c)) }} placeholder="model-id" />
                </Field>
                <Field label="推理强度（effort）" hint="映射为 reasoning_effort / DeepSeek thinking budget，影响深度与成本">
                  <Select value={cfg.agent.effort ?? "medium"} onChange={(e) => patchAgent({ effort: e.target.value as "low" | "medium" | "high" })}>
                    <option value="low">低（快速 / 省 token）</option>
                    <option value="medium">中（默认）</option>
                    <option value="high">高（深度推理 / 更贵）</option>
                  </Select>
                </Field>
                {probe && (
                  <div className={cls("rounded-lg border px-3 py-2 text-xs", probe.ok ? "border-ok/40 bg-ok/8 text-ok" : "border-err/40 bg-err/8 text-err")}>
                    {probe.ok ? `连接成功 · 延迟 ${probe.latency_ms}ms · ${probe.model}` : `连接失败：${probe.error}`}
                  </div>
                )}
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">模型档案</h3>
                  <Button size="sm" variant="soft" onClick={() => setShowProfileForm((v) => !v)}>
                    <IconPlus size={12} /> 添加
                  </Button>
                </div>

                {showProfileForm && (
                  <div className="flex flex-col gap-2.5 rounded-lg border border-line bg-panel2 p-3">
                    <Field label="显示名称">
                      <TextInput value={profileForm.name} onChange={(e) => setProfileForm((p) => ({ ...p, name: e.target.value }))} placeholder="如 GPT-4o / 本地 qwen" />
                    </Field>
                    <Field label="Provider">
                      <Select value={profileForm.provider} onChange={(e) => setProfileForm((p) => ({ ...p, provider: e.target.value }))}>
                        <option value="openai_compat">openai_compat</option>
                        <option value="ollama">ollama</option>
                      </Select>
                    </Field>
                    <Field label="Base URL">
                      <TextInput value={profileForm.base_url} onChange={(e) => setProfileForm((p) => ({ ...p, base_url: e.target.value }))} placeholder="https://api.example.com/v1" />
                    </Field>
                    <Field label="Model">
                      <TextInput value={profileForm.model} onChange={(e) => setProfileForm((p) => ({ ...p, model: e.target.value }))} placeholder="model-id" />
                    </Field>
                    <Field label="API Key">
                      <TextInput value={profileForm.api_key} onChange={(e) => setProfileForm((p) => ({ ...p, api_key: e.target.value }))} placeholder="留空则沿用当前全局 Key" type="password" />
                    </Field>
                    <div className="flex gap-2">
                      <Button variant="primary" full onClick={handleSaveProfile} disabled={busy === "profile" || !profileForm.name || !profileForm.base_url || !profileForm.model}>
                        {busy === "profile" ? <Spinner size={12} /> : "保存并启用"}
                      </Button>
                      <Button variant="ghost" onClick={() => setShowProfileForm(false)}>取消</Button>
                    </div>
                  </div>
                )}

                <div className="flex max-h-64 flex-col gap-1.5 overflow-y-auto">
                  {profiles.length === 0 && <p className="py-2 text-xs text-ink3">暂无档案。</p>}
                  {profiles.map((p) => (
                    <div key={p.id} className={cls("flex items-center gap-2.5 rounded-lg border px-3 py-2", p.active ? "border-accent/50 bg-accent-soft/50" : "border-line bg-panel2")}>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[13px] font-medium text-ink">
                          {p.name}
                          {p.active && <Badge tone="accent" className="ml-1.5">使用中</Badge>}
                        </p>
                        <p className="truncate font-mono text-[10.5px] text-ink3">{p.model} · {p.base_url}</p>
                      </div>
                      {!p.active && (
                        <Button size="sm" variant="soft" onClick={() => handleActivate(p.id)} disabled={busy === "profile"}>
                          使用
                        </Button>
                      )}
                      <IconButton title="删除档案" onClick={() => handleDeleteProfile(p.id)} className="h-7 w-7 text-ink3 hover:text-err">
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
                <h3 className="text-sm font-semibold text-ink">审批模式</h3>
                {(["full-auto", "approve-plan", "auto-edit", "suggest", "plan-only", "manual"] as const).map((m) => (
                  <label key={m} className="flex cursor-pointer items-center gap-2.5">
                    <input type="radio" name="approval" className="accent-accent" checked={cfg.agent.approval === m} onChange={() => patchAgent({ approval: m })} />
                    <span className="text-[13px] text-ink2">
                      {m === "full-auto" ? "full-auto（全自动）" :
                       m === "approve-plan" ? "approve-plan（先批准计划再自动执行）" :
                       m === "auto-edit" ? "auto-edit（自动改文件）" :
                       m === "suggest" ? "suggest（每步确认）" :
                       m === "plan-only" ? "plan-only（只出计划不执行）" :
                       "manual（逐条人工确认）"}
                    </span>
                  </label>
                ))}
                <label className="flex items-center justify-between gap-2 rounded-lg bg-panel2 px-3 py-2.5">
                  <span className="text-[13px] text-ink2">限制在工作目录内（workdir_only）</span>
                  <Toggle checked={cfg.agent.workdir_only} onChange={(v) => patchAgent({ workdir_only: v })} />
                </label>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">执行参数</h3>
                <Field label="最大工具轮数">
                  <TextInput type="number" value={cfg.agent.max_tool_rounds} onChange={(e) => patchAgent({ max_tool_rounds: Number(e.target.value) || 0 })} />
                </Field>
                <Field label="Shell 超时（秒）">
                  <TextInput type="number" value={cfg.agent.shell_timeout_sec} onChange={(e) => patchAgent({ shell_timeout_sec: Number(e.target.value) || 0 })} />
                </Field>
                <Field label="输出上限（字符）">
                  <TextInput type="number" value={cfg.agent.max_output_chars} onChange={(e) => patchAgent({ max_output_chars: Number(e.target.value) || 0 })} />
                </Field>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">长任务护栏</h3>
                <p className="text-xs leading-relaxed text-ink3">防止死循环与费用失控。0 表示关闭对应护栏。</p>
                <Field label="重复调用熔断（次）">
                  <TextInput type="number" value={cfg.agent.max_repeat_calls} onChange={(e) => patchAgent({ max_repeat_calls: Number(e.target.value) || 0 })} />
                </Field>
                <Field label="单轮 Token 预算（0=不限）">
                  <TextInput type="number" value={cfg.agent.max_turn_tokens} onChange={(e) => patchAgent({ max_turn_tokens: Number(e.target.value) || 0 })} />
                </Field>
              </section>
            </div>
          )}

          {tab === "security" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">沙箱访问级别</h3>
                {([
                  ["sandbox-only", "仅沙箱内访问", "只读工作区文件，禁用命令执行与文件写入"],
                  ["workspace", "局部访问（推荐）", "可读写文件、执行命令，但仅限工作目录内"],
                  ["full-access", "完全访问", "允许操作工作目录之外的路径"],
                  ["unrestricted", "无限制（危险）", "零防护：所有拦截全部关闭"],
                ] as const).map(([v, label, desc]) => (
                  <label key={v} className={cls("flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 transition-colors", cfg.agent.sandbox_mode === v ? "border-accent/50 bg-accent-soft/40" : "border-line bg-panel2 hover:bg-hover")}>
                    <input type="radio" name="sandbox" className="mt-0.5 accent-accent" checked={cfg.agent.sandbox_mode === v} onChange={() => patchAgent({ sandbox_mode: v })} />
                    <span className="min-w-0">
                      <span className="block text-[13px] font-medium text-ink">{label}</span>
                      <span className="block text-xs text-ink3">{desc}</span>
                    </span>
                  </label>
                ))}
              </section>

              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">受保护路径</h3>
                <p className="text-xs leading-relaxed text-ink3">每行一个绝对路径，访问会被拦截。无限制档时仅保留此清单生效。</p>
                <TextArea rows={4} value={protectedText} onChange={(e) => { dirtyRef.current.cfg = true; setProtectedText(e.target.value) }} placeholder={"/etc\n/root/.spark"} />
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">生命周期钩子</h3>
                <p className="text-xs leading-relaxed text-ink3">pre_tool 在每次工具调用前运行，退出码非 0 即拦截该工具。</p>
                <div className="flex flex-col gap-2">
                  {hookRows.map((h, i) => (
                    <div key={i} className="flex items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2">
                      <span className="shrink-0 rounded-md bg-hover px-1.5 py-0.5 font-mono text-[10px] text-ink2">{h.event}</span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink2">{h.command} {h.args.join(" ")}</span>
                      <IconButton title="删除钩子" onClick={() => setHookRows((rows) => rows.filter((_, idx) => idx !== i))} className="h-6 w-6 text-ink3 hover:text-err">
                        <IconTrash size={11} />
                      </IconButton>
                    </div>
                  ))}
                  <Button size="sm" variant="soft" onClick={() => setHookRows((rows) => [...rows, { event: "pre_tool", command: "", args: [], name: "", timeout_sec: 10 }])}>
                    <IconPlus size={12} /> 添加钩子
                  </Button>
                </div>
              </section>
            </div>
          )}

          {tab === "display" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">主题颜色</h3>
                <div className="grid grid-cols-3 gap-2">
                  {(["light", "dark", "system"] as Theme[]).map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => { setTheme(t); applyTheme(t) }}
                      className={cls("flex flex-col items-center gap-1.5 rounded-lg border px-2 py-3 transition-colors", theme === t ? "border-accent bg-accent-soft/40" : "border-line bg-panel2 hover:bg-hover")}
                    >
                      {t === "light" ? <IconSun size={16} /> : t === "dark" ? <IconMoon size={16} /> : <IconLayers size={16} />}
                      <span className="text-xs font-medium text-ink">{t === "light" ? "亮色" : t === "dark" ? "暗色" : "跟随系统"}</span>
                      {theme === t && <IconCheck size={12} className="text-accent" />}
                    </button>
                  ))}
                </div>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">界面语言</h3>
                <div className="flex gap-2">
                  {(["zh", "en"] as const).map((l) => (
                    <Button key={l} variant={lang === l ? "primary" : "outline"} size="sm" onClick={() => setLang(l)}>
                      {l === "zh" ? "中文" : "English"}
                    </Button>
                  ))}
                </div>
              </section>

              <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">展示开关</h3>
                {displayKeys.map(({ key, label }) => (
                  <label key={key} className="flex items-center justify-between rounded-lg px-2 py-2">
                    <span className="text-[13px] text-ink2">{label}</span>
                    <Toggle checked={Boolean(cfg.agent[key])} onChange={(v) => patchAgent({ [key]: v })} />
                  </label>
                ))}
              </section>
            </div>
          )}

          {tab === "memory" && <MemorySection />}

          {tab === "mcp" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">MCP 服务器</h3>
                <p className="text-xs leading-relaxed text-ink3">通过 stdio 启动的 MCP 服务器，工具以 mcp__名称__工具 注册给模型。</p>
                <div className="flex flex-col gap-2">
                  {mcpRows.length === 0 && <p className="py-2 text-xs text-ink3">暂未配置。</p>}
                  {mcpRows.map((s, i) => (
                    <div key={i} className="flex flex-col gap-2 rounded-lg border border-line bg-panel2 p-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-hover text-ink2">
                          <IconGlobe size={13} />
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">{s.name}</span>
                        <IconButton title="删除" onClick={() => setMcpRows((rows) => rows.filter((_, idx) => idx !== i))} className="h-7 w-7 text-ink3 hover:text-err">
                          <IconTrash size={12} />
                        </IconButton>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <Field label="命令">
                          <TextInput value={s.command} onChange={(e) => patchMcp(i, { command: e.target.value })} placeholder="如 npx" />
                        </Field>
                        <Field label="参数">
                          <TextInput value={s.args.join(" ")} onChange={(e) => patchMcp(i, { args: e.target.value.split(/\s+/).filter(Boolean) })} placeholder="空格分隔" />
                        </Field>
                      </div>
                      <Field label="只读工具">
                        <TextInput value={s.readonly_tools.join(" ")} onChange={(e) => patchMcp(i, { readonly_tools: e.target.value.split(/\s+/).filter(Boolean) })} placeholder="空格分隔，可空" />
                      </Field>
                    </div>
                  ))}
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="soft" onClick={() => setMcpRows((rows) => [...rows, { name: `mcp-${rows.length + 1}`, command: "", args: [], readonly_tools: [] }])}>
                      <IconPlus size={12} /> 添加服务器
                    </Button>
                    <Button size="sm" variant="soft" onClick={() => setShowRegistry(true)}>
                      <IconGlobe size={12} /> 从 Registry 浏览
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
                  <h3 className="text-sm font-semibold text-ink">项目记忆（AGENTS.md）</h3>
                  <Button size="sm" variant="soft" onClick={handleSaveMd} disabled={busy === "save"}>
                    {busy === "save" ? <Spinner size={11} /> : "保存"}
                  </Button>
                </div>
                <p className="text-xs leading-relaxed text-ink3">每轮注入给模型的项目级指令，跨会话生效。</p>
                {agentsMd ? (
                  <>
                    <TextArea
                      rows={8}
                      value={agentsMd.content}
                      onChange={(e) => {
                        dirtyRef.current.md = true
                        setAgentsMd((a) => (a ? { ...a, content: e.target.value, chars: e.target.value.length } : a))
                      }}
                      placeholder="例如：本项目使用 pnpm；提交信息用中文；不要动 legacy/ 目录…"
                      className="font-mono text-[12.5px]"
                    />
                    <span className="text-right text-[10.5px] text-ink3">
                      {agentsMd.chars} / {agentsMd.max_fragment_chars} 字符
                    </span>
                  </>
                ) : (
                  <p className="text-xs text-ink3">加载中…</p>
                )}
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">上下文管理</h3>
                <p className="text-xs leading-relaxed text-ink3">上下文接近预算时，旧对话自动压缩为结构化摘要。</p>
                <Field label="自动压缩阈值（%）">
                  <TextInput type="number" value={cfg.context.compact_threshold ?? 85} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, context: { ...c.context, compact_threshold: Number(e.target.value) || 85 } } : c)) }} />
                </Field>
                <Field label="上下文预算（tokens）">
                  <TextInput type="number" value={cfg.context.max_context_tokens ?? 0} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, context: { ...c.context, max_context_tokens: Number(e.target.value) || 0 } } : c)) }} />
                </Field>
                <Field label="保留最近原文（条）">
                  <TextInput type="number" value={cfg.context.keep_recent_messages ?? 20} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, context: { ...c.context, keep_recent_messages: Number(e.target.value) || 0 } } : c)) }} />
                </Field>
              </section>

              <section className="rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">工作目录</h3>
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
                <span className="flex items-center gap-1.5"><Spinner size={12} /> 保存中…</span>
              ) : (
                "保存设置"
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
