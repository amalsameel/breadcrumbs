import { useId } from "react";

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  description?: string;
}

/** Labeled on/off switch. The whole row is the touch target. */
export function Switch({ checked, onChange, label, description }: SwitchProps) {
  const descId = useId();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-describedby={description ? descId : undefined}
      onClick={() => onChange(!checked)}
      className="flex min-h-14 w-full cursor-pointer items-center justify-between gap-4 rounded-2xl border border-line bg-bg-raised px-4 py-3 text-left [touch-action:manipulation]"
    >
      <span className="flex flex-col">
        <span className="text-[16px] font-medium text-ink">{label}</span>
        {description && (
          <span id={descId} className="text-[14px] text-ink-muted">
            {description}
          </span>
        )}
      </span>
      <span
        aria-hidden="true"
        className={`relative h-7 w-12 shrink-0 rounded-full transition-colors duration-200 ${checked ? "bg-amber" : "bg-line"}`}
      >
        <span
          className={`absolute top-1 left-1 h-5 w-5 rounded-full bg-ink shadow transition-transform duration-200 ${checked ? "translate-x-5" : ""}`}
        />
      </span>
    </button>
  );
}
