import { memo, useEffect, useState } from "react"
import { fetchFsRead, fetchFsTree, type FsEntry } from "../api"
import { cls, IconButton, Spinner } from "./ui"
import { IconChevronRight, IconFile, IconFolder, IconX } from "./icons"
import { useUiStore } from "../store/uiStore"
import { useSessionStore } from "../store/sessionStore"
import hljs from "highlight.js/lib/common"

/** 行号前缀高亮的文件预览 */
function CodePreview({ code, name }: { code: string; name: string }) {
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : ""
  let html: string
  try {
    const lang = ext && hljs.getLanguage(ext) ? ext : "plaintext"
    html = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
  } catch {
    html = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  }
  const lines = code.split("\n")
  return (
    <div className="overflow-auto font-mono text-[12px] leading-[1.55]">
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((_, i) => (
            <tr key={i} className="align-top">
              <td className="w-9 pr-3 text-right text-ink3 select-none">{i + 1}</td>
              <td className="pr-4">
                <span dangerouslySetInnerHTML={{ __html: html.split("\n")[i] ?? "" }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export const FileViewer = memo(function FileViewer() {
  const open = useUiStore((s) => s.filesOpen)
  const close = useUiStore((s) => s.closeFiles)
  const workdir = useSessionStore((s) => s.status?.workdir ?? "")

  const [stack, setStack] = useState<string[]>([]) // 相对路径栈（[] = 工作目录根）
  const [entries, setEntries] = useState<FsEntry[] | null>(null)
  const [err, setErr] = useState("")
  const [busy, setBusy] = useState(false)
  const [file, setFile] = useState<{ name: string; content: string; truncated?: boolean; binary?: boolean } | null>(null)
  const [fileBusy, setFileBusy] = useState(false)

  async function loadDir(dir: string) {
    setBusy(true)
    setErr("")
    setFile(null)
    try {
      const data = await fetchFsTree(dir)
      if (data.error) setErr(data.error)
      else setEntries(data.entries ?? [])
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      setEntries(null)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (open) void loadDir("")
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, close])

  async function openFile(name: string) {
    const path = stack.length ? [...stack, name].join("/") : name
    setFileBusy(true)
    setFile(null)
    try {
      const data = await fetchFsRead(path)
      if (data.error) setFile({ name, content: data.error })
      else setFile({ name: data.name ?? name, content: data.content ?? "", truncated: data.truncated, binary: data.binary })
    } catch (e) {
      setFile({ name, content: e instanceof Error ? e.message : String(e) })
    } finally {
      setFileBusy(false)
    }
  }

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={close}>
      <div
        className="anim-fade flex h-[min(88vh,640px)] w-[min(96vw,980px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="文件查看器"
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconFolder size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">文件查看器</span>
          <span className="ml-2 min-w-0 flex-1 truncate font-mono text-[11px] text-ink3" title={workdir}>
            {workdir || "—"}
          </span>
          <IconButton title="关闭" onClick={close}>
            <IconX size={15} />
          </IconButton>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* 左栏：目录树 */}
          <div className="flex w-[260px] shrink-0 flex-col border-r border-line">
            {/* 面包屑 */}
            <div className="flex items-center gap-1 border-b border-line px-3 py-2 text-[11px]">
              <button
                type="button"
                onClick={() => {
                  setStack([])
                  void loadDir("")
                }}
                className={cls("rounded px-1 py-0.5 font-medium", stack.length === 0 ? "text-ink" : "text-ink3 hover:bg-hover hover:text-ink2")}
              >
                ~
              </button>
              {stack.map((seg, i) => (
                <span key={i} className="flex min-w-0 items-center gap-1 text-ink3">
                  <IconChevronRight size={10} className="shrink-0" />
                  <button
                    type="button"
                    onClick={() => {
                      const prev = stack.slice(0, i + 1)
                      setStack(prev)
                      void loadDir(prev.join("/"))
                    }}
                    className={cls("truncate rounded px-1 py-0.5", i === stack.length - 1 ? "text-ink" : "hover:bg-hover hover:text-ink2")}
                  >
                    {seg}
                  </button>
                </span>
              ))}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {busy ? (
                <div className="flex justify-center py-10">
                  <Spinner size={16} className="text-accent" />
                </div>
              ) : err ? (
                <p className="px-2 py-4 text-xs text-err">{err}</p>
              ) : (
                <ul className="flex flex-col gap-0.5">
                  {entries?.map((e) =>
                    e.type === "dir" ? (
                      <li key={e.name}>
                        <button
                          type="button"
                          onClick={() => {
                            const next = stack.length ? [...stack, e.name] : [e.name]
                            setStack(next)
                            void loadDir(next.join("/"))
                          }}
                          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12.5px] text-ink2 transition-colors hover:bg-hover"
                        >
                          <IconFolder size={13} className="shrink-0 text-accent" />
                          <span className="truncate font-medium">{e.name}</span>
                          <IconChevronRight size={11} className="ml-auto shrink-0 text-ink3" />
                        </button>
                      </li>
                    ) : (
                      <li key={e.name}>
                        <button
                          type="button"
                          onClick={() => void openFile(e.name)}
                          className={cls(
                            "flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12.5px] transition-colors hover:bg-hover",
                            file?.name === e.name ? "bg-selected text-ink" : "text-ink2",
                          )}
                        >
                          <IconFile size={13} className="shrink-0 text-ink3" />
                          <span className="truncate">{e.name}</span>
                          {e.size !== undefined && <span className="ml-auto shrink-0 font-mono text-[10px] text-ink3">{fmtSize(e.size)}</span>}
                        </button>
                      </li>
                    ),
                  )}
                  {entries?.length === 0 && <p className="px-2 py-6 text-center text-xs text-ink3">空目录</p>}
                </ul>
              )}
            </div>
          </div>

          {/* 右栏：文件内容 */}
          <div className="flex min-w-0 flex-1 flex-col bg-panel2/60">
            {fileBusy ? (
              <div className="flex flex-1 items-center justify-center">
                <Spinner size={18} className="text-accent" />
              </div>
            ) : file ? (
              <>
                <div className="flex items-center gap-2 border-b border-line px-4 py-2">
                  <IconFile size={13} className="text-ink3" />
                  <span className="truncate font-mono text-[12px] font-medium text-ink">{file.name}</span>
                  {file.binary && <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] text-ink3">二进制文件</span>}
                  {file.truncated && <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] text-warn">内容过长已截断</span>}
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-3">
                  {file.binary ? (
                    <p className="py-8 text-center text-xs text-ink3">该文件为二进制内容，不支持文本预览。</p>
                  ) : (
                    <CodePreview code={file.content} name={file.name} />
                  )}
                </div>
              </>
            ) : (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 text-ink3">
                <IconFile size={26} />
                <p className="text-xs">选择左侧文件查看内容</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
})

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}
