/**
 * UI primitives — shadcn/ui backed.
 *
 * Public API preserved for downstream consumers:
 *   cn, Section, EmptyState, Button, Input, Textarea, Select, Badge, Modal, Drawer
 *
 * shadcn component files live in ./ui/* and use the same clsx+tailwind-merge `cn`
 * utility defined in ../lib/utils.
 */
import { useId, type ReactNode } from "react";
import { Button as ShadcnButton } from "./ui/button";
import { Badge as ShadcnBadge } from "./ui/badge";
import { Card } from "./ui/card";
import { Input as ShadcnInput } from "./ui/input";
import { Textarea as ShadcnTextarea } from "./ui/textarea";
import {
  Select as ShadcnSelect,
  SelectGroup,
  SelectValue,
  SelectTrigger,
  SelectContent,
  SelectLabel,
  SelectItem,
  SelectSeparator,
  SelectScrollUpButton,
  SelectScrollDownButton,
} from "./ui/select";
import {
  Modal as ShadcnModal,
  ModalPortal,
  ModalOverlay,
  ModalClose,
  ModalTrigger,
  ModalContent,
  ModalHeader,
  ModalFooter,
  ModalTitle,
  ModalDescription,
} from "./ui/modal";
import {
  Drawer as ShadcnDrawer,
  DrawerPortal,
  DrawerOverlay,
  DrawerTrigger,
  DrawerClose,
  DrawerContent,
  DrawerHeader,
  DrawerFooter,
  DrawerTitle,
  DrawerDescription,
} from "./ui/drawer";
import { cn } from "../lib/utils";

// Re-export cn (defined in lib/utils as clsx+tailwind-merge)
export { cn };

// ---------------------------------------------------------------------------
// Card re-export (shadcn Card component)
// ---------------------------------------------------------------------------
export { Card, CardHeader, CardFooter, CardTitle, CardDescription, CardContent } from "./ui/card";

// ---------------------------------------------------------------------------
// Section — card wrapper with optional header
// ---------------------------------------------------------------------------
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
    <Card
      id={id}
      className={cn("rounded-2xl p-4", className)}
    >
      {(title || description) && (
        <div className="mb-4 space-y-1">
          {title && <h2 className="text-sm font-semibold">{title}</h2>}
          {description && (
            <p className="text-xs text-ink-muted">
              {description}
            </p>
          )}
        </div>
      )}
      {children}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// EmptyState — dashed placeholder panel
// ---------------------------------------------------------------------------
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
  return (
    <div
      className={cn(
        "flex h-full min-h-[280px] flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-surface p-8 text-center",
        className
      )}
    >
      <div className="text-lg font-semibold">{title}</div>
      {description && (
        <p className="mt-2 max-w-md text-sm text-ink-muted">
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

// ---------------------------------------------------------------------------
// Button — thin wrapper around shadcn Button that maps legacy variant names.
//
// Legacy variants:  "default" | "primary" | "ghost" | "danger" | "outline"
// shadcn variants:  "default" | "secondary" | "ghost" | "destructive" | "outline"
//
// Mapping:
//   "default" (legacy gray)  -> "secondary"
//   "primary" (legacy accent) -> "default" (shadcn uses accent bg for default)
//   "ghost"                  -> "ghost"
//   "danger"                 -> "destructive"
//   "outline"                -> "outline"
// ---------------------------------------------------------------------------
type LegacyButtonVariant = "default" | "primary" | "ghost" | "danger" | "outline";

const variantMap: Record<LegacyButtonVariant, "secondary" | "default" | "ghost" | "destructive" | "outline"> = {
  default: "secondary",
  primary: "default",
  ghost: "ghost",
  danger: "destructive",
  outline: "outline",
};

const sizeMap: Record<string, "default" | "sm" | "lg" | "icon"> = {
  md: "default",
  sm: "sm",
  lg: "lg",
  icon: "icon",
};

export function Button({
  variant = "default",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: LegacyButtonVariant;
  size?: "sm" | "md" | "lg" | "icon";
}) {
  const shadcnVariant = variantMap[variant] ?? "default";
  const shadcnSize = sizeMap[size] ?? "default";

  return (
    <ShadcnButton
      variant={shadcnVariant}
      size={shadcnSize}
      className={className}
      {...props}
    />
  );
}

// ---------------------------------------------------------------------------
// Input — shadcn Input (forwardRef + standard HTML input props)
// ---------------------------------------------------------------------------
export function Input({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return <ShadcnInput className={className} {...props} />;
}

// ---------------------------------------------------------------------------
// Textarea — shadcn Textarea
// ---------------------------------------------------------------------------
export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <ShadcnTextarea className={className} {...props} />;
}

// ---------------------------------------------------------------------------
// Select — shadcn Radix UI Select (compound component API).
//
// Usage matches the shadcn Select convention:
//   <Select value={val} onValueChange={setVal}>
//     <SelectTrigger><SelectValue placeholder="Pick" /></SelectTrigger>
//     <SelectContent>
//       <SelectItem value="a">A</SelectItem>
//     </SelectContent>
//   </Select>
// ---------------------------------------------------------------------------
export const Select = ShadcnSelect;
export {
  SelectGroup,
  SelectValue,
  SelectTrigger,
  SelectContent,
  SelectLabel,
  SelectItem,
  SelectSeparator,
  SelectScrollUpButton,
  SelectScrollDownButton,
};

// ---------------------------------------------------------------------------
// Badge — thin wrapper around shadcn Badge mapping legacy variant names.
//
// Legacy variants: "default" | "success" | "warning" | "danger" | "accent"
// shadcn variants: "default" | "secondary" | "destructive" | "outline"
//
// Mapping:
//   "default" (legacy gray)  -> "secondary"
//   "success" (legacy green) -> custom green classes
//   "warning" (legacy amber) -> custom amber classes
//   "danger" (legacy red)    -> "destructive"
//   "accent"                 -> "default" (accent bg)
// ---------------------------------------------------------------------------
type LegacyBadgeVariant = "default" | "success" | "warning" | "danger" | "accent";

const badgeVariantClasses: Record<LegacyBadgeVariant, string> = {
  default: "bg-surface-2 text-ink-muted border border-border",
  success: "bg-emerald-100 text-emerald-700 border border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-400 dark:border-emerald-800",
  warning: "bg-amber-100 text-amber-700 border border-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:border-amber-800",
  danger: "bg-red-100 text-red-700 border border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800",
  accent: "bg-accent/10 text-accent border border-accent/20",
};

export function Badge({
  children,
  variant = "default",
  className,
}: {
  children: ReactNode;
  variant?: LegacyBadgeVariant;
  className?: string;
}) {
  return (
    <ShadcnBadge
      className={cn(badgeVariantClasses[variant], className)}
    >
      {children}
    </ShadcnBadge>
  );
}

// ---------------------------------------------------------------------------
// Modal — shadcn Dialog-based, preserving legacy API { open, onClose, title, children }
// ---------------------------------------------------------------------------
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
  return (
    <ShadcnModal open={open} onOpenChange={(o) => { if (!o) onClose?.(); }}>
      <ModalContent className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl">
        <ModalHeader>
          <ModalTitle>{title}</ModalTitle>
          <ModalClose className="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none">
            <span className="text-xs text-ink-muted">✕</span>
            <span className="sr-only">Close</span>
          </ModalClose>
        </ModalHeader>
        {children}
      </ModalContent>
    </ShadcnModal>
  );
}

// ---------------------------------------------------------------------------
// Drawer — shadcn Vaul-based (bottom drawer on mobile, also supports side drawers).
// Legacy API: { open, onClose, title, children, side?, width? }
// ---------------------------------------------------------------------------
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
  return (
    <ShadcnDrawer open={open} onOpenChange={(o) => { if (!o) onClose?.(); }}>
      <DrawerContent className="fixed inset-x-0 bottom-0 z-50 mt-24 flex h-auto flex-col rounded-t-[10px] border border-border bg-card">
        {/* Drag handle */}
        <div className="mx-auto mt-4 h-2 w-[100px] rounded-full bg-ink-muted/30" />
        {title && (
          <DrawerHeader>
            <DrawerTitle>{title}</DrawerTitle>
            <DrawerClose className="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none">
              <span className="text-xs text-ink-muted">✕</span>
              <span className="sr-only">Close</span>
            </DrawerClose>
          </DrawerHeader>
        )}
        <div className="flex-1 overflow-y-auto px-4">{children}</div>
      </DrawerContent>
    </ShadcnDrawer>
  );
}
