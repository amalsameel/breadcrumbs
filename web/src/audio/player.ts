// Crumb playback on the Table (PRD 6.7, 14.1): chime, WAV via WebAudio with a fade-out for barge-in,
// and a speechSynthesis fallback when the server sent no audio (no voice provider or TTS failure).
//
// The player never truncates a line on its own: a line ends when its audio ends, when fadeOut() is
// called (barge-in), or when the next play() supersedes it. Every node and utterance in flight is
// referenced until it ends, and watchdogs guarantee onEnd fires even when the browser drops an event.

export interface PlayCallbacks {
  onStart(): void;
  onEnd(interrupted: boolean): void;
}

/** What is sounding: decoded audio through WebAudio, the browser voice, or nothing (caption timer only). */
export type OutputKind = "audio" | "speech" | "silent";

const DEFAULT_FADE_MS = 250; // PRD 6.7 barge-in budget
const CHIME_NOTES: { freq: number; at: number }[] = [
  { freq: 659.25, at: 0 }, // E5
  { freq: 987.77, at: 0.14 }, // B5
];
const CHIME_PEAK = 0.12;
const CHIME_DECAY_S = 0.55;
const FADE_IN_S = 0.006; // removes the click of a hard start without softening the first word
const RESUME_TIMEOUT_MS = 800; // AudioContext.resume() can hang without a user gesture
const AUDIO_WATCHDOG_SLACK_MS = 1500; // past duration + this, a missing `ended` event is treated as the end

const SPEECH_RATE = 1.0;
const FALLBACK_WORDS_PER_S = 2.6; // caption timing when even speechSynthesis is unavailable
const SPEECH_WORDS_PER_S = 2.2; // conservative estimate for the speech watchdog
const VOICES_TIMEOUT_MS = 1500; // Chrome fills getVoices() asynchronously (voiceschanged)
const CANCEL_SETTLE_MS = 60; // speak() right after cancel() is silently dropped by Chrome
const START_TIMEOUT_MS = 2500; // no onstart by then: the engine is wedged, fall back to the caption timer
const SPEECH_WATCHDOG_FACTOR = 2.5;
const SPEECH_WATCHDOG_SLACK_MS = 4000;
const CHUNK_CHARS = 180; // Chrome's network voices stop after ~15 s; long lines are spoken sentence by sentence
const KEEPALIVE_MS = 9000; // and a non-local voice gets a pause/resume nudge so it is never cut mid-sentence

// Higher scores win. Names cover macOS/iOS (Premium/Enhanced, Ava, Samantha), Edge (… Online (Natural)),
// Chrome (Google US English) and Windows (Aria, Jenny).
const VOICE_NAME_SCORES: [RegExp, number][] = [
  [/\(Natural\)|\bNatural\b|\bNeural\b/i, 60],
  [/\bPremium\b/i, 55],
  [/\bEnhanced\b/i, 50],
  [/Google US English/i, 40],
  [/Google UK English Female/i, 34],
  [/\bAva\b/i, 30],
  [/\bSamantha\b/i, 28],
  [/\bZoe\b/i, 26],
  [/\bAllison\b/i, 24],
  [/\bJenny\b|\bAria\b/i, 24],
  [/\bSerena\b|\bKaren\b|\bMoira\b|\bTessa\b/i, 18],
];
// macOS novelty and legacy voices that sound robotic or comic.
const NOVELTY_VOICES =
  /\b(Albert|Bad News|Bahh|Bells|Boing|Bubbles|Cellos|Good News|Jester|Organ|Superstar|Trinoids|Whisper|Wobble|Zarvox|Fred|Junior|Ralph|Kathy|Princess|Deranged|Hysterical|Grandma|Grandpa|Eddy|Flo|Reed|Rocko|Sandy|Shelley)\b/i;

interface Playback {
  id: string;
  cb: PlayCallbacks;
  startedAt: number | null;
  ended: boolean;
  interrupted: boolean;
  kind: OutputKind | null;
  ctx?: AudioContext;
  source?: AudioBufferSourceNode;
  gain?: GainNode;
  analyser?: AnalyserNode;
  onCtxState?: () => void;
  utterances: SpeechSynthesisUtterance[];
  timers: number[];
  intervals: number[];
  resolve: () => void;
}

// Strong references to everything in flight. Chrome can garbage-collect an utterance (or a source
// whose only reference is a local) mid-line, and its `end` event then never fires.
const inFlight = new Set<unknown>();

// When the player last cancelled the shared speech engine (performance.now()). Every cancel() here goes
// through cancelEngine() so the next speak() waits out CANCEL_SETTLE_MS whichever path cancelled.
// cancel() clears `speaking` at once, so a superseded line (fadeOut() then play()) would otherwise call
// speak() in the same task, and Chrome drops it without an error event.
let lastCancelAt = Number.NEGATIVE_INFINITY;

function cancelEngine(synth: SpeechSynthesis | null | undefined): void {
  if (!synth) return;
  synth.cancel();
  lastCancelAt = performance.now();
}

export class CrumbPlayer {
  private ctxRef: AudioContext | null;
  private current: Playback | null = null;
  private levelBuf: Float32Array<ArrayBuffer> | null = null;

  constructor(ctx?: AudioContext) {
    this.ctxRef = ctx ?? null;
    const synth = speechSynth();
    if (synth) void loadVoices(synth); // warm the voice list so the first fallback line does not wait
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

  /**
   * How the current line is sounding, or null when nothing plays. Barge-in logic can use this: WebAudio
   * output is partly removed by the mic's echo cancellation, browser speech output usually is not.
   */
  get outputKind(): OutputKind | null {
    return this.playing ? (this.current?.kind ?? null) : null;
  }

  /**
   * RMS (0 to 1) of what the speaker is playing right now, for "audio" lines; null otherwise. Lets
   * barge-in compare the mic level with the level the Table itself is putting out.
   */
  outputLevel(): number | null {
    const analyser = this.playing ? this.current?.analyser : undefined;
    if (!analyser) return null;
    if (!this.levelBuf || this.levelBuf.length !== analyser.fftSize) this.levelBuf = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(this.levelBuf);
    let sum = 0;
    for (let i = 0; i < this.levelBuf.length; i++) sum += this.levelBuf[i] * this.levelBuf[i];
    return Math.sqrt(sum / this.levelBuf.length);
  }

  /** Use a fresh (gesture-unlocked) context, e.g. after the mic restarts; keeps a live one in place. */
  attach(ctx: AudioContext): void {
    if (!this.ctxRef || this.ctxRef.state === "closed") this.ctxRef = ctx;
  }

  /** Resume a suspended AudioContext (autoplay policy). Call from a user gesture when possible. */
  async resume(): Promise<void> {
    const ctx = this.ctx();
    if (ctx.state !== "suspended") return;
    await Promise.race([ctx.resume().catch(() => undefined), delay(RESUME_TIMEOUT_MS)]);
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
      inFlight.add(osc);
      osc.start(start);
      osc.stop(start + CHIME_DECAY_S + 0.05);
      osc.onended = () => {
        gain.disconnect();
        inFlight.delete(osc);
      };
    }
    const totalS = CHIME_NOTES[CHIME_NOTES.length - 1].at + CHIME_DECAY_S + 0.05;
    await delay(totalS * 1000);
  }

  /**
   * Play one crumb. A new play() interrupts the previous one (its onEnd(true) fires first).
   * Resolves when this crumb finishes, naturally or interrupted.
   */
  async play(id: string, audioB64: string, text: string, cb: PlayCallbacks): Promise<void> {
    this.stopCurrent();
    let resolve!: () => void;
    const done = new Promise<void>((r) => (resolve = r));
    const pb: Playback = {
      id,
      cb,
      startedAt: null,
      ended: false,
      interrupted: false,
      kind: null,
      utterances: [],
      timers: [],
      intervals: [],
      resolve,
    };
    this.current = pb;

    let buffer: AudioBuffer | null = null;
    if (audioB64) {
      try {
        await this.resume();
        buffer = await toAudioBuffer(this.ctx(), base64ToArrayBuffer(audioB64));
      } catch {
        buffer = null; // undecodable audio: fall back to the browser voice below
      }
    }
    if (pb.ended) return done; // interrupted while decoding
    if (buffer && this.ctx().state === "running") this.startBuffer(pb, buffer);
    else
      this.startSpeech(pb, text).catch(() => {
        this.cancelSpeech(pb);
        this.startSilent(pb, text); // a throwing speech engine must not leave the line hanging
      });
    return done;
  }

  /** Barge-in: ramp to silence over `ms`, then stop. onEnd(true) fires once the audio has stopped. */
  fadeOut(ms: number = DEFAULT_FADE_MS): void {
    const pb = this.current;
    if (!pb || pb.ended) return;
    pb.interrupted = true;
    if (pb.source && pb.gain && pb.ctx) {
      const ctx = pb.ctx;
      const now = ctx.currentTime;
      const fadeS = Math.max(0, ms) / 1000;
      pb.gain.gain.cancelScheduledValues(now);
      pb.gain.gain.setValueAtTime(pb.gain.gain.value, now);
      pb.gain.gain.linearRampToValueAtTime(0, now + fadeS);
      try {
        pb.source.stop(now + fadeS + 0.01);
      } catch {
        this.finish(pb, true);
        return;
      }
      // A closed or stalled context never fires `ended`: end the line on the wall clock as well.
      pb.timers.push(window.setTimeout(() => this.finish(pb, true), Math.max(0, ms) + 120));
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
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    source.buffer = buffer;
    source.connect(gain);
    gain.connect(ctx.destination);
    gain.connect(analyser); // a side tap for outputLevel(); not connected to the speakers twice
    const t0 = ctx.currentTime;
    gain.gain.setValueAtTime(0, t0);
    gain.gain.linearRampToValueAtTime(1, t0 + FADE_IN_S);
    source.onended = () => this.finish(pb, pb.interrupted);
    const onCtxState = () => {
      if (ctx.state === "closed") this.finish(pb, true); // the mic restarted and took this context with it
    };
    ctx.addEventListener("statechange", onCtxState);
    Object.assign(pb, { kind: "audio", ctx, source, gain, analyser, onCtxState });
    inFlight.add(source);
    source.start(t0);
    this.markStarted(pb);
    const watchdogMs = buffer.duration * 1000 + AUDIO_WATCHDOG_SLACK_MS;
    pb.timers.push(window.setTimeout(() => this.finish(pb, pb.interrupted), watchdogMs));
  }

  private async startSpeech(pb: Playback, text: string): Promise<void> {
    const synth = speechSynth();
    const spoken = text.replace(/\s+/g, " ").trim();
    if (!synth || typeof SpeechSynthesisUtterance === "undefined" || !spoken) {
      this.startSilent(pb, spoken);
      return;
    }
    const voices = await loadVoices(synth);
    if (pb.ended) return;
    const voice = pickNaturalVoice(voices);
    // Clear anything an earlier line left queued, then let the engine settle: speak() issued within
    // CANCEL_SETTLE_MS of cancel() is dropped (or ends at once) in Chrome. The wait counts from the
    // player's latest cancel, including the fadeOut() or stopCurrent() that superseded the previous line.
    if (synth.speaking || synth.pending) cancelEngine(synth);
    const settleMs = CANCEL_SETTLE_MS - (performance.now() - lastCancelAt);
    if (settleMs > 0) {
      await delay(Math.ceil(settleMs) + 1); // timers can fire up to 1 ms early
      if (pb.ended) return;
    }
    synth.resume(); // Chrome can be left paused by an earlier cancel; a no-op otherwise
    pb.kind = "speech";
    const chunks = splitForSpeech(spoken);
    let index = 0;
    let retried = false;

    const speakNext = (): void => {
      if (pb.ended) return;
      if (index >= chunks.length) {
        this.finish(pb, pb.interrupted);
        return;
      }
      const utterance = new SpeechSynthesisUtterance(chunks[index]);
      if (voice) utterance.voice = voice;
      utterance.lang = voice?.lang ?? "en-US";
      utterance.rate = SPEECH_RATE;
      utterance.pitch = 1;
      utterance.volume = 1;
      utterance.onstart = () => this.markStarted(pb);
      utterance.onend = () => {
        inFlight.delete(utterance);
        index += 1;
        retried = false;
        speakNext();
      };
      utterance.onerror = (event: SpeechSynthesisErrorEvent) => {
        inFlight.delete(utterance);
        if (pb.ended) return;
        // Someone else cancelled the shared engine (another tab, a stray cancel()): say this part again
        // once rather than dropping the rest of the line.
        if ((event.error === "interrupted" || event.error === "canceled") && !retried) {
          retried = true;
          pb.timers.push(window.setTimeout(speakNext, CANCEL_SETTLE_MS));
          return;
        }
        if (event.error === "not-allowed" || event.error === "audio-busy" || event.error === "audio-hardware") {
          this.cancelSpeech(pb);
          this.startSilent(pb, chunks.slice(index).join(" "));
          return;
        }
        index += 1;
        retried = false;
        speakNext();
      };
      pb.utterances.push(utterance);
      inFlight.add(utterance);
      synth.speak(utterance);
    };

    speakNext();

    // The engine never started: hand the line to the caption timer instead of hanging.
    pb.timers.push(
      window.setTimeout(() => {
        if (pb.ended || pb.startedAt !== null) return;
        if (synth.speaking) {
          this.markStarted(pb); // some engines skip `start`; it is sounding, so count it from now
          return;
        }
        this.cancelSpeech(pb);
        this.startSilent(pb, spoken);
      }, START_TIMEOUT_MS),
    );
    // A lost `end` event must not leave the Table "speaking" forever.
    const watchdogMs = (wordCount(spoken) / SPEECH_WORDS_PER_S) * 1000 * SPEECH_WATCHDOG_FACTOR + SPEECH_WATCHDOG_SLACK_MS;
    pb.timers.push(
      window.setTimeout(() => {
        if (pb.ended) return;
        this.cancelSpeech(pb);
        this.finish(pb, pb.interrupted);
      }, watchdogMs),
    );
    if (voice && !voice.localService) {
      pb.intervals.push(
        window.setInterval(() => {
          if (pb.ended || !synth.speaking) return;
          synth.pause();
          synth.resume();
        }, KEEPALIVE_MS),
      );
    }
  }

  /** No voice at all: still drive the caption and the engine's bot-speaking interval. */
  private startSilent(pb: Playback, text: string): void {
    if (pb.ended) return;
    pb.kind = "silent";
    this.markStarted(pb);
    const ms = (Math.max(1, wordCount(text)) / FALLBACK_WORDS_PER_S) * 1000;
    pb.timers.push(window.setTimeout(() => this.finish(pb, pb.interrupted), ms));
  }

  private cancelSpeech(pb: Playback): void {
    if (!pb.utterances.length) return;
    for (const u of pb.utterances) {
      u.onstart = null;
      u.onend = null;
      u.onerror = null;
      inFlight.delete(u);
    }
    pb.utterances = [];
    cancelEngine(speechSynth());
  }

  private markStarted(pb: Playback): void {
    if (pb.ended || pb.startedAt !== null) return;
    pb.startedAt = performance.now();
    pb.cb.onStart();
  }

  private finish(pb: Playback, interrupted: boolean): void {
    if (pb.ended) return;
    pb.ended = true;
    for (const t of pb.timers) window.clearTimeout(t);
    for (const t of pb.intervals) window.clearInterval(t);
    pb.timers = [];
    pb.intervals = [];
    if (pb.source) {
      pb.source.onended = null;
      inFlight.delete(pb.source);
    }
    if (pb.ctx && pb.onCtxState) pb.ctx.removeEventListener("statechange", pb.onCtxState);
    pb.gain?.disconnect();
    for (const u of pb.utterances) inFlight.delete(u);
    if (this.current === pb) this.current = null;
    pb.cb.onEnd(interrupted); // fires exactly once; onStart fires at most once, always before it
    pb.resolve();
  }
}

// --- helpers ------------------------------------------------------------------------------------

function delay(ms: number): Promise<void> {
  return new Promise((r) => window.setTimeout(r, ms));
}

function speechSynth(): SpeechSynthesis | undefined {
  return typeof window !== "undefined" && "speechSynthesis" in window ? window.speechSynthesis : undefined;
}

function wordCount(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/**
 * An AudioBuffer for the payload. The server sends PCM16 WAV, which is copied straight into a buffer
 * (synchronous, identical in every browser); anything else goes through decodeAudioData.
 */
async function toAudioBuffer(ctx: AudioContext, data: ArrayBuffer): Promise<AudioBuffer> {
  const pcm = parsePcm16Wav(data);
  if (pcm) {
    const frames = Math.floor(pcm.samples.length / pcm.channels);
    if (frames > 0) {
      const buffer = ctx.createBuffer(pcm.channels, frames, pcm.sampleRate);
      for (let c = 0; c < pcm.channels; c++) {
        const out = buffer.getChannelData(c);
        for (let i = 0; i < frames; i++) out[i] = pcm.samples[i * pcm.channels + c] / 32768;
      }
      return buffer;
    }
  }
  return new Promise<AudioBuffer>((resolve, reject) => {
    // Older Safari only has the callback form; newer browsers return a promise (and may call both).
    const maybe = ctx.decodeAudioData(data, resolve, reject) as Promise<AudioBuffer> | undefined;
    if (maybe && typeof maybe.then === "function") maybe.then(resolve, reject);
  });
}

interface Pcm16 {
  sampleRate: number;
  channels: number;
  samples: Int16Array;
}

function parsePcm16Wav(data: ArrayBuffer): Pcm16 | null {
  if (data.byteLength < 44) return null;
  const view = new DataView(data);
  const tag = (at: number) => String.fromCharCode(view.getUint8(at), view.getUint8(at + 1), view.getUint8(at + 2), view.getUint8(at + 3));
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE") return null;
  let offset = 12;
  let fmt: { channels: number; sampleRate: number; bits: number; format: number } | null = null;
  while (offset + 8 <= data.byteLength) {
    const id = tag(offset);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === "fmt " && size >= 16) {
      fmt = {
        format: view.getUint16(body, true),
        channels: view.getUint16(body + 2, true),
        sampleRate: view.getUint32(body + 4, true),
        bits: view.getUint16(body + 14, true),
      };
    } else if (id === "data") {
      if (!fmt || fmt.format !== 1 || fmt.bits !== 16 || fmt.channels < 1 || fmt.sampleRate < 3000) return null;
      const bytes = Math.min(size, data.byteLength - body);
      const count = Math.floor(bytes / 2);
      const samples = new Int16Array(count);
      for (let i = 0; i < count; i++) samples[i] = view.getInt16(body + i * 2, true);
      return { sampleRate: fmt.sampleRate, channels: fmt.channels, samples };
    }
    offset = body + size + (size % 2);
  }
  return null;
}

let voicesPending: { synth: SpeechSynthesis; list: Promise<SpeechSynthesisVoice[]> } | null = null;

/** The voice list, waiting (bounded) for Chrome's asynchronous `voiceschanged`. */
function loadVoices(synth: SpeechSynthesis): Promise<SpeechSynthesisVoice[]> {
  const ready = synth.getVoices();
  if (ready.length) return Promise.resolve(ready);
  if (voicesPending?.synth === synth) return voicesPending.list;
  const list = new Promise<SpeechSynthesisVoice[]>((resolve) => {
    let timer = 0;
    const done = () => {
      synth.removeEventListener?.("voiceschanged", onChange);
      window.clearTimeout(timer);
      if (voicesPending?.synth === synth) voicesPending = null;
      resolve(synth.getVoices());
    };
    const onChange = () => {
      if (synth.getVoices().length) done();
    };
    synth.addEventListener?.("voiceschanged", onChange);
    timer = window.setTimeout(done, VOICES_TIMEOUT_MS);
  });
  voicesPending = { synth, list };
  return list;
}

/** Score a voice for a warm, natural English read; negative means "never". Exported for tests. */
export function scoreVoice(v: Pick<SpeechSynthesisVoice, "name" | "lang" | "localService" | "default">): number {
  const lang = v.lang.toLowerCase().replace("_", "-");
  if (!lang.startsWith("en")) return -1;
  if (NOVELTY_VOICES.test(v.name)) return -1;
  let score = lang === "en-us" ? 30 : lang === "en-gb" ? 24 : 12;
  for (const [pattern, bonus] of VOICE_NAME_SCORES) {
    if (pattern.test(v.name)) {
      score += bonus;
      break;
    }
  }
  if (v.default) score += 2;
  return score;
}

export function pickNaturalVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  let best: SpeechSynthesisVoice | null = null;
  let bestScore = -1;
  for (const v of voices) {
    const s = scoreVoice(v);
    if (s > bestScore) {
      best = v;
      bestScore = s;
    }
  }
  return bestScore >= 0 ? best : null;
}

/** Sentence-sized chunks of at most CHUNK_CHARS; a short line stays one utterance (better prosody). */
export function splitForSpeech(text: string): string[] {
  if (text.length <= CHUNK_CHARS) return [text];
  const sentences = text.match(/[^.!?]+[.!?]+["')\]]*\s*|[^.!?]+$/g) ?? [text];
  const chunks: string[] = [];
  let currentChunk = "";
  const push = (piece: string) => {
    const p = piece.trim();
    if (!p) return;
    if ((currentChunk + " " + p).trim().length <= CHUNK_CHARS) currentChunk = (currentChunk + " " + p).trim();
    else {
      if (currentChunk) chunks.push(currentChunk);
      currentChunk = p;
    }
  };
  for (const sentence of sentences) {
    if (sentence.trim().length <= CHUNK_CHARS) {
      push(sentence);
      continue;
    }
    // A very long sentence: break at commas or semicolons, then at spaces.
    for (const clause of sentence.split(/(?<=[,;:])\s+/)) {
      if (clause.length <= CHUNK_CHARS) push(clause);
      else for (const word of clause.split(/\s+/)) push(word);
    }
  }
  if (currentChunk) chunks.push(currentChunk);
  return chunks;
}
