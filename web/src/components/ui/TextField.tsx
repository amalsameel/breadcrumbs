import { forwardRef, useId } from "react";
import type { InputHTMLAttributes } from "react";

interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  hint?: string;
  error?: string | null;
}

/** Visible label + input + hint/error wired with aria-describedby. 17px text avoids iOS zoom-on-focus. */
export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { label, hint, error, className = "", ...rest },
  ref,
) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errId = `${id}-err`;
  const describedBy = [hint ? hintId : null, error ? errId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="text-[15px] font-medium text-ink">
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={
          "min-h-13 w-full rounded-2xl border bg-bg-sunken px-4 text-[17px] text-ink placeholder:text-ink-faint " +
          "outline-none transition-colors focus:border-amber " +
          (error ? "border-danger" : "border-line")
        }
        {...rest}
      />
      {hint && !error && (
        <p id={hintId} className="text-[14px] text-ink-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={errId} role="alert" className="text-[14px] text-danger">
          {error}
        </p>
      )}
    </div>
  );
});
