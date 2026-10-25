import { forwardRef, useId } from "react";
import type { InputHTMLAttributes } from "react";

interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  hint?: string;
  error?: string | null;
}

/** Shared input look: white field, hairline border, ink focus ring. Callers add the height and border color. */
export const FIELD_CLASS =
  "w-full rounded-2xl border bg-bg-raised px-4 text-[17px] text-ink placeholder:text-ink-faint " +
  "outline-none transition-[border-color,box-shadow] duration-150 focus:border-ink focus:shadow-[0_0_0_4px_rgb(12_11_10/0.08)]";

/**
 * Visible label + input + hint/error wired with aria-describedby. 17px text avoids iOS zoom-on-focus.
 * The error is ink text with a red dot: --color-danger is under 4.5:1 for small text on the light theme.
 */
export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { label, hint, error, className = "", ...rest },
  ref,
) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errId = `${id}-err`;
  const describedBy = [hint ? hintId : null, error ? errId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      <label htmlFor={id} className="text-[15px] font-medium text-ink">
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={`${FIELD_CLASS} min-h-13 ${error ? "border-danger" : "border-line"}`}
        {...rest}
      />
      {hint && !error && (
        <p id={hintId} className="text-[14px] text-ink-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={errId} role="alert" className="flex items-start gap-2 text-[14px] leading-snug text-ink">
          <span aria-hidden="true" className="mt-[5px] h-2 w-2 shrink-0 rounded-full bg-danger" />
          <span className="min-w-0">{error}</span>
        </p>
      )}
    </div>
  );
});
