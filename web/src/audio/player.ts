// Crumb playback on the Table (PRD 6.7, 14.1): chime, WAV via WebAudio with a fade-out for barge-in,
// and a speechSynthesis fallback when the server sent no audio (mock mode or TTS failure).

export interface PlayCallbacks {
  onStart(): void;
  onEnd(interrupted: boolean): void;
}

const DEFAULT_FADE_MS = 250; // PRD 6.7 barge-in budget
const CHIME_NOTES: { freq: number; at: number }[] = [
  { freq: 659.25, at: 0 }, // E5
  { freq: 987.77, at: 0.14 }, // B5
];
const CHIME_PEAK = 0.12;
const CHIME_DECAY_S = 0.55;
const SPEECH_RATE = 1.0;
const FALLBACK_WORDS_PER_S = 2.6; // caption timing when even speechSynthesis is unavailable
const PREFERRED_VOICES = ["Samantha", "Google US English", "Karen", "Serena", "Moira", "Microsoft Aria", "Microsoft Jenny"];

interface Playback {
  id: string;
  cb: PlayCallbacks;
  startedAt: number | null;
  ended: boolean;
  interrupted: boolean;
  source?: AudioBufferSourceNode;
  gain?: GainNode;
  utterance?: SpeechSynthesisUtterance;
  timer?: number;
  resolve: () => void;
}

export class CrumbPlayer {
  private ctxRef: AudioContext | null;
  private current: Playback | null = null;

  constructor(ctx?: AudioContext) {
    this.ctxRef = ctx ?? null;
  }

  get playing(): boolean {
    return this.current !== null && !this.current.ended;
  }

  /** performance.now() when the current crumb started sounding (for the 300 ms VAD grace), else null. */
  get playingSinceMs(): number | null {
    return this.playing ? (this.current?.startedAt ?? null) : null;
  }

  get currentId(): string | null {
    return this.playing ? (this.current?.id ?? null) : null;
  }

  /** Resume a suspended AudioContext (autoplay policy). Call from a user gesture when possible. */
  async resume(): Promise<void> {
    const ctx = this.ctx();
    if (ctx.state === "suspended") await ctx.resume().catch(() => undefined);
  }

  /** A soft two-note chime synthesized with oscillators; resolves when it has faded out. */
  async playChime(): Promise<void> {
    await this.resume();
    const ctx = this.ctx();
    const t0 = ctx.currentTime + 0.01;
    for (const note of CHIME_NOTES) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = note.freq;
      const start = t0 + note.at;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(CHIME_PEAK, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + CHIME_DECAY_S);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + CHIME_DECAY_S + 0.05);
      osc.onended = () => gain.disconnect();
    }
    const totalS = CHIME_NOTES[CHIME_NOTES.length - 1].at + CHIME_DECAY_S + 0.05;
    await new Promise<void>((r) => window.setTimeout(r, totalS * 1000));
  }

  /**
   * Play one crumb. A new play() interrupts the previous one (its onEnd(true) fires first).
   * Resolves when this crumb finishes, naturally or interrupted.
   */
  async play(id: string, audioB64: string, text: string, cb: PlayCallbacks): Promise<void> {
    this.stopCurrent();
    let resolve!: () => void;
    const done = new Promise<void>((r) => (resolve = r));
    const pb: Playback = { id, cb, startedAt: null, ended: false, interrupted: false, resolve };
    this.current = pb;

    let buffer: AudioBuffer | null = null;
    if (audioB64) {
      try {
        await this.resume();
        buffer = await this.ctx().decodeAudioData(base64ToArrayBuffer(audioB64));
      } catch {
        buffer = null; // undecodable audio: fall back to the browser voice below
      }
    }
    if (pb.ended) return done; // interrupted while decoding
    if (buffer) this.startBuffer(pb, buffer);
    else this.startSpeech(pb, text);
    return done;
  }

  /** Barge-in: ramp to silence over `ms`, then stop. onEnd(true) fires once the audio has stopped. */
  fadeOut(ms: number = DEFAULT_FADE_MS): void {
    const pb = this.current;
    if (!pb || pb.ended) return;
    pb.interrupted = true;
    if (pb.source && pb.gain) {
      const ctx = this.ctx();
      const now = ctx.currentTime;
      const end = now + Math.max(0, ms) / 1000;
      pb.gain.gain.cancelScheduledValues(now);
      pb.gain.gain.setValueAtTime(pb.gain.gain.value, now);
      pb.gain.gain.linearRampToValueAtTime(0, end);
      try {
        pb.source.stop(end + 0.01);
      } catch {
        this.finish(pb, true);
      }
      return;
    }
    // speechSynthesis has no volume ramp mid-utterance: cut it immediately (well within 250 ms).
    this.cancelSpeech(pb);
    this.finish(pb, true);
  }

  private ctx(): AudioContext {
    if (!this.ctxRef || this.ctxRef.state === "closed") this.ctxRef = new AudioContext();
    return this.ctxRef;
  }

  private stopCurrent(): void {
    const pb = this.current;
    if (!pb || pb.ended) return;
    pb.interrupted = true;
    if (pb.source) {
      pb.source.onended = null;
      try {
        pb.source.stop();
      } catch {
        // already stopped
      }
    }
    this.cancelSpeech(pb);
    this.finish(pb, true);
  }

  private startBuffer(pb: Playback, buffer: AudioBuffer): void {
    const ctx = this.ctx();
    const source = ctx.createBufferSource();
    const gain = ctx.createGain();
    source.buffer = buffer;
    gain.gain.value = 1;
    source.connect(gain).connect(ctx.destination);
    source.onended = () => this.finish(pb, pb.interrupted);
    pb.source = source;
    pb.gain = gain;
    source.start();
    this.markStarted(pb);
  }

  private startSpeech(pb: Playback, text: string): void {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
    if (!synth || typeof SpeechSynthesisUtterance === "undefined") {
      // No voice at all: still drive the caption and the engine's bot-speaking interval.
      this.markStarted(pb);
      const words = Math.max(1, text.trim().split(/\s+/).length);
      pb.timer = window.setTimeout(() => this.finish(pb, false), (words / FALLBACK_WORDS_PER_S) * 1000);
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    const voice = pickCalmVoice(synth.getVoices());
    if (voice) utterance.voice = voice;
    utterance.lang = voice?.lang ?? "en-US";
    utterance.rate = SPEECH_RATE;
    utterance.pitch = 1;
    utterance.volume = 1;
    utterance.onstart = () => this.markStarted(pb);
    utterance.onend = () => this.finish(pb, pb.interrupted);
    utterance.onerror = () => this.finish(pb, pb.interrupted);
    pb.utterance = utterance;
    synth.cancel(); // clear anything queued by an earlier crumb
    synth.speak(utterance);
  }

  private cancelSpeech(pb: Playback): void {
    if (pb.timer !== undefined) window.clearTimeout(pb.timer);
    if (pb.utterance && typeof window !== "undefined" && window.speechSynthesis) {
      pb.utterance.onend = null;
      pb.utterance.onerror = null;
      window.speechSynthesis.cancel();
    }
  }

  private markStarted(pb: Playback): void {
    if (pb.ended || pb.startedAt !== null) return;
    pb.startedAt = performance.now();
    pb.cb.onStart();
  }

  private finish(pb: Playback, interrupted: boolean): void {
    if (pb.ended) return;
    pb.ended = true;
    pb.gain?.disconnect();
    if (this.current === pb) this.current = null;
    pb.cb.onEnd(interrupted); // fires exactly once; onStart fires at most once, always before it
    pb.resolve();
  }
}

function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function pickCalmVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const english = voices.filter((v) => v.lang.toLowerCase().startsWith("en"));
  for (const name of PREFERRED_VOICES) {
    const match = english.find((v) => v.name.includes(name));
    if (match) return match;
  }
  return english.find((v) => v.localService) ?? english[0] ?? null;
}
