// Single-select pill group (radiogroup with roving focus): the active option is a raised white pill.
import { useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";

export interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
}

interface SegmentedProps<T extends string> {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string; // accessible name of the group
  size?: "md" | "lg";
  className?: string;
  describedBy?: string;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "md",
  className = "",
  describedBy,
}: SegmentedProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = options.length - 1;
    let next = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = index === last ? 0 : index + 1;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = index === 0 ? last : index - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    if (next < 0) return;
    e.preventDefault();
    onChange(options[next].value);
    refs.current[next]?.focus();
  }

  // Large options step their padding and size down with the viewport so three mode names fit whole at
  // 320 px. As a last resort options shrink and their labels truncate instead of overflowing.
  const pad =
    size === "lg"
      ? "min-h-12 px-2 text-[13px] min-[360px]:px-2.5 min-[360px]:text-[14px] min-[400px]:px-3 min-[400px]:text-[15px] sm:px-5 sm:text-[16px]"
      : "min-h-11 px-3 text-[15px] min-[360px]:px-4";
  return (
    <div
      role="radiogroup"
      aria-label={label}
      aria-describedby={describedBy}
      className={`inline-flex max-w-full min-w-0 items-center gap-1 rounded-full p-1 ${className}`}
    >
      {options.map((o, i) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={
              `inline-flex min-w-0 shrink cursor-pointer items-center justify-center gap-2 rounded-full font-medium whitespace-nowrap ${pad} ` +
              "transition-[background-color,color,box-shadow] duration-200 [touch-action:manipulation] " +
              (active ? "bg-bg-raised text-ink shadow-card" : "text-ink-muted hover:text-ink")
            }
          >
            {o.icon}
            <span className="min-w-0 truncate">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}
