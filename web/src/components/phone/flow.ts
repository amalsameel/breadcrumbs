// Pure phone-flow logic: step derivation (resume after reload), seat helpers, copy helpers.
// Only `import type` here so Node's test runner can load this file directly (flow.test.mjs).
import type { Mode, ProfileItemKind, Seat, SeatKey, SeatSummary, SessionSummary, TrailItem } from "../../lib/types";

export type StepKind =
  | "consent"
  | "interview"
  | "building"
  | "review"
  | "waiting"
  | "live"
  | "optin"
  | "trail";

/**
 * Progress the phone knows about before the server summary catches up. Every flag is monotonic
 * (it only ever turns on), so a stale `session` event can never move the flow backwards.
 */
export interface LocalProgress {
  consented: boolean;
  interviewDone: boolean;
  hasProfile: boolean;
  ready: boolean;
  optedIn: boolean;
}

export const NO_PROGRESS: LocalProgress = {
  consented: false,
  interviewDone: false,
  hasProfile: false,
  ready: false,
  optedIn: false,
};

export function parseSeat(raw: string | undefined): Seat | null {
  if (raw === "1") return 1;
  if (raw === "2") return 2;
  return null;
}

export const seatKey = (seat: Seat): SeatKey => (seat === 1 ? "1" : "2");
export const otherSeat = (seat: Seat): Seat => (seat === 1 ? 2 : 1);

export function mySeat(s: SessionSummary, seat: Seat): SeatSummary {
  return s.seats[seatKey(seat)];
}

export function partnerName(s: SessionSummary, seat: Seat): string | null {
  return s.seats[seatKey(otherSeat(seat))].name;
}

/**
 * The interview is only "done" from the server's point of view on first load: during a live
 * interview the answered count runs ahead of the person tapping "Sounds right".
 */
export function interviewDoneOnLoad(me: SeatSummary, questionCount: number): boolean {
  return questionCount > 0 && me.answered >= questionCount;
}

export function interviewStartIndex(answered: number, questionCount: number): number {
  if (questionCount <= 0) return 0;
  return Math.max(0, Math.min(answered, questionCount - 1));
}

/** Which screen this seat should see now. The server phase wins, then the seat's own progress. */
export function deriveStep(s: SessionSummary, seat: Seat, local: LocalProgress): StepKind {
  const me = mySeat(s, seat);
  if (s.phase === "ended") return me.opted_in !== null || local.optedIn ? "trail" : "optin";
  if (s.phase === "calibrating" || s.phase === "live") return "live";
  if (!me.consented && !local.consented) return "consent";
  if (me.ready || local.ready) return "waiting";
  if (me.has_profile || local.hasProfile) return "review";
  if (local.interviewDone) return "building";
  return "interview";
}

export function stepIndex(step: StepKind): number {
  // Index into the four visible milestones: consent, interview, review, ready.
  switch (step) {
    case "consent":
      return 0;
    case "interview":
      return 1;
    case "building":
    case "review":
      return 2;
    default:
      return 3;
  }
}

export function trailForSeat(items: TrailItem[], seat: Seat): TrailItem[] {
  return items.filter((i) => i.for_seat === seat).slice(0, 3);
}

// ---- Off-limits ---------------------------------------------------------------------

export const OFF_LIMITS_PRESETS = ["work", "family", "dating history", "politics", "religion", "health"] as const;

export function normalizeTopic(raw: string): string {
  return raw.replace(/\s+/g, " ").trim().toLowerCase().slice(0, 40);
}

export function addTopic(list: string[], raw: string): string[] {
  const topic = normalizeTopic(raw);
  if (!topic || list.includes(topic)) return list;
  return [...list, topic];
}

export function toggleTopic(list: string[], topic: string, on: boolean): string[] {
  const t = normalizeTopic(topic);
  if (on) return addTopic(list, t);
  return list.filter((x) => x !== t);
}

// ---- Copy helpers -------------------------------------------------------------------

export const KIND_LABEL: Record<ProfileItemKind, string> = {
  interest: "interest",
  story: "story",
  goal: "goal",
  fun_fact: "fun fact",
  value: "value",
  preference: "preference",
  offer: "can offer",
  seeking: "looking for",
};

export function optInCopy(mode: Mode): { title: string; yes: string; no: string; contactLabel: string; contactHint: string } {
  if (mode === "speed_dating") {
    return {
      title: "Match?",
      yes: "Yes, match",
      no: "No thanks",
      contactLabel: "How should they reach you?",
      contactHint: "Instagram, email or LinkedIn. Shown only if you both say yes.",
    };
  }
  return {
    title: "Keep in touch?",
    yes: "Yes, share my contact",
    no: "Not this time",
    contactLabel: mode === "networking" ? "Your LinkedIn or email" : "Your Instagram, email or LinkedIn",
    contactHint: "Shown to your partner only if you both say yes.",
  };
}

export function waitingLine(partner: string | null, partnerReady: boolean): string {
  if (partnerReady) return "You're both ready. Setting the table...";
  return `Waiting for ${partner ?? "your partner"}...`;
}

/** Turns a shared contact into a safe link when it is clearly an email or a URL. */
export function contactHref(contact: string): string | null {
  const c = contact.trim();
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(c)) return `mailto:${c}`;
  if (/^https?:\/\/[^\s]+$/i.test(c)) return c;
  if (/^(www\.)?linkedin\.com\/[^\s]+$/i.test(c)) return `https://${c}`;
  if (/^@[A-Za-z0-9._]{1,30}$/.test(c)) return `https://instagram.com/${c.slice(1)}`;
  return null;
}

export type MicProblem = "insecure" | "denied" | "no_device" | "busy" | "unknown";

export function classifyMicError(err: unknown): MicProblem {
  const name = typeof err === "object" && err !== null && "name" in err ? String((err as { name: unknown }).name) : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
    case "PermissionDeniedError":
      return "denied";
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "OverconstrainedError":
      return "no_device";
    case "NotReadableError":
    case "AbortError":
      return "busy";
    default:
      return "unknown";
  }
}

export const MIC_MESSAGES: Record<MicProblem, { title: string; body: string }> = {
  insecure: {
    title: "This link can't use your mic",
    body: "Phones only allow the microphone on secure (https) pages. Scan the QR code on the Table again, or ask the host for the https link.",
  },
  denied: {
    title: "Microphone is blocked",
    body: "Allow microphone access for this site in your browser settings, then tap Try again. On iPhone: tap aA in the address bar, then Website Settings.",
  },
  no_device: {
    title: "No microphone found",
    body: "We couldn't find a microphone on this device. Check that nothing is blocking it and try again.",
  },
  busy: {
    title: "Your mic is busy",
    body: "Another app may be using the microphone. Close it and try again.",
  },
  unknown: {
    title: "Recording didn't start",
    body: "Something went wrong starting the microphone. Try again in a moment.",
  },
};
