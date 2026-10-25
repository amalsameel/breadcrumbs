// Crumb playback sequencing on the Table (PRD 6.7, 8.11, 14.1), kept free of React and WebAudio so it
// can be unit tested (see __tests__/playback.test.mjs).
//
// A crumb's playback starts with its chime. The engine's bot-speaking interval (tts_state playing) opens
// there, so the chime is covered as bot sound. Barge-in stays off through the chime and the first 300 ms
// of the voice (PRD 8.11.3), and speech that *starts* inside that grace is held back from the server.
//
// Echo-aware barge-in (round 2): the laptop mic hears the laptop speaker, and browser speechSynthesis is
// not removed by echo cancellation, so "any VAD during playback" used to make the Table stop itself after
// one word. While a line sounds the gate learns how loud its own echo is (a robust high percentile of the
// speech-band level over recent bot-sound frames) and only treats activity as a person when it is voiced
// and clearly louder than that echo (ECHO_MARGIN_DB), sustained for PERSON_SUSTAIN_MS. Echo-level activity
// is held back from the server, also for a short tail after the line ends.
//
// The local Vad needs a 200 ms attack before it reports a stretch, so the gate also watches loud voiced
// frames while the VAD is still inactive (the attack run). They are not learned as echo, and when the
// stretch opens they count towards PERSON_SUSTAIN_MS: a person over the echo fades the line about one
// frame after the VAD turns on, not PERSON_SUSTAIN_MS later (PRD F12: stop within 250 ms).
import type { ClientEvent } from "../../lib/types";

export const BARGE_IN_GRACE_MS = 300; // PRD 8.11 / 14.1: ignore VAD for the first 300 ms of playback
export const BARGE_IN_FADE_MS = 250;
export const ECHO_MARGIN_DB = 6; // a person must be this much louder than the learned echo
export const ECHO_MARGIN_COLD_DB = 12; // until enough echo has been heard to trust the estimate
export const ECHO_MIN_FRAMES = 20; // 2 s of bot sound (chime included), kept across lines
export const ECHO_PERCENTILE = 0.9;
export const ECHO_KEEP_FRAMES = 80; // about 8 s of bot sound, across lines (the speaker volume can change)
export const PERSON_SUSTAIN_MS = 200;
export const LINE_WARM_FRAMES = 10; // first second of each line's voice after the grace
export const ECHO_TAIL_MS = 400; // reverb and decoder tail after the line: an onset here is still echo
export const ECHO_HOLD_AFTER_MS = 800; // an echo-level stretch still going this long after the line is a person
export const ATTACK_RUN_MS = PERSON_SUSTAIN_MS; // loud frames before the VAD turns on that count for a person
const SUPERSEDE_FADE_MS = 120;
const DEFAULT_STEP_MS = 100;
const MAX_STEP_MS = 250;

export interface LinePlayer {
  readonly playing: boolean;
  playChime(): Promise<void>;
  play(id: string, audioB64: string, text: string, cb: { onStart(): void; onEnd(interrupted: boolean): void }): Promise<void>;
  fadeOut(ms?: number): void;
}

/** One local VAD reading (10 Hz) as the gate needs it. */
export interface VadSample {
  active: boolean;
  /** Speech-band level, dBFS. */
  levelDb: number;
  /** The frame looks like voice (see Vad in audio/capture.ts). Defaults to `active`. */
  voiced?: boolean;
}

export interface GateVerdict {
  /** Send this reading to the server (a held-back echo onset is not sent). */
  send: boolean;
  /** Fade the line out now: a person is talking over it. */
  fade: boolean;
}

/** Recent speech-band levels heard while the bot was sounding. */
export class EchoModel {
  private levels: number[] = [];

  add(levelDb: number): void {
    if (!Number.isFinite(levelDb)) return;
    this.levels.push(levelDb);
    if (this.levels.length > ECHO_KEEP_FRAMES) this.levels.splice(0, this.levels.length - ECHO_KEEP_FRAMES);
  }

  get frames(): number {
    return this.levels.length;
  }

  /** Robust echo level (dBFS), or null before any bot sound was heard. */
  get level(): number | null {
    if (this.levels.length === 0) return null;
    const sorted = [...this.levels].sort((a, b) => a - b);
    return sorted[Math.floor((sorted.length - 1) * ECHO_PERCENTILE)];
  }

  /** Level a person must exceed to count as a voice over the echo. */
  get personThreshold(): number {
    const level = this.level;
    if (level === null) return Infinity;
    return level + (this.levels.length >= ECHO_MIN_FRAMES ? ECHO_MARGIN_DB : ECHO_MARGIN_COLD_DB);
  }
}

interface Stretch {
  /** Began while the bot was sounding (or in its tail): echo until shown otherwise. */
  held: boolean;
  personMs: number;
  person: boolean;
  /** Levels of the current loud run, learned as echo only if the run turns out not to be a person. */
  pending: number[];
}

interface Frame {
  levelDb: number;
  ms: number;
}

interface Line {
  id: string;
  voiceAt: number | null;
  voiceFrames: number;
  maxDb: number; // loudest frame of this line learned as echo
}

/** The line the Table is sounding right now (chime included), its VAD grace, and the echo gate. */
export class PlaybackGate {
  readonly echo = new EchoModel();
  private line: Line | null = null;
  private latest: string | null = null;
  private endedAt: number | null = null;
  private sentActive = false;
  private stretch: Stretch | null = null;
  private lastT: number | null = null;
  /** Loud voiced frames while a line sounds and the VAD is still in its attack (newest last). */
  private attackRun: Frame[] = [];

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
    this.line = { id, voiceAt: null, voiceFrames: 0, maxDb: -Infinity };
  }

  voiceStarted(id: string, at: number): void {
    if (this.line?.id === id) this.line.voiceAt = at;
  }

  end(id: string, at?: number): void {
    if (this.line?.id !== id) return;
    this.line = null;
    this.endedAt = at ?? this.lastT;
    if (this.stretch) this.flushPending(this.stretch);
  }

  /** True during the chime and the first 300 ms of the voice: VAD there is most likely our own sound. */
  inGrace(now: number): boolean {
    const line = this.line;
    if (!line) return false;
    return line.voiceAt === null || now - line.voiceAt < BARGE_IN_GRACE_MS;
  }

  /** Level a person must exceed. For the first second of each line's voice it is also kept above that
   * line's own loudest echo so far by the cold margin: a line louder than the last (volume turned up, a
   * different voice path) must not cut itself off before the model has caught up. */
  personThreshold(): number {
    let threshold = this.echo.personThreshold;
    const line = this.line;
    if (line && line.voiceFrames < LINE_WARM_FRAMES && Number.isFinite(line.maxDb)) {
      threshold = Math.max(threshold, line.maxDb + ECHO_MARGIN_COLD_DB);
    }
    return threshold;
  }

  /**
   * One VAD reading. Decides whether the server hears about it and whether the line fades.
   *  - Releases always pass. Speech already under way when a line starts is passed on unchanged.
   *  - A new onset while the bot sounds (or in its tail) is held back as echo, unless after the grace it
   *    is voiced and louder than the learned echo by the margin for PERSON_SUSTAIN_MS: then it is a
   *    person, it reaches the server, and the line fades.
   *  - A held stretch still going ECHO_HOLD_AFTER_MS after the line ended is a person speaking at echo
   *    level: it is passed on (no fade, nothing is playing).
   */
  observe(sample: VadSample, now: number): GateVerdict {
    const step = this.lastT === null ? DEFAULT_STEP_MS : Math.min(MAX_STEP_MS, Math.max(0, now - this.lastT));
    this.lastT = now;
    const line = this.line;
    const grace = this.inGrace(now);
    if (line && line.voiceAt !== null && !grace) line.voiceFrames++;
    const voiced = sample.voiced ?? sample.active;
    const loud = !grace && voiced && sample.levelDb > this.personThreshold();

    if (!sample.active) {
      if (this.stretch) this.flushPending(this.stretch);
      this.stretch = null;
      this.sentActive = false;
      if (line && loud) {
        this.pushAttack(sample.levelDb, step); // maybe a person's onset: decided when the VAD turns on
      } else {
        this.flushAttack();
        if (line) this.learn(sample.levelDb);
      }
      return { send: true, fade: false };
    }

    if (this.stretch === null) {
      const held = !this.sentActive && (line !== null || this.inTail(now, ECHO_TAIL_MS));
      // The VAD's attack already found these frames voiced; with this loud one they are the person's start.
      const run = loud ? this.attackRun : [];
      this.stretch = {
        held,
        personMs: run.reduce((ms, f) => ms + f.ms, 0),
        person: false,
        pending: line ? run.map((f) => f.levelDb) : [],
      };
      if (!loud) this.flushAttack();
      this.attackRun = [];
    }
    const stretch = this.stretch;
    if (!stretch.person) {
      if (loud) {
        stretch.personMs += step;
        if (line) stretch.pending.push(sample.levelDb);
        if (stretch.personMs >= PERSON_SUSTAIN_MS) {
          stretch.person = true;
          stretch.pending = []; // a person, not our echo
        }
      } else {
        stretch.personMs = 0;
        this.flushPending(stretch);
        if (line) this.learn(sample.levelDb);
      }
    }

    if (stretch.held && !stretch.person) {
      const lingering = line === null && !this.inTail(now, ECHO_HOLD_AFTER_MS);
      if (!lingering) return { send: false, fade: false };
      stretch.held = false;
    }
    this.sentActive = true;
    return { send: true, fade: line !== null && stretch.person };
  }

  /** Within `ms` after the last line ended. */
  private inTail(now: number, ms: number): boolean {
    return this.endedAt !== null && now - this.endedAt < ms;
  }

  private learn(levelDb: number): void {
    this.echo.add(levelDb);
    const line = this.line;
    if (line && line.voiceAt !== null && Number.isFinite(levelDb)) line.maxDb = Math.max(line.maxDb, levelDb);
  }

  /** Keeps the newest ATTACK_RUN_MS of the run; older loud frames the VAD never took up are echo. */
  private pushAttack(levelDb: number, ms: number): void {
    this.attackRun.push({ levelDb, ms });
    let total = this.attackRun.reduce((sum, f) => sum + f.ms, 0);
    while (this.attackRun.length > 1 && total > ATTACK_RUN_MS) {
      const old = this.attackRun.shift();
      if (!old) break;
      total -= old.ms;
      this.echo.add(old.levelDb);
    }
  }

  private flushAttack(): void {
    for (const f of this.attackRun) this.echo.add(f.levelDb);
    this.attackRun = [];
  }

  private flushPending(stretch: Stretch): void {
    for (const db of stretch.pending) this.echo.add(db);
    stretch.pending = [];
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
    gate.end(id, deps.now());
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
