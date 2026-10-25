// Table state + I/O (PRD 6.5 to 6.7, 12.1, 14.1): the events socket (role=table), the mic capture,
// the PCM uplink, crumb playback with barge-in, and every piece of Table UI state.
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { startCapture, type CaptureHandle } from "../audio/capture";
import { CrumbPlayer } from "../audio/player";
import { estimateSpeechS } from "../components/table/format";
import { BARGE_IN_FADE_MS, PlaybackGate, speakLine } from "../components/table/playback";
import { initialState, tableReducer, type TableState } from "../components/table/tableState";
import { api, ApiError } from "./api";
import type { ClientEvent, ControlAction, Seat, ServerEvent } from "./types";
import { AudioSocket, EventSocket } from "./ws";

export type {
  AudioLinkStatus,
  CalibrationStep,
  CrumbRecord,
  HistoryPoint,
  MicState,
  MuseStatus,
  PlaybackStatus,
  SpokenLine,
  TableState,
} from "../components/table/tableState";

const FORBIDDEN_RETRY_MS = 3000;
const PAUSE_SYNC_GRACE_MS = 1500;
const ERROR_TOAST_MS = 6000;

// ---- Mic errors -------------------------------------------------------------------

function micErrorMessage(err: unknown): string {
  if (typeof navigator !== "undefined" && !navigator.mediaDevices) {
    return "The microphone needs a secure page. Open the Table on localhost or over HTTPS.";
  }
  const name = err instanceof DOMException ? err.name : err instanceof Error ? err.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone access was blocked. Allow the mic from the address bar, then try again.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return "No microphone was found. Plug one in or pick an input in system settings.";
  }
  if (name === "MicLost") {
    return "The microphone stopped. Turn it back on to keep listening.";
  }
  if (name === "NotReadableError") {
    return "The microphone is busy in another app. Close it there, then try again.";
  }
  return "The microphone could not start. Check the browser permissions and try again.";
}

// ---- Hook ---------------------------------------------------------------------------

export interface TableActions {
  startListening(): Promise<void>;
  control(action: ControlAction): void;
  spark(): void;
  skip(): void;
  toggleHold(): void;
  togglePause(): void;
  end(): Promise<void>;
  wipe(): Promise<void>;
  setParams(changes: Record<string, unknown>): void;
  devTurn(seat: Seat, text: string): void;
}

export interface TableView extends TableState {
  joinUrls: Record<Seat, string>;
  listeningPaused: boolean;
  bothConsented: boolean;
}

export function useTable(sid: string): [TableView, TableActions] {
  const [state, dispatch] = useReducer(tableReducer, initialState);

  const eventsRef = useRef<EventSocket | null>(null);
  const audioRef = useRef<AudioSocket | null>(null);
  const captureRef = useRef<CaptureHandle | null>(null);
  const playerRef = useRef<CrumbPlayer | null>(null);
  const [gate] = useState(() => new PlaybackGate()); // the line being sounded, chime included
  const interruptedRef = useRef<Set<string>>(new Set());
  const pausedRef = useRef(false);
  const stoppedRef = useRef(false); // speaker off for good: End, wipe or unmount
  const bothConsentedRef = useRef(false);
  const pauseToggledAtRef = useRef(-Infinity);

  const send = useCallback((event: ClientEvent) => eventsRef.current?.send(event), []);

  // Speaks one line: chime (crumbs only), then audio or speech synthesis, with tts_state reporting.
  const speak = useCallback(
    async (id: string, audioB64: string, text: string, chime: boolean) => {
      const player = playerRef.current;
      if (!player || stoppedRef.current) {
        // No unlocked audio yet: still reveal the caption on its estimated timeline.
        dispatch({ type: "line_started", id, at: performance.now() });
        const ms = estimateSpeechS(audioB64, text) * 1000;
        window.setTimeout(() => dispatch({ type: "line_ended", id, at: performance.now(), interrupted: false }), ms);
        return;
      }
      await speakLine(
        {
          player,
          gate,
          send,
          started: (lineId, at) => dispatch({ type: "line_started", id: lineId, at }),
          ended: (lineId, at, interrupted) => dispatch({ type: "line_ended", id: lineId, at, interrupted }),
          cancelled: (lineId) => interruptedRef.current.has(lineId) || stoppedRef.current,
          now: () => performance.now(),
        },
        id,
        audioB64,
        text,
        chime,
      );
    },
    [send, gate],
  );

  const fadeIfPlaying = useCallback(
    (id?: string) => {
      const current = gate.currentId;
      if (!current || (id && current !== id)) return;
      playerRef.current?.fadeOut(BARGE_IN_FADE_MS);
    },
    [gate],
  );

  const onServerEvent = useCallback(
    (event: ServerEvent) => {
      dispatch({ type: "server", event, at: performance.now() });
      switch (event.type) {
        case "crumb":
          void speak(event.crumb.id, event.audio_b64, event.crumb.text, true);
          break;
        case "system_say":
          void speak(event.id, event.audio_b64, event.text, false);
          break;
        case "crumb_interrupted":
          interruptedRef.current.add(event.crumb_id);
          fadeIfPlaying(event.crumb_id);
          break;
        case "wiped":
          stoppedRef.current = true;
          playerRef.current?.fadeOut(BARGE_IN_FADE_MS);
          break;
      }
    },
    [speak, fadeIfPlaying],
  );

  // Session summary + config, then the events socket.
  useEffect(() => {
    let cancelled = false;
    stoppedRef.current = false;
    api
      .getSession(sid)
      .then((session) => !cancelled && dispatch({ type: "loaded", session }))
      .catch((err: unknown) => {
        if (cancelled) return;
        const notFound = err instanceof ApiError && err.status === 404;
        dispatch({ type: "load_failed", notFound, message: err instanceof Error ? err.message : "Request failed" });
      });
    api
      .config()
      .then((config) => !cancelled && dispatch({ type: "config", config }))
      .catch(() => undefined);

    const socket = new EventSocket(sid, "table");
    eventsRef.current = socket;
    const offEvents = socket.on(onServerEvent);
    const offStatus = socket.onStatus((status) => dispatch({ type: "socket", status }));
    dispatch({ type: "socket", status: socket.status });
    return () => {
      cancelled = true;
      offEvents();
      offStatus();
      socket.close();
      if (eventsRef.current === socket) eventsRef.current = null;
    };
  }, [sid, onServerEvent]);

  // Server error toasts are transient.
  useEffect(() => {
    if (!state.serverError) return;
    const timer = window.setTimeout(() => dispatch({ type: "clear_error" }), ERROR_TOAST_MS);
    return () => window.clearTimeout(timer);
  }, [state.serverError]);

  // Close the events socket for good once the session is gone.
  useEffect(() => {
    if (state.load === "not_found" || state.wiped) eventsRef.current?.close();
  }, [state.load, state.wiped]);

  const phase = state.session?.phase ?? null;
  const seats = state.session?.seats;
  const bothConsented = Boolean(seats?.["1"].consented && seats?.["2"].consented);
  const micOn = state.mic.status === "on";
  const sessionOver = phase === "ended" || state.wiped || state.load === "not_found";
  bothConsentedRef.current = bothConsented;

  // PCM uplink: needs the mic and both consents (the server answers 4403 otherwise).
  const [retryTick, bumpRetry] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    if (!micOn || !bothConsented || sessionOver) return;
    const socket = new AudioSocket(sid);
    let retry: number | null = null;
    socket.onStatus = (status, code) => {
      if (status === "closed" && code === 4403) {
        dispatch({ type: "audio_link", status: "forbidden" });
        retry = window.setTimeout(bumpRetry, FORBIDDEN_RETRY_MS);
        return;
      }
      dispatch({ type: "audio_link", status });
    };
    audioRef.current = socket;
    return () => {
      if (retry !== null) window.clearTimeout(retry);
      socket.onStatus = null;
      socket.close();
      if (audioRef.current === socket) audioRef.current = null;
      dispatch({ type: "audio_link", status: "idle" });
    };
  }, [sid, micOn, bothConsented, sessionOver, retryTick]);

  // Keep capture in sync with the server's listening state (covers a rejected toggle or a reconnect).
  // A local toggle wins for a short grace window while the server catches up.
  const listeningPaused = state.engine?.listening_paused ?? false;
  useEffect(() => {
    if (!state.engine || performance.now() - pauseToggledAtRef.current < PAUSE_SYNC_GRACE_MS) return;
    if (pausedRef.current === listeningPaused) return;
    captureRef.current?.setPaused(listeningPaused); // may emit a last vad=false before the gate closes
    pausedRef.current = listeningPaused;
  }, [state.engine, listeningPaused]);

  // Session over: the mic and the speaker stop (nothing is captured after End).
  useEffect(() => {
    if (!sessionOver) return;
    stoppedRef.current = true; // a crumb still in its chime (or decoding) must not start after End
    captureRef.current?.stop();
    captureRef.current = null;
    playerRef.current?.fadeOut(BARGE_IN_FADE_MS);
    if (state.mic.status === "on") dispatch({ type: "mic", mic: { status: "idle" } });
  }, [sessionOver, state.mic.status]);

  useEffect(
    () => () => {
      stoppedRef.current = true;
      captureRef.current?.stop();
      captureRef.current = null;
      playerRef.current?.fadeOut(BARGE_IN_FADE_MS);
    },
    [],
  );

  const onVad = useCallback(
    (active: boolean, rms: number) => {
      // Nothing about the room leaves the laptop until both seats consent (PRD 7 F2).
      if (pausedRef.current || !bothConsentedRef.current) return;
      const now = performance.now();
      // Chime and first 300 ms of the voice: a new VAD onset there is held back (PRD 8.11.3).
      if (gate.forward(active, now)) send({ type: "vad", active, rms });
      if (gate.shouldFade(active, now)) playerRef.current?.fadeOut(BARGE_IN_FADE_MS);
    },
    [send, gate],
  );

  const startListening = useCallback(async () => {
    if (captureRef.current) return;
    dispatch({ type: "mic", mic: { status: "starting" } });
    try {
      const handle = await startCapture({
        onFrame: (pcm) => audioRef.current?.send(pcm),
        onVad,
        onLost: () => {
          // Unplugged or revoked mid-session: drop the handle so Start listening can open it again.
          if (captureRef.current !== handle) return;
          handle.stop();
          captureRef.current = null;
          const err = new Error("The microphone stopped");
          err.name = "MicLost";
          dispatch({ type: "mic", mic: { status: "error", message: micErrorMessage(err) } });
        },
      });
      if (stoppedRef.current) {
        handle.stop(); // the session ended while the mic was starting
        dispatch({ type: "mic", mic: { status: "idle" } });
        return;
      }
      captureRef.current = handle;
      handle.setPaused(pausedRef.current);
      // Created inside the user gesture, so playback is unlocked for the whole session (and re-attached
      // to the new context after a mic restart).
      if (!playerRef.current) playerRef.current = new CrumbPlayer(handle.ctx);
      else playerRef.current.attach(handle.ctx);
      if (handle.ctx.state === "suspended") await handle.ctx.resume().catch(() => undefined);
      dispatch({ type: "mic", mic: { status: "on" } });
    } catch (err) {
      dispatch({ type: "mic", mic: { status: "error", message: micErrorMessage(err) } });
    }
  }, [onVad]);

  const control = useCallback((action: ControlAction) => send({ type: "control", action }), [send]);

  const actions = useMemo<TableActions>(
    () => ({
      startListening,
      control,
      spark: () => control("spark"),
      skip: () => {
        // A crumb still in its chime is skipped too: it must not start afterwards.
        const current = gate.currentId;
        if (current) interruptedRef.current.add(current);
        fadeIfPlaying();
        control("skip");
      },
      toggleHold: () => control(state.engine?.hold ? "resume" : "hold"),
      togglePause: () => {
        const next = !pausedRef.current;
        pauseToggledAtRef.current = performance.now();
        captureRef.current?.setPaused(next); // may emit a last vad=false before the gate closes
        pausedRef.current = next;
        control(next ? "pause_listening" : "resume_listening");
      },
      end: async () => {
        dispatch({ type: "ending", value: true });
        try {
          await api.end(sid);
        } catch {
          control("end");
        } finally {
          dispatch({ type: "ending", value: false });
        }
      },
      wipe: async () => {
        try {
          await api.wipe(sid);
        } catch (err) {
          // Already gone counts as wiped.
          if (!(err instanceof ApiError && err.status === 404)) throw err;
        }
        stoppedRef.current = true;
        dispatch({ type: "wiped" });
      },
      setParams: (changes) => {
        dispatch({ type: "params_local", changes });
        send({ type: "params", changes });
      },
      devTurn: (seat, text) => {
        const clean = text.trim();
        if (clean) send({ type: "dev_turn", seat, text: clean });
      },
    }),
    [startListening, control, fadeIfPlaying, send, sid, state.engine?.hold, gate],
  );

  const joinUrls = useMemo<Record<Seat, string>>(() => {
    const base = (state.config?.public_base_url || window.location.origin).replace(/\/+$/, "");
    return { 1: `${base}/join/${sid}/1`, 2: `${base}/join/${sid}/2` };
  }, [state.config?.public_base_url, sid]);

  const view = useMemo<TableView>(
    () => ({ ...state, joinUrls, listeningPaused, bothConsented }),
    [state, joinUrls, listeningPaused, bothConsented],
  );

  return [view, actions];
}
