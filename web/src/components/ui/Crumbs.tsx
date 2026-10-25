// Small brand primitives: the tiny orb dot (wordmark, tabs, list bullets), the step indicator and a
// sized orb figure for the calm phone screens. Color lives only inside the orbs; everything else is ink.
import type { CSSProperties } from "react";
import CrumbOrb from "../CrumbOrb";
import type { OrbPalette } from "../CrumbOrb";
import type { OrbMode } from "../table/format";
import { PALETTES } from "../table/orbLook";

/** CSS background for a static gradient sphere with a soft white glint near the upper left. */
export function orbDotBackground(palette: OrbPalette): string {
  const c = PALETTES[palette]; // same colors as the Crumb Orb
  return [
    "radial-gradient(circle at 32% 26%, rgba(255,255,255,0.98) 0%, rgba(255,255,255,0.75) 9%, rgba(255,255,255,0) 30%)",
    `radial-gradient(circle at 76% 80%, ${c.rim} 0%, transparent 48%)`,
    `radial-gradient(circle at 30% 74%, ${c.deep} 0%, transparent 62%)`,
    `radial-gradient(circle at 62% 34%, ${c.main} 0%, transparent 72%)`,
    c.soft,
  ].join(", ");
}

interface OrbDotProps {
  palette?: OrbPalette;
  size?: number;
  className?: string;
  style?: CSSProperties;
}

/** A tiny static orb (12 to 28 px). Decorative. */
export function OrbDot({ palette = "ember", size = 14, className = "", style }: OrbDotProps) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block shrink-0 rounded-full ${className}`}
      style={{
        width: size,
        height: size,
        background: orbDotBackground(palette),
        boxShadow: "inset 0 -1px 2px rgb(12 11 10 / 0.18), 0 1px 2px rgb(12 11 10 / 0.08)",
        ...style,
      }}
    />
  );
}

/** Wordmark: a tiny ember orb and the name. */
export function Wordmark({ size = "md" }: { size?: "md" | "lg" }) {
  const lg = size === "lg";
  return (
    <span className="inline-flex items-center gap-2">
      <OrbDot size={lg ? 22 : 18} />
      <span className={`font-display font-semibold tracking-[-0.03em] text-ink ${lg ? "text-[22px]" : "text-[19px]"}`}>
        Breadcrumbs
      </span>
    </span>
  );
}

interface OrbFigureProps {
  size: number;
  mode?: OrbMode;
  palette?: OrbPalette;
  still?: boolean;
  className?: string;
}

/** A Crumb Orb in a fixed square box, for the calm phone screens. Decorative. */
export function OrbFigure({ size, mode = "listening", palette, still, className = "" }: OrbFigureProps) {
  return (
    <div aria-hidden="true" className={`relative shrink-0 ${className}`} style={{ width: size, height: size }}>
      <CrumbOrb mode={mode} palette={palette} still={still} />
    </div>
  );
}

/** Row of small marks used as a step indicator: filled for done, long for current. */
export function CrumbSteps({ total, current, label }: { total: number; current: number; label: string }) {
  return (
    <div role="img" aria-label={label} className="flex items-center gap-1.5">
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={
            "h-1.5 rounded-full transition-all duration-300 " +
            (i < current ? "w-1.5 bg-ink" : i === current ? "w-5 bg-ink" : "w-1.5 bg-line-strong")
          }
        />
      ))}
    </div>
  );
}
