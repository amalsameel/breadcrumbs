import type { ReactNode } from "react";
import { CrumbSteps, Wordmark } from "../ui/Crumbs";
import { Pill } from "../ui/Pill";

interface PhoneScreenProps {
  children: ReactNode;
  modeLabel?: string;
  progress?: { current: number; total: number; label: string };
  footer?: ReactNode; // sticky bottom action bar (primary CTA within thumb reach)
}

/** Mobile shell: off-white canvas, safe-area padding, readable column, optional sticky footer. No horizontal scroll. */
export function PhoneScreen({ children, modeLabel, progress, footer }: PhoneScreenProps) {
  return (
    <div className="flex min-h-dvh flex-col overflow-x-clip bg-bg text-ink">
      <header
        className="mx-auto flex w-full max-w-md items-center justify-between gap-3 pb-2"
        style={{
          paddingTop: "max(1rem, env(safe-area-inset-top))",
          paddingLeft: "max(1.25rem, env(safe-area-inset-left))",
          paddingRight: "max(1.25rem, env(safe-area-inset-right))",
        }}
      >
        <span className="shrink-0">
          <Wordmark />
        </span>
        <div className="flex min-w-0 items-center justify-end gap-3">
          {progress && (
            <span className="shrink-0">
              <CrumbSteps {...progress} />
            </span>
          )}
          {/* The pill gives way first on narrow screens: it truncates with an ellipsis. */}
          {modeLabel && <Pill className="max-w-[40vw]">{modeLabel}</Pill>}
        </div>
      </header>
      <main
        className="mx-auto flex w-full max-w-md flex-1 flex-col pt-6"
        style={{
          paddingLeft: "max(1.25rem, env(safe-area-inset-left))",
          paddingRight: "max(1.25rem, env(safe-area-inset-right))",
          paddingBottom: footer ? "1.5rem" : "max(2rem, env(safe-area-inset-bottom))",
        }}
      >
        {children}
      </main>
      {footer && (
        <div
          className="sticky bottom-0 z-10 border-t border-line bg-bg/90 backdrop-blur-md"
          style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom))" }}
        >
          <div className="mx-auto w-full max-w-md px-5 pt-3">{footer}</div>
        </div>
      )}
    </div>
  );
}

/** Large regular-weight display heading + optional lead paragraph used at the top of each step. */
interface StepHeadingProps {
  eyebrow?: string;
  title: ReactNode;
  lead?: ReactNode;
  id?: string;
  size?: "lg" | "md"; // md for long titles such as interview questions
}

export function StepHeading({ eyebrow, title, lead, id, size = "lg" }: StepHeadingProps) {
  return (
    <div className="mb-8">
      {eyebrow && <p className="mb-3 text-[14px] font-medium text-ink-muted">{eyebrow}</p>}
      <h1
        id={id}
        tabIndex={-1}
        style={{ outline: "none" }}
        className={`font-display ${size === "md" ? "text-[28px] leading-[1.15]" : "text-[34px] leading-[1.08]"} font-normal tracking-[-0.035em] text-balance text-ink`}
      >
        {title}
      </h1>
      {lead && <p className="mt-4 text-[17px] leading-[1.5] text-ink-muted">{lead}</p>}
    </div>
  );
}

/** Inline error banner on the light canvas. */
export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="flex items-start gap-3 rounded-2xl border border-danger/30 bg-bg-raised px-4 py-3 text-[15px] leading-snug text-ink">
      <span aria-hidden="true" className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-danger" />
      <span className="min-w-0">{children}</span>
    </p>
  );
}
