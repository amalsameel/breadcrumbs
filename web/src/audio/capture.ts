// Table mic capture (PRD 14.1): 24 kHz mono PCM16 frames for /ws/audio + a local speech-aware VAD at 10 Hz.

const WORKLET_URL = "/pcm-worklet.js";
const SAMPLE_RATE = 24000;

export interface CaptureHandle {
  stop(): void;
  setPaused(paused: boolean): void;
  readonly ctx: AudioContext;
}

export interface CaptureOptions {
  onFrame: (pcm: ArrayBuffer) => void; // 3,840 bytes per 80 ms
  /** 10 Hz. `reading` carries the speech-band level, floor and SNR (the Table's echo gate uses them). */
  onVad: (active: boolean, rms: number, reading?: VadReading) => void;
  /** The mic went away on its own (unplugged, permission revoked, the audio context closed). */
  onLost?: () => void;
}

interface TrackLike {
  addEventListener(type: "ended", cb: () => void): void;
  removeEventListener(type: "ended", cb: () => void): void;
}

interface ContextLike {
  readonly state: string;
  addEventListener(type: "statechange", cb: () => void): void;
  removeEventListener(type: "statechange", cb: () => void): void;
}

/**
 * Calls `onLost` once when any mic track ends or the capture context closes (or is interrupted) without
 * us stopping it. Returns the unsubscribe used by a deliberate stop.
 */
export function watchCaptureLoss(tracks: TrackLike[], ctx: ContextLike, onLost: () => void): () => void {
  let done = false;
  const lost = () => {
    if (done) return;
    done = true;
    off();
    onLost();
  };
  const onState = () => {
    if (ctx.state === "closed" || ctx.state === "interrupted") lost();
  };
  const off = () => {
    for (const t of tracks) t.removeEventListener("ended", lost);
    ctx.removeEventListener("statechange", onState);
  };
  for (const t of tracks) t.addEventListener("ended", lost);
  ctx.addEventListener("statechange", onState);
  return () => {
    done = true;
    off();
  };
}

/** Per-100 ms voice features from public/pcm-worklet.js. */
export interface VadFrame {
  rms: number; // full-band RMS, 0..1
  speech: number; // RMS of the 300 to 3,400 Hz speech band
  zcr: number; // zero crossings per sample
  impulse: number; // peak-to-median ratio of the 10 ms sub-block energies, dB
}

/** What the VAD concluded about one frame (the optional third argument of `onVad`). */
export interface VadReading {
  active: boolean;
  rms: number;
  levelDb: number; // speech-band level, dBFS
  floorDb: number; // adaptive noise floor of the speech band, dBFS
  snrDb: number; // levelDb - floorDb
  /** This frame looks like voice: speech-band energy, not hiss, not a click, above the absolute minimum. */
  voiced: boolean;
  calibrating: boolean;
}

type WorkletMessage = { type: "frame"; pcm: ArrayBuffer } | ({ type: "vad" } & VadFrame);

const toDb = (x: number) => 20 * Math.log10(Math.max(x, 1e-9));

function percentile(values: number[], q: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor((sorted.length - 1) * q)] ?? -Infinity;
}

function stdDev(values: number[]): number {
  if (values.length < 2) return 0;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  return Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length);
}

/**
 * Speech-aware voice activity detector (PRD 14.1). Loud is not enough: a frame must look like voice
 * (energy in the 300 to 3,400 Hz band, no hiss, no click) and stand out from an adaptive noise floor by an
 * SNR with hysteresis, and an onset must be modulated the way syllables are (fans, HVAC hum and steady
 * noise are flat). Attack 200 ms, release 500 ms. The first 1.5 s calibrate the floor to the room.
 *
 * Floor: the 20th percentile of the speech-band level, rising toward its 8 s value at most 3 dB/s, falling
 * at once to its 2 s value, and jumping to a new level that has stayed flat (std < 1.5 dB) for 1.5 s (a fan
 * switched on).
 */
export class Vad {
  static readonly CALIBRATION_MS = 1500;
  static readonly WINDOW_MS = 8000;
  static readonly PERCENTILE = 0.2;
  static readonly FLOOR_RISE_DB_PER_S = 3;
  static readonly FALL_WINDOW_MS = 2000;
  static readonly FLOOR_MIN_DB = -80;
  static readonly STATIONARY_MS = 1500;
  static readonly STATIONARY_STD_DB = 1.5;
  static readonly MIN_LEVEL_DB = -52; // absolute minimum speech-band level (about 0.0025 RMS)
  static readonly ON_SNR_DB = 10;
  static readonly OFF_SNR_DB = 5;
  static readonly MIN_BAND_DB = -8; // speech band holds at least 16% of the energy (hum and rumble sit near -30)
  static readonly MAX_ZCR = 0.3; // white noise and hiss cross zero about every other sample
  static readonly MAX_IMPULSE_DB = 12; // a click puts its energy in one or two 10 ms sub-blocks
  static readonly MOD_WINDOW_MS = 1000;
  static readonly ON_MOD_DB = 3; // syllables: the level moves by several dB within a second
  static readonly HOLD_MOD_DB = 1.5;
  static readonly ATTACK_MS = 200;
  static readonly RELEASE_MS = 500;
  // Each frame covers the audio since the previous one (100 ms from the worklet).
  private static readonly DEFAULT_STEP_MS = 100;
  private static readonly MAX_STEP_MS = 250;

  private history: { t: number; db: number }[] = [];
  private startedAt: number | null = null;
  private calibrated = false;
  private floor = Vad.FLOOR_MIN_DB;
  private aboveMs = 0;
  private belowMs = 0;
  private lastT: number | null = null;
  private isActive = false;
  private lastReading: VadReading | null = null;

  update(frame: VadFrame, nowMs: number): VadReading {
    const step =
      this.lastT === null ? Vad.DEFAULT_STEP_MS : Math.min(Vad.MAX_STEP_MS, Math.max(0, nowMs - this.lastT));
    this.lastT = nowMs;
    if (this.startedAt === null) this.startedAt = nowMs;

    const levelDb = Math.max(Vad.FLOOR_MIN_DB, toDb(frame.speech));
    this.pushHistory(levelDb, nowMs);
    const calibrating = !this.calibrated && nowMs - this.startedAt < Vad.CALIBRATION_MS;
    if (calibrating) {
      this.floor = Math.max(Vad.FLOOR_MIN_DB, percentile(this.levels(), Vad.PERCENTILE));
    } else {
      this.calibrated = true;
      this.trackFloor(nowMs, step);
    }

    const snrDb = levelDb - this.floor;
    const voiced =
      levelDb >= Vad.MIN_LEVEL_DB &&
      levelDb - toDb(frame.rms) >= Vad.MIN_BAND_DB &&
      frame.zcr <= Vad.MAX_ZCR &&
      frame.impulse <= Vad.MAX_IMPULSE_DB;
    const modulation = stdDev(this.levels(nowMs - Vad.MOD_WINDOW_MS));

    if (calibrating) {
      this.aboveMs = 0;
    } else if (!this.isActive) {
      const onset = voiced && snrDb >= Vad.ON_SNR_DB && modulation >= Vad.ON_MOD_DB;
      this.aboveMs = onset ? this.aboveMs + step : 0;
      if (this.aboveMs >= Vad.ATTACK_MS) {
        this.isActive = true;
        this.belowMs = 0;
      }
    } else {
      // Plosives may look like clicks; voiced-band energy above the lower threshold keeps the stretch.
      const sustain =
        levelDb >= Vad.MIN_LEVEL_DB &&
        levelDb - toDb(frame.rms) >= Vad.MIN_BAND_DB &&
        snrDb >= Vad.OFF_SNR_DB &&
        modulation >= Vad.HOLD_MOD_DB;
      this.belowMs = sustain ? 0 : this.belowMs + step;
      if (this.belowMs >= Vad.RELEASE_MS) {
        this.isActive = false;
        this.aboveMs = 0;
      }
    }
    this.lastReading = {
      active: this.isActive,
      rms: frame.rms,
      levelDb,
      floorDb: this.floor,
      snrDb,
      voiced,
      calibrating,
    };
    return this.lastReading;
  }

  get active(): boolean {
    return this.isActive;
  }

  get calibrating(): boolean {
    return !this.calibrated;
  }

  /** Speech-band noise floor, dBFS. */
  get noiseFloorDb(): number {
    return this.floor;
  }

  get last(): VadReading | null {
    return this.lastReading;
  }

  /** Forget the speech state (keeps the noise floor), e.g. after listening resumes. */
  reset(): void {
    this.aboveMs = 0;
    this.belowMs = 0;
    this.lastT = null;
    this.isActive = false;
  }

  private levels(since = -Infinity): number[] {
    const out: number[] = [];
    for (const h of this.history) if (h.t > since) out.push(h.db);
    return out;
  }

  private trackFloor(nowMs: number, stepMs: number): void {
    // The room got quieter: follow at once (judged on the last 2 s, so an old quiet spell in the long
    // window does not drag a floor that just absorbed a fan back down).
    const recentFloor = percentile(this.levels(nowMs - Vad.FALL_WINDOW_MS), Vad.PERCENTILE);
    const longFloor = percentile(this.levels(), Vad.PERCENTILE);
    if (recentFloor < this.floor) this.floor = recentFloor;
    else if (longFloor > this.floor) {
      this.floor = Math.min(longFloor, this.floor + (Vad.FLOOR_RISE_DB_PER_S * stepMs) / 1000);
    }
    // A new steady level (fan, HVAC) is absorbed once it has been flat for STATIONARY_MS.
    const steady = this.levels(nowMs - Vad.STATIONARY_MS);
    const span = this.history.length ? nowMs - this.history[0].t : 0;
    if (span >= Vad.STATIONARY_MS && steady.length >= 10 && stdDev(steady) < Vad.STATIONARY_STD_DB) {
      this.floor = Math.max(this.floor, percentile(steady, 0.5));
    }
    this.floor = Math.max(Vad.FLOOR_MIN_DB, this.floor);
  }

  private pushHistory(db: number, nowMs: number): void {
    this.history.push({ t: nowMs, db });
    const cutoff = nowMs - Vad.WINDOW_MS;
    let drop = 0;
    while (drop < this.history.length && this.history[drop].t < cutoff) drop++;
    if (drop > 0) this.history.splice(0, drop);
  }
}

export async function startCapture(opts: CaptureOptions): Promise<CaptureHandle> {
  const ctx = new AudioContext({ sampleRate: SAMPLE_RATE }); // Chrome resamples the mic for us
  let stream: MediaStream;
  try {
    await ctx.audioWorklet.addModule(WORKLET_URL);
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    void ctx.close();
    throw err;
  }
  if (ctx.state === "suspended") await ctx.resume().catch(() => undefined);

  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-worklet", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
  // The worklet must be pulled by the graph to keep processing; a muted sink keeps it inaudible.
  const sink = ctx.createGain();
  sink.gain.value = 0;
  src.connect(node);
  node.connect(sink);
  sink.connect(ctx.destination);

  const vad = new Vad();
  let paused = false;
  let stopped = false;
  const unwatch = watchCaptureLoss(stream.getAudioTracks(), ctx, () => {
    if (!stopped) opts.onLost?.();
  });

  node.port.onmessage = (e: MessageEvent<WorkletMessage>) => {
    if (paused || stopped) return;
    const msg = e.data;
    if (msg.type === "frame") {
      opts.onFrame(msg.pcm);
    } else if (msg.type === "vad") {
      const reading = vad.update(msg, performance.now());
      opts.onVad(reading.active, reading.rms, reading);
    }
  };

  return {
    ctx,
    setPaused(next: boolean) {
      if (stopped || next === paused) return;
      paused = next;
      // Disabled tracks deliver silence, so nothing is captured while paused (Pause listening, PRD 6.6).
      for (const track of stream.getAudioTracks()) track.enabled = !next;
      if (next && vad.active) opts.onVad(false, 0);
      vad.reset();
    },
    stop() {
      if (stopped) return;
      stopped = true;
      unwatch();
      node.port.onmessage = null;
      src.disconnect();
      node.disconnect();
      sink.disconnect();
      for (const track of stream.getTracks()) track.stop();
      void ctx.close().catch(() => undefined);
    },
  };
}
