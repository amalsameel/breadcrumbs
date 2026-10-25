// pcm-worklet.js: Float32 → Int16, 80 ms frames (1,920 samples at 24 kHz), RMS every 100 ms
class PcmWorklet extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(1920); this.n = 0; this.sumSq = 0; this.cnt = 0; }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      this.sumSq += s * s; this.cnt++;
      if (this.n === 1920) { this.port.postMessage({ type: "frame", pcm: this.buf.slice().buffer }); this.n = 0; }
      if (this.cnt === 2400) { this.port.postMessage({ type: "rms", rms: Math.sqrt(this.sumSq / this.cnt) }); this.sumSq = 0; this.cnt = 0; }
    }
    return true;
  }
}
registerProcessor("pcm-worklet", PcmWorklet);
