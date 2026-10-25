// Pure helpers for the Table screen: names, captions, durations and orb state derivation.
import type { Crumb, EngineSnapshot, Mode, Seat, SeatKey, SessionSummary } from "../../lib/types";

export const MODE_LABEL: Record<Mode, string> = {
  social: "Social",
  networking: "Networking",
  speed_dating: "Speed dating",
};

export function seatName(session: SessionSummary | null, seat: Seat): string {
  const name = session?.seats[String(seat) as SeatKey]?.name;
  return name && name.trim() ? name : `Seat ${seat}`;
}

export function seatNames(session: SessionSummary | null): Record<Seat, string> {
  return { 1: seatName(session, 1), 2: seatName(session, 2) };
}

/** The subtle "where did this come from" line under a crumb (PRD 6.7). */
export function sourceCaption(crumb: Crumb, names: Record<Seat, string>): string {
  switch (crumb.source) {
    case "overlap":
      return "from something you both shared";
    case "profile_seat1":
      return `from ${names[1]}'s profile`;
    case "profile_seat2":
      return `from ${names[2]}'s profile`;
    case "conversation":
      return "from your conversation";
    case "outside":
      return "outside the box";
  }
}

export function wordsOf(text: string): string[] {
  return text.trim().split(/\s+/).filter(Boolean);
}

const SPOKEN_WORDS_PER_S = 2.6; // roughly a calm 155 wpm speaking voice
const MIN_LINE_S = 1.5;
const MAX_LINE_S = 16;

function decodeHeader(b64: string, bytes: number): Uint8Array | null {
  try {
    const chars = Math.ceil(bytes / 3) * 4;
    const bin = atob(b64.slice(0, chars));
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

/** Duration of a base64 WAV, read from its header without decoding the whole payload. */
export function wavDurationS(b64: string): number | null {
  if (!b64) return null;
  const head = decodeHeader(b64, 48);
  if (!head || head.length < 44) return null;
  const tag = String.fromCharCode(head[0], head[1], head[2], head[3]);
  if (tag !== "RIFF") return null;
  const view = new DataView(head.buffer);
  const byteRate = view.getUint32(28, true);
  if (!byteRate) return null;
  const padding = b64.endsWith("==") ? 2 : b64.endsWith("=") ? 1 : 0;
  const totalBytes = (b64.length * 3) / 4 - padding;
  return Math.max(0, totalBytes - 44) / byteRate;
}

/** How long a line will take to speak: the WAV length when present, else a words-per-second estimate. */
export function estimateSpeechS(audioB64: string, text: string): number {
  const fromWav = wavDurationS(audioB64);
  const estimate = fromWav ?? wordsOf(text).length / SPOKEN_WORDS_PER_S;
  return Math.min(MAX_LINE_S, Math.max(MIN_LINE_S, estimate));
}

export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function fmt(n: number | null | undefined, digits = 1): string {
  return n === null || n === undefined || Number.isNaN(n) ? "--" : n.toFixed(digits);
}

export function isLocalUrl(url: string): boolean {
  try {
    const host = new URL(url).hostname;
    return host === "localhost" || host === "127.0.0.1" || host === "0.0.0.0" || host === "[::1]";
  } catch {
    return false;
  }
}

export function isInsecureUrl(url: string): boolean {
  try {
    return new URL(url).protocol === "http:";
  } catch {
    return false;
  }
}

// ---- Orb state (PRD 6.6) --------------------------------------------------

export type OrbMode = "idle" | "listening" | "warming" | "crumb" | "holding" | "paused" | "safety";

export interface OrbView {
  mode: OrbMode;
  label: string;
  detail: string | null;
}

/** Every orb state carries a text label so no information is color-only (PRD 14.3). */
export function deriveOrb(engine: EngineSnapshot | null, speaking: boolean): OrbView {
  if (!engine) return { mode: "idle", label: "Connecting", detail: null };
  if (engine.listening_paused) return { mode: "paused", label: "Listening paused", detail: null };
  if (speaking) return { mode: "crumb", label: "Dropping a crumb", detail: null };
  if (engine.auto_paused) {
    return { mode: "safety", label: "Auto crumbs paused", detail: "Drop a crumb whenever you want one" };
  }
  if (engine.hold) {
    return {
      mode: "holding",
      label: `Holding (${engine.hold.kind})`,
      detail: `${Math.ceil(engine.hold.remaining_s)} s`,
    };
  }
  if (engine.pending_delivery) return { mode: "warming", label: "Finding a crumb", detail: null };
  if (engine.band !== "normal" && !engine.turn_active) {
    return { mode: "warming", label: "Listening closely", detail: null };
  }
  return { mode: "listening", label: "Listening", detail: null };
}
