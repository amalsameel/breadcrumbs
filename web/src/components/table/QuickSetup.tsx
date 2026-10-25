// The "Just talk" setup on the Table: no phones, no forms beyond one consent card. Consent (with two
// optional names) -> listening straight away (no say-hi calibration) -> after End, the trail on the
// laptop with a Done button that deletes the session.
import { motion } from "framer-motion";
import { useState, type FormEvent, type ReactNode } from "react";
import type { Seat, Trail } from "../../lib/types";
import type { TableView } from "../../lib/useTable";
import CrumbOrb from "../CrumbOrb";
import { FIELD_CLASS } from "../ui/TextField";
import { micOnMessage, speechServiceProblem } from "./format";
import { Icon } from "./Icon";
import { TableButton } from "./primitives";
import StartListening from "./StartListening";

const EASE_OUT: [number, number, number, number] = [0.22, 1, 0.36, 1];
const HEADLINE = "font-display font-normal leading-[1.04] tracking-[-0.04em] text-ink text-balance";

/** Exactly what the consent card says (both people read it on the laptop before anything is heard). */
export const QUICK_CONSENT_LINE = "Breadcrumbs listens and transcribes your conversation live. Nothing is stored.";

function Column({ children, gap = "gap-8" }: { children: ReactNode; gap?: string }) {
  return (
    <motion.div
      className={`mx-auto flex w-full max-w-[640px] flex-1 flex-col items-center justify-center ${gap} px-4 py-8 text-center sm:px-8`}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: EASE_OUT }}
    >
      {children}
    </motion.div>
  );
}

// ---- Consent ----------------------------------------------------------------------------

interface QuickConsentProps {
  onStart(names: Record<Seat, string>): Promise<void>;
}

export function QuickConsent({ onStart }: QuickConsentProps) {
  const [names, setNames] = useState<Record<Seat, string>>({ 1: "", 2: "" });
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (starting) return;
    setStarting(true);
    setError(null);
    try {
      await onStart(names);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't start. Try again.");
      setStarting(false);
    }
  };

  return (
    <Column>
      <div className="size-[min(22vh,200px,44vw)]">
        <CrumbOrb mode="idle" label="Breadcrumbs: ready when you are" />
      </div>
      <h1 className={`${HEADLINE} text-[clamp(38px,min(4.4vw,7vh),60px)]`}>Just talk.</h1>

      <form
        onSubmit={submit}
        noValidate
        aria-labelledby="quick-consent-title"
        className="flex w-full flex-col gap-5 rounded-[28px] border border-line bg-bg-raised p-5 text-left shadow-card sm:p-7"
      >
        <p id="quick-consent-title" className="flex items-start gap-3 text-[17px] leading-[1.5] text-ink">
          <Icon name="mic" size={20} className="mt-0.5 shrink-0 text-amber-ink" />
          {QUICK_CONSENT_LINE}
        </p>

        <div className="grid gap-3 sm:grid-cols-2">
          {([1, 2] as const).map((seat) => (
            <label key={seat} className="flex min-w-0 flex-col gap-1.5">
              <span className="text-[14px] font-medium text-ink">
                Person {seat} <span className="font-normal text-ink-muted">(optional)</span>
              </span>
              <input
                type="text"
                value={names[seat]}
                onChange={(e) => setNames((n) => ({ ...n, [seat]: e.target.value }))}
                maxLength={60}
                autoComplete="off"
                autoCapitalize="words"
                enterKeyHint={seat === 1 ? "next" : "go"}
                placeholder="First name"
                className={`${FIELD_CLASS} min-h-12 border-line`}
              />
            </label>
          ))}
        </div>

        {error && (
          <p role="alert" className="flex items-start gap-2 rounded-2xl border border-danger/30 bg-danger/5 px-4 py-2.5 text-[14px] text-ink">
            <span aria-hidden="true" className="mt-[6px] size-2 shrink-0 rounded-full bg-danger" />
            {error}
          </p>
        )}

        <div className="flex flex-col gap-2.5">
          <TableButton type="submit" tone="primary" icon="check" disabled={starting} className="w-full">
            {starting ? "Starting" : "We're both in"}
          </TableButton>
          <p className="text-center text-[13px] leading-relaxed text-ink-muted">
            Pressing this means you both agree. The mic turns on, and the session is deleted when you finish.
          </p>
        </div>
      </form>
    </Column>
  );
}

// ---- Starting: consented, waiting for the mic and the first audio -----------------------------

export function QuickStarting({ view, onStartListening }: { view: TableView; onStartListening(): void }) {
  const micOn = view.mic.status === "on";
  const problem = micOn ? speechServiceProblem(view.muse?.status, view.muse?.detail) : null;
  return (
    <Column>
      <div className="size-[min(40vh,340px,74vw)]">
        <CrumbOrb mode={micOn ? "listening" : "idle"} label={`Breadcrumbs: ${micOn ? "listening" : "waiting for the mic"}`} />
      </div>
      <div className="space-y-3">
        <h1 className={`${HEADLINE} text-[clamp(34px,min(3.8vw,6vh),52px)]`}>
          {micOn ? "Listening. Just talk." : "Turn on the mic to begin."}
        </h1>
        <p className="mx-auto max-w-[38ch] text-[17px] leading-[1.5] text-ink-muted">
          Breadcrumbs stays quiet while you talk and only steps in when a pause runs long.
        </p>
      </div>
      {problem && (
        <p role="alert" className="max-w-xl rounded-2xl border border-danger/30 bg-danger/5 px-4 py-2.5 text-[14px] text-ink">
          {problem}
        </p>
      )}
      {!micOn ? (
        <StartListening mic={view.mic} onStart={onStartListening} onMessage={micOnMessage("calibrating", view.bothConsented)} />
      ) : view.audioLink === "forbidden" ? (
        <p className="text-[14px] text-ink-muted" role="status">
          Recording your consent. Nothing is sent until it is on file.
        </p>
      ) : null}
    </Column>
  );
}

// ---- End: the trail on the laptop ---------------------------------------------------------

interface QuickTrailProps {
  trail: Trail | null;
  names: Record<Seat, string>;
  crumbCount: number;
  onDone(): Promise<void>;
}

export function QuickTrail({ trail, names, crumbCount, onDone }: QuickTrailProps) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const items = trail?.items ?? [];

  const done = async () => {
    setDeleting(true);
    setError(null);
    try {
      await onDone();
    } catch {
      setError("Couldn't reach the server to delete the session. It is deleted automatically within 10 minutes.");
      setDeleting(false);
    }
  };

  return (
    <Column gap="gap-7">
      <div className="size-[min(18vh,150px,40vw)]">
        <CrumbOrb mode={trail ? "idle" : "warming"} label={trail ? "Breadcrumbs: session ended" : "Breadcrumbs: writing your trail"} />
      </div>
      <div className="space-y-3">
        <h1 className={`${HEADLINE} text-[clamp(36px,min(4vw,6.4vh),56px)]`}>Threads to pick up.</h1>
        <p className="text-[17px] leading-[1.5] text-ink-muted" role="status">
          {trail
            ? items.length
              ? "A few things worth coming back to next time."
              : "No loose threads this time."
            : "Writing your trail from the conversation..."}
        </p>
      </div>

      {items.length > 0 && (
        <ol className="flex w-full flex-col gap-2.5 text-left">
          {items.map((item, i) => (
            <motion.li
              key={`${i}-${item.text}`}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.08 + i * 0.1, duration: 0.35, ease: EASE_OUT }}
              className="flex items-start gap-4 rounded-3xl border border-line bg-bg-raised p-4 sm:p-5"
            >
              <span className="mt-0.5 font-mono text-[12px] text-ink-muted" aria-hidden="true">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0 flex-1 space-y-1">
                <span className="block text-[12px] font-medium uppercase tracking-[0.14em] text-ink-muted">
                  For {names[item.for_seat] ?? `Person ${item.for_seat}`}
                </span>
                <span className="block text-[17px] leading-[1.45] text-ink">{item.text}</span>
              </span>
            </motion.li>
          ))}
        </ol>
      )}

      <div className="flex flex-col items-center gap-3">
        <TableButton tone="primary" icon="check" onClick={done} disabled={deleting}>
          {deleting ? "Deleting" : "Done"}
        </TableButton>
        <p className="max-w-md text-[14px] leading-relaxed text-ink-muted">
          Done deletes the transcript and everything Breadcrumbs picked up, right now. Otherwise it goes in 10 minutes.
          {crumbCount > 0 ? ` ${crumbCount} ${crumbCount === 1 ? "crumb" : "crumbs"} dropped.` : ""}
        </p>
        {error && (
          <p role="alert" className="max-w-md rounded-2xl border border-danger/30 bg-danger/5 px-4 py-2.5 text-[14px] text-ink">
            {error}
          </p>
        )}
      </div>
    </Column>
  );
}
