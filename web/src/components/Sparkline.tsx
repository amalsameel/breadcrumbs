// Tiny dependency-free SVG sparkline with optional dashed threshold lines (X-ray, PRD 14.3).
import { useId } from "react";

export interface SparkThreshold {
  value: number;
  label: string;
  color?: string;
}

interface SparklineProps {
  values: number[];
  max?: number;
  min?: number;
  thresholds?: SparkThreshold[];
  color?: string;
  height?: number;
  label: string; // accessible summary, e.g. "Silence over the last 60 seconds"
}

const WIDTH = 240;

export default function Sparkline({
  values,
  max,
  min = 0,
  thresholds = [],
  color = "var(--color-amber)",
  height = 44,
  label,
}: SparklineProps) {
  const gradientId = `spark-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`; // safe inside url(#...)
  const top = Math.max(max ?? 0, ...values, ...thresholds.map((t) => t.value), min + 1e-6);
  const y = (v: number) => height - 2 - ((Math.min(Math.max(v, min), top) - min) / (top - min)) * (height - 4);
  const step = values.length > 1 ? WIDTH / (values.length - 1) : WIDTH;
  const points = values.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`);
  const line = points.join(" ");
  const area = points.length > 1 ? `0,${height} ${line} ${WIDTH},${height}` : "";
  const last = values.at(-1);

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${height}`}
      preserveAspectRatio="none"
      className="block h-11 w-full overflow-visible"
      role="img"
      aria-label={last === undefined ? `${label}: no data yet` : `${label}: now ${last.toFixed(2)}`}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {thresholds.map((t) => (
        <line
          key={t.label}
          x1={0}
          x2={WIDTH}
          y1={y(t.value)}
          y2={y(t.value)}
          stroke={t.color ?? "var(--color-ink-faint)"}
          strokeWidth={1}
          strokeDasharray="3 3"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {area && <polygon points={area} fill={`url(#${gradientId})`} />}
      {points.length > 1 && (
        <polyline
          points={line}
          fill="none"
          stroke={color}
          strokeWidth={1.6}
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}
