// The Crumb Orb (PRD 6.6, 14.3): a breathing amber light that is the whole live UI.
// Fills its parent box; the parent decides the size.
import { AnimatePresence, motion, useReducedMotion, type Transition } from "framer-motion";
import type { OrbMode } from "./table/format";

interface OrbLook {
  color: string;
  scale: [number, number];
  halo: [number, number];
  body: number;
  periodS: number | null; // null = still
}

// Listening breathes at 0.2 Hz (5 s period); every other state is a variation on it.
const LOOKS: Record<OrbMode, OrbLook> = {
  idle: { color: "#C98A2A", scale: [0.97, 1.0], halo: [0.18, 0.28], body: 0.75, periodS: 5 },
  listening: { color: "#F5A524", scale: [0.965, 1.03], halo: [0.32, 0.55], body: 0.95, periodS: 5 },
  warming: { color: "#F8B640", scale: [0.98, 1.055], halo: [0.6, 0.9], body: 1, periodS: 3.4 },
  crumb: { color: "#FFD27A", scale: [1.02, 1.09], halo: [0.85, 1], body: 1, periodS: 1.9 },
  holding: { color: "#5B8DEF", scale: [0.975, 1.01], halo: [0.22, 0.4], body: 0.78, periodS: 7 },
  paused: { color: "#6B6B6B", scale: [1, 1], halo: [0.1, 0.1], body: 0.55, periodS: null },
  safety: { color: "#7A7068", scale: [0.99, 1.0], halo: [0.12, 0.2], body: 0.65, periodS: 6 },
};

const BODY_BG =
  "radial-gradient(circle at 36% 30%, rgba(255,248,232,0.95) 0%, rgba(255,248,232,0.35) 18%, currentColor 46%, color-mix(in srgb, currentColor 45%, #17120E) 100%)";
const HALO_BG = "radial-gradient(circle, currentColor 0%, color-mix(in srgb, currentColor 30%, transparent) 32%, transparent 64%)";
const SHIMMER_BG =
  "conic-gradient(from 0deg, transparent 0deg, rgba(255,255,255,0.22) 60deg, transparent 140deg, rgba(255,255,255,0.12) 220deg, transparent 300deg)";

interface CrumbOrbProps {
  mode: OrbMode;
  /** Changing this value fires a single bright ripple (a new crumb landed). */
  pulseKey?: string | null;
}

export default function CrumbOrb({ mode, pulseKey = null }: CrumbOrbProps) {
  const reduce = useReducedMotion() ?? false;
  const look = LOOKS[mode];
  const still = reduce || look.periodS === null;
  const loop: Transition = still
    ? { duration: 0.8, ease: "easeOut" }
    : { duration: look.periodS ?? 5, repeat: Infinity, ease: "easeInOut" };
  const [lo, hi] = look.scale;
  const [haloLo, haloHi] = look.halo;

  return (
    <motion.div
      aria-hidden="true"
      className="relative h-full w-full"
      initial={false}
      animate={{ color: look.color }}
      transition={{ color: { duration: 1.1, ease: "easeInOut" } }}
    >
      <motion.div
        className="absolute -inset-[48%] rounded-full"
        style={{ background: HALO_BG }}
        initial={false}
        animate={still ? { opacity: (haloLo + haloHi) / 2, scale: 1 } : { opacity: [haloLo, haloHi, haloLo], scale: [0.94, 1.04, 0.94] }}
        transition={loop}
      />
      <motion.div
        className="absolute inset-0 overflow-hidden rounded-full"
        style={{
          background: BODY_BG,
          boxShadow:
            "0 0 90px -8px currentColor, inset 0 -18px 48px rgba(23,18,14,0.45), inset 0 8px 24px rgba(255,248,232,0.25)",
        }}
        initial={false}
        animate={still ? { scale: hi, opacity: look.body } : { scale: [lo, hi, lo], opacity: look.body }}
        transition={{ scale: loop, opacity: { duration: 0.9 } }}
      >
        <motion.div
          className="absolute -inset-1/4 opacity-60 mix-blend-soft-light"
          style={{ background: SHIMMER_BG }}
          animate={reduce ? { rotate: 0 } : { rotate: 360 }}
          transition={reduce ? { duration: 0 } : { duration: 48, repeat: Infinity, ease: "linear" }}
        />
      </motion.div>
      <AnimatePresence>
        {pulseKey && !reduce && (
          <motion.div
            key={pulseKey}
            className="absolute inset-0 rounded-full border-2"
            style={{ borderColor: "currentColor" }}
            initial={{ opacity: 0.9, scale: 0.95 }}
            animate={{ opacity: 0, scale: 1.9 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 1.5, ease: "easeOut" }}
          />
        )}
      </AnimatePresence>
    </motion.div>
  );
}

interface OrbStatusProps {
  label: string;
  detail: string | null;
}

/** The text label under the orb so no state is carried by color alone. */
export function OrbStatus({ label, detail }: OrbStatusProps) {
  return (
    <div className="flex min-h-7 items-center justify-center gap-2 text-sm font-medium uppercase tracking-[0.18em] text-ink-muted">
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={label}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.25 }}
        >
          {label}
        </motion.span>
      </AnimatePresence>
      {detail && <span className="font-mono text-xs normal-case tracking-normal text-ink-faint">{detail}</span>}
    </div>
  );
}
