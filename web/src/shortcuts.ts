import type { TranslationKey } from "./i18n"

/** 可重新绑定的动作。`send`/`stop` 是行为键，不是全局开关键。 */
export type ShortcutAction =
  | "newSession"
  | "palette"
  | "toggleSidebar"
  | "terminal"
  | "help"
  | "stop"
  | "send"

export type BindingMap = Record<ShortcutAction, string>

export const DEFAULT_BINDINGS: BindingMap = {
  newSession: "Mod+N",
  palette: "Mod+K",
  toggleSidebar: "Mod+\\",
  terminal: "Mod+`",
  help: "?",
  stop: "Escape",
  send: "Enter",
}

const STORAGE_KEY = "spark.shortcuts.v1"

export function actionLabelKey(action: ShortcutAction): TranslationKey {
  return `shortcuts.action.${action}` as TranslationKey
}

/** "Mod+N" → ["Ctrl", "N"]（macOS 上 Mod 显示为 ⌘）。 */
export function bindingSignature(binding: string): string {
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform)
  return binding
    .split("+")
    .map((p) => (p === "Mod" ? (isMac ? "⌘" : "Ctrl") : p))
    .join("+")
}

export function loadBindings(): BindingMap {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_BINDINGS }
    const parsed = JSON.parse(raw) as Partial<BindingMap>
    const out = { ...DEFAULT_BINDINGS }
    for (const key of Object.keys(DEFAULT_BINDINGS) as ShortcutAction[]) {
      const v = parsed[key]
      if (typeof v === "string" && v.trim()) out[key] = v
    }
    return out
  } catch {
    return { ...DEFAULT_BINDINGS }
  }
}

export function saveBindings(map: BindingMap): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map))
  } catch {
    /* storage 不可用时保持内存态，不阻塞 UI */
  }
}

export function resetBindings(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* ignore */
  }
}

/**
 * 判断一次键盘事件是否命中某个绑定。
 *
 * 不用 `KeyboardEvent.key` 直接比对的原因：Ctrl+\\ 在部分键盘布局下 key 是
 * "|"，Ctrl+` 在中文输入法下会变成 Process。这里以 code 为准做兜底。
 */
export function matchesBinding(e: KeyboardEvent, binding: string): boolean {
  const parts = binding.split("+")
  const needMod = parts.includes("Mod")
  const needAlt = parts.includes("Alt")
  const needShift = parts.includes("Shift")
  const key = parts[parts.length - 1].toLowerCase()

  if (needMod !== (e.ctrlKey || e.metaKey)) return false
  if (needAlt !== e.altKey) return false

  const target = e.target as HTMLElement | null
  const tag = target?.tagName?.toLowerCase()
  const inField = tag === "input" || tag === "textarea" || Boolean(target?.isContentEditable)

  // 单字符绑定（? / Enter / Escape）在输入框里必须让路，否则用户没法打字。
  if (inField && !needMod && !needAlt && key.length <= 6) return false

  const k = e.key.toLowerCase()
  if (k === key) {
    if (needShift && !e.shiftKey) return false
    if (!needShift && e.shiftKey && key.length === 1) return false
    return true
  }
  const code = (e.code || "").toLowerCase()
  if (code === `key${key}` || code === `digit${key}`) return true
  if (key === "\\" && (code === "backslash" || k === "|")) return true
  if (key === "`" && (code === "backquote" || k === "~")) return true
  return false
}
