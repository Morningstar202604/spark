import { memo, useCallback, useEffect, useState } from "react"
import { useUiStore } from "../store/uiStore"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { listCheckpoints, rollbackCheckpoint } from "../api"
import { Button, cls, IconButton, Spinner } from "./ui"
import { IconClock, IconHistory, IconX } from "./icons"
import { historyToItems } from "../store/chatStore"
import { t, type Lang } from "../i18n"

export const CheckpointModal = memo(function CheckpointModal() {
  const open = useUiStore((s) => s.checkpointsOpen)
  const close = useUiStore((s) => s.closeCheckpoints)
  const lang = useUiStore((s) => s.lang)
  const busy = useUiStore((s) => s.cpsBusy)
  const setBusy = useUiStore((s) => s.setCpsBusy)
  const rows = useUiStore((s) => s.checkpoints)
  const setRows = useUiStore((s) => s.setCheckpoints)
  const pushToast = useUiStore((s) => s.pushToast)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [rolling, setRolling] = useState<number | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const data = await listCheckpoints()
      setRows(data.checkpoints)
    } catch {
      pushToast("toast.checkpointsLoadFailed", "error")
    } finally {
      setBusy(false)
    }
  }, [setBusy, setRows, pushToast])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, close])

  async function doRollback(id: number) {
    setRolling(id)
    try {
      const data = await rollbackCheckpoint(id)
      const items = historyToItems(data.messages)
      useChatStore.getState().setItems(items)
      useSessionStore.getState().setStatus(data.status)
      useSessionStore.getState().setPlan(data.status.plan || [])
      useSessionStore.getState().syncPlanFromItems(items)
      pushToast("toast.rollbackDone", "success")
      await load()
    } catch {
      pushToast("toast.rollbackFailed", "error")
    } finally {
      setRolling(null)
      setConfirmId(null)
    }
  }

  function fmt(ts: number, lang: Lang): string {
    return new Date(ts * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
  }

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={close}>
      <aside
        className="anim-fade flex h-[min(80vh,560px)] w-[min(94vw,560px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={t("sidebar.checkpoint", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconHistory size={16} className="text-ink2" />
          <span className="text-sm font-semibold text-ink">{t("sidebar.checkpoint", lang)}</span>
          <IconButton title={t("common.close", lang)} onClick={close} className="ml-auto">
            <IconX size={15} />
          </IconButton>
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {busy ? (
            <div className="flex justify-center py-10">
              <Spinner size={18} className="text-accent" />
            </div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <IconClock size={22} className="text-ink3" />
              <p className="px-6 text-xs leading-relaxed text-ink3">{t("checkpoint.empty", lang)}</p>
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {rows.map((r) => (
                <li key={r.id} className={cls("rounded-xl border bg-panel p-3", confirmId === r.id ? "border-err/40" : "border-line")}>
                  <div className="flex items-center gap-2">
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-hover text-ink2">
                      <IconClock size={13} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium text-ink">{r.label || t("checkpoint.label", lang)}</p>
                      <p className="font-mono text-[10.5px] text-ink3">{fmt(r.created_at, lang)}</p>
                    </div>
                  </div>
                  {confirmId === r.id ? (
                    <div className="mt-2.5 flex gap-2">
                      <Button size="sm" variant="danger" full disabled={rolling !== null} onClick={() => doRollback(r.id)}>
                        {rolling === r.id ? <Spinner size={11} /> : t("checkpoint.confirmRollback", lang)}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>
                        {t("common.cancel", lang)}
                      </Button>
                    </div>
                  ) : (
                    <Button size="sm" variant="soft" full className="mt-2.5" onClick={() => setConfirmId(r.id)}>
                       {t("checkpoint.rollbackTo", lang)}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  )
})
