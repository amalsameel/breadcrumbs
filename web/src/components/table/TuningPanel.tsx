// Live engine tuning from the X-ray panel (PRD 8.12, 16.2): sends {type:"params", changes}.
import { useEffect, useRef, useState } from "react";

interface SliderSpec {
  key: "T_NORMAL_S" | "T_LONG_S" | "T_EXC_S" | "STALE_SWITCH" | "NEAR_MARGIN_DB" | "FAR_MARGIN_DB";
  label: string;
  min: number;
  max: number;
  step: number;
  fallback: number;
  unit: string;
}

const SLIDERS: SliderSpec[] = [
  { key: "T_NORMAL_S", label: "Glow after", min: 0.5, max: 5, step: 0.5, fallback: 1.5, unit: "s" },
  { key: "T_LONG_S", label: "Steer after", min: 1.5, max: 10, step: 0.5, fallback: 3, unit: "s" },
  { key: "T_EXC_S", label: "Switch after", min: 2.5, max: 12, step: 0.5, fallback: 4.5, unit: "s" },
  { key: "STALE_SWITCH", label: "Stale at", min: 0.3, max: 0.9, step: 0.05, fallback: 0.6, unit: "" },
  // Bystanders: a voice counts as someone at the table within NEAR of the seated level, as far below FAR.
  { key: "NEAR_MARGIN_DB", label: "Near within", min: 4, max: 20, step: 1, fallback: 8, unit: "dB" },
  { key: "FAR_MARGIN_DB", label: "Far below", min: 4, max: 20, step: 1, fallback: 12, unit: "dB" },
];

const SEND_DEBOUNCE_MS = 300;

interface TuningPanelProps {
  params: Record<string, unknown>;
  onChange(changes: Record<string, unknown>): void;
}

function numberParam(params: Record<string, unknown>, key: string, fallback: number): number {
  const v = params[key];
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

export function TuningPanel({ params, onChange }: TuningPanelProps) {
  const [draft, setDraft] = useState<Record<string, number>>({});
  const timers = useRef<Record<string, number>>({});

  useEffect(
    () => () => Object.values(timers.current).forEach((t) => window.clearTimeout(t)),
    [],
  );

  const setSlider = (key: string, value: number) => {
    setDraft((d) => ({ ...d, [key]: value }));
    window.clearTimeout(timers.current[key]);
    timers.current[key] = window.setTimeout(() => {
      onChange({ [key]: value });
      setDraft((d) => {
        const rest = { ...d };
        delete rest[key];
        return rest;
      });
    }, SEND_DEBOUNCE_MS);
  };

  const wildcard = params.WILDCARD_ENABLED === true;
  const gating = params.BYSTANDER_GATING !== false; // on by default

  return (
    <div className="space-y-3">
      {SLIDERS.map((s) => {
        const value = draft[s.key] ?? numberParam(params, s.key, s.fallback);
        const id = `tune-${s.key}`;
        return (
          <div key={s.key} className="space-y-1">
            <label htmlFor={id} className="flex items-baseline justify-between">
              <span className="text-ink-muted">
                {s.label} <span className="text-ink-muted/80">{s.key}</span>
              </span>
              <span className="tabular-nums text-ink">
                {value.toFixed(s.step < 0.1 ? 2 : 1)}
                {s.unit && ` ${s.unit}`}
              </span>
            </label>
            <input
              id={id}
              type="range"
              min={s.min}
              max={s.max}
              step={s.step}
              value={value}
              onChange={(e) => setSlider(s.key, Number(e.target.value))}
              className="h-6 w-full cursor-pointer accent-ink"
            />
          </div>
        );
      })}
      <Toggle label="Wildcard drops" param="WILDCARD_ENABLED" checked={wildcard} onChange={onChange} />
      <Toggle label="Ignore voices away from the table" param="BYSTANDER_GATING" checked={gating} onChange={onChange} />
    </div>
  );
}

function Toggle({
  label,
  param,
  checked,
  onChange,
}: {
  label: string;
  param: string;
  checked: boolean;
  onChange(changes: Record<string, unknown>): void;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 py-1">
      <span className="text-ink-muted">
        {label} <span className="text-ink-muted/80">{param}</span>
      </span>
      <span className="relative inline-flex items-center">
        <input
          type="checkbox"
          role="switch"
          checked={checked}
          onChange={(e) => onChange({ [param]: e.target.checked })}
          className="peer sr-only"
        />
        <span
          aria-hidden="true"
          className="h-6 w-11 rounded-full border border-line bg-bg-sunken transition-colors peer-checked:border-ink peer-checked:bg-ink peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ink"
        />
        <span
          aria-hidden="true"
          className="absolute left-1 size-4 rounded-full bg-bg-raised shadow-pill transition-transform peer-checked:translate-x-5"
        />
        <span className="ml-2 w-6 text-ink">{checked ? "on" : "off"}</span>
      </span>
    </label>
  );
}
