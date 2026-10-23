import { useCallback, useEffect, useState } from "react"
import { Badge, Button, cls } from "./ui"
import { IconRefresh } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"
import {
  DEFAULT_BINDINGS,
  actionLabelKey,
  loadBindings,
  saveBindings,
  resetBindings,
  bindingSignature,
  type BindingMap,
  type ShortcutAction,
} from "../shortcuts"

const ACTIONS: ShortcutAction[] = [
  "newSession",
  "palette",
  "toggleSidebar",
  "terminal",
  "help",
  "stop",
  "send",
]

function keyFromEvent(e: KeyboardEvent): string | null {
  const mods: string[] = []
  if (e.ctrlKey || e.metaKey) mods.push("Mod")
  if (e.altKey) mods.push("Alt")
  if (e.shiftKey) mods.push("Shift")
  const k = e.key
  if (k === "Control" || k === "Meta" || k === "Alt" || k === "Shift") return null
  const norm = k === " " ? "Space" : k.length === 1 ? k.toUpperCase() : k
  return [...mods, norm].join("+")
}

/** 快捷键：查看 + 重新绑定（持久化到 localStorage，与运行时读同一份）。 */
export default function ShortcutsSection() {
  const lang = useUiStore((s) => s.lang)
  const [bindings, setBindings] = useState<BindingMap>(DEFAULT_BINDINGS)
  const [capturing, setCapturing] = useState<ShortcutAction | null>(null)
  const [notice, setNotice] = useState("")

  const load = useCallback(() => {
    setBindings(loadBindings())
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!capturing) return
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (e.key === "Escape") {
        setCapturing(null)
        return
      }
      const combo = keyFromEvent(e)
      if (!combo) return
      const conflict = ACTIONS.find((a) => a !== capturing && bindings[a] === combo)
      if (conflict) {
        setNotice(`${combo} ${t("shortcuts.conflict", lang)}: ${t(actionLabelKey(conflict), lang)}`)
        return
      }
      const next = { ...bindings, [capturing]: combo }
      setBindings(next)
      saveBindings(next)
      setCapturing(null)
      setNotice("")
    }
    window.addEventListener("keydown", onKey, true)
    return () => window.removeEventListener("keydown", onKey, true)
  }, [capturing, bindings, lang])

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("shortcuts.title", lang)}</h3>
          <Button
            size="sm"
            variant="soft"
            className="ml-auto shrink-0"
            onClick={() => {
              resetBindings()
              setBindings(DEFAULT_BINDINGS)
              setNotice("")
            }}
          >
            <IconRefresh size={12} /> {t("shortcuts.resetAll", lang)}
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-ink3">{t("shortcuts.desc", lang)}</p>

        <div className="flex flex-col gap-1.5">
          {ACTIONS.map((a) => (
            <div key={a} className="flex items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2">
              <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink2">{t(actionLabelKey(a), lang)}</span>
              {bindings[a] === DEFAULT_BINDINGS[a] ? null : <Badge tone="accent">{t("shortcuts.custom", lang)}</Badge>}
              <button
                type="button"
                onClick={() => setCapturing(a)}
                className={cls(
                  "shrink-0 rounded-md border px-2 py-1 font-mono text-[11px] transition-colors",
                  capturing === a
                    ? "border-accent bg-accent-soft text-accent"
                    : "border-line bg-panel text-ink2 hover:border-line2",
                )}
              >
                {capturing === a ? t("shortcuts.press", lang) : bindingSignature(bindings[a])}
              </button>
              {bindings[a] !== DEFAULT_BINDINGS[a] && (
                <button
                  type="button"
                  className="shrink-0 text-[10.5px] text-ink3 underline-offset-2 hover:text-ink hover:underline"
                  onClick={() => {
                    const next = { ...bindings, [a]: DEFAULT_BINDINGS[a] }
                    setBindings(next)
                    saveBindings(next)
                  }}
                >
                  {t("shortcuts.revert", lang)}
                </button>
              )}
            </div>
          ))}
        </div>

        {notice && <div className="rounded-lg border border-warn/30 bg-warn/5 px-3 py-2 text-[11.5px] text-warn">{notice}</div>}
        <p className="text-[10.5px] leading-relaxed text-ink3">{t("shortcuts.inputHint", lang)}</p>
      </section>

      <section className="rounded-xl border border-line bg-panel p-4">
        <h3 className="text-sm font-semibold text-ink">{t("shortcuts.otherTitle", lang)}</h3>
        <ul className="mt-2 flex flex-col gap-1.5 text-[12px] text-ink3">
          <li className="flex gap-2"><code className="font-mono text-ink2">Enter</code> {t("shortcuts.enter", lang)}</li>
          <li className="flex gap-2"><code className="font-mono text-ink2">Shift+Enter</code> {t("shortcuts.shiftEnter", lang)}</li>
          <li className="flex gap-2"><code className="font-mono text-ink2">/</code> {t("shortcuts.slash", lang)}</li>
          <li className="flex gap-2"><code className="font-mono text-ink2">@</code> {t("shortcuts.at", lang)}</li>
          <li className="flex gap-2"><code className="font-mono text-ink2">Esc</code> {t("shortcuts.esc", lang)}</li>
        </ul>
      </section>
    </div>
  )
}
