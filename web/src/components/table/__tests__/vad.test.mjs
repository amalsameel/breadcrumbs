// Speech-aware VAD (issue: background noise kept the Table "listening"). Synthetic 24 kHz signals go
// through the real public/pcm-worklet.js feature extractor and into Vad, the way the Table runs them.
// Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test 'src/components/table/__tests__/*.test.mjs'
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { Vad } from "../../../audio/capture.ts";

const FS = 24000;
const BLOCK = 128; // AudioWorklet render quantum

// Load the worklet with a minimal AudioWorkletGlobalScope.
let Processor = null;
globalThis.sampleRate = FS;
globalThis.AudioWorkletProcessor = class {
  constructor() {
    this.port = { postMessage: (m) => this.port.onmessage?.(m) };
  }
};
globalThis.registerProcessor = (_name, cls) => {
  Processor = cls;
};
await import("../../../../public/pcm-worklet.js");

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Gaussian-ish white noise with the given RMS. */
function white(seconds, rms, seed = 1) {
  const r = rng(seed);
  const out = new Float32Array(Math.round(seconds * FS));
  for (let i = 0; i < out.length; i++) out[i] = (r() + r() + r() + r() - 2) * Math.sqrt(3) * rms;
  return out;
}

/** Pink-ish noise (Paul Kellet's filter on white noise), normalized to the given RMS. */
function pink(seconds, rms, seed = 2) {
  const w = white(seconds, 1, seed);
  const out = new Float32Array(w.length);
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  for (let i = 0; i < w.length; i++) {
    const x = w[i];
    b0 = 0.99886 * b0 + x * 0.0555179;
    b1 = 0.99332 * b1 + x * 0.0750759;
    b2 = 0.969 * b2 + x * 0.153852;
    b3 = 0.8665 * b3 + x * 0.3104856;
    b4 = 0.55 * b4 + x * 0.5329522;
    b5 = -0.7616 * b5 - x * 0.016898;
    out[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + x * 0.5362;
    b6 = x * 0.115926;
  }
  return scaleTo(out, rms);
}

function hum(seconds, amp) {
  const out = new Float32Array(Math.round(seconds * FS));
  for (let i = 0; i < out.length; i++) {
    const t = i / FS;
    out[i] = amp * (Math.sin(2 * Math.PI * 60 * t) + 0.6 * Math.sin(2 * Math.PI * 120 * t) + 0.2 * Math.sin(2 * Math.PI * 180 * t));
  }
  return out;
}

/** Keyboard taps: short decaying broadband bursts every 90 to 220 ms. */
function clicks(seconds, amp, seed = 3) {
  const r = rng(seed);
  const out = new Float32Array(Math.round(seconds * FS));
  let at = Math.round(0.05 * FS);
  while (at < out.length) {
    const len = Math.round(0.012 * FS);
    for (let k = 0; k < len && at + k < out.length; k++) {
      out[at + k] += amp * (r() * 2 - 1) * Math.exp(-k / (0.002 * FS));
    }
    at += Math.round((0.09 + 0.13 * r()) * FS);
  }
  return out;
}

/**
 * Voice-like: a harmonic series (F0 around 140 Hz with drift) with a formant-ish spectral tilt, shaped
 * by a 4.5 Hz syllable envelope, in words of `wordS` separated by `gapS` pauses.
 */
function speechLike(seconds, rms, { wordS = 1.4, gapS = 0.35, seed = 4 } = {}) {
  const r = rng(seed);
  const out = new Float32Array(Math.round(seconds * FS));
  let phase = 0;
  const period = wordS + gapS;
  for (let i = 0; i < out.length; i++) {
    const t = i / FS;
    const inWord = t % period < wordS;
    if (!inWord) continue;
    const f0 = 140 + 20 * Math.sin(2 * Math.PI * 0.7 * t) + 8 * Math.sin(2 * Math.PI * 3.1 * t);
    phase += (2 * Math.PI * f0) / FS;
    let v = 0;
    for (let k = 1; k <= 22; k++) {
      const f = k * f0;
      const formant = 1 / (1 + ((f - 600) / 400) ** 2) + 0.5 / (1 + ((f - 1700) / 500) ** 2) + 0.15;
      v += (formant / Math.sqrt(k)) * Math.sin(k * phase);
    }
    const env = 0.5 - 0.5 * Math.cos(2 * Math.PI * 4.5 * (t % period));
    out[i] = v * env * (0.9 + 0.2 * r());
  }
  return scaleTo(out, rms, (x) => x !== 0);
}

function scaleTo(x, rms, include = () => true) {
  let sum = 0;
  let n = 0;
  for (const v of x) if (include(v)) (sum += v * v), n++;
  const k = rms / Math.sqrt(sum / Math.max(1, n));
  return x.map((v) => v * k);
}

const concat = (...parts) => {
  const out = new Float32Array(parts.reduce((a, p) => a + p.length, 0));
  let o = 0;
  for (const p of parts) out.set(p, o), (o += p.length);
  return out;
};

const mix = (...parts) => {
  const out = new Float32Array(Math.max(...parts.map((p) => p.length)));
  for (const p of parts) for (let i = 0; i < p.length; i++) out[i] += p[i];
  return out;
};

const silence = (seconds) => white(seconds, 0.00002, 9);

/** Runs a signal through the worklet + Vad; returns one reading per 100 ms with its time in seconds. */
function run(signal) {
  const proc = new Processor();
  const vad = new Vad();
  const readings = [];
  proc.port.onmessage = (m) => {
    if (m.type !== "vad") return;
    const t = (readings.length + 1) * 100;
    readings.push({ t: t / 1000, ...vad.update(m, t) });
  };
  for (let i = 0; i < signal.length; i += BLOCK) proc.process([[signal.subarray(i, i + BLOCK)]]);
  return readings;
}

const activeSeconds = (rs, from = 0, to = Infinity) => rs.filter((r) => r.active && r.t > from && r.t <= to).length / 10;

describe("Vad rejects background noise", () => {
  it("near-silence stays inactive", () => {
    assert.equal(activeSeconds(run(silence(8))), 0);
  });

  it("steady white noise stays inactive", () => {
    assert.equal(activeSeconds(run(white(10, 0.03))), 0);
  });

  it("steady pink-ish noise stays inactive", () => {
    assert.equal(activeSeconds(run(pink(10, 0.04))), 0);
  });

  it("60/120 Hz hum stays inactive, even loud", () => {
    assert.equal(activeSeconds(run(mix(hum(10, 0.2), silence(10)))), 0);
  });

  it("keyboard-like clicks stay inactive", () => {
    const rs = run(mix(silence(10), clicks(10, 0.4)));
    assert.equal(activeSeconds(rs), 0);
  });

  it("clicks over room noise stay inactive", () => {
    const rs = run(mix(pink(10, 0.01), clicks(10, 0.3)));
    assert.equal(activeSeconds(rs), 0);
  });

  it("loud taps over room noise stay inactive", () => {
    const rs = run(mix(pink(10, 0.01), clicks(10, 0.9, 8)));
    assert.equal(activeSeconds(rs), 0);
  });

  it("slowly fluctuating noise (HVAC, AGC pumping) stays inactive", () => {
    const base = pink(12, 0.03, 12);
    const rs = run(base.map((v, i) => v * 10 ** ((4 * Math.sin((2 * Math.PI * 0.4 * i) / FS)) / 20)));
    assert.equal(activeSeconds(rs), 0);
  });

  it("hum switched on mid-session stays inactive", () => {
    const rs = run(concat(silence(3), mix(hum(7, 0.2), silence(7))));
    assert.equal(activeSeconds(rs), 0);
  });

  it("a fan switched on mid-session is absorbed into the floor quickly", () => {
    const rs = run(concat(silence(3), pink(9, 0.03, 7)));
    assert.ok(activeSeconds(rs) <= 1.5, `active ${activeSeconds(rs)} s`);
    assert.equal(activeSeconds(rs, 6), 0, "inactive once the fan has settled");
  });
});

describe("Vad detects speech", () => {
  it("speech-like signal at moderate SNR over pink noise turns active fast and releases after", () => {
    const noise = pink(12, 0.01, 11);
    const voice = concat(new Float32Array(3 * FS), speechLike(6, 0.04), new Float32Array(3 * FS)); // ~12 dB SNR
    const rs = run(mix(noise, voice));
    const first = rs.find((r) => r.active);
    assert.ok(first, "speech detected");
    assert.ok(first.t > 3 && first.t <= 3.5, `onset at ${first.t}`);
    assert.ok(activeSeconds(rs, 3, 9) >= 4.5, `active ${activeSeconds(rs, 3, 9)} of 6 s`);
    assert.equal(activeSeconds(rs, 9.8), 0, "released within ~800 ms after the speech");
    assert.equal(activeSeconds(rs, 0, 3), 0);
  });

  it("speech in a quiet room", () => {
    const rs = run(concat(silence(2), speechLike(4, 0.02), silence(2)));
    assert.ok(activeSeconds(rs, 2, 6) >= 3, `active ${activeSeconds(rs, 2, 6)} s`);
  });

  it("speech over a steady fan is still heard", () => {
    const rs = run(mix(pink(10, 0.02, 5), concat(new Float32Array(4 * FS), speechLike(4, 0.08))));
    assert.ok(activeSeconds(rs, 4, 8) >= 3, `active ${activeSeconds(rs, 4, 8)} s`);
    assert.equal(activeSeconds(rs, 0, 4), 0);
  });

  it("calibrates for the first 1.5 s and reports the reading", () => {
    const rs = run(concat(speechLike(3, 0.05)));
    assert.ok(rs.slice(0, 14).every((r) => r.calibrating && !r.active));
    const last = rs.at(-1);
    assert.equal(typeof last.snrDb, "number");
    assert.equal(typeof last.levelDb, "number");
  });

  it("reset keeps the floor but clears the speech state", () => {
    const vad = new Vad();
    for (let t = 100; t <= 2000; t += 100) vad.update({ rms: 0.001, speech: 0.0008, zcr: 0.1, impulse: 2 }, t);
    const floor = vad.noiseFloorDb;
    vad.reset();
    assert.equal(vad.active, false);
    assert.equal(vad.noiseFloorDb, floor);
  });
});
