// Table state machine (pure): every server event and local playback/mic change folds into one
// `TableState`. Kept free of I/O so it can be unit tested (see __tests__/tableState.test.mjs).
import type { AppConfig, Crumb, Decision, EngineSnapshot, Seat, ServerEvent, SessionSummary, TurnEvent } from "../../lib/types";
import type { SocketStatus } from "../../lib/ws";
import { captionStage, estimateSpeechS, STUCK_PENDING_MS } from "./format";

export type MicState =
  | { status: "idle" }
  | { status: "starting" }
  | { status: "on" }
  | { status: "error"; message: string };

export type AudioLinkStatus = "idle" | SocketStatus | "forbidden";

export type PlaybackStatus = "pending" | "playing" | "done" | "interrupted";

/** One spoken line: a crumb or a calibration prompt. Times are `performance.now()` ms. */
export interface SpokenLine {
  id: string;
  text: string;
  durationS: number;
  status: PlaybackStatus;
  receivedAt: number;
  startedAt: number | null;
  endedAt: number | null;
  /** When someone pressed Skip on it; a skipped line leaves at once (captionHideAt). */
  skippedAt: number | null;
}

export interface CrumbRecord extends SpokenLine {
  crumb: Crumb;
  cacheHit: boolean;
  latencyMs: number;
}

export interface HistoryPoint {
  at: number; // ms
  silence: number;
  staleness: number;
}

export type MuseStatus = Extract<ServerEvent, { type: "muse_status" }>;
export type CalibrationStep = "seat1" | "seat2" | "done";

export interface TableState {
  load: "loading" | "ready" | "not_found" | "error";
  loadError: string | null;
  session: SessionSummary | null;
  config: AppConfig | null;
  socket: SocketStatus;
  audioLink: AudioLinkStatus;
  mic: MicState;
  engine: EngineSnapshot | null;
  history: HistoryPoint[];
  turns: TurnEvent[];
  partial: { text: string; label: string | null } | null;
  crumbs: CrumbRecord[];
  systemLine: SpokenLine | null;
  calibrationStep: CalibrationStep | null;
  labelMap: Record<string, Seat>;
  decisions: Decision[];
  params: Record<string, unknown>;
  muse: MuseStatus | null;
  serverError: string | null;
  wiped: boolean;
  ending: boolean;
}

const HISTORY_MS = 60_000;
const MAX_TURNS = 200;
const MAX_DECISIONS = 120;

export const initialState: TableState = {
  load: "loading",
  loadError: null,
  session: null,
  config: null,
  socket: "connecting",
  audioLink: "idle",
  mic: { status: "idle" },
  engine: null,
  history: [],
  turns: [],
  partial: null,
  crumbs: [],
  systemLine: null,
  calibrationStep: null,
  labelMap: {},
  decisions: [],
  params: {},
  muse: null,
  serverError: null,
  wiped: false,
  ending: false,
};

export type TableAction =
  | { type: "server"; event: ServerEvent; at: number }
  | { type: "loaded"; session: SessionSummary }
  | { type: "load_failed"; notFound: boolean; message: string }
  | { type: "config"; config: AppConfig }
  | { type: "socket"; status: SocketStatus }
  | { type: "audio_link"; status: AudioLinkStatus }
  | { type: "mic"; mic: MicState }
  | { type: "line_started"; id: string; at: number }
  | { type: "line_ended"; id: string; at: number; interrupted: boolean }
  /** The user pressed Skip on this line (crumb or prompt): unlike a barge-in it leaves at once. */
  | { type: "line_skipped"; id: string; at: number }
  | { type: "params_local"; changes: Record<string, unknown> }
  | { type: "ending"; value: boolean }
  | { type: "clear_error" }
  | { type: "wiped" };

function updateLine<T extends SpokenLine>(line: T, id: string, patch: Partial<SpokenLine>): T {
  return line.id === id ? { ...line, ...patch } : line;
}

function applyLinePatch(state: TableState, id: string, patch: Partial<SpokenLine>): TableState {
  return {
    ...state,
    crumbs: state.crumbs.map((c) => updateLine(c, id, patch)),
    systemLine: state.systemLine ? updateLine(state.systemLine, id, patch) : null,
  };
}

/** Skip: a line still pending or playing is cut; a finished one keeps its status and just leaves. */
function skipLine<T extends SpokenLine>(line: T, id: string, at: number): T {
  if (line.id !== id || line.skippedAt !== null) return line;
  if (line.status === "done") return { ...line, skippedAt: at };
  return { ...line, status: "interrupted", endedAt: line.endedAt ?? at, skippedAt: at };
}

function reduceServer(state: TableState, event: ServerEvent, at: number): TableState {
  switch (event.type) {
    case "session":
      return { ...state, session: event.session, load: "ready" };
    case "engine": {
      const e = event.engine;
      const history = state.history.filter((p) => at - p.at <= HISTORY_MS);
      history.push({ at, silence: e.silence_s, staleness: e.staleness });
      return { ...state, engine: e, history, labelMap: e.label_map ?? state.labelMap };
    }
    case "partial":
      return { ...state, partial: event.text ? { text: event.text, label: event.label } : null };
    case "turn": {
      // Upsert by turn_id (never arrival order), keeping the original position of an updated turn.
      const i = state.turns.findIndex((t) => t.turn_id === event.turn.turn_id);
      const turns = i === -1 ? [...state.turns, event.turn] : state.turns.map((t, j) => (j === i ? event.turn : t));
      return { ...state, turns: turns.slice(-MAX_TURNS), partial: null };
    }
    case "crumb": {
      const record: CrumbRecord = {
        id: event.crumb.id,
        text: event.crumb.text,
        durationS: estimateSpeechS(event.audio_b64, event.crumb.text),
        status: "pending",
        receivedAt: at,
        startedAt: null,
        endedAt: null,
        skippedAt: null,
        crumb: event.crumb,
        cacheHit: event.cache_hit,
        latencyMs: event.latency_ms,
      };
      return { ...state, crumbs: [...state.crumbs.filter((c) => c.id !== record.id), record] };
    }
    case "crumb_interrupted":
      return {
        ...state,
        crumbs: state.crumbs.map((c) =>
          c.id === event.crumb_id && c.status !== "done"
            ? { ...c, status: "interrupted", endedAt: c.endedAt ?? at }
            : c,
        ),
      };
    case "system_say":
      return {
        ...state,
        systemLine: {
          id: event.id,
          text: event.text,
          durationS: estimateSpeechS(event.audio_b64, event.text),
          status: "pending",
          receivedAt: at,
          startedAt: null,
          endedAt: null,
          skippedAt: null,
        },
      };
    case "calibration":
      return { ...state, calibrationStep: event.step, labelMap: event.label_map };
    case "decision":
      return { ...state, decisions: [event.decision, ...state.decisions].slice(0, MAX_DECISIONS) };
    case "params":
      return { ...state, params: event.params };
    case "muse_status":
      return { ...state, muse: event };
    case "wiped":
      return wipedState(state);
    case "error":
      return { ...state, serverError: event.message };
    case "trail_ready":
      return state;
  }
}

/** On wipe, drop every piece of conversation content from memory (PRD 15.1). */
function wipedState(state: TableState): TableState {
  return {
    ...initialState,
    load: state.load === "not_found" ? "not_found" : "ready",
    config: state.config,
    socket: state.socket,
    mic: { status: "idle" },
    wiped: true,
  };
}

export function tableReducer(state: TableState, action: TableAction): TableState {
  switch (action.type) {
    case "server":
      return reduceServer(state, action.event, action.at);
    case "loaded":
      return { ...state, session: state.session ?? action.session, load: "ready" };
    case "load_failed":
      if (action.notFound) return { ...state, load: "not_found", loadError: action.message };
      return state.session ? state : { ...state, load: "error", loadError: action.message };
    case "config":
      return { ...state, config: action.config };
    case "socket":
      return { ...state, socket: action.status };
    case "audio_link":
      return { ...state, audioLink: action.status };
    case "mic":
      return { ...state, mic: action.mic };
    case "line_started":
      return applyLinePatch(state, action.id, { status: "playing", startedAt: action.at });
    case "line_ended": {
      const status: PlaybackStatus = action.interrupted ? "interrupted" : "done";
      return applyLinePatch(state, action.id, { status, endedAt: action.at });
    }
    case "line_skipped":
      return {
        ...state,
        crumbs: state.crumbs.map((c) => skipLine(c, action.id, action.at)),
        systemLine: state.systemLine ? skipLine(state.systemLine, action.id, action.at) : null,
      };
    case "params_local":
      return { ...state, params: { ...state.params, ...action.changes } };
    case "ending":
      return { ...state, ending: action.value };
    case "clear_error":
      return { ...state, serverError: null };
    case "wiped":
      return wipedState(state);
  }
}

// ---- Live caption selection -----------------------------------------------------------

/** Whether a crumb still counts as being spoken (pending covers the chime and audio decode). */
export function isSpeaking(c: CrumbRecord, now: number): boolean {
  if (c.status === "playing") return true;
  return c.status === "pending" && now - c.receivedAt < STUCK_PENDING_MS;
}

/**
 * The crumb to caption in full under the orb right now, if any. It appears the moment it arrives,
 * stays while it is voiced and for a reading time afterwards (see captionHideAt).
 */
export function visibleCrumb(crumbs: CrumbRecord[], now: number): CrumbRecord | null {
  const last = crumbs.at(-1);
  return last && captionStage(last, now) === "full" ? last : null;
}

/** After its reading time the last crumb shrinks into a small "last crumb" line for a while (never a skipped one). */
export function recapCrumb(crumbs: CrumbRecord[], now: number): CrumbRecord | null {
  const last = crumbs.at(-1);
  return last && captionStage(last, now) === "recap" ? last : null;
}
