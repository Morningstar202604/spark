import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react"
import { forwardRef } from "react"
import { IconChevronDown } from "./icons"

/* ------------------------------------------------------------------ */
/* 基础 UI 基元：全站统一的按钮 / 输入 / 开关 / 表单布局               */
/* ------------------------------------------------------------------ */

export function cls(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ")
}

export function Spinner({ size = 14, className }: { size?: number; className?: string }) {
  return (
    <span
      className={cls("spin inline-block shrink-0 rounded-full border-2 border-transparent", className)}
      style={{ width: size, height: size, borderTopColor: "currentColor", borderRightColor: "currentColor" }}
      aria-label="loading"
    />
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line bg-panel2 px-1 font-mono text-[10px] text-ink3">
      {children}
    </kbd>
  )
}

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "soft" | "danger" | "outline"
  size?: "sm" | "md"
  full?: boolean
}

export function Button({ variant = "ghost", size = "md", full, className, children, ...rest }: BtnProps) {
  const v =
    variant === "primary"
      ? "bg-accent text-on-accent hover:brightness-110 active:brightness-95"
      : variant === "soft"
        ? "bg-accent-soft text-accent hover:bg-accent/20"
        : variant === "danger"
          ? "bg-err/12 text-err hover:bg-err/20"
          : variant === "outline"
            ? "border border-line2 bg-transparent text-ink hover:bg-hover"
            : "text-ink2 hover:bg-hover hover:text-ink"
  const s = size === "sm" ? "h-7 px-2.5 text-xs" : "h-8.5 px-3.5 text-sm"
  return (
    <button
      type="button"
      className={cls(
        "inline-flex select-none items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:pointer-events-none disabled:opacity-45",
        v,
        s,
        full && "w-full",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

export function IconButton({ className, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cls(
        "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink2 transition-colors hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-45",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

export function Toggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  label?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cls(
        "relative inline-flex h-5.5 w-10 shrink-0 items-center rounded-full transition-colors disabled:opacity-45",
        checked ? "bg-accent" : "bg-line2",
      )}
    >
      <span
        className={cls(
          "inline-block h-4.5 w-4.5 transform rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-5" : "translate-x-0.5",
        )}
      />
    </button>
  )
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="flex min-w-0 flex-col gap-1.5">
      <span className="flex items-center justify-between text-[12px] font-medium text-ink2">
        <span>{label}</span>
        {hint}
      </span>
      {children}
    </label>
  )
}

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function TextInput(
  { className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cls(
        "h-9 w-full rounded-lg border border-line bg-panel px-3 text-sm text-ink placeholder:text-ink3 outline-none transition-colors focus:border-accent",
        className,
      )}
      {...rest}
    />
  )
})

export const TextArea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function TextArea(
  { className, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      className={cls(
        "w-full rounded-lg border border-line bg-panel px-3 py-2 text-sm text-ink placeholder:text-ink3 outline-none transition-colors focus:border-accent",
        className,
      )}
      {...rest}
    />
  )
})

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className={cls("relative", className)}>
      <select
        className="h-9 w-full appearance-none rounded-lg border border-line bg-panel pr-8 pl-3 text-sm text-ink outline-none transition-colors focus:border-accent"
        {...rest}
      >
        {children}
      </select>
      <IconChevronDown size={14} className="pointer-events-none absolute top-2.5 right-2.5 text-ink3" />
    </div>
  )
}

export function Section({ title, desc, children, action }: { title: string; desc?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink">{title}</h3>
          {desc && <p className="mt-0.5 text-xs leading-relaxed text-ink3">{desc}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

export function Badge({ tone = "neutral", className, children }: { tone?: "neutral" | "ok" | "err" | "accent" | "warn"; className?: string; children: ReactNode }) {
  const t =
    tone === "ok"
      ? "bg-ok/12 text-ok"
      : tone === "err"
        ? "bg-err/12 text-err"
        : tone === "accent"
          ? "bg-accent-soft text-accent"
          : tone === "warn"
            ? "bg-warn/12 text-warn"
            : "bg-hover text-ink2"
  return <span className={cls("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium", t, className)}>{children}</span>
}

export function Divider({ className }: { className?: string }) {
  return <div className={cls("my-1 h-px bg-line", className)} />
}
