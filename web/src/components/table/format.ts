// Pure helpers for the Table screen: names, captions, durations and orb state derivation.
import type { Crumb, EngineSnapshot, Mode, Phase, Seat, SeatKey, SessionSummary } from "../../lib/types";

export const MODE_LABEL: Record<Mode, string> = {
  social: "Social",
  networking: "Networking",
  speed_dating: "Speed dating",
};

/** Each mode's orb palette (matches the Home screen's mode cards). */
export const MODE_PALETTE: Record<Mode, "ember" | "cocoa" | "lilac"> = {
  social: "ember",
  networking: "cocoa",
  speed_dating: "lilac",
};

export const MODES: Mode[] = ["social", "networking", "speed_dating"];

export type LobbyStep = 1 | 2 | 3;

/** Where the pair is in setup: 1 scan the codes, 2 consent and record, 3 both ready (say hi next). */
export function lobbyStep(session: SessionSummary): LobbyStep {
  const s1 = session.seats["1"];
  const s2 = session.seats["2"];
  if (!s1.joined || !s2.joined) return 1;
  if (!s1.ready || !s2.ready) return 2;
  return 3;
}

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

// ---- Caption timing (issue 2: show the whole line at once, then leave time to read it) ----

/** The caption's entrance: the whole line lands in one quick motion, not word by word. */
export const CAPTION_ENTER_S = 0.3;
/**
 * The outgoing caption's exit. A new crumb never waits on it: the caption slot overlays the leaving
 * line and the arriving one (no AnimatePresence "wait"), so this only sets how long the crossfade is.
 */
export const CAPTION_EXIT_S = 0.2;
/** The caption slot: a one-cell grid, so a leaving line and an arriving one share the same space. */
export const CAPTION_SLOT = "grid w-full grid-cols-1 items-start justify-items-center";
/** Every direct child of CAPTION_SLOT sits in its single cell. */
export const CAPTION_SLOT_ITEM = "col-start-1 row-start-1";
/** A crumb that never starts playing (blocked audio) stops counting as being spoken after this. */
export const STUCK_PENDING_MS = 8_000;
const LINGER_MIN_MS = 4_000;
const LINGER_PER_WORD_MS = 350;
const LINGER_BASE_MS = 2_000;
/** Never show a caption for less than this in total, however short the line or its audio. */
export const CAPTION_MIN_TOTAL_MS = 6_000;
/** After the main caption eases out, a small "last crumb" line stays this long. */
export const CAPTION_RECAP_MS = 30_000;

/** Reading time after the voice ends: at least 4 s, else 0.35 s per word plus 2 s. */
export function captionLingerMs(text: string): number {
  return Math.max(LINGER_MIN_MS, wordsOf(text).length * LINGER_PER_WORD_MS + LINGER_BASE_MS);
}

/** Per-word delay (s) for the entrance: a hint of left-to-right flow, capped so the line lands in ~0.4 s. */
export function captionStaggerS(wordCount: number): number {
  if (wordCount <= 1) return 0;
  return Math.min(0.02, 0.12 / (wordCount - 1));
}

/** The timing fields of a spoken line (crumb or calibration prompt). Times are `performance.now()` ms. */
export interface CaptionTiming {
  text: string;
  durationS: number;
  status: "pending" | "playing" | "done" | "interrupted";
  receivedAt: number;
  startedAt: number | null;
  endedAt: number | null;
  /** Set when someone pressed Skip on this line: it leaves at once, with no reading time or recap. */
  skippedAt?: number | null;
}

/**
 * When the full caption should start easing out, or null while the line is still being voiced. An
 * interrupted line keeps the same reading time, so a barge-in never yanks the text away mid-read.
 * A deliberate Skip is different: the line goes the moment it was skipped.
 */
export function captionHideAt(line: CaptionTiming, now: number): number | null {
  if (line.skippedAt != null) return line.skippedAt;
  const spokenMs = line.durationS * 1000;
  if (line.status === "playing") return null; // never take the text away while the voice is speaking
  if (line.status === "pending" && now - line.receivedAt < STUCK_PENDING_MS) return null;
  const end = line.endedAt ?? (line.startedAt ?? line.receivedAt) + spokenMs;
  return Math.max(end + captionLingerMs(line.text), line.receivedAt + CAPTION_MIN_TOTAL_MS);
}

export type CaptionStage = "full" | "recap" | "gone";

/** full = the big caption, recap = the small "last crumb" line, gone = the orb alone. */
export function captionStage(line: CaptionTiming, now: number): CaptionStage {
  const hideAt = captionHideAt(line, now);
  if (hideAt === null || now < hideAt) return "full";
  if (line.skippedAt != null) return "gone"; // a skipped crumb is not worth a "last crumb" recap
  return now < hideAt + CAPTION_RECAP_MS ? "recap" : "gone";
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

// ---- Mic copy (PRD 7 F2, 15.2) ----------------------------------------------

/**
 * What the Table says once the mic is open. Before both seats consent nothing leaves the laptop, and
 * nothing is transcribed until calibration starts, so only then does it claim live transcription.
 */
export function micOnMessage(phase: Phase | null, bothConsented: boolean): string {
  if (!bothConsented) return "Mic is ready. Nothing is sent until you have both consented.";
  if (phase === "calibrating" || phase === "live") return "Mic is on. Audio is transcribed live and never stored.";
  return "Mic is ready. Nothing is transcribed until you are both ready.";
}

/**
 * A visible problem line when live transcription is down. Calibration waits on diarized turns, so
 * without this a failed speech connection looks like a silent stall.
 */
export function speechServiceProblem(status: string | null | undefined, detail?: string): string | null {
  const why = detail ? ` (${detail})` : "";
  if (status === "reconnecting") return `Can't reach the speech service. Retrying${why}.`;
  if (status === "error") return `Speech service error${why}. Check the server log.`;
  if (status === "closed") return "Speech service disconnected. Tap Start listening again.";
  return null;
}

// ---- X-ray thresholds (PRD F18, 16.2) ---------------------------------------

export const DEFAULT_STALE_SWITCH = 0.6;

/** The live-tuned STALE_SWITCH (the engine's "stale" cut-off), else the default. */
export function staleThreshold(params: Record<string, unknown>): number {
  const v = params.STALE_SWITCH;
  return typeof v === "number" && Number.isFinite(v) ? v : DEFAULT_STALE_SWITCH;
}

// ---- Orb state (PRD 6.6) --------------------------------------------------

export type OrbMode = "idle" | "listening" | "warming" | "crumb" | "holding" | "paused" | "safety";

export interface OrbView {
  mode: OrbMode;
  label: string;
  detail: string | null;
}

/** A calibration prompt during live means the voices are being re-mapped after a reconnect. */
export function isResyncStep(step: "seat1" | "seat2" | "done" | null): boolean {
  return step === "seat1" || step === "seat2";
}

/** Every orb state carries a text label so no information is color-only (PRD 14.3). */
export function deriveOrb(engine: EngineSnapshot | null, speaking: boolean, resyncing = false): OrbView {
  if (!engine) return { mode: "idle", label: "Connecting", detail: null };
  if (engine.listening_paused) return { mode: "paused", label: "Listening paused", detail: null };
  if (resyncing) {
    // Auto crumbs wait until both voices are mapped again (CONTRACTS on_muse_reconnect).
    return { mode: "warming", label: "Re-syncing voices", detail: "Crumbs resume once both of you have spoken" };
  }
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
  if (noiseSuspected(engine)) return { mode: "listening", label: "Listening", detail: "Background noise ignored" };
  return { mode: "listening", label: "Listening", detail: null };
}

/**
 * The engine's steady-noise flag (issue 4). Optional so the Table still type-checks and renders against
 * a server that does not send it yet.
 */
export function noiseSuspected(engine: EngineSnapshot | null): boolean {
  return (engine as (EngineSnapshot & { noise_suspected?: boolean }) | null)?.noise_suspected === true;
}
