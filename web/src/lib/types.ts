// Mirrors server/models.py and the WebSocket protocol (PRD Section 12 + docs/CONTRACTS.md).
// Keep in sync with the server. JSON object keys for seats are strings ("1", "2").

export type Mode = "social" | "networking" | "speed_dating";
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

export interface InterviewQuestion {
  id: string;
  text: string;
}

// ---- REST ----------------------------------------------------------------

export interface CreateSessionResponse {
  session_id: string;
  join_urls: Record<SeatKey, string>;
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
  /** Local VAD is active but Muse has not confirmed speech within VAD_UNCONFIRMED_S: likely background noise. */
  noise_suspected: boolean;
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
  action: "steer" | "switch" | "opener" | "manual" | "wildcard" | "hold" | "release" | "barge_in" | "skip" | "quiet" | "safety_pause" | "dropped_bot_turn" | "calibration";
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
  | { type: "wiped" }
  | { type: "error"; message: string };

// ---- WebSocket: clients -> server -----------------------------------------------------

export type ControlAction =
  | "spark" | "skip" | "hold" | "resume" | "pause_listening" | "resume_listening" | "end" | "xray";

export type ClientEvent =
  | { type: "vad"; active: boolean; rms: number }
  | { type: "control"; action: ControlAction }
  | { type: "tts_state"; crumb_id: string; playing: boolean }
  | { type: "params"; changes: Record<string, unknown> }
  | { type: "dev_turn"; seat: Seat; text: string; duration_s?: number };
