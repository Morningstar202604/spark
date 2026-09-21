import { useRef, useState, type KeyboardEvent } from "react"

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: (images: string[]) => void
  onStop: () => void
  streaming: boolean
  model?: string
  onOpenSettings?: () => void
}

const MAX_H = 180

export default function InputBox({ value, onChange, onSubmit, onStop, streaming, model, onOpenSettings }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [images, setImages] = useState<string[]>([])

  function autoGrow() {
    const el = ref.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = Math.min(el.scrollHeight, MAX_H) + "px"
  }

  function resetHeight() {
    const el = ref.current
    if (el) el.style.height = "auto"
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (!streaming && (value.trim() || images.length)) submit()
    }
  }

  function submit() {
    onSubmit(images)
    setImages([])
    resetHeight()
    ref.current?.focus()
  }

  function clearInput() {
    onChange("")
    resetHeight()
    ref.current?.focus()
  }

  function readFiles(files: FileList | null) {
    if (!files) return
    for (const file of Array.from(files)) {
      if (!file.type.startsWith("image/")) continue
      const reader = new FileReader()
      reader.onload = () => {
        setImages((prev) => [...prev, String(reader.result)])
      }
      reader.readAsDataURL(file)
    }
  }

  const canSend = !streaming && (value.trim().length > 0 || images.length > 0)

  return (
    <div className="border-t border-spark-line bg-spark-panel px-2.5 py-2.5 sm:px-4 sm:py-3">
      <div className="mx-auto max-w-4xl">
        {images.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {images.map((dataUrl) => (
              <div key={dataUrl.slice(-24)} className="group relative">
                <img src={dataUrl} alt="attached" className="h-16 w-16 rounded-lg border border-spark-line object-cover" />
                <button
                  type="button"
                  onClick={() => setImages((prev) => prev.filter((u) => u !== dataUrl))}
                  className="absolute -top-1.5 -right-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-red-900 text-xs font-bold text-white shadow transition-transform hover:scale-110"
                  title="移除图片"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="rounded-2xl border border-spark-line bg-spark-bg transition-colors focus-within:border-spark-accent/70">
          <textarea
            ref={ref}
            rows={1}
            value={value}
            onChange={(e) => {
              onChange(e.target.value)
              autoGrow()
            }}
            onKeyDown={onKey}
            placeholder={streaming ? "模型回复中，可点右下角按钮停止…" : "给 Spark 发送任务"}
            className="block w-full resize-none overflow-hidden bg-transparent px-3.5 pt-3 pb-1 text-sm leading-relaxed outline-none placeholder:text-spark-muted/80"
          />
          <div className="flex items-center justify-between gap-2 px-2 pb-2">
            <div className="flex min-w-0 items-center gap-0.5">
              <button
                type="button"
                title="附加图片"
                disabled={streaming}
                onClick={() => fileRef.current?.click()}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-spark-muted transition-colors hover:bg-spark-line hover:text-spark-text disabled:opacity-50"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                  <circle cx="8.5" cy="8.5" r="1.5" />
                  <path d="m21 15-5-5L5 21" />
                </svg>
              </button>
              {value && !streaming && (
                <button
                  type="button"
                  title="清空输入"
                  onClick={clearInput}
                  className="flex h-8 w-8 animate-in items-center justify-center rounded-lg text-spark-muted transition-colors hover:bg-spark-line hover:text-spark-text"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              )}
              {model && (
                <button
                  type="button"
                  title="当前模型，点击到设置中管理"
                  onClick={onOpenSettings}
                  className="ml-0.5 max-w-[9rem] truncate rounded-md bg-spark-line/60 px-2 py-1 text-[10px] font-medium text-spark-muted transition-colors hover:bg-spark-line hover:text-spark-text"
                >
                  {model}
                </button>
              )}
              <span className="ml-1 hidden shrink-0 text-[10px] text-spark-muted/70 md:block">Enter 发送 · Shift+Enter 换行</span>
            </div>
            {streaming ? (
              <button
                type="button"
                title="停止生成"
                onClick={onStop}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-950 text-spark-err transition-transform hover:scale-105 active:scale-95"
              >
                <span className="block h-2.5 w-2.5 rounded-[2px] bg-current" />
              </button>
            ) : (
              <button
                type="button"
                title="发送"
                disabled={!canSend}
                onClick={submit}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-spark-accent text-teal-950 transition-all hover:brightness-110 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M12 19V5" />
                  <path d="m5 12 7-7 7 7" />
                </svg>
              </button>
            )}
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
}
