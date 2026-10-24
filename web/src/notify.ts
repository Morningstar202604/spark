import { useSessionStore } from "./store/sessionStore"
import { useChatStore } from "./store/chatStore"

/** 桌面通知开关（设置 → 展示页；localStorage 持久化，默认关）。 */
const NOTIFY_KEY = "spark.notify.v1"

export function notificationsEnabled(): boolean {
  try {
    return localStorage.getItem(NOTIFY_KEY) === "1"
  } catch {
    return false
  }
}

export function setNotificationsEnabled(on: boolean): void {
  try {
    localStorage.setItem(NOTIFY_KEY, on ? "1" : "0")
  } catch {
    /* storage blocked */
  }
}

export function notificationPermission(): NotificationPermission | "unsupported" {
  if (typeof Notification === "undefined") return "unsupported"
  return Notification.permission
}

export async function requestNotificationPermission(): Promise<NotificationPermission | "unsupported"> {
  if (typeof Notification === "undefined") return "unsupported"
  if (Notification.permission === "granted") return "granted"
  try {
    return await Notification.requestPermission()
  } catch {
    return Notification.permission
  }
}

/**
 * 回合结束 / 审批等待时发一条系统通知（codex notifications 等价物）。
 *
 * 只在页面不可见时发：用户正盯着窗口时弹通知是噪音，且 macOS/Windows 会
 * 把它当成抢占焦点的行为。
 */
export function notify(title: string, body: string): void {
  if (!notificationsEnabled()) return
  if (document.visibilityState === "visible") return
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return
  try {
    new Notification(title, { body, tag: "spark-turn", silent: false })
  } catch {
    /* some browsers require a service worker; a failed toast is not fatal */
  }
}

/** 监听「回合结束」与「等待审批」两个信号，发通知。由 App 挂载一次。 */
export function installNotificationWatcher(): () => void {
  let wasStreaming = useChatStore.getState().streaming
  let wasWaiting = Boolean(useSessionStore.getState().approval)
  const unsubChat = useChatStore.subscribe((state) => {
    const streaming = state.streaming
    if (wasStreaming && !streaming) {
      const last = [...state.items].reverse().find((m) => m.role === "assistant" || m.role === "error")
      notify("Spark · 回合结束", last?.text?.slice(0, 120) || "任务已完成")
    }
    wasStreaming = streaming
  })
  const unsubSession = useSessionStore.subscribe((state) => {
    const waiting = Boolean(state.approval)
    if (!wasWaiting && waiting) {
      notify("Spark · 等待审批", state.approval?.info?.summary || "有工具调用需要你确认")
    }
    wasWaiting = waiting
  })
  return () => {
    unsubChat()
    unsubSession()
  }
}
