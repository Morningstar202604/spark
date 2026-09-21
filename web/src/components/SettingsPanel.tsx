import { useEffect, useRef, useState } from "react"
import type { FullConfig, ModelProfile, ProbeResult, Status, McpServer, SettingsPayload } from "../types"
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

const tabs: { key: Tab; label: string }[] = [
  { key: "model", label: "模型" },
  { key: "agent", label: "Agent" },
  { key: "security", label: "权限" },
  { key: "display", label: "展示" },
  { key: "memory", label: "记忆" },
  { key: "mcp", label: "MCP" },
  { key: "project", label: "项目" },
]

const displayOptions: { key: keyof FullConfig["agent"] & `show_${string}`; label: string; desc: string }[] = [
  { key: "show_thinking", label: "思考过程", desc: "模型的 reasoning 流式块（默认折叠，可点击展开）" },
  { key: "show_tools", label: "工具调用", desc: "工具执行的名称、参数与结果块" },
  { key: "show_plan", label: "计划卡片", desc: "Plan 工具产出的任务清单卡片" },
  { key: "show_context", label: "上下文用量", desc: "顶部 ContextMeter 百分比与用量" },
  { key: "show_keywords", label: "会话关键词", desc: "侧栏会话列表的关键词徽标" },
  { key: "show_notices", label: "系统提示", desc: "压缩、错误等临时通知横幅" },
]

export default function SettingsPanel({ status, onClose, onSaved }: Props) {
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
  const dirtyRef = useRef({ cfg: false, mcp: false, md: false })

  async function refreshProfiles() {
    try {
      const data = await listModelProfiles()
      setProfiles(data.profiles)
    } catch {
      /* ignore */
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
      })
      .catch(() => undefined)
    fetchAgentsMd().then((d) => {
      if (!dirtyRef.current.md) setAgentsMd(d)
    }).catch(() => undefined)
  }, [])

  function patchAgent(patch: Partial<FullConfig["agent"]>) {
    dirtyRef.current.cfg = true
    setCfg((c) => (c ? { ...c, agent: { ...c.agent, ...patch } } : c))
  }

  function patchMcp(i: number, patch: Partial<McpServer>) {
    dirtyRef.current.mcp = true
    setMcpRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
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
        setMcpErrors([])
      })
      .catch(() => undefined)
    fetchAgentsMd().then(setAgentsMd).catch(() => undefined)
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
    }
    try {
      const data = await saveSettings(payload)
      onSaved(data.status)
      setCfg(data.config)
      setMcpErrors(data.mcp_errors || [])
      setMcpRows(data.config.mcp_servers.map((s) => ({ ...s, args: [...s.args], readonly_tools: [...s.readonly_tools] })))
      setNotice("已保存并生效。")
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
      setNotice("项目记忆已保存，下一轮对话生效。")
    } catch (e) {
      setProbe({ ok: false, error: String(e instanceof Error ? e.message : e) })
    }
  }

  return (
    <div className="animate-fade fixed inset-0 z-40">
      <div className="absolute inset-0 bg-black/55" onClick={onClose} />
      <aside className="animate-in absolute top-0 right-0 flex h-full w-full flex-col border-l border-spark-line bg-spark-panel sm:w-[38rem]">
        <div className="flex items-center justify-between border-b border-spark-line px-4 py-3 sm:px-5">
          <h2 className="text-base font-bold">设置</h2>
          <div className="flex items-center gap-2">
            <button type="button" onClick={resetForm} title="放弃修改，恢复当前配置" className="rounded-lg bg-spark-line px-3 py-1.5 text-xs text-spark-muted transition-colors hover:text-spark-text">
              重置
            </button>
            <button type="button" onClick={onClose} className="rounded-lg bg-spark-line px-3 py-1.5 text-sm text-spark-text transition-colors hover:text-spark-accent">
              关闭
            </button>
          </div>
        </div>
        <div className="flex gap-1 overflow-x-auto border-b border-spark-line px-3 py-2 sm:px-4">
          {tabs.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={`shrink-0 rounded-lg px-3 py-1.5 text-xs font-bold transition-colors ${
                tab === t.key ? "bg-spark-accent text-teal-950" : "text-spark-muted hover:bg-spark-line hover:text-spark-text"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-x-hidden overflow-y-auto p-4 sm:p-5">
          {tab === "model" && (
            <div className="flex flex-col gap-4">
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">模型档案</div>
                <p className="text-xs text-spark-muted">保存多个模型配置，随时一键切换。当前生效的档案会高亮显示。</p>
                {profiles.length === 0 && <p className="text-xs text-spark-muted">暂无档案，在下方添加第一个模型。</p>}
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
                          {p.active && <span className="shrink-0 rounded bg-spark-accent px-1.5 py-0.5 text-[10px] text-teal-950">使用中</span>}
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
                                setNotice(`已切换到 ${p.name}。`)
                              } catch (e) {
                                setProbe({ ok: false, error: String(e instanceof Error ? e.message : e) })
                              } finally {
                                setBusy(null)
                              }
                            }}
                            className="flex-1 rounded-lg bg-spark-accent px-3 py-1.5 text-xs font-bold text-teal-950 hover:opacity-90 disabled:opacity-50 sm:flex-none"
                          >
                            使用
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
                          编辑
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
                          className="flex-1 rounded-lg bg-red-950 px-3 py-1.5 text-xs font-bold text-spark-err hover:opacity-80 disabled:opacity-50 sm:flex-none"
                        >
                          删除
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
                    + 添加模型
                  </button>
                )}
              </div>

              {showProfileForm && (
                <div className={sectionCls}>
                  <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">
                    {profileForm.id ? "编辑模型" : "新模型"}
                  </div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <label className={labelCls}>
                      显示名称
                      <input
                        value={profileForm.name}
                        onChange={(e) => setProfileForm((f) => ({ ...f, name: e.target.value }))}
                        placeholder="如 GPT-4o / 本地 qwen"
                        className={inputCls}
                      />
                    </label>
                    <label className={labelCls}>
                      Provider
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
                      Base URL
                      <input
                        value={profileForm.base_url}
                        onChange={(e) => setProfileForm((f) => ({ ...f, base_url: e.target.value }))}
                        placeholder="https://api.example.com/v1"
                        className={inputCls}
                      />
                    </label>
                    <label className={`${labelCls} sm:col-span-2`}>
                      Model
                      <input
                        value={profileForm.model}
                        onChange={(e) => setProfileForm((f) => ({ ...f, model: e.target.value }))}
                        placeholder="model-id"
                        className={inputCls}
                      />
                    </label>
                    <label className={`${labelCls} sm:col-span-2`}>
                      API Key
                      <input
                        value={profileForm.api_key}
                        onChange={(e) => setProfileForm((f) => ({ ...f, api_key: e.target.value }))}
                        type="password"
                        placeholder={
                          profileForm.id
                            ? profiles.find((p) => p.id === profileForm.id)?.has_api_key
                              ? `留空保持 ${profiles.find((p) => p.id === profileForm.id)?.api_key_masked}`
                              : "your-api-key-here"
                            : "留空则沿用当前全局 Key"
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
                          setNotice("模型档案已保存。")
                        } catch (e) {
                          setProbe({ ok: false, error: String(e instanceof Error ? e.message : e) })
                        } finally {
                          setBusy(null)
                        }
                      }}
                      className="rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-teal-950 hover:opacity-90 disabled:opacity-50"
                    >
                      保存档案
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
                      {busy === "test" ? "测试中…" : "测试连接"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowProfileForm(false)}
                      className="rounded-lg bg-spark-line px-4 py-2 text-sm font-bold text-spark-text hover:opacity-80"
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {probe === null ? (
                <p className="text-xs text-spark-muted">测试标准：连续两轮真实文本，流式完整结束。点「测试连接」后结果在这里显示。</p>
              ) : (
                <div
                  className={`rounded-lg border px-3 py-2 font-mono text-xs whitespace-pre-wrap ${
                    probe.ok ? "border-emerald-800 text-spark-ok" : "border-red-900 text-spark-err"
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
                审批模式
                <select value={cfg?.agent.approval || "suggest"} onChange={(e) => patchAgent({ approval: e.target.value })} className={inputCls}>
                  <option value="suggest">suggest（每步确认）</option>
                  <option value="auto-edit">auto-edit（自动改文件）</option>
                  <option value="full-auto">full-auto（全自动）</option>
                </select>
              </label>
              <label className="flex items-center gap-2 text-sm text-spark-text">
                <input type="checkbox" checked={cfg?.agent.workdir_only ?? true} onChange={(e) => patchAgent({ workdir_only: e.target.checked })} className="h-4 w-4 accent-teal-300" />
                限制在工作目录内（workdir_only）
              </label>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <label className={labelCls}>
                  最大工具轮数
                  <input type="number" min={1} value={cfg?.agent.max_tool_rounds ?? 30} onChange={(e) => patchAgent({ max_tool_rounds: Number(e.target.value) })} className={inputCls} />
                </label>
                <label className={labelCls}>
                  Shell 超时（秒）
                  <input type="number" min={1} value={cfg?.agent.shell_timeout_sec ?? 60} onChange={(e) => patchAgent({ shell_timeout_sec: Number(e.target.value) })} className={inputCls} />
                </label>
                <label className={labelCls}>
                  输出上限（字符）
                  <input type="number" min={200} value={cfg?.agent.max_output_chars ?? 8000} onChange={(e) => patchAgent({ max_output_chars: Number(e.target.value) })} className={inputCls} />
                </label>
              </div>
              <button type="button" onClick={handleSave} disabled={busy !== null} className="self-start rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-teal-950 hover:opacity-90 disabled:opacity-50">
                {busy === "save" ? "保存中…" : "保存设置"}
              </button>
            </div>
          )}

          {tab === "security" && (
            <div className="flex flex-col gap-4">
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">访问级别</div>
                <p className="text-xs text-spark-muted">限制模型对系统的操作范围，立即生效（下一轮对话起）。</p>
                <div className="flex flex-col gap-2">
                  {([
                    { key: "sandbox-only", title: "仅沙箱内访问", desc: "只读工作区文件，可分析和给建议，禁用命令执行与文件写入" },
                    { key: "workspace", title: "局部访问（推荐）", desc: "可读写文件、执行命令，但仅限工作目录内；系统目录与危险命令一律拦截" },
                    { key: "full-access", title: "完全访问", desc: "允许操作工作目录之外的路径；系统目录与 Spark 自身代码仍受保护" },
                    { key: "unrestricted", title: "无限制版", desc: "零防护：所有拦截全部关闭，包括 Spark 自身代码与配置，任何操作均放行；用户自定义保护路径仍生效" },
                  ] as const).map((opt) => (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => patchAgent({ sandbox_mode: opt.key })}
                      className={`rounded-lg border p-3 text-left transition-colors ${
                        (cfg?.agent.sandbox_mode || "workspace") === opt.key
                          ? opt.key === "unrestricted"
                            ? "border-red-500 bg-red-950/40"
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
                        {opt.key === "unrestricted" && <span className="rounded bg-red-900 px-1.5 py-0.5 text-[10px] text-red-200">危险</span>}
                      </div>
                      <p className="mt-1 text-xs text-spark-muted">{opt.desc}</p>
                    </button>
                  ))}
                </div>
              </div>
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">受保护路径</div>
                <p className="text-xs text-spark-muted">
                  每行一个绝对路径。访问这些路径（读/写/命令内引用）会被拦截。选择「无限制版」时，系统内置保护失效，仅保留此清单；其余档位额外默认保护 /etc、/root/.spark 及 Spark 自身代码。
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
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">运行环境探测</div>
                <p className="text-xs text-spark-muted">启动时自动探测的默认工具链，模型被告知同样的信息。</p>
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
                  <p className="text-xs text-spark-muted">暂无探测数据。</p>
                )}
              </div>
              <button type="button" onClick={handleSave} disabled={busy !== null} className="self-start rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-teal-950 hover:opacity-90 disabled:opacity-50">
                {busy === "save" ? "保存中…" : "保存权限设置"}
              </button>
            </div>
          )}

          {tab === "memory" && <MemorySection />}

          {tab === "display" && (
            <div className="flex flex-col gap-4">
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">前端展示开关</div>
                <p className="text-xs text-spark-muted">控制聊天界面中各元素的显示与隐藏，保存后立即生效。</p>
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
                          <span className="block text-sm font-bold text-spark-text">{opt.label}</span>
                          <span className="block text-xs text-spark-muted">{opt.desc}</span>
                        </span>
                      </label>
                    )
                  })}
                </div>
              </div>
              <button type="button" onClick={handleSave} disabled={busy !== null} className="self-start rounded-lg bg-spark-accent px-4 py-2 text-sm font-bold text-teal-950 hover:opacity-90 disabled:opacity-50">
                {busy === "save" ? "保存中…" : "保存展示设置"}
              </button>
            </div>
          )}

          {tab === "mcp" && (
            <div className="flex flex-col gap-3">
              <div className={sectionCls}>
                <p className="text-xs text-spark-muted">通过 stdio 启动的 MCP 服务器，其工具会以 mcp__名称__工具 注册给模型。</p>
                {mcpRows.length === 0 && <p className="text-xs text-spark-muted">暂未配置。</p>}
                {mcpRows.map((row, i) => (
                  <div key={i} className="flex flex-col gap-2 rounded-lg border border-spark-line bg-spark-bg p-3">
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <input value={row.name} onChange={(e) => patchMcp(i, { name: e.target.value })} placeholder="名称" className={inputCls} />
                      <input value={row.command} onChange={(e) => patchMcp(i, { command: e.target.value })} placeholder="命令 (如 npx)" className={inputCls} />
                    </div>
                    <input value={row.args.join(" ")} onChange={(e) => patchMcp(i, { args: e.target.value.split(" ") })} placeholder="参数（空格分隔）" className={inputCls} />
                    <input value={row.readonly_tools.join(" ")} onChange={(e) => patchMcp(i, { readonly_tools: e.target.value.split(" ") })} placeholder="只读工具名（空格分隔，可空）" className={inputCls} />
                    <button type="button" onClick={() => setMcpRows((rows) => rows.filter((_, idx) => idx !== i))} className="self-start rounded-lg bg-red-950 px-3 py-1.5 text-xs font-bold text-spark-err hover:opacity-80">
                      移除
                    </button>
                  </div>
                ))}
                <button type="button" onClick={() => setMcpRows((rows) => [...rows, { name: "", command: "", args: [], readonly_tools: [] }])} className="self-start rounded-lg bg-spark-line px-3 py-1.5 text-xs font-bold text-spark-text hover:opacity-80">
                  + 添加服务器
                </button>
                {mcpErrors.length > 0 && <div className="rounded-lg border border-red-900 px-3 py-2 text-xs text-spark-err">{mcpErrors.join("\n")}</div>}
                <button type="button" onClick={handleSave} disabled={busy !== null} className="self-start rounded-lg bg-spark-line px-4 py-2 text-sm font-bold text-spark-text hover:opacity-80 disabled:opacity-50">
                  {busy === "save" ? "保存中…" : "保存设置"}
                </button>
              </div>
            </div>
          )}

          {tab === "project" && (
            <div className="flex flex-col gap-4">
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">项目记忆（AGENTS.md）</div>
                <p className="text-xs text-spark-muted">每轮注入给模型的项目级指令，跨会话生效。超出片段上限会截断。</p>
                <textarea
                  value={agentsMd?.content ?? ""}
                  onChange={(e) => {
                    dirtyRef.current.md = true
                    setAgentsMd((m) => (m ? { ...m, content: e.target.value } : m))
                  }}
                  placeholder="例如：本项目使用 pnpm；提交信息用中文；不要动 legacy/ 目录…"
                  rows={10}
                  className={`${inputCls} resize-y font-mono text-xs leading-relaxed`}
                />
                <div className="flex items-center justify-between">
                  <span className={`text-xs ${(agentsMd?.chars || 0) > (agentsMd?.max_fragment_chars || 8000) ? "text-spark-err" : "text-spark-muted"}`}>
                    {agentsMd?.chars || 0} / {agentsMd?.max_fragment_chars || 8000} 字符
                  </span>
                  <button type="button" onClick={handleSaveMemory} className="rounded-lg bg-spark-line px-3 py-1.5 text-xs font-bold text-spark-text hover:opacity-80">
                    保存记忆
                  </button>
                </div>
              </div>
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">上下文管理</div>
                <p className="text-xs text-spark-muted">上下文接近预算时，旧对话自动压缩为结构化摘要。</p>
                <dl className="flex flex-col gap-1.5 text-xs">
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-spark-muted">自动压缩阈值</dt>
                    <dd className="text-spark-text">{cfg?.context.compact_threshold ? `${Math.round(cfg.context.compact_threshold * 100)}%` : "—"}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-spark-muted">上下文预算</dt>
                    <dd className="text-spark-text">{cfg?.context.max_context_tokens ?? "—"} tokens</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-spark-muted">保留最近原文</dt>
                    <dd className="text-spark-text">{cfg?.context.keep_recent_messages ?? 8} 条</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-spark-muted">AGENTS.md 片段上限</dt>
                    <dd className="text-spark-text">{cfg?.context.max_fragment_chars} 字符</dd>
                  </div>
                </dl>
              </div>
              <div className={sectionCls}>
                <div className="text-xs font-bold tracking-widest text-spark-accent uppercase">工作目录</div>
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
            模型 <b className="font-medium text-spark-text">{status?.model}</b>
          </span>
          <span>
            审批 <b className="font-medium text-spark-text">{status?.approval}</b>
          </span>
          <span className="ml-auto hidden font-mono text-[10px] sm:inline">session {status?.session_id}</span>
        </div>
      </aside>
    </div>
  )
}
