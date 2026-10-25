// Mirrors server/models.py and the WebSocket protocol (PRD Section 12 + docs/CONTRACTS.md).
// Keep in sync with the server. JSON object keys for seats are strings ("1", "2").

// Mode stays in the protocol for compatibility; the UI never shows it and new sessions are "social".
export type Mode = "social" | "networking" | "speed_dating";
/** How a table runs: "quick" = just talk (no phones, learns from the conversation), "profiles" = phones + interviews. */
export type Setup = "quick" | "profiles";
export type Seat = 1 | 2;
export type SeatKey = "1" | "2";
export type Phase = "lobby" | "profiles" | "calibrating" | "live" | "ended";
export type CrumbKind = "opener" | "steer" | "switch" | "manual" | "wildcard";
export type CrumbFormat = "question" | "fun_fact" | "would_you_rather" | "callback" | "collab";
export type CrumbSource = "overlap" | "profile_seat1" | "profile_seat2" | "conversation" | "outside";
export type HoldKind = "show" | "think" | "external";
export type Band = "normal" | "warming" | "long" | "exceptional";
export type ProfileItemKind =
  | "interest" | "story" | "goal" | "fun_fact" | "value" | "preference" | "offer" | "seeking";

export interface ProfileItem {
  id: string;
  kind: ProfileItemKind;
  text: string;
  hook: string;
  depth: number;
}

export interface Profile {
  seat: Seat;
  display_name: string;
  items: ProfileItem[];
  vibe: string;
  off_limits: string[];
  removed_item_ids: string[];
}

export interface Crumb {
  id: string;
  kind: CrumbKind;
  format: CrumbFormat;
  text: string;
  addressed_to: "both" | "seat1" | "seat2";
  source: CrumbSource;
  source_item_ids: string[];
  topic: string;
  depth: number;
  why: string;
  citation_url: string | null;
  citation_title: string | null;
}

export interface TrailItem {
  for_seat: Seat;
  text: string;
}

export interface TrailResponse {
  matched: boolean;
  partner_name: string | null;
  partner_contact: string | null;
  trail: TrailItem[];
}

/** A trail of threads to pick up (server `Trail`). In a quick session the Table shows it on the laptop. */
export interface Trail {
  items: TrailItem[];
}

/**
 * What the table learned from the live conversation (short items, wiped with the session). "1" and "2"
 * are per person, "shared" is common ground, "threads" are open questions worth a follow-up.
 */
export interface Memory {
  "1": string[];
  "2": string[];
  shared: string[];
  threads: string[];
}

export interface InterviewQuestion {
  id: string;
  text: string;
}

// ---- REST ----------------------------------------------------------------

export interface CreateSessionBody {
  setup?: Setup; // default "quick"
  mode?: Mode; // default "social"
  quick?: boolean; // profiles only: one interview question instead of three
}

export interface CreateSessionResponse {
  session_id: string;
  join_urls: Partial<Record<SeatKey, string>>; // {} for a quick session
  setup: Setup;
}

/** POST /api/sessions/{sid}/start (quick only): both people consent from the laptop. Blank names become "Person 1/2". */
export interface StartSessionBody {
  names?: Partial<Record<SeatKey, string>>;
}

export interface AppConfig {
  mock: { muse: boolean; gemini: boolean }; // `gemini` = text AI mocked (no Gemini or Claude key)
  ai_provider: "claude" | "gemini" | "mock"; // first text provider; fallbacks: Gemini, then Muse Spark
  dev_tools: boolean;
  public_base_url: string;
  ffmpeg: boolean;
}

export interface SeatSummary {
  joined: boolean;
  consented: boolean;
  ready: boolean;
  name: string | null;
  has_profile: boolean;
  answered: number;
  opted_in: boolean | null; // null = not answered yet (never reveals the choice to the partner)
}

export interface SessionSummary {
  session_id: string;
  phase: Phase;
  /** Older servers omit it: treat a missing value as "profiles" (today's flow). */
  setup?: Setup;
  mode: Mode;
  quick: boolean;
  seats: Record<SeatKey, SeatSummary>;
  created_at: number; // unix seconds
  expires_in_s: number;
  dev_tools: boolean;
  mock: { muse: boolean; gemini: boolean };
}

// ---- WebSocket: server -> clients ----------------------------------------------------

export interface EngineSnapshot {
  phase: Phase;
  silence_s: number;
  band: Band;
  t_normal: number;
  t_long: number;
  t_exc: number;
  staleness: number;
  stale: boolean;
  parts: { L: number; R: number; G: number; Q: number; E: number };
  hold: { kind: HoldKind; remaining_s: number; source: "lexical" | "llm" | "manual" } | null;
  cooldown_s: number;
  next: "none" | "steer" | "switch";
  cached: { steer: string | null; switch: string | null };
  listening_paused: boolean;
  auto_paused: boolean; // safety pause (PRD 15.3)
  turn_active: boolean;
  vad_active: boolean;
  /** Local VAD is active but no longer defers a crumb (VAD_DEFER_S passed, not a near voice): likely background noise. */
  noise_suspected: boolean;
  /** Why a crumb would wait right now: an open Muse turn, a VAD onset, or the settle after a background turn. */
  defer: "turn" | "vad" | "settle" | null;
  /** A Muse turn is open that started with a near voice at the table. */
  foreground_open: boolean;
  /** Seated speech level (dBFS) once two seat turns were heard; null until then. */
  seat_level_db: number | null;
  /** Turns judged as voices away from the table this session. */
  background_turns: number;
  tts_playing: boolean;
  pending_delivery: boolean;
  crumbs_this_silence: number;
  quiet_hint: boolean; // 2 unanswered crumbs: show "Drop a crumb" hint
  depth_max: number;
  topic: string | null;
  energy: "high" | "medium" | "low" | null;
  label_map: Record<string, Seat>;
  round_remaining_s: number | null;
  crumbs_last_10min: number;
  last_decision: Decision | null;
  metrics: EngineMetrics;
}

export interface Decision {
  at_s: number; // seconds since live start
  action: "steer" | "switch" | "opener" | "manual" | "wildcard" | "hold" | "release" | "barge_in" | "skip" | "quiet" | "safety_pause" | "dropped_bot_turn" | "calibration" | "background_turn" | "dispatch_blocked";
  reason: string;
  inputs?: Record<string, unknown>;
}

export interface EngineMetrics {
  crumbs: number;
  interrupted: number;
  answered: number; // followed by >= 3 human turns within 60 s
  success_rate: number | null;
  false_interruptions: number;
  latency_ms_p50: number | null;
  latency_ms_p95: number | null;
  /** How far the pause start was backdated from Muse's speechEnd to the end of voice, ms. */
  endpoint_lag_ms_p50: number | null;
  endpoint_lag_ms_p95: number | null;
}

export interface TurnEvent {
  turn_id: number;
  seat: Seat | null;
  label: string | null;
  text: string;
  words: number;
  dropped_as_bot: boolean;
  is_question: boolean;
  is_backchannel: boolean;
  hold: HoldKind | null;
  /** A voice away from the table (bystander, far voice): not part of the conversation. */
  background?: boolean;
}

export type ServerEvent =
  | { type: "session"; session: SessionSummary }
  | { type: "partial"; text: string; label: string | null }
  | { type: "turn"; turn: TurnEvent }
  | { type: "engine"; engine: EngineSnapshot }
  | { type: "crumb"; crumb: Crumb; audio_b64: string; cache_hit: boolean; latency_ms: number }
  | { type: "crumb_interrupted"; crumb_id: string }
  | { type: "system_say"; id: string; text: string; audio_b64: string }
  | { type: "calibration"; step: "seat1" | "seat2" | "done"; label_map: Record<string, Seat> }
  | { type: "decision"; decision: Decision }
  | { type: "params"; params: Record<string, unknown> }
  | { type: "muse_status"; status: "connecting" | "connected" | "reconnecting" | "resyncing" | "closed" | "error" | "mock"; detail?: string }
  | { type: "trail_ready" }
  | { type: "memory"; memory: Memory }
  | { type: "table_trail"; trail: Trail }
  | { type: "wiped" }
  | { type: "error"; message: string };

// ---- WebSocket: clients -> server -----------------------------------------------------

export type ControlAction =
  | "spark" | "skip" | "hold" | "resume" | "pause_listening" | "resume_listening" | "end" | "xray";

/** Table VAD frame (10 Hz). `level_db` and `quiet_ms` are optional: older Tables send only `active` and `rms`. */
export interface VadMessage {
  type: "vad";
  active: boolean;
  rms: number;
  level_db?: number; // speech-band level, dBFS, rounded to 0.1 (VadReading.levelDb)
  quiet_ms?: number; // integer ms since the last voiced or sustain frame, capped at 60000; 0 while sustaining
}

export type ClientEvent =
  | VadMessage
  | { type: "control"; action: ControlAction }
  | { type: "tts_state"; crumb_id: string; playing: boolean }
  | { type: "params"; changes: Record<string, unknown> }
  | { type: "dev_turn"; seat: Seat; text: string; duration_s?: number };
