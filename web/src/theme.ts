export type Theme = "light" | "dark" | "system"

const STORAGE_KEY = "spark-theme"

export function getStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === "light" || v === "dark" || v === "system") return v
  } catch {
    /* ignore */
  }
  return "system"
}

export function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
}

/** 解析实际生效的肤色（system 时跟随系统） */
export function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme === "system") return systemPrefersDark() ? "dark" : "light"
  return theme
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.add("theme-transition")
  const resolved = resolveTheme(theme)
  document.documentElement.setAttribute("data-theme", resolved)
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* ignore */
  }
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute("content", resolved === "light" ? "#fafafa" : "#101012")
  // 过渡结束移除 class，避免常驻 transition 干扰滚动性能
  window.setTimeout(() => {
    document.documentElement.classList.remove("theme-transition")
  }, 220)
}

export function initTheme(): Theme {
  const theme = getStoredTheme()
  applyTheme(theme)
  // 系统主题实时跟随
  try {
    window
      .matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", () => {
        const cur = getStoredTheme()
        if (cur === "system") {
          document.documentElement.setAttribute("data-theme", resolveTheme(cur))
        }
      })
  } catch {
    /* ignore */
  }
  return theme
}
