import { memo, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent } from "react"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { fetchCommands, type SlashCommandInfo } from "../api"
import { IconGlobe, IconImage, IconPaperclip, IconSend, IconStop, IconX } from "./icons"
import { cls } from "./ui"
import { t } from "../i18n"

const MAX_H = 200

export const Composer = memo(function Composer() {
  const input = useChatStore((s) => s.input)
  const streaming = useChatStore((s) => s.streaming)
  const setInput = useChatStore((s) => s.setInput)
  const send = useChatStore((s) => s.send)
  const webSearch = useChatStore((s) => s.webSearch)
  const setWebSearch = useChatStore((s) => s.setWebSearch)
  // Route through the session store so the local abort and POST /api/cancel
  // happen together (chatStore.cancel only aborts the fetch).
  const cancel = useSessionStore((s) => s.handleCancel)
  const status = useSessionStore((s) => s.status)
  const openSettings = useUiStore((s) => s.openSettings)
  const lang = useUiStore((s) => s.lang)

  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [images, setImages] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)
  const [commands, setCommands] = useState<SlashCommandInfo[]>([])
  const [slashIndex, setSlashIndex] = useState(0)

  const model = status?.model

  // Slash menu: only when the message starts with "/" (like Claude Code / Codex).
  const slashQuery = input.startsWith("/") && !input.includes("\n") ? input.slice(1).split(/\s/)[0].toLowerCase() : null
  const slashItems = useMemo(() => {
    if (slashQuery === null) return []
    return commands.filter((c) => c.name.toLowerCase().includes(slashQuery))
  }, [commands, slashQuery])
  const slashOpen = slashQuery !== null && slashItems.length > 0

  useEffect(() => {
    let alive = true
    fetchCommands()
      .then((data) => {
        if (alive) setCommands(data.commands)
      })
      .catch(() => {
        /* 没有自定义命令是正常状态 */
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    setSlashIndex(0)
  }, [slashQuery])

  const applySlash = useCallback((name: string) => {
    setInput(`/${name} `)
    taRef.current?.focus()
  }, [setInput])

  function autoGrow() {
    const el = taRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = Math.min(el.scrollHeight, MAX_H) + "px"
  }

  useEffect(() => {
    autoGrow()
  }, [input])

  const submit = useCallback(() => {
    if (streaming) return
    if (!input.trim() && images.length === 0) return
    send(images)
    setImages([])
  }, [input, images, streaming, send])

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (slashOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSlashIndex((i) => Math.min(i + 1, slashItems.length - 1))
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setSlashIndex((i) => Math.max(i - 1, 0))
        return
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault()
        const pick = slashItems[slashIndex]
        if (pick) applySlash(pick.name)
        return
      }
      if (e.key === "Escape") {
        e.preventDefault()
        setInput("")
        return
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  function readFiles(list: FileList | null) {
    if (!list) return
    for (const file of Array.from(list)) {
      if (!file.type.startsWith("image/")) continue
      const reader = new FileReader()
      reader.onload = () => setImages((prev) => [...prev, String(reader.result)])
      reader.readAsDataURL(file)
    }
  }

  function onPaste(e: React.ClipboardEvent) {
    const files = e.clipboardData?.files
    if (files && files.length > 0 && Array.from(files).some((f) => f.type.startsWith("image/"))) {
      e.preventDefault()
      readFiles(files)
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    readFiles(e.dataTransfer.files)
  }

  const canSend = !streaming && (input.trim().length > 0 || images.length > 0)

  return (
    <div
      className={cls("border-t border-line bg-canvas/80 px-3 pt-2 pb-3 backdrop-blur-sm sm:px-4", dragging && "pt-0")}
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {dragging && (
        <div className="anim-fade pointer-events-none absolute inset-0 z-40 flex items-center justify-center border-2 border-dashed border-accent bg-accent/5">
          <span className="rounded-lg bg-panel px-4 py-2 text-sm font-medium text-accent shadow-lg">{t("composer.dropImage", lang)}</span>
        </div>
      )}
      <div className="mx-auto max-w-[780px]">
        {images.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {images.map((dataUrl) => (
              <div key={dataUrl.slice(-32)} className="group relative">
                <img src={dataUrl} alt="attached" className="h-16 w-16 rounded-lg border border-line object-cover" />
                <button
                  type="button"
                  onClick={() => setImages((prev) => prev.filter((u) => u !== dataUrl))}
                  className="absolute -top-1.5 -right-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-err text-white shadow transition-transform hover:scale-110"
                  title={t("common.removeImage", lang)}
                >
                  <IconX size={10} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          className={cls(
            "relative rounded-2xl border bg-panel shadow-sm transition-colors focus-within:border-accent/70",
            dragging ? "border-accent" : "border-line",
          )}
        >
          {slashOpen && (
            <div
              className="anim-fade absolute bottom-full left-0 mb-2 w-full overflow-hidden rounded-xl border border-line bg-panel shadow-[var(--shadow)]"
              role="listbox"
              aria-label={t("composer.slashHint", lang)}
            >
              {slashItems.length === 0 && (
                <p className="px-3 py-2.5 text-[12px] text-ink3">{t("composer.slashEmpty", lang)}</p>
              )}
              {slashItems.map((c, i) => (
                <button
                  key={c.name}
                  type="button"
                  role="option"
                  aria-selected={i === slashIndex}
                  onMouseEnter={() => setSlashIndex(i)}
                  onMouseDown={(e) => {
                    // mousedown, not click: the textarea blur would close the menu first
                    e.preventDefault()
                    applySlash(c.name)
                  }}
                  className={cls(
                    "flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px]",
                    i === slashIndex ? "bg-hover text-ink" : "text-ink2",
                  )}
                >
                  <span className="font-mono text-accent">/{c.name}</span>
                  <span className="min-w-0 flex-1 truncate text-ink3">{c.description}</span>
                </button>
              ))}
            </div>
          )}
          <textarea
            ref={taRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            onPaste={onPaste}
            placeholder={streaming ? t("composer.placeholderStreaming", lang) : t("composer.placeholder", lang)}
            className="block max-h-[200px] w-full resize-none overflow-y-auto bg-transparent px-4 pt-3.5 pb-1 text-[14px] leading-relaxed outline-none placeholder:text-ink3"
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
            <div className="flex min-w-0 items-center gap-1">
              <button
                type="button"
                title={t("composer.attachImageHint", lang)}
                disabled={streaming}
                onClick={() => fileRef.current?.click()}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:opacity-45"
              >
                <IconPaperclip size={16} />
              </button>
              <button
                type="button"
                title={t("common.attachImage", lang)}
                disabled={streaming}
                onClick={() => fileRef.current?.click()}
                className="hidden h-8 w-8 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:opacity-45 sm:flex"
              >
                <IconImage size={16} />
              </button>
              <button
                type="button"
                title={webSearch ? t("composer.webSearchOn", lang) : t("composer.webSearchOff", lang)}
                aria-pressed={webSearch}
                onClick={() => {
                  const next = !webSearch
                  setWebSearch(next)
                  useUiStore.getState().pushToast(next ? "toast.webSearchOn" : "toast.webSearchOff", "info")
                }}
                className={cls(
                  "flex h-8 items-center gap-1.5 rounded-lg px-2 text-[11.5px] font-medium transition-colors",
                  webSearch ? "bg-accent-soft text-accent" : "text-ink3 hover:bg-hover hover:text-ink",
                )}
              >
                <IconGlobe size={14} />
                <span className="hidden sm:inline">{t("composer.webSearch", lang)}</span>
              </button>
              {model && (
                <button
                  type="button"
                  onClick={openSettings}
                  title={t("common.currentModel", lang)}
                  className="ml-1 max-w-[10rem] truncate rounded-md bg-hover px-2 py-1 text-[11px] font-medium text-ink2 transition-colors hover:bg-selected"
                >
                  {model}
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="hidden text-[11px] text-ink3 md:block">{t("common.enterSend", lang)}</span>
              {streaming ? (
                <button
                  type="button"
                  title={t("common.stopGenerate", lang)}
                  onClick={cancel}
                  className="flex h-8.5 w-8.5 shrink-0 items-center justify-center rounded-full bg-hover text-ink transition-colors hover:bg-selected active:scale-95"
                >
                  <IconStop size={15} />
                </button>
              ) : (
                <button
                  type="button"
                  title={t("common.send", lang)}
                  disabled={!canSend}
                  onClick={submit}
                  className={cls(
                    "flex h-8.5 w-8.5 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition-all hover:brightness-110 active:scale-95",
                    !canSend && "disabled:opacity-40",
                  )}
                >
                  <IconSend size={15} />
                </button>
              )}
            </div>
          </div>
        </div>

        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(e) => {
            readFiles(e.target.files)
            e.target.value = ""
          }}
        />
      </div>
    </div>
  )
})
