// End of session (PRD 6.8, F17): private opt-in, then the trail (or a kind thank-you). Never reveals who declined.
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../../lib/api";
import type { Mode, Seat, TrailResponse } from "../../lib/types";
import { TrailCard } from "../TrailCard";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { BreathingCrumb } from "../ui/Crumbs";
import { TextField } from "../ui/TextField";
import { contactHref, optInCopy, trailForSeat } from "./flow";
import { StepHeading } from "./PhoneScreen";

// ---- Opt-in -------------------------------------------------------------------------

interface OptInStepProps {
  mode: Mode;
  partner: string | null;
  onSubmit: (share: boolean, contact: string | null) => Promise<void>;
  describeError: (err: unknown) => string;
}

export function OptInStep({ mode, partner, onSubmit, describeError }: OptInStepProps) {
  const copy = optInCopy(mode);
  const [choice, setChoice] = useState<"yes" | null>(null);
  const [contact, setContact] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<"yes" | "no" | null>(null);
  const contactRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (choice === "yes") contactRef.current?.focus();
  }, [choice]);

  async function send(share: boolean) {
    const value = contact.trim();
    if (share && !value) {
      setFieldError("Add a way to reach you, or choose " + copy.no.toLowerCase() + ".");
      return;
    }
    setSubmitting(share ? "yes" : "no");
    setError(null);
    try {
      await onSubmit(share, share ? value : null);
    } catch (err) {
      setError(describeError(err));
      setSubmitting(null);
    }
  }

  function submitYes(e: FormEvent) {
    e.preventDefault();
    void send(true);
  }

  return (
    <section aria-labelledby="step-title" className="flex flex-col gap-6">
      <StepHeading
        id="step-title"
        eyebrow="That's a wrap"
        title={copy.title}
        lead={
          <>
            {askLine(mode, partner)}{" "}
            This is private: nobody sees your answer, and contacts are swapped only if you both say yes.
          </>
        }
      />

      {choice === null ? (
        <div className="flex flex-col gap-3">
          <Button block onClick={() => setChoice("yes")}>
            {copy.yes}
          </Button>
          <Button block variant="secondary" loading={submitting === "no"} onClick={() => void send(false)}>
            {copy.no}
          </Button>
        </div>
      ) : (
        <form onSubmit={submitYes} noValidate className="flex flex-col gap-4">
          <TextField
            ref={contactRef}
            label={copy.contactLabel}
            hint={copy.contactHint}
            error={fieldError}
            value={contact}
            onChange={(e) => {
              setContact(e.target.value);
              if (fieldError) setFieldError(null);
            }}
            maxLength={200}
            autoComplete="email"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="send"
            placeholder="@maya.runs or maya@example.com"
          />
          <Button type="submit" block loading={submitting === "yes"}>
            Share
          </Button>
          <Button block variant="ghost" disabled={submitting !== null} onClick={() => void send(false)}>
            Actually, {copy.no.toLowerCase()}
          </Button>
        </form>
      )}

      {error && (
        <p role="alert" className="rounded-2xl border border-danger/50 bg-danger/10 px-4 py-3 text-[15px] text-ink">
          {error}
        </p>
      )}
    </section>
  );
}

function askLine(mode: Mode, partner: string | null): string {
  const who = partner ?? "them";
  return mode === "speed_dating" ? `Would you like to see ${who} again?` : `Would you like to stay in touch with ${who}?`;
}

// ---- Trail --------------------------------------------------------------------------

const TRAIL_POLL_MS = 3000;

interface TrailStepProps {
  sid: string;
  seat: Seat;
  trail: TrailResponse | null;
  trailReadyTick: number;
  wiped: boolean;
  onTrail: (t: TrailResponse) => void;
  checkSession: () => Promise<void>; // a 404 may mean the session is gone
}

export function TrailStep({ sid, seat, trail, trailReadyTick, wiped, onTrail, checkSession }: TrailStepProps) {
  useEffect(() => {
    if (trail) return;
    let cancelled = false;
    const fetchTrail = async () => {
      try {
        const t = await api.trail(sid, seat);
        if (!cancelled) onTrail(t);
      } catch (err) {
        // Not ready yet (the partner hasn't answered or the trail is still generating): keep polling.
        if (err instanceof ApiError && err.status === 404 && !cancelled) await checkSession();
      }
    };
    void fetchTrail();
    const id = window.setInterval(() => void fetchTrail(), TRAIL_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [sid, seat, trail, trailReadyTick, onTrail, checkSession]);

  if (!trail) {
    return (
      <section aria-labelledby="step-title" className="flex flex-1 flex-col items-center justify-center text-center">
        <BreathingCrumb size={140} />
        <div className="mt-8" aria-live="polite">
          <StepHeading id="step-title" title="Thanks! Gathering the trail..." lead="This only takes a moment." />
        </div>
      </section>
    );
  }

  const mine = trailForSeat(trail.trail, seat);
  return (
    <section aria-labelledby="step-title" className="flex flex-col gap-6">
      {trail.matched ? (
        <>
          <StepHeading
            id="step-title"
            eyebrow="It's a match"
            title={trail.partner_name ? `Keep in touch with ${trail.partner_name}` : "You both said yes"}
          />
          {trail.partner_contact && <ContactCard name={trail.partner_name} contact={trail.partner_contact} />}
          <TrailCard items={mine} />
        </>
      ) : (
        <div className="flex flex-col items-center pt-8 text-center">
          <BreathingCrumb size={120} />
          <div className="mt-6">
            <StepHeading
              id="step-title"
              title="Thanks for the conversation!"
              lead="Every good chat leaves a few crumbs behind. We hope you enjoyed this one."
            />
          </div>
        </div>
      )}
      <DeletedNote wiped={wiped} />
    </section>
  );
}

function ContactCard({ name, contact }: { name: string | null; contact: string }) {
  const [copied, setCopied] = useState(false);
  const href = contactHref(contact);
  const canCopy = typeof navigator !== "undefined" && !!navigator.clipboard;

  async function copy() {
    try {
      await navigator.clipboard.writeText(contact);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Card className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <p className="text-[14px] text-ink-muted">{name ? `${name} shared` : "Their contact"}</p>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="block truncate text-[19px] font-semibold text-crumb underline decoration-amber/40 underline-offset-4"
          >
            {contact}
          </a>
        ) : (
          <p className="truncate text-[19px] font-semibold text-crumb select-all">{contact}</p>
        )}
      </div>
      {canCopy && (
        <Button variant="secondary" size="md" onClick={() => void copy()} aria-label={`Copy ${contact}`}>
          {copied ? "Copied" : "Copy"}
        </Button>
      )}
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </Card>
  );
}

function DeletedNote({ wiped }: { wiped: boolean }) {
  return (
    <p className="flex items-start gap-2 rounded-2xl bg-bg-sunken px-4 py-3 text-[15px] leading-relaxed text-ink-muted" aria-live="polite">
      <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-ok" />
      {wiped
        ? "This session has been deleted. The transcript and profiles are gone, and no audio was ever stored."
        : "This session is being deleted now. The transcript and profiles go with it, and no audio was ever stored."}
    </p>
  );
}

// ---- Keep the trail across a reload (it is the person's own result; tab-scoped) ----------

const storageKey = (sid: string, seat: Seat) => `breadcrumbs:trail:${sid}:${seat}`;

export function saveTrail(sid: string, seat: Seat, t: TrailResponse): void {
  try {
    window.sessionStorage.setItem(storageKey(sid, seat), JSON.stringify(t));
  } catch {
    /* storage unavailable (private mode): the trail just won't survive a reload */
  }
}

export function loadTrail(sid: string, seat: Seat): TrailResponse | null {
  try {
    const raw = window.sessionStorage.getItem(storageKey(sid, seat));
    return raw ? (JSON.parse(raw) as TrailResponse) : null;
  } catch {
    return null;
  }
}
