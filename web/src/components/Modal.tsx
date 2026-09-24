import { useEffect, useRef, type ReactNode } from "react"

interface Props {
  labelledBy: string
  onClose: () => void
  children: ReactNode
  className?: string
}

export default function Modal({ labelledBy, onClose, children, className = "" }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return

    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (!dialog.open) dialog.showModal()
    const frame = window.requestAnimationFrame(() => {
      dialog.querySelector<HTMLElement>("[data-modal-initial-focus]")?.focus()
    })

    return () => {
      window.cancelAnimationFrame(frame)
      if (dialog.open) dialog.close()
      previous?.focus()
    }
  }, [])

  return (
    <dialog
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
      onCancel={(event) => {
        event.preventDefault()
        onCloseRef.current()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onCloseRef.current()
      }}
      className={`fixed inset-0 z-40 m-0 h-full w-full max-w-none border-0 bg-transparent p-0 text-spark-text ${className}`}
    >
      <div className="absolute inset-0 bg-black/55" onClick={() => onCloseRef.current()} />
      {children}
    </dialog>
  )
}
