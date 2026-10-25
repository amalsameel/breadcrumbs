// pcm-worklet.js: Float32 -> Int16 80 ms frames (1,920 samples at 24 kHz) for /ws/audio, plus cheap voice
// features every 100 ms (2,400 samples) for the Table's speech-aware VAD (web/src/audio/capture.ts):
//   rms     full-band RMS (0..1)
//   speech  RMS of the 300 to 3,400 Hz speech band (4th-order high-pass + 2nd-order low-pass biquads)
//   zcr     zero crossings per sample (white noise and hiss sit near 0.5, voiced speech far below)
//   impulse peak-to-median ratio of the ten 10 ms sub-block energies, in dB (keyboard taps and clicks
//           put their energy into one or two sub-blocks; speech spreads it over the frame)

const FRAME_SAMPLES = 1920;
const FEATURE_SAMPLES = 2400;
const SUB_BLOCKS = 10;
const SUB_SAMPLES = FEATURE_SAMPLES / SUB_BLOCKS;
const EPS = 1e-12;

// RBJ audio-EQ-cookbook biquad, direct form I.
function biquad(type, f0, fs, q) {
  const w = (2 * Math.PI * f0) / fs;
  const cos = Math.cos(w);
  const alpha = Math.sin(w) / (2 * q);
  const a0 = 1 + alpha;
  const b1 = type === "lowpass" ? 1 - cos : -(1 + cos);
  const b0 = type === "lowpass" ? (1 - cos) / 2 : (1 + cos) / 2;
  return { b0: b0 / a0, b1: b1 / a0, b2: b0 / a0, a1: (-2 * cos) / a0, a2: (1 - alpha) / a0, x1: 0, x2: 0, y1: 0, y2: 0 };
}

function step(f, x) {
  const y = f.b0 * x + f.b1 * f.x1 + f.b2 * f.x2 - f.a1 * f.y1 - f.a2 * f.y2;
  f.x2 = f.x1;
  f.x1 = x;
  f.y2 = f.y1;
  f.y1 = y;
  return y;
}

class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    const fs = typeof sampleRate === "number" ? sampleRate : 24000;
    this.buf = new Int16Array(FRAME_SAMPLES);
    this.n = 0;
    // Butterworth sections: 4th-order high-pass at 300 Hz (rejects 50/60/120 Hz hum and rumble), 2nd-order
    // low-pass at 3,400 Hz (rejects hiss).
    this.band = [biquad("highpass", 300, fs, 0.5412), biquad("highpass", 300, fs, 1.3066), biquad("lowpass", 3400, fs, 0.7071)];
    this.sub = new Float64Array(SUB_BLOCKS);
    this.resetFeatures();
  }

  resetFeatures() {
    this.cnt = 0;
    this.sumSq = 0;
    this.bandSq = 0;
    this.crossings = 0;
    this.subSq = 0;
    this.subN = 0;
    this.prev = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === FRAME_SAMPLES) {
        this.port.postMessage({ type: "frame", pcm: this.buf.slice().buffer });
        this.n = 0;
      }

      let b = s;
      for (const f of this.band) b = step(f, b);
      this.sumSq += s * s;
      this.bandSq += b * b;
      this.subSq += s * s;
      if ((s >= 0) !== (this.prev >= 0)) this.crossings++;
      this.prev = s;
      this.cnt++;
      if (++this.subN === SUB_SAMPLES) {
        this.sub[(this.cnt / SUB_SAMPLES - 1) | 0] = this.subSq;
        this.subSq = 0;
        this.subN = 0;
      }
      if (this.cnt === FEATURE_SAMPLES) this.emit();
    }
    return true;
  }

  emit() {
    const sorted = Array.from(this.sub).sort((a, b) => a - b);
    const median = (sorted[SUB_BLOCKS / 2 - 1] + sorted[SUB_BLOCKS / 2]) / 2;
    const peak = sorted[SUB_BLOCKS - 1];
    this.port.postMessage({
      type: "vad",
      rms: Math.sqrt(this.sumSq / this.cnt),
      speech: Math.sqrt(this.bandSq / this.cnt),
      zcr: this.crossings / this.cnt,
      impulse: 10 * Math.log10((peak + EPS) / (median + EPS)),
    });
    this.resetFeatures();
  }
}

registerProcessor("pcm-worklet", PcmWorklet);
