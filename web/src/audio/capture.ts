// Table mic capture (PRD 14.1): 24 kHz mono PCM16 frames for /ws/audio + a local RMS VAD at 10 Hz.

const WORKLET_URL = "/pcm-worklet.js";
const SAMPLE_RATE = 24000;

export interface CaptureHandle {
  stop(): void;
  setPaused(paused: boolean): void;
  readonly ctx: AudioContext;
}

export interface CaptureOptions {
  onFrame: (pcm: ArrayBuffer) => void; // 3,840 bytes per 80 ms
  onVad: (active: boolean, rms: number) => void; // 10 Hz
}

type WorkletMessage = { type: "frame"; pcm: ArrayBuffer } | { type: "rms"; rms: number };

/**
 * Local voice activity detector (PRD 14.1). The noise floor is the rolling 10th percentile of RMS over
 * the last 10 s; speech is `rms > max(0.01, 3 * floor)` held for 150 ms, released after 400 ms below.
 */
export class Vad {
  static readonly WINDOW_MS = 10_000;
  static readonly PERCENTILE = 0.1;
  static readonly MIN_THRESHOLD = 0.01;
  static readonly FLOOR_FACTOR = 3;
  static readonly ATTACK_MS = 150;
  static readonly RELEASE_MS = 400;
  // Each RMS sample covers the audio since the previous one (100 ms from the worklet).
  private static readonly DEFAULT_STEP_MS = 100;
  private static readonly MAX_STEP_MS = 250;

  private history: { t: number; rms: number }[] = [];
  private aboveMs = 0;
  private belowMs = 0;
  private lastT: number | null = null;
  private isActive = false;
  private floor = 0;

  update(rms: number, nowMs: number): boolean {
    const step =
      this.lastT === null ? Vad.DEFAULT_STEP_MS : Math.min(Vad.MAX_STEP_MS, Math.max(0, nowMs - this.lastT));
    this.lastT = nowMs;
    this.pushHistory(rms, nowMs);

    if (rms > this.threshold) {
      this.aboveMs += step;
      this.belowMs = 0;
      if (!this.isActive && this.aboveMs >= Vad.ATTACK_MS) this.isActive = true;
    } else {
      this.belowMs += step;
      this.aboveMs = 0;
      if (this.isActive && this.belowMs >= Vad.RELEASE_MS) this.isActive = false;
    }
    return this.isActive;
  }

  get active(): boolean {
    return this.isActive;
  }

  get noiseFloor(): number {
    return this.floor;
  }

  get threshold(): number {
    return Math.max(Vad.MIN_THRESHOLD, Vad.FLOOR_FACTOR * this.floor);
  }

  /** Forget the speech state (keeps the noise floor), e.g. after listening resumes. */
  reset(): void {
    this.aboveMs = 0;
    this.belowMs = 0;
    this.lastT = null;
    this.isActive = false;
  }

  private pushHistory(rms: number, nowMs: number): void {
    this.history.push({ t: nowMs, rms });
    const cutoff = nowMs - Vad.WINDOW_MS;
    let drop = 0;
    while (drop < this.history.length && this.history[drop].t < cutoff) drop++;
    if (drop > 0) this.history.splice(0, drop);
    // About 100 samples at 10 Hz, so a sort per update is cheap.
    const sorted = this.history.map((h) => h.rms).sort((a, b) => a - b);
    this.floor = sorted[Math.floor((sorted.length - 1) * Vad.PERCENTILE)] ?? 0;
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

  node.port.onmessage = (e: MessageEvent<WorkletMessage>) => {
    if (paused || stopped) return;
    const msg = e.data;
    if (msg.type === "frame") {
      opts.onFrame(msg.pcm);
    } else if (msg.type === "rms") {
      opts.onVad(vad.update(msg.rms, performance.now()), msg.rms);
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
      node.port.onmessage = null;
      src.disconnect();
      node.disconnect();
      sink.disconnect();
      for (const track of stream.getTracks()) track.stop();
      void ctx.close().catch(() => undefined);
    },
  };
}
