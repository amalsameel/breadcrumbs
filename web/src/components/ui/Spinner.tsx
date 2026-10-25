// Small circular spinner. Decorative by default: pair it with visible or aria-live text.
interface SpinnerProps {
  size?: number;
  className?: string;
  label?: string; // when set, the spinner is announced instead of hidden
}

export function Spinner({ size = 20, className = "", label }: SpinnerProps) {
  const a11y = label ? { role: "status", "aria-label": label } : { "aria-hidden": true };
  return (
    <svg
      {...a11y}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={`animate-spin ${className}`}
    >
      <circle cx="12" cy="12" r="9.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21.5 12a9.5 9.5 0 0 0-9.5-9.5" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
