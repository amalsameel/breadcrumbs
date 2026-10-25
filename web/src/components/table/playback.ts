// Crumb playback sequencing on the Table (PRD 6.7, 8.11, 14.1), kept free of React and WebAudio so it
// can be unit tested (see __tests__/playback.test.mjs).
//
// A crumb's playback starts with its chime. The engine's bot-speaking interval (tts_state playing) opens
// there, so the chime is covered as bot sound: its echo never counts as a person talking. Barge-in stays
// off through the chime and the first 300 ms of the voice (PRD 8.11.3: the grace exists so the chime
// does not trigger it). The engine applies its own 300 ms grace from tts_state, which is shorter than the
// chime, so speech that *starts* inside the Table's grace is held back from the server until it ends.
import type { ClientEvent } from "../../lib/types";

export const BARGE_IN_GRACE_MS = 300; // PRD 8.11 / 14.1: ignore VAD for the first 300 ms of playback
export const BARGE_IN_FADE_MS = 250;
const SUPERSEDE_FADE_MS = 120;

export interface LinePlayer {
  readonly playing: boolean;
  playChime(): Promise<void>;
  play(id: string, audioB64: string, text: string, cb: { onStart(): void; onEnd(interrupted: boolean): void }): Promise<void>;
  fadeOut(ms?: number): void;
}

/** The line the Table is sounding right now (chime included) and the VAD grace around it. */
export class PlaybackGate {
  private line: { id: string; voiceAt: number | null } | null = null;
  private latest: string | null = null;
  private sentActive = false;

  /** The line whose bot interval is open (from its chime on), if any. */
  get currentId(): string | null {
    return this.line?.id ?? null;
  }

  /** A newer line arrived: an older one still in its chime must not start afterwards. */
  claim(id: string): void {
    this.latest = id;
  }

  superseded(id: string): boolean {
    return this.latest !== null && this.latest !== id;
  }

  begin(id: string): void {
    this.line = { id, voiceAt: null };
  }

  voiceStarted(id: string, at: number): void {
    if (this.line?.id === id) this.line.voiceAt = at;
  }

  end(id: string): void {
    if (this.line?.id === id) this.line = null;
  }

  /** True during the chime and the first 300 ms of the voice: VAD there is most likely our own sound. */
  inGrace(now: number): boolean {
    const line = this.line;
    if (!line) return false;
    return line.voiceAt === null || now - line.voiceAt < BARGE_IN_GRACE_MS;
  }

  /** Local barge-in: VAD after the grace fades the line out. */
  shouldFade(active: boolean, now: number): boolean {
    return active && this.line !== null && !this.inGrace(now);
  }

  /**
   * Whether to send this VAD reading to the server. Activity that starts inside the grace is held back
   * until the grace ends (so the chime is never taken for a person); speech already under way when the
   * line started is passed on unchanged, and so is every release.
   */
  forward(active: boolean, now: number): boolean {
    if (active && !this.sentActive && this.inGrace(now)) return false;
    this.sentActive = active;
    return true;
  }
}

export interface SpeakDeps {
  player: LinePlayer;
  gate: PlaybackGate;
  send(event: ClientEvent): void;
  started(id: string, at: number): void;
  ended(id: string, at: number, interrupted: boolean): void;
  /** Interrupted by the server, skipped, or the speaker stopped (End, wipe, unmount). */
  cancelled(id: string): boolean;
  now(): number;
}

/** Speaks one line: chime (crumbs only), then the voice, with tts_state covering both. */
export async function speakLine(deps: SpeakDeps, id: string, audioB64: string, text: string, chime: boolean): Promise<void> {
  const { player, gate, send } = deps;
  gate.claim(id);
  if (player.playing) player.fadeOut(SUPERSEDE_FADE_MS);

  let open = false;
  let voiced = false;
  let finished = false;
  const openInterval = () => {
    if (open) return;
    open = true;
    gate.begin(id);
    send({ type: "tts_state", crumb_id: id, playing: true });
  };
  const finish = (interrupted: boolean) => {
    if (finished) return;
    finished = true;
    gate.end(id);
    if (open) send({ type: "tts_state", crumb_id: id, playing: false });
    deps.ended(id, deps.now(), interrupted);
  };
  const stop = () => deps.cancelled(id) || gate.superseded(id);

  if (chime) {
    openInterval();
    try {
      await player.playChime();
    } catch {
      /* a missing chime must never block the crumb */
    }
  }
  if (stop()) {
    finish(true);
    return;
  }
  try {
    await player.play(id, audioB64, text, {
      onStart: () => {
        openInterval();
        voiced = true;
        const at = deps.now();
        gate.voiceStarted(id, at);
        deps.started(id, at);
        // Interrupted (or the session ended) while the audio was still decoding.
        if (stop()) player.fadeOut(BARGE_IN_FADE_MS);
      },
      onEnd: (interrupted) => finish(interrupted),
    });
  } catch {
    if (!voiced) deps.started(id, deps.now());
    finish(false);
  }
}
