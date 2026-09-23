export type Theme = "light" | "dark"

const STORAGE_KEY = "spark-theme"

export function getStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === "light" || v === "dark") return v
  } catch {
    /* ignore */
  }
  return "light"
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme)
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* ignore */
  }
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute("content", theme === "light" ? "#fbf8f4" : "#14110f")
}

export function initTheme(): Theme {
  const theme = getStoredTheme()
  applyTheme(theme)
  return theme
}
