import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant; // primary = black pill, secondary = white pill with a hairline
  size?: ButtonSize;
  loading?: boolean;
  block?: boolean; // full width
  icon?: ReactNode;
}

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-full font-medium tracking-[-0.01em] select-none " +
  "transition-[background-color,color,opacity,box-shadow,transform] duration-150 ease-out " +
  "active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 disabled:active:scale-100 " +
  "[touch-action:manipulation] cursor-pointer";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-ink text-on-ink hover:bg-ink/85",
  secondary: "bg-bg-raised text-ink shadow-pill hover:bg-bg",
  ghost: "bg-transparent text-ink-muted hover:bg-ink/5 hover:text-ink",
  // Text is the danger token darkened 20% toward black: the raw token is under 4.5:1 on white.
  danger:
    "bg-bg-raised text-[color-mix(in_srgb,var(--color-danger)_80%,black)] " +
    "shadow-[0_1px_2px_rgb(12_11_10/0.06),0_0_0_1px_rgb(214_69_61/0.35)] hover:bg-danger/5",
};

const SIZES: Record<ButtonSize, string> = {
  md: "min-h-11 px-5 text-[15px]",
  lg: "min-h-[54px] px-7 text-[17px]",
};

export function Button({
  variant = "primary",
  size = "lg",
  loading = false,
  block = false,
  icon,
  disabled,
  className = "",
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`${BASE} ${VARIANTS[variant]} ${SIZES[size]} ${block ? "w-full" : ""} ${className}`}
      {...rest}
    >
      {loading ? <Spinner size={18} /> : icon}
      <span>{children}</span>
    </button>
  );
}
