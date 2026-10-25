import type { ReactNode } from "react";
import { CrumbMark, CrumbSteps } from "../ui/Crumbs";
import { Pill } from "../ui/Pill";

interface PhoneScreenProps {
  children: ReactNode;
  modeLabel?: string;
  progress?: { current: number; total: number; label: string };
  footer?: ReactNode; // sticky bottom action bar (primary CTA within thumb reach)
}

/** Mobile shell: safe-area padding, readable column, optional sticky footer. No horizontal scroll. */
export function PhoneScreen({ children, modeLabel, progress, footer }: PhoneScreenProps) {
  return (
    <div className="relative flex min-h-dvh flex-col overflow-x-hidden bg-bg text-ink">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-72 bg-[radial-gradient(60%_100%_at_50%_0%,rgba(245,165,36,0.12),transparent)]"
      />
      <header
        className="relative mx-auto flex w-full max-w-md items-center justify-between gap-3 px-5 pb-2"
        style={{ paddingTop: "max(1rem, env(safe-area-inset-top))" }}
      >
        <div className="flex items-center gap-2">
          <CrumbMark size={26} className="text-ink" />
          <span className="font-display text-[19px] font-semibold tracking-tight">Breadcrumbs</span>
        </div>
        <div className="flex items-center gap-3">
          {progress && <CrumbSteps {...progress} />}
          {modeLabel && <Pill>{modeLabel}</Pill>}
        </div>
      </header>
      <main
        className="relative mx-auto flex w-full max-w-md flex-1 flex-col px-5 pt-4"
        style={{
          paddingLeft: "max(1.25rem, env(safe-area-inset-left))",
          paddingRight: "max(1.25rem, env(safe-area-inset-right))",
          paddingBottom: footer ? "1.5rem" : "max(1.5rem, env(safe-area-inset-bottom))",
        }}
      >
        {children}
      </main>
      {footer && (
        <div
          className="sticky bottom-0 z-10 border-t border-line/60 bg-bg/90 backdrop-blur-md"
          style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom))" }}
        >
          <div className="mx-auto w-full max-w-md px-5 pt-3">{footer}</div>
        </div>
      )}
    </div>
  );
}

/** Large display heading + optional lead paragraph used at the top of each step. */
export function StepHeading({ eyebrow, title, lead, id }: { eyebrow?: string; title: ReactNode; lead?: ReactNode; id?: string }) {
  return (
    <div className="mb-6">
      {eyebrow && <p className="mb-2 text-[14px] font-medium tracking-wide text-amber uppercase">{eyebrow}</p>}
      <h1 id={id} tabIndex={-1} style={{ outline: "none" }} className="font-display text-[30px] leading-[1.15] font-semibold text-ink">
        {title}
      </h1>
      {lead && <p className="mt-3 text-[17px] leading-relaxed text-ink-muted">{lead}</p>}
    </div>
  );
}
