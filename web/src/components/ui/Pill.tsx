import type { ButtonHTMLAttributes, ReactNode } from "react";

interface PillProps {
  children: ReactNode;
  tone?: "neutral" | "amber" | "hold" | "ok" | "danger";
  className?: string;
}

const TONES = {
  neutral: "bg-bg-sunken text-ink-muted border-line",
  amber: "bg-amber/12 text-crumb border-amber/40",
  hold: "bg-hold/15 text-hold border-hold/40",
  ok: "bg-ok/12 text-ok border-ok/40",
  danger: "bg-danger/12 text-danger border-danger/40",
} as const;

/** Static label pill (status, mode, kind). */
export function Pill({ children, tone = "neutral", className = "" }: PillProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[13px] font-medium leading-none ${TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

interface TogglePillProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> {
  pressed: boolean;
  onPressedChange: (pressed: boolean) => void;
}

/** Pressable pill for multi-select toggles (aria-pressed), sized for touch. */
export function TogglePill({ pressed, onPressedChange, className = "", children, ...rest }: TogglePillProps) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={() => onPressedChange(!pressed)}
      className={
        "inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-full border px-4 text-[15px] font-medium " +
        "transition-colors duration-150 [touch-action:manipulation] " +
        (pressed
          ? "border-amber/70 bg-amber/15 text-crumb"
          : "border-line bg-bg-sunken text-ink-muted hover:text-ink") +
        ` ${className}`
      }
      {...rest}
    >
      <span
        aria-hidden="true"
        className={`h-2 w-2 rounded-full transition-colors ${pressed ? "bg-crumb shadow-[0_0_8px_#FFD27A]" : "bg-line"}`}
      />
      {children}
    </button>
  );
}
