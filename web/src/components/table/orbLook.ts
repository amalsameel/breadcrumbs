// The Crumb Orb's look (pure data, unit tested): five painterly palettes and how each Table state
// moves. The renderer (CrumbOrb.tsx) only interpolates between these targets.
import type { OrbMode } from "./format";

export type OrbPalette = "ember" | "lilac" | "cocoa" | "sage" | "citrus";

export type Rgb = [number, number, number];

/**
 * Four cloud colors from deepest to lightest, plus the limb tint. The specular tip is always pure
 * white and is not part of the palette, so it stays white on every orb, orange ones included.
 */
export interface PaletteColors {
  deep: string;
  main: string;
  soft: string;
  light: string;
  rim: string;
}

export const PALETTES: Record<OrbPalette, PaletteColors> = {
  // orange / burnt sienna / peach / cream
  ember: { deep: "#B4461A", main: "#F07A2C", soft: "#F8A877", light: "#FCE4CB", rim: "#FFD7B5" },
  // lilac / pink / periwinkle / lavender
  lilac: { deep: "#7C74E0", main: "#BFA4F0", soft: "#F2B6D6", light: "#ECE6FC", rim: "#F7EDFF" },
  // deep cocoa brown with a periwinkle rim
  cocoa: { deep: "#24140F", main: "#45271C", soft: "#6C4130", light: "#8E5D46", rim: "#93A0F2" },
  // sage / silver gray
  sage: { deep: "#6E7A72", main: "#A2ACA5", soft: "#C8CFCA", light: "#EBEEEB", rim: "#F4F6F4" },
  // pastel butter yellow / mint / green
  citrus: { deep: "#86C695", main: "#F4DF78", soft: "#BDE8C8", light: "#FFF7CF", rim: "#FFFBEA" },
};

export interface OrbLook {
  palette: OrbPalette;
  /** Cloud drift speed (flow units per second). */
  speed: number;
  /** Breathing scale amplitude (fraction of the radius). */
  breath: number;
  /** Breathing frequency, Hz. Listening breathes at 0.2 Hz (PRD 6.6). */
  breathHz: number;
  /** Domain-warp strength of the color clouds. */
  warp: number;
  /** White specular tip strength (1 = normal). Never 0. */
  glint: number;
  saturation: number;
  brightness: number;
  /** Film-grain amplitude. */
  grain: number;
}

const BASE: Omit<OrbLook, "palette"> = {
  speed: 0.55,
  breath: 0.022,
  breathHz: 0.2,
  warp: 1,
  glint: 1,
  saturation: 1,
  brightness: 1,
  grain: 0.05,
};

export const MODE_LOOKS: Record<OrbMode, OrbLook> = {
  idle: { ...BASE, palette: "ember", speed: 0.3, breath: 0.012, warp: 0.85, glint: 0.9, saturation: 0.9, brightness: 1.02 },
  listening: { ...BASE, palette: "ember" },
  warming: { ...BASE, palette: "ember", speed: 1.05, breath: 0.03, breathHz: 0.32, warp: 1.2, saturation: 1.12, glint: 1.05 },
  crumb: { ...BASE, palette: "ember", speed: 1.25, breath: 0.034, breathHz: 0.42, warp: 1.25, saturation: 1.15, brightness: 1.06, glint: 1.3 },
  holding: { ...BASE, palette: "lilac", speed: 0.38, breath: 0.016, breathHz: 0.14, warp: 0.9 },
  paused: { ...BASE, palette: "sage", speed: 0.1, breath: 0, breathHz: 0.1, warp: 0.8, glint: 0.85, saturation: 0.9 },
  safety: { ...BASE, palette: "cocoa", speed: 0.22, breath: 0.01, breathHz: 0.15, warp: 0.9, saturation: 0.85, glint: 0.9 },
};

/** The look for a state, with an optional decorative palette override (mode cards, dots). */
export function resolveLook(mode: OrbMode, palette?: OrbPalette): OrbLook {
  const look = MODE_LOOKS[mode];
  return palette ? { ...look, palette } : look;
}

export function hexToRgb(hex: string): Rgb {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.replace(/./g, (c) => c + c) : h;
  const n = Number.parseInt(full, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function rgba(hex: string, alpha: number): string {
  const [r, g, b] = hexToRgb(hex).map((c) => Math.round(c * 255));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Exponential approach factor for `dtS` seconds with time constant `tauS` (frame-rate independent). */
export function approach(dtS: number, tauS: number): number {
  if (tauS <= 0) return 1;
  return 1 - Math.exp(-Math.max(0, dtS) / tauS);
}

/** Backing-store size for a canvas: device-pixel-ratio aware, capped so big orbs stay cheap. */
export function backingSize(cssPx: number, dpr: number, cap = 560): number {
  if (!Number.isFinite(cssPx) || cssPx <= 0) return 0;
  const ratio = Number.isFinite(dpr) && dpr > 0 ? Math.min(dpr, 2) : 1;
  return Math.max(1, Math.min(cap, Math.round(cssPx * ratio)));
}

/** Ripple progress 0..1 for a pulse that started at `startMs`, or null when none is running. */
export function rippleProgress(startMs: number | null, now: number, durationMs = 1500): number | null {
  if (startMs === null) return null;
  const p = (now - startMs) / durationMs;
  return p >= 0 && p < 1 ? p : null;
}
