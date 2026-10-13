import React, { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import App from "./App"
import { initTheme } from "./theme"

initTheme()

class RootErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }
  static getDerivedStateFromError(error: Error) {
    return { error }
  }
  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full items-center justify-center bg-canvas p-6 text-ink">
          <div className="text-center space-y-3">
            <div className="text-3xl">⚠️</div>
            <p className="text-sm text-ink2">Something went wrong</p>
            <p className="max-w-sm text-xs text-ink3">{String(this.state.error)}</p>
            <button
              className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-on-accent"
              onClick={() => window.location.reload()}
            >
              刷新重试
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </StrictMode>,
)
