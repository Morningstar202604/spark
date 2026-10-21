import { memo, useEffect } from "react"
import { IconButton } from "./ui"
import { IconCommand, IconX } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

const ROWS: Array<{ keys: string[]; zh: string; en: string }> = [
  { keys: ["Ctrl", "K"], zh: "命令面板 / 搜索会话", en: "Command palette / search sessions" },
  { keys: ["Ctrl", "N"], zh: "新建会话", en: "New session" },
  { keys: ["Ctrl", "\\"], zh: "显示 / 隐藏侧栏", en: "Toggle sidebar" },
  { keys: ["Ctrl", "`"], zh: "内置终端", en: "Built-in terminal" },
  { keys: ["Enter"], zh: "发送（Shift+Enter 换行）", en: "Send (Shift+Enter for newline)" },
  { keys: ["Esc"], zh: "停止生成 / 关闭弹层", en: "Stop generating / close overlay" },
  { keys: ["/" ], zh: "斜杠命令补全（.spark/commands）", en: "Slash-command completion (.spark/commands)" },
  { keys: ["@"], zh: "引用工作区文件", en: "Reference a workspace file" },
  { keys: ["?"], zh: "打开这个帮助面板", en: "Open this help panel" },
]

/** 快捷键帮助（Claude Code 的 `?` 面板）。 */
export const ShortcutsHelp = memo(function ShortcutsHelp() {
  const open = useUiStore((s) => s.helpOpen)
  const close = useUiStore((s) => s.closeHelp)
  const lang = useUiStore((s) => s.lang)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, close])

  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]"
      onClick={close}
      role="dialog"
      aria-modal="true"
      aria-label={t("help.title", lang)}
    >
      <div
        className="anim-fade flex max-h-[86vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconCommand size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("help.title", lang)}</span>
          <IconButton title={t("common.close", lang)} onClick={close} className="ml-auto">
            <IconX size={15} />
          </IconButton>
        </div>
        <div className="overflow-y-auto p-3">
          <table className="w-full border-separate border-spacing-y-1 text-[12.5px]">
            <tbody>
              {ROWS.map((row) => (
                <tr key={row.keys.join("+") + row.en}>
                  <td className="w-[38%] pr-3 align-middle">
                    <span className="flex flex-wrap gap-1">
                      {row.keys.map((k) => (
                        <kbd
                          key={k}
                          className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line bg-panel2 px-1 font-mono text-[10px] text-ink2"
                        >
                          {k}
                        </kbd>
                      ))}
                    </span>
                  </td>
                  <td className="align-middle text-ink2">{lang === "zh" ? row.zh : row.en}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="border-t border-line px-4 py-2 text-[11px] text-ink3">{t("help.footer", lang)}</div>
      </div>
    </div>
  )
})
