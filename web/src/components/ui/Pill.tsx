import type { ButtonHTMLAttributes, ReactNode } from "react";

interface PillProps {
  children: ReactNode;
  tone?: "neutral" | "amber" | "hold" | "ok" | "danger";
  className?: string;
}

// Status is carried by the text; the dot only reinforces it, so ink text keeps contrast on the light canvas.
// Danger uses ink text too: --color-danger is under 4.5:1 for small text on white and on the canvas.
const DOTS = {
  neutral: null,
  amber: "bg-amber",
  hold: "bg-hold",
  ok: "bg-ok",
  danger: "bg-danger",
} as const;

/** Static label pill (status, mode, kind): white, hairline, quiet. */
export function Pill({ children, tone = "neutral", className = "" }: PillProps) {
  const dot = DOTS[tone];
  return (
    <span
      className={
        "inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full bg-bg-raised px-2.5 py-[3px] text-[13px] leading-none font-medium shadow-pill " +
        (tone === "danger" ? "text-ink " : "text-ink-muted ") +
        className
      }
    >
      {dot && <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />}
      {/* The label truncates with an ellipsis when the pill is squeezed (an inline-flex box cannot). */}
      <span className="min-w-0 truncate py-px">{children}</span>
    </span>
  );
}

interface TogglePillProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> {
  pressed: boolean;
  onPressedChange: (pressed: boolean) => void;
}

/** Pressable pill for multi-select toggles (aria-pressed), sized for touch. Pressed = black pill with a check. */
export function TogglePill({ pressed, onPressedChange, className = "", children, ...rest }: TogglePillProps) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={() => onPressedChange(!pressed)}
      className={
        "inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-full px-4 text-[15px] font-medium " +
        "transition-[background-color,color,box-shadow] duration-150 [touch-action:manipulation] active:scale-[0.98] " +
        (pressed ? "bg-ink text-on-ink" : "bg-bg-raised text-ink shadow-pill hover:bg-bg") +
        ` ${className}`
      }
      {...rest}
    >
      <span
        aria-hidden="true"
        className={
          "grid h-4 w-4 shrink-0 place-items-center rounded-full transition-colors " +
          (pressed ? "bg-on-ink text-ink" : "border border-line-strong")
        }
      >
        {pressed && (
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2.5 6.2l2.3 2.3 4.7-4.9" />
          </svg>
        )}
      </span>
      {children}
    </button>
  );
}
