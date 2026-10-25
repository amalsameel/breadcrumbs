// Big tap-to-record button with a countdown ring (PRD 14.2). Purely presentational: the parent owns timing.
export type RecordButtonState = "idle" | "starting" | "recording" | "busy" | "disabled";

interface RecordButtonProps {
  state: RecordButtonState;
  elapsedMs: number;
  limitMs: number;
  onPress: () => void;
  idleLabel?: string;
}

const SIZE = 148;
const STROKE = 6;
const R = (SIZE - STROKE) / 2;
const CIRC = 2 * Math.PI * R;

export function RecordButton({ state, elapsedMs, limitMs, onPress, idleLabel = "Tap to record" }: RecordButtonProps) {
  const recording = state === "recording";
  const progress = recording ? Math.min(1, elapsedMs / limitMs) : 0;
  const secondsLeft = Math.max(0, Math.ceil((limitMs - elapsedMs) / 1000));
  const disabled = state === "busy" || state === "disabled" || state === "starting";
  const ariaLabel = recording ? `Stop recording, ${secondsLeft} seconds left` : "Start recording";

  return (
    <div className="flex flex-col items-center gap-3">
      <button
        type="button"
        onClick={onPress}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-pressed={recording}
        className="relative grid cursor-pointer place-items-center rounded-full [touch-action:manipulation] disabled:cursor-not-allowed"
        style={{ width: SIZE, height: SIZE }}
      >
        <svg aria-hidden="true" width={SIZE} height={SIZE} className="absolute inset-0 -rotate-90">
          <circle cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none" stroke="var(--color-line)" strokeWidth={STROKE} />
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            fill="none"
            stroke="var(--color-crumb)"
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={CIRC}
            strokeDashoffset={CIRC * progress}
            style={{ transition: recording ? "stroke-dashoffset 120ms linear" : "none", opacity: recording ? 1 : 0 }}
          />
        </svg>
        <span
          aria-hidden="true"
          className={
            "grid h-[116px] w-[116px] place-items-center rounded-full transition-all duration-200 " +
            (recording
              ? "bg-danger/90 shadow-[0_0_48px_-6px_rgba(240,113,103,0.75)]"
              : disabled
                ? "bg-bg-raised opacity-60"
                : "bg-amber shadow-[0_0_56px_-8px_rgba(245,165,36,0.85)] active:scale-95")
          }
        >
          {recording ? (
            <span className="flex flex-col items-center gap-1 text-bg">
              <span className="h-6 w-6 rounded-md bg-bg" />
              <span className="font-mono text-[18px] font-medium tabular-nums">{secondsLeft}s</span>
            </span>
          ) : (
            <MicIcon className={disabled ? "text-ink-muted" : "text-bg"} />
          )}
        </span>
      </button>
      <span aria-hidden="true" className="min-h-6 text-[16px] font-medium text-ink-muted">
        {recording ? "Tap to stop" : state === "starting" ? "Opening your mic..." : state === "busy" ? "" : idleLabel}
      </span>
    </div>
  );
}

function MicIcon({ className = "" }: { className?: string }) {
  return (
    <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="9" y="2.5" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3.5" />
    </svg>
  );
}
