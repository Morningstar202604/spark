import { memo, useEffect, useRef, useState } from "react"
import { Terminal as XTerm } from "@xterm/xterm"
import { FitAddon } from "@xterm/addon-fit"
import "@xterm/xterm/css/xterm.css"
import { ptyClose, ptyOpen, ptyResize, ptyWrite, streamPty } from "../api"
import { Button, IconButton, Spinner } from "./ui"
import { IconTerminal, IconX } from "./icons"
import { useUiStore } from "../store/uiStore"

const TAB_ID = "main"

/**
 * 内置终端（P1-5）：xterm.js 6.0.0 + 后端真实 PTY。
 *
 * 通道：后端不支持 WebSocket，读侧走 SSE（streamPty），写侧走 POST。
 * 关闭弹窗只是断开前端附着（shell 保持后台常驻，重开自动恢复）；
 * 「结束会话」才真正 kill shell。
 */
export const Terminal = memo(function Terminal() {
  const open = useUiStore((s) => s.terminalOpen)
  const close = useUiStore((s) => s.closeTerminal)
  const containerRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<XTerm | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [ready, setReady] = useState(false)
  const [exited, setExited] = useState(false)
  const [cwd, setCwd] = useState("")

  useEffect(() => {
    if (!open) return
    setReady(false)
    setExited(false)
    const el = containerRef.current
    if (!el) return

    const term = new XTerm({
      fontSize: 12.5,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      theme: {
        background: "#0d1117",
        foreground: "#d6deeb",
        cursor: "#7aa2f7",
        selectionBackground: "#283550",
      },
      scrollback: 5000,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)
    fit.fit()
    xtermRef.current = term
    fitRef.current = fit
    term.focus()

    let cancelled = false

    async function boot() {
      try {
        const info = await ptyOpen(TAB_ID)
        setCwd(info.cwd)
      } catch {
        term.writeln("\x1b[31m[无法连接后端 PTY——Windows 不支持，或 pty extras 未安装]\x1b[0m")
        setReady(true)
        return
      }
      // 同步终端尺寸给后端
      const dims = fitRef.current
      if (dims) {
        try {
          const { cols, rows } = term
          await ptyResize(TAB_ID, rows, cols)
        } catch { /* ignore */ }
      }
      setReady(true)

      const ac = new AbortController()
      abortRef.current = ac
      try {
        await streamPty(
          TAB_ID,
          (ev) => {
            if (cancelled) return
            if (ev.type === "pty_data") {
              term.write(ev.data)
            } else if (ev.type === "pty_exit") {
              setExited(true)
              term.writeln("\r\n\x1b[90m[进程已退出 —— 按任意键在同一标签页重启]\x1b[0m")
            }
          },
          ac.signal,
        )
      } catch {
        /* 弹窗关闭时 abort 会走这里 */
      }
    }

    const onDataSub = term.onData((d) => {
      if (exited) {
        // 退出后按键重启 shell
        setExited(false)
        void ptyOpen(TAB_ID).catch(() => undefined)
        return
      }
      void ptyWrite(TAB_ID, d).catch(() => undefined)
    })

    const onResize = () => {
      try {
        fit.fit()
        void ptyResize(TAB_ID, term.rows, term.cols).catch(() => undefined)
      } catch { /* ignore */ }
    }
    window.addEventListener("resize", onResize)

    void boot()

    return () => {
      cancelled = true
      window.removeEventListener("resize", onResize)
      onDataSub.dispose()
      abortRef.current?.abort()
      abortRef.current = null
      try { term.dispose() } catch { /* ignore */ }
      xtermRef.current = null
      fitRef.current = null
    }
  }, [open])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-0 backdrop-blur-[2px]" onClick={close}>
      <div
        className="anim-fade flex h-[82vh] w-full sm:w-[min(94vw,980px)] flex-col overflow-hidden border-t border-line bg-[#0d1117] sm:rounded-2xl sm:border shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="内置终端"
      >
        <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2.5">
          <IconTerminal size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">内置终端</span>
          {cwd && <span className="ml-1 hidden font-mono text-[11px] text-ink3 sm:inline">{cwd}</span>}
          {!ready && <Spinner size={12} className="ml-2 text-ink3" />}
          <div className="ml-auto flex items-center gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                void ptyClose(TAB_ID).then(() => {
                  setExited(true)
                  xtermRef.current?.writeln("\r\n\x1b[90m[会话已结束 —— 按任意键重启]\x1b[0m")
                })
              }}
            >
              结束会话
            </Button>
            <IconButton title="关闭" onClick={close}>
              <IconX size={15} />
            </IconButton>
          </div>
        </div>
        <div ref={containerRef} className="min-h-0 flex-1 p-2" />
      </div>
    </div>
  )
})
