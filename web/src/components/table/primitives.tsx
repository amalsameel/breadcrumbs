// Table-local UI primitives (deliberately independent of components/ui/*): black and white pill
// buttons, status chips, pills and the segmented track, in the light canvas language.
import type { ButtonHTMLAttributes, MouseEvent, ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

type Tone = "primary" | "quiet" | "danger" | "ghost" | "segment";

const TONE_CLASS: Record<Tone, string> = {
  primary: "bg-ink text-on-ink border-ink hover:bg-ink/85 hover:border-ink/85",
  quiet: "bg-bg-raised text-ink border-line shadow-pill hover:border-line-strong hover:bg-bg",
  danger: "bg-danger text-on-ink border-danger hover:bg-danger/90",
  ghost: "bg-transparent text-ink-muted border-transparent hover:text-ink hover:bg-ink/5",
  // an item inside a SegmentTrack: plain text, raised white pill when pressed
  segment:
    "bg-transparent text-ink-muted border-transparent hover:text-ink aria-pressed:bg-bg-raised aria-pressed:text-ink aria-pressed:shadow-raised",
};

export interface TableButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: IconName;
  tone?: Tone;
  size?: "lg" | "md" | "sm";
  /** Keyboard shortcut, shown as a small key cap inside the pill on wide screens. */
  shortcut?: string;
  children: ReactNode;
}

const SIZE_CLASS = {
  lg: "min-h-[54px] px-6 text-[17px] gap-2.5",
  md: "min-h-11 px-4 text-[15px] gap-2 sm:px-5",
  sm: "min-h-9 px-3.5 text-[13px] gap-1.5",
};

/**
 * Large, always-labelled pill. Mouse clicks drop focus afterwards so the global keyboard shortcuts
 * (Space in particular) keep working instead of re-activating the last button.
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
    <button
      type="button"
      className={`inline-flex cursor-pointer select-none items-center justify-center whitespace-nowrap rounded-full border font-medium tracking-[-0.005em] transition-[background-color,border-color,color,box-shadow,transform] duration-200 ease-out active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 disabled:active:scale-100 ${SIZE_CLASS[size]} ${TONE_CLASS[tone]} ${className}`}
      onClick={handleClick}
      aria-keyshortcuts={shortcut ? shortcutAria(shortcut) : undefined}
      {...rest}
    >
      {icon && <Icon name={icon} size={size === "sm" ? 16 : size === "md" ? 18 : 20} />}
      <span>{children}</span>
      {shortcut && <Kbd tone={tone === "primary" || tone === "danger" ? "on-ink" : "plain"}>{shortcut}</Kbd>}
    </button>
  );
}

function shortcutAria(label: string): string {
  return label === "Space" ? "Space" : label.toUpperCase();
}

/** A key cap. Inside buttons it is decorative (aria-keyshortcuts carries it) and hidden on small screens. */
export function Kbd({ children, tone = "plain" }: { children: ReactNode; tone?: "plain" | "on-ink" }) {
  const look =
    tone === "on-ink" ? "border-on-ink/25 bg-on-ink/10 text-on-ink/80" : "border-line bg-bg text-ink-muted";
  return (
    <kbd
      aria-hidden="true"
      className={`ml-1 hidden rounded-md border px-1.5 py-px font-mono text-[11px] font-normal leading-4 lg:inline-block ${look}`}
    >
      {children}
    </kbd>
  );
}

/** The rounded track that holds segment buttons or step pills (the reference's tab group). */
export function SegmentTrack({
  children,
  className = "",
  label,
  as = "div",
}: {
  children: ReactNode;
  className?: string;
  label: string;
  as?: "div" | "ol" | "ul";
}) {
  const Tag = as;
  return (
    <Tag
      role={as === "div" ? "group" : undefined}
      aria-label={label}
      className={`inline-flex flex-wrap items-center gap-1 rounded-[22px] border border-line bg-well p-1 sm:rounded-full ${className}`}
    >
      {children}
    </Tag>
  );
}

export type ChipState = "done" | "active" | "pending";

/** A progress chip: always says its state in text too (screen readers get "(done)" and friends). */
export function StatusChip({ state, children }: { state: ChipState; children: ReactNode }) {
  const tone =
    state === "done"
      ? "border-ok/25 bg-ok/10 text-ink"
      : state === "active"
        ? "border-line bg-bg-raised text-ink shadow-raised"
        : "border-line/80 bg-transparent text-ink-muted";
  return (
    <span
      className={`inline-flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 text-[13px] font-medium ${tone}`}
    >
      {state === "done" ? (
        <Icon name="check" size={14} strokeWidth={2.4} className="text-ok" />
      ) : state === "active" ? (
        <span className="size-2 animate-pulse rounded-full bg-amber" aria-hidden="true" />
      ) : (
        <span className="size-2 rounded-full border border-ink-muted/60" aria-hidden="true" />
      )}
      {children}
      <span className="sr-only">{state === "done" ? "(done)" : state === "active" ? "(in progress)" : "(waiting)"}</span>
    </span>
  );
}

/** A quiet white pill for labels and statuses. */
export function Badge({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded-full border border-line bg-bg-raised px-3 text-[13px] font-medium text-ink-muted ${className}`}
    >
      {children}
    </span>
  );
}

/** The flat warm canvas behind every Table screen (color lives only inside the orbs). */
export function Backdrop() {
  return <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 bg-bg" />;
}
