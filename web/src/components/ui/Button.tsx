import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  block?: boolean; // full width
  icon?: ReactNode;
}

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-2xl font-semibold select-none " +
  "transition-[background-color,color,opacity,box-shadow,transform] duration-150 ease-out " +
  "active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45 disabled:active:scale-100 " +
  "[touch-action:manipulation] cursor-pointer";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-amber text-bg shadow-[0_0_0_1px_rgba(255,210,122,0.35),0_8px_28px_-10px_rgba(245,165,36,0.75)] " +
    "hover:bg-crumb",
  secondary: "bg-bg-raised text-ink border border-line hover:border-ink-faint",
  ghost: "bg-transparent text-ink-muted hover:text-ink hover:bg-bg-raised",
  danger: "bg-transparent text-danger border border-danger/50 hover:bg-danger/10",
};

const SIZES: Record<ButtonSize, string> = {
  md: "min-h-11 px-4 text-[15px]",
  lg: "min-h-14 px-6 text-[17px]",
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
