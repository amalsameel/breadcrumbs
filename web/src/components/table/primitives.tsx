// Table-local UI primitives (deliberately independent of components/ui/*).
import type { ButtonHTMLAttributes, MouseEvent, ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

type Tone = "primary" | "quiet" | "danger" | "ghost";

const TONE_CLASS: Record<Tone, string> = {
  primary:
    "bg-amber text-bg border-amber hover:bg-crumb hover:border-crumb shadow-[0_0_32px_-6px_rgba(245,165,36,0.65)]",
  quiet: "bg-bg-raised/80 text-ink border-line hover:border-ink-faint hover:bg-bg-raised",
  danger: "bg-danger/15 text-ink border-danger/60 hover:bg-danger/25",
  ghost: "bg-transparent text-ink-muted border-transparent hover:text-ink hover:bg-bg-raised/60",
};

export interface TableButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: IconName;
  tone?: Tone;
  size?: "lg" | "md" | "sm";
  shortcut?: string;
  children: ReactNode;
}

const SIZE_CLASS = {
  lg: "min-h-14 px-5 text-base gap-2.5 rounded-2xl",
  md: "min-h-11 px-4 text-sm gap-2 rounded-xl",
  sm: "min-h-9 px-3 text-xs gap-1.5 rounded-lg",
};

/**
 * Large, always-labelled control. Mouse clicks drop focus afterwards so the global keyboard
 * shortcuts (Space in particular) keep working instead of re-activating the last button.
 */
export function TableButton({
  icon,
  tone = "quiet",
  size = "lg",
  shortcut,
  className = "",
  children,
  onClick,
  ...rest
}: TableButtonProps) {
  const handleClick = (e: MouseEvent<HTMLButtonElement>) => {
    if (e.detail > 0) e.currentTarget.blur();
    onClick?.(e);
  };
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        className={`inline-flex cursor-pointer select-none items-center justify-center border font-medium transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${SIZE_CLASS[size]} ${TONE_CLASS[tone]} ${className}`}
        onClick={handleClick}
        aria-keyshortcuts={shortcut ? shortcutAria(shortcut) : undefined}
        {...rest}
      >
        {icon && <Icon name={icon} size={size === "sm" ? 16 : 20} />}
        <span>{children}</span>
      </button>
      {shortcut && (
        <span
          role="tooltip"
          className="pointer-events-none absolute -top-9 left-1/2 z-30 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-bg-sunken px-2 py-1 font-mono text-[11px] text-ink-muted opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
        >
          Shortcut: <Kbd>{shortcut}</Kbd>
        </span>
      )}
    </span>
  );
}

function shortcutAria(label: string): string {
  return label === "Space" ? "Space" : label.toUpperCase();
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line bg-bg-raised px-1.5 py-px font-mono text-[11px] text-ink">{children}</kbd>
  );
}

export type ChipState = "done" | "active" | "pending";

export function StatusChip({ state, children }: { state: ChipState; children: ReactNode }) {
  const tone =
    state === "done"
      ? "border-ok/50 bg-ok/10 text-ink"
      : state === "active"
        ? "border-amber/60 bg-amber/10 text-ink"
        : "border-line text-ink-faint";
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium ${tone}`}>
      {state === "done" ? (
        <Icon name="check" size={13} strokeWidth={2.4} className="text-ok" />
      ) : state === "active" ? (
        <span className="size-2 animate-pulse rounded-full bg-amber" aria-hidden="true" />
      ) : (
        <span className="size-2 rounded-full border border-ink-faint" aria-hidden="true" />
      )}
      {children}
      <span className="sr-only">{state === "done" ? "(done)" : state === "active" ? "(in progress)" : "(waiting)"}</span>
    </span>
  );
}

export function Badge({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border border-line bg-bg-raised/70 px-3 py-1 text-xs font-medium tracking-wide text-ink-muted ${className}`}
    >
      {children}
    </span>
  );
}

/** Soft warm vignette behind every Table screen. */
export function Backdrop() {
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_38%,rgba(245,165,36,0.09),transparent_60%)]" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_120%,rgba(255,210,122,0.05),transparent_55%)]" />
    </div>
  );
}
