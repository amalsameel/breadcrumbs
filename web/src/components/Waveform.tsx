// Live scrolling waveform from the recorder's AnalyserNode (PRD 14.2). Idle state is a quiet row of dots.
import { useEffect, useRef } from "react";

interface WaveformProps {
  analyser: AnalyserNode | null; // null = idle
  height?: number;
  bars?: number;
}

// Ink on the light canvas (tokens --color-ink and --color-line-strong); color stays inside the orb.
const BAR_COLOR = "#0C0B0A";
const IDLE_COLOR = "#D3CEC6";

export function Waveform({ analyser, height = 48, bars = 40 }: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const history = new Array<number>(bars).fill(0);
    const samples = analyser ? new Uint8Array(analyser.fftSize) : null;
    let raf = 0;
    let lastPush = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = (now: number) => {
      if (analyser && samples && now - lastPush > 50) {
        analyser.getByteTimeDomainData(samples);
        let sum = 0;
        for (let i = 0; i < samples.length; i++) {
          const v = (samples[i] - 128) / 128;
          sum += v * v;
        }
        // RMS of speech is small; a square-root curve makes quiet voices still visibly move.
        history.push(Math.min(1, Math.sqrt(Math.sqrt(sum / samples.length)) * 1.6));
        history.shift();
        lastPush = now;
      }
      const w = canvas.clientWidth;
      ctx.clearRect(0, 0, w, height);
      const slot = w / bars;
      const barW = Math.max(2, slot * 0.45);
      for (let i = 0; i < bars; i++) {
        const x = i * slot + (slot - barW) / 2;
        if (!analyser) {
          ctx.fillStyle = IDLE_COLOR;
          ctx.beginPath();
          ctx.arc(x + barW / 2, height / 2, Math.min(2.5, barW / 2), 0, Math.PI * 2);
          ctx.fill();
          continue;
        }
        const h = Math.max(barW, history[i] * (height - 4));
        ctx.globalAlpha = 0.18 + 0.72 * (i / bars);
        ctx.fillStyle = BAR_COLOR;
        roundRect(ctx, x, (height - h) / 2, barW, h, barW / 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      if (analyser) raf = requestAnimationFrame(draw);
    };

    resize();
    const ro = new ResizeObserver(() => {
      resize();
      if (!analyser) draw(performance.now());
    });
    ro.observe(canvas);
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [analyser, height, bars]);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={analyser ? "Live audio level" : "Microphone idle"}
      className="block w-full"
      style={{ height }}
    />
  );
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}
