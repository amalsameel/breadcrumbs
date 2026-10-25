// Tap-to-record orb with a countdown ring (PRD 14.2). Presentational: the parent owns timing.
// The Crumb Orb breathes while idle and reacts to the live mic level while recording.
import { useEffect, useState } from "react";
import CrumbOrb from "./CrumbOrb";
import { recordLabel } from "./phone/flow";
import type { OrbMode } from "./table/format";
import { Spinner } from "./ui/Spinner";

export type RecordButtonState = "idle" | "starting" | "recording" | "busy" | "disabled";

interface RecordButtonProps {
  state: RecordButtonState;
  elapsedMs: number;
  limitMs: number;
  onPress: () => void;
  idleLabel?: string;
  analyser?: AnalyserNode | null; // live level while recording
}

const SIZE = 224;
const STROKE = 3;
const R = (SIZE - STROKE) / 2;
const CIRC = 2 * Math.PI * R;
const ORB_INSET = 18;

export function RecordButton({
  state,
  elapsedMs,
  limitMs,
  onPress,
  idleLabel = "Tap to record",
  analyser = null,
}: RecordButtonProps) {
  const recording = state === "recording";
  const level = useAudioLevel(recording ? analyser : null);
  const progress = recording ? Math.min(1, elapsedMs / limitMs) : 0;
  const secondsLeft = Math.max(0, Math.ceil((limitMs - elapsedMs) / 1000));
  const disabled = state === "busy" || state === "disabled" || state === "starting";
  const orbMode: OrbMode = recording ? "warming" : state === "disabled" ? "paused" : state === "busy" ? "idle" : "listening";

  return (
    <div className="flex flex-col items-center gap-4">
      <button
        type="button"
        onClick={onPress}
        disabled={disabled}
        aria-label={recordLabel(recording)}
        className="group relative grid cursor-pointer place-items-center rounded-full [touch-action:manipulation] disabled:cursor-not-allowed"
        style={{ width: SIZE, height: SIZE }}
      >
        <svg aria-hidden="true" width={SIZE} height={SIZE} className="absolute inset-0 -rotate-90">
          <circle cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none" stroke="var(--color-line)" strokeWidth={STROKE} />
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            fill="none"
            stroke="var(--color-ink)"
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={CIRC}
            strokeDashoffset={CIRC * progress}
            style={{ transition: recording ? "stroke-dashoffset 120ms linear, opacity 200ms" : "opacity 200ms", opacity: recording ? 1 : 0 }}
          />
        </svg>
        <span
          aria-hidden="true"
          className="absolute transition-transform duration-200 group-active:scale-[0.97]"
          style={{ inset: ORB_INSET }}
        >
          <CrumbOrb mode={orbMode} level={recording ? level : undefined} />
        </span>
        <span
          aria-hidden="true"
          className={
            "relative grid h-[76px] w-[76px] place-items-center rounded-full bg-bg-raised text-ink " +
            "shadow-[0_2px_6px_rgb(12_11_10/0.12),0_12px_32px_-8px_rgb(12_11_10/0.3)] transition-[transform,opacity] duration-200 " +
            (disabled && state !== "busy" ? "opacity-80" : "group-hover:scale-105")
          }
        >
          {recording ? (
            <span className="flex flex-col items-center gap-1">
              <span className="h-4 w-4 rounded-[4px] bg-ink" />
              <span className="font-mono text-[14px] font-medium tabular-nums">{secondsLeft}s</span>
            </span>
          ) : state === "busy" || state === "starting" ? (
            <Spinner size={24} />
          ) : (
            <MicIcon className={state === "disabled" ? "text-ink-faint" : "text-ink"} />
          )}
        </span>
      </button>
      <span aria-hidden="true" className="min-h-6 text-[16px] font-medium text-ink-muted">
        {recording ? "Tap to stop" : state === "starting" ? "Opening your mic..." : state === "busy" ? "" : idleLabel}
      </span>
    </div>
  );
}

/** Smoothed 0..1 RMS level from an AnalyserNode, sampled about 20 times a second (cheap re-renders). */
function useAudioLevel(analyser: AnalyserNode | null): number {
  const [level, setLevel] = useState(0);
  useEffect(() => {
    if (!analyser) {
      setLevel(0);
      return;
    }
    const samples = new Uint8Array(analyser.fftSize);
    let smooth = 0;
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (now - last < 50) return;
      last = now;
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (let i = 0; i < samples.length; i++) {
        const v = (samples[i] - 128) / 128;
        sum += v * v;
      }
      // Speech RMS is small; a fourth-root curve keeps quiet voices visible. Fast attack, slow release.
      const target = Math.min(1, Math.sqrt(Math.sqrt(sum / samples.length)) * 1.4);
      smooth = target > smooth ? smooth + (target - smooth) * 0.6 : smooth + (target - smooth) * 0.2;
      setLevel(Math.round(smooth * 100) / 100);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [analyser]);
  return level;
}

function MicIcon({ className = "" }: { className?: string }) {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="9" y="2.5" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3.5" />
    </svg>
  );
}
