import { useId, type ReactNode } from "react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function Section({
  title,
  description,
  children,
  className,
}: {
  title?: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <section
      id={id}
      className={cn(
        "rounded-2xl border bg-card p-4 shadow-sm",
        className
      )}
      style={{ borderColor: "var(--border)" }}
    >
      {(title || description) && (
        <header className="mb-4 space-y-1">
          {title && <h2 className="text-sm font-semibold">{title}</h2>}
          {description && (
            <p className="text-xs" style={{ color: "var(--ink-muted)" }}>
              {description}
            </p>
          )}
        </header>
      )}
      {children}
    </section>
  );
}

export function EmptyState({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div
      id={`empty-${id}`}
      className={cn(
        "flex h-full min-h-[280px] flex-col items-center justify-center rounded-2xl border border-dashed p-8 text-center",
        className
      )}
      style={{ borderColor: "var(--border)", background: "var(--surface)" }}
    >
      <div className="text-lg font-semibold">{title}</div>
      {description && (
        <p className="mt-2 max-w-md text-sm" style={{ color: "var(--ink-muted)" }}>
          {description}
        </p>
      )}
      {children && (
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          {children}
        </div>
      )}
    </div>
  );
}

export function Button({
  variant = "default",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost" | "danger" | "outline";
  size?: "sm" | "md" | "lg" | "icon";
}) {
  const base =
    "inline-flex items-center justify-center font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none";

  const sizes: Record<string, string> = {
    sm: "h-7 px-2.5 text-xs rounded-md",
    md: "h-9 px-4 text-sm rounded-lg",
    lg: "h-10 px-5 text-sm rounded-lg",
    icon: "h-9 w-9 rounded-lg",
  };

  const variants: Record<string, React.CSSProperties> = {
    default: {
      background: "var(--surface-2)",
      border: "1px solid var(--border)",
      color: "var(--ink)",
    },
    primary: {
      background: "var(--accent)",
      color: "var(--on-accent, #fff)",
      border: "1px solid var(--accent)",
    },
    ghost: {
      background: "transparent",
      border: "1px solid var(--border)",
      color: "var(--ink-muted)",
    },
    danger: {
      background: "transparent",
      border: "1px solid rgba(214, 69, 69, 0.35)",
      color: "var(--red, #d64545)",
    },
    outline: {
      background: "var(--surface)",
      border: "1px solid var(--border)",
      color: "var(--ink)",
    },
  };

  return (
    <button
      className={cn(base, sizes[size], className)}
      style={variants[variant]}
      {...props}
    />
  );
}

export function Input({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full rounded-lg border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
        className
      )}
      style={{
        border: "1px solid var(--border)",
        background: "var(--surface)",
        color: "var(--ink)",
        ["--tw-ring-color" as string]: "var(--accent)",
      }}
      {...props}
    />
  );
}

export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full rounded-lg border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
        className
      )}
      style={{
        border: "1px solid var(--border)",
        background: "var(--surface)",
        color: "var(--ink)",
        ["--tw-ring-color" as string]: "var(--accent)",
      }}
      {...props}
    />
  );
}

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "w-full rounded-lg border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
        className
      )}
      style={{
        border: "1px solid var(--border)",
        background: "var(--surface)",
        color: "var(--ink)",
        ["--tw-ring-color" as string]: "var(--accent)",
      }}
      {...props}
    >
      {children}
    </select>
  );
}

export function Badge({
  children,
  variant = "default",
  className,
}: {
  children: ReactNode;
  variant?: "default" | "success" | "warning" | "danger" | "accent";
  className?: string;
}) {
  const colors: Record<string, { bg: string; color: string; border?: string }> = {
    default: {
      bg: "var(--surface-2)",
      color: "var(--ink-muted)",
      border: "1px solid var(--border)",
    },
    success: {
      bg: "rgba(14, 157, 110, 0.1)",
      color: "var(--green, #0e9d6e)",
      border: "1px solid rgba(14, 157, 110, 0.3)",
    },
    warning: {
      bg: "rgba(178, 106, 0, 0.1)",
      color: "var(--amber, #b26a00)",
      border: "1px solid rgba(178, 106, 0, 0.3)",
    },
    danger: {
      bg: "rgba(214, 69, 69, 0.1)",
      color: "var(--red, #d64545)",
      border: "1px solid rgba(214, 69, 69, 0.3)",
    },
    accent: {
      bg: "color-mix(in srgb, var(--accent) 10%, transparent)",
      color: "var(--accent)",
      border: "1px solid color-mix(in srgb, var(--accent) 40%, transparent)",
    },
  };

  const c = colors[variant];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        className
      )}
      style={{
        background: c.bg,
        color: c.color,
        border: c.border,
      }}
    >
      {children}
    </span>
  );
}

export function Modal({
  open,
  onClose,
  children,
  title,
}: {
  open: boolean;
  onClose?: () => void;
  children: ReactNode;
  title?: string;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "color-mix(in srgb, #101828 42%, transparent)" }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border p-6 shadow-2xl"
        style={{
          background: "var(--surface)",
          borderColor: "var(--border)",
        }}
      >
        {title && (
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-base font-semibold">{title}</h3>
            {onClose && (
              <button
                onClick={onClose}
                className="rounded-md p-1 text-sm hover:opacity-70"
                style={{ color: "var(--ink-muted)" }}
              >
                ✕
              </button>
            )}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

export function Drawer({
  open,
  onClose,
  children,
  title,
  side = "right",
  width = 420,
}: {
  open: boolean;
  onClose?: () => void;
  children: ReactNode;
  title?: string;
  side?: "left" | "right";
  width?: number;
}) {
  if (!open) return null;
  return (
    <>
      <div
        className="fixed inset-0 z-40"
        style={{ background: "color-mix(in srgb, #101828 42%, transparent)" }}
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose?.();
        }}
      />
      <div
        className="fixed z-50 flex h-full flex-col shadow-2xl"
        style={{
          top: 0,
          [side]: 0,
          width,
          maxWidth: "94vw",
          background: "var(--surface)",
          borderLeft: side === "right" ? "1px solid var(--border)" : undefined,
          borderRight: side === "left" ? "1px solid var(--border)" : undefined,
        }}
      >
        {title && (
          <div
            className="flex flex-none items-center justify-between border-b p-4"
            style={{ borderColor: "var(--border)" }}
          >
            <h3 className="text-sm font-semibold">{title}</h3>
            {onClose && (
              <button
                onClick={onClose}
                className="rounded-md p-1 text-sm hover:opacity-70"
                style={{ color: "var(--ink-muted)" }}
              >
                ✕
              </button>
            )}
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </>
  );
}
