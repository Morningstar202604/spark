import { memo, useCallback, useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from "react"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { IconImage, IconPaperclip, IconSend, IconStop, IconX } from "./icons"
import { cls } from "./ui"

const MAX_H = 200

export const Composer = memo(function Composer() {
  const input = useChatStore((s) => s.input)
  const streaming = useChatStore((s) => s.streaming)
  const setInput = useChatStore((s) => s.setInput)
  const send = useChatStore((s) => s.send)
  const cancel = useChatStore((s) => s.cancel)
  const status = useSessionStore((s) => s.status)
  const openSettings = useUiStore((s) => s.openSettings)

  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [images, setImages] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)

  const model = status?.model

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
          <span className="rounded-lg bg-panel px-4 py-2 text-sm font-medium text-accent shadow-lg">拖放图片以附加</span>
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
                  title="移除图片"
                >
                  <IconX size={10} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          className={cls(
            "rounded-2xl border bg-panel shadow-sm transition-colors focus-within:border-accent/70",
            dragging ? "border-accent" : "border-line",
          )}
        >
          <textarea
            ref={taRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            onPaste={onPaste}
            placeholder={streaming ? "模型正在回复…（Esc 停止）" : "给 Spark 发送任务，Enter 发送 / Shift+Enter 换行（/test 跑测试）"}
            className="block max-h-[200px] w-full resize-none overflow-y-auto bg-transparent px-4 pt-3.5 pb-1 text-[14px] leading-relaxed outline-none placeholder:text-ink3"
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
            <div className="flex min-w-0 items-center gap-1">
              <button
                type="button"
                title="附加图片（可粘贴 / 拖拽）"
                disabled={streaming}
                onClick={() => fileRef.current?.click()}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:opacity-45"
              >
                <IconPaperclip size={16} />
              </button>
              <button
                type="button"
                title="附加图片"
                disabled={streaming}
                onClick={() => fileRef.current?.click()}
                className="hidden h-8 w-8 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:opacity-45 sm:flex"
              >
                <IconImage size={16} />
              </button>
              {model && (
                <button
                  type="button"
                  onClick={openSettings}
                  title="当前模型，点击管理"
                  className="ml-1 max-w-[10rem] truncate rounded-md bg-hover px-2 py-1 text-[11px] font-medium text-ink2 transition-colors hover:bg-selected"
                >
                  {model}
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="hidden text-[11px] text-ink3 md:block">Enter 发送 · Shift+Enter 换行</span>
              {streaming ? (
                <button
                  type="button"
                  title="停止生成"
                  onClick={cancel}
                  className="flex h-8.5 w-8.5 shrink-0 items-center justify-center rounded-full bg-hover text-ink transition-colors hover:bg-selected active:scale-95"
                >
                  <IconStop size={15} />
                </button>
              ) : (
                <button
                  type="button"
                  title="发送"
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
