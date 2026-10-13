import { memo, useCallback, useEffect, useState } from "react"
import { useUiStore } from "../store/uiStore"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { listCheckpoints, rollbackCheckpoint } from "../api"
import { Button, cls, IconButton, Spinner } from "./ui"
import { IconClock, IconHistory, IconX } from "./icons"
import { historyToItems } from "../store/chatStore"

export const CheckpointPanel = memo(function CheckpointPanel() {
  const open = useUiStore((s) => s.checkpointsOpen)
  const close = useUiStore((s) => s.closeCheckpoints)
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

  function fmt(ts: number): string {
    return new Date(ts * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
  }

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={close}>
      <aside
        className="anim-fade flex h-full w-[min(92vw,340px)] flex-col border-l border-line bg-sidebar shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="检查点回滚"
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconHistory size={16} className="text-ink2" />
          <span className="text-sm font-semibold text-ink">检查点回滚</span>
          <IconButton title="关闭" onClick={close} className="ml-auto">
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
              <p className="px-6 text-xs leading-relaxed text-ink3">暂无检查点。每轮涉及文件修改或命令执行后，Spark 会自动创建快照。</p>
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
                      <p className="truncate text-[13px] font-medium text-ink">{r.label || "检查点"}</p>
                      <p className="font-mono text-[10.5px] text-ink3">{fmt(r.created_at)}</p>
                    </div>
                  </div>
                  {confirmId === r.id ? (
                    <div className="mt-2.5 flex gap-2">
                      <Button size="sm" variant="danger" full disabled={rolling !== null} onClick={() => doRollback(r.id)}>
                        {rolling === r.id ? <Spinner size={11} /> : "确认回滚"}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>
                        取消
                      </Button>
                    </div>
                  ) : (
                    <Button size="sm" variant="soft" full className="mt-2.5" onClick={() => setConfirmId(r.id)}>
                      回滚到此检查点
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
