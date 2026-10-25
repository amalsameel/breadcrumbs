// The Crumb Orb (PRD 6.6, 14.3): a matte, grainy, painterly sphere that is the Table's hero element.
// Fills its parent box; the parent decides the size.
//
// Rendering: a fragment-shader sphere (domain-warped color clouds, soft shading, a soft limb, film
// grain and a white specular tip) drawn by one shared WebGL context (table/orbGl.ts) into this orb's
// own canvas. Small orbs, browsers without WebGL and the first frame use a layered CSS gradient with
// SVG grain instead. prefers-reduced-motion or `still` renders a single static frame.
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import type { OrbMode } from "./table/format";
import {
  acquireOrbRenderer,
  createDedicatedOrbRenderer,
  paletteRgb,
  releaseOrbRenderer,
  type OrbFrame,
  type OrbRenderer,
} from "./table/orbGl";
import {
  approach,
  backingSize,
  PALETTES,
  resolveLook,
  rgba,
  rippleProgress,
  type OrbLook,
  type OrbPalette,
  type PaletteColors,
  type Rgb,
} from "./table/orbLook";

export type { OrbPalette } from "./table/orbLook";
export type { OrbMode } from "./table/format";

export interface CrumbOrbProps {
  mode: OrbMode;
  /** Changing this value fires one bright ripple (a crumb landed). */
  pulseKey?: string | null;
  /** Decorative orbs (mode cards, dots): overrides the mode's palette. */
  palette?: OrbPalette;
  /** Optional 0..1 live audio level for subtle reactivity. */
  level?: number;
  className?: string;
  /** Render a static frame (thumbnails, reduced motion). */
  still?: boolean;
  /** Accessible name. Decorative orbs are aria-hidden when omitted. */
  label?: string;
}

const MIN_GL_PX = 40; // below this the shader's detail is invisible: use the CSS orb
const RIPPLE_MS = 1500;
const LOOK_TAU_S = 0.7; // how quickly colors and energy follow a state change

export default function CrumbOrb({
  mode,
  pulseKey = null,
  palette,
  level = 0,
  className = "",
  still = false,
  label,
}: CrumbOrbProps) {
  const reduce = useReducedMotion() ?? false;
  const frozen = still || reduce;
  const look = resolveLook(mode, palette);
  const colors = PALETTES[look.palette];
  const boxRef = useRef<HTMLDivElement>(null);
  const cssPx = useBoxSize(boxRef);
  const gl = useOrbCanvas({ cssPx, look, level, frozen, pulseKey });
  const glShown = gl.status === "ready";
  const animated = !frozen;

  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };

  return (
    <div ref={boxRef} className={`relative h-full w-full ${className}`} {...a11y}>
      {cssPx >= 120 && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-[24%] bottom-[-1%] h-[9%] rounded-full transition-[background] duration-1000"
          style={{
            background: `radial-gradient(closest-side, ${rgba(colors.deep, 0.26)}, transparent)`,
            filter: `blur(${Math.round(cssPx * 0.035)}px)`,
          }}
        />
      )}
      <div className="absolute inset-0" style={animated && cssPx >= 48 ? { animation: "orb-float 9s ease-in-out infinite" } : undefined}>
        <CssOrb
          colors={colors}
          look={look}
          animated={animated && !glShown}
          grain={cssPx >= 28}
          className={`transition-opacity duration-500 ${glShown ? "opacity-0" : "opacity-100"}`}
        />
        {gl.wanted && (
          <canvas
            key={gl.canvasKey}
            ref={gl.canvasRef}
            aria-hidden="true"
            className={`absolute inset-0 h-full w-full transition-opacity duration-500 ${glShown ? "opacity-100" : "opacity-0"}`}
          />
        )}
        <AnimatePresence>
          {pulseKey && animated && cssPx >= 48 && (
            <motion.div
              key={pulseKey}
              aria-hidden="true"
              className="pointer-events-none absolute inset-[5%] rounded-full"
              style={{
                border: `2px solid ${rgba(colors.main, 0.45)}`,
                boxShadow: `0 0 ${Math.round(cssPx * 0.12)}px ${rgba(colors.soft, 0.35)}`,
              }}
              initial={{ opacity: 0.85, scale: 0.98 }}
              animate={{ opacity: 0, scale: 1.4 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 1.6, ease: [0.22, 1, 0.36, 1] }}
            />
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

// ---- Sizing ---------------------------------------------------------------------------------

function useBoxSize(ref: RefObject<HTMLDivElement | null>): number {
  const [size, setSize] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = (w: number, h: number) => setSize(Math.round(Math.min(w, h)));
    measure(el.offsetWidth, el.offsetHeight); // layout size, ignoring transforms (layout animations, float)
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) measure(box.width, box.height);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

// ---- WebGL path ------------------------------------------------------------------------------

interface AnimState {
  deep: Rgb;
  main: Rgb;
  soft: Rgb;
  light: Rgb;
  rim: Rgb;
  speed: number;
  breath: number;
  breathHz: number;
  warp: number;
  glint: number;
  saturation: number;
  brightness: number;
  grain: number;
  level: number;
  flow: number;
  phase: number;
}

function targetState(look: OrbLook, level: number): Omit<AnimState, "flow" | "phase"> {
  return { ...paletteRgb(look), ...look, level };
}

function lerp(a: number, b: number, k: number): number {
  return a + (b - a) * k;
}

function lerpRgb(a: Rgb, b: Rgb, k: number): Rgb {
  return [lerp(a[0], b[0], k), lerp(a[1], b[1], k), lerp(a[2], b[2], k)];
}

function stepState(s: AnimState, look: OrbLook, level: number, dtS: number, snap: boolean): void {
  const t = targetState(look, level);
  const k = snap ? 1 : approach(dtS, LOOK_TAU_S);
  s.deep = lerpRgb(s.deep, t.deep, k);
  s.main = lerpRgb(s.main, t.main, k);
  s.soft = lerpRgb(s.soft, t.soft, k);
  s.light = lerpRgb(s.light, t.light, k);
  s.rim = lerpRgb(s.rim, t.rim, k);
  s.speed = lerp(s.speed, t.speed, k);
  s.breath = lerp(s.breath, t.breath, k);
  s.breathHz = lerp(s.breathHz, t.breathHz, k);
  s.warp = lerp(s.warp, t.warp, k);
  s.glint = lerp(s.glint, t.glint, k);
  s.saturation = lerp(s.saturation, t.saturation, k);
  s.brightness = lerp(s.brightness, t.brightness, k);
  s.grain = lerp(s.grain, t.grain, k);
  s.level = lerp(s.level, t.level, snap ? 1 : approach(dtS, 0.12));
  if (snap) return;
  s.flow += dtS * (s.speed + s.level * 0.6);
  s.phase += dtS * 2 * Math.PI * s.breathHz;
}

function frameOf(s: AnimState, ripple: number | null, grainSeed: number, seed: number): OrbFrame {
  const scale = 1 + s.breath * Math.sin(s.phase) + s.level * 0.03;
  const boost = ripple === null ? 1 : 1 + 0.6 * (1 - ripple) * (1 - ripple);
  return {
    deep: s.deep,
    main: s.main,
    soft: s.soft,
    light: s.light,
    rim: s.rim,
    flow: s.flow,
    scale,
    warp: s.warp,
    glint: s.glint * boost,
    saturation: s.saturation,
    brightness: s.brightness * (ripple === null ? 1 : 1 + 0.05 * (1 - ripple)),
    grain: s.grain,
    grainSeed,
    ripple: ripple ?? 0,
    seed,
  };
}

function hashString(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10_000) / 1000;
}

interface OrbCanvasInput {
  cssPx: number;
  look: OrbLook;
  level: number;
  frozen: boolean;
  pulseKey: string | null;
}

interface OrbCanvas {
  canvasRef: RefObject<HTMLCanvasElement | null>;
  wanted: boolean;
  status: "pending" | "ready" | "failed";
  /** A canvas holds one kind of context for life: switching still <-> animated remounts it. */
  canvasKey: "still" | "live";
}

function useOrbCanvas({ cssPx, look, level, frozen, pulseKey }: OrbCanvasInput): OrbCanvas {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState<OrbCanvas["status"]>("pending");
  const wanted = cssPx >= MIN_GL_PX && status !== "failed";
  const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio;
  // Quantize so a layout animation does not resize the backing store every few pixels.
  const backing = Math.ceil(backingSize(cssPx, dpr) / 8) * 8;
  const seed = hashString(useId());

  const lookRef = useRef(look);
  lookRef.current = look;
  const levelRef = useRef(level);
  levelRef.current = Number.isFinite(level) ? Math.min(1, Math.max(0, level)) : 0;
  const backingRef = useRef(backing);
  backingRef.current = backing;
  const stateRef = useRef<AnimState | null>(null);
  const rippleRef = useRef<number | null>(null);
  const lastPulse = useRef<string | null>(null);
  const redrawRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (pulseKey && pulseKey !== lastPulse.current && !frozen) rippleRef.current = performance.now();
    lastPulse.current = pulseKey;
  }, [pulseKey, frozen]);

  const ready = backing > 0;

  // Lifecycle: one effect per canvas element. A resize never rebuilds the context (no shader
  // recompile, so no hitch when the orb shrinks as a crumb lands); the draw reads the latest size.
  // A layout effect so a freshly mounted canvas has its first frame before the browser paints.
  useLayoutEffect(() => {
    if (!wanted || !ready) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Animated: a context of its own that draws straight into the canvas. Still: the shared one.
    let renderer: OrbRenderer | null = null;
    let ctx: CanvasRenderingContext2D | null = null;
    let shared = false;
    if (!frozen) renderer = createDedicatedOrbRenderer(canvas);
    if (!renderer) {
      // Still orbs, or no context left for this one: draw with the shared context and copy.
      ctx = canvas.getContext("2d");
      renderer = ctx ? acquireOrbRenderer() : null;
      shared = renderer !== null;
    }
    if (!renderer) {
      setStatus("failed");
      return;
    }
    const gl = renderer;

    if (!stateRef.current) {
      stateRef.current = { ...targetState(lookRef.current, levelRef.current), flow: seed * 3, phase: 0 };
    }
    const state = stateRef.current;
    let drawn = false;
    const draw = (now: number, dtS: number, snap: boolean) => {
      const size = backingRef.current;
      if (ctx && (canvas.width !== size || canvas.height !== size)) {
        canvas.width = size;
        canvas.height = size;
      }
      stepState(state, lookRef.current, levelRef.current, dtS, snap);
      const ripple = frozen ? null : rippleProgress(rippleRef.current, now, RIPPLE_MS);
      const grainSeed = frozen ? 1 : Math.floor(now / 90) % 997;
      if (gl.draw(frameOf(state, ripple, grainSeed, seed), size, ctx) && !drawn) {
        drawn = true;
        setStatus("ready");
      }
    };

    if (frozen) {
      redrawRef.current = () => draw(performance.now(), 0, true);
      redrawRef.current();
      return () => {
        redrawRef.current = null;
        if (shared) releaseOrbRenderer();
        else gl.dispose();
      };
    }

    let raf = 0;
    let last = performance.now();
    let lastDraw = 0;
    let onScreen = true;
    const tick = (now: number) => {
      raf = window.requestAnimationFrame(tick);
      const calm = state.speed < 0.5 && rippleProgress(rippleRef.current, now, RIPPLE_MS) === null;
      if (calm && now - lastDraw < 32) return; // slow states run at ~30 fps
      const dtS = Math.min(0.1, (now - last) / 1000);
      last = now;
      lastDraw = now;
      draw(now, dtS, false);
    };
    const start = () => {
      if (raf || !onScreen || document.hidden) return;
      last = performance.now();
      raf = window.requestAnimationFrame(tick);
    };
    const stop = () => {
      if (raf) window.cancelAnimationFrame(raf);
      raf = 0;
    };
    const onVisibility = () => (document.hidden ? stop() : start());
    document.addEventListener("visibilitychange", onVisibility);
    let io: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== "undefined") {
      io = new IntersectionObserver((entries) => {
        onScreen = entries.some((e) => e.isIntersecting);
        if (onScreen) start();
        else stop();
      });
      io.observe(canvas);
    }
    draw(performance.now(), 0, false);
    start();

    return () => {
      stop();
      io?.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      if (shared) releaseOrbRenderer();
      else gl.dispose();
    };
  }, [wanted, ready, frozen, seed]);

  // A still orb redraws only when what it shows (or its size) changes.
  const stillKey = frozen ? `${look.palette}:${look.saturation}:${look.brightness}:${look.glint}:${backing}` : "";
  useEffect(() => {
    if (stillKey) redrawRef.current?.();
  }, [stillKey]);

  return { canvasRef, wanted, status, canvasKey: frozen ? "still" : "live" };
}

// ---- CSS path (small orbs, no WebGL, first frame) -------------------------------------------

const GRAIN_SVG = encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='g'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='100%' height='100%' filter='url(#g)'/></svg>",
);
const GRAIN_BG = `url("data:image/svg+xml;charset=utf-8,${GRAIN_SVG}")`;

function cloudLayers(c: PaletteColors): string {
  return [
    `radial-gradient(circle at 72% 78%, ${rgba(c.deep, 0.92)} 0%, transparent 55%)`,
    `radial-gradient(circle at 22% 68%, ${rgba(c.soft, 0.95)} 0%, transparent 50%)`,
    `radial-gradient(circle at 66% 28%, ${rgba(c.main, 1)} 0%, transparent 60%)`,
    `radial-gradient(circle at 30% 30%, ${c.light} 0%, ${c.main} 58%, ${c.deep} 100%)`,
  ].join(", ");
}

function shadingLayers(c: PaletteColors): string {
  return [
    // the white specular tip, upper left
    "radial-gradient(circle at 33% 27%, rgba(255,255,255,0.98) 0%, rgba(255,255,255,0.62) 7%, rgba(255,255,255,0) 23%)",
    // soft limb tinted by the rim color
    `radial-gradient(circle at 50% 50%, transparent 60%, ${rgba(c.rim, 0.5)} 100%)`,
    // gentle 3D shading away from the light
    `radial-gradient(circle at 34% 30%, transparent 42%, ${rgba(c.deep, 0.34)} 100%)`,
  ].join(", ");
}

interface CssOrbProps {
  colors: PaletteColors;
  look: OrbLook;
  animated: boolean;
  grain: boolean;
  className?: string;
}

function CssOrb({ colors, look, animated, grain, className = "" }: CssOrbProps) {
  const breathe: CSSProperties | undefined =
    animated && look.breath > 0
      ? ({
          "--orb-breath": String(look.breath),
          animation: `orb-breathe ${(1 / look.breathHz).toFixed(2)}s ease-in-out infinite`,
        } as CSSProperties)
      : undefined;
  const driftS = Math.round(60 / Math.max(0.2, look.speed));
  const tone: CSSProperties = { filter: `saturate(${look.saturation}) brightness(${look.brightness})` };
  return (
    <div aria-hidden="true" className={`absolute inset-[5%] ${className}`} style={{ ...tone, ...breathe }}>
      <div className="absolute inset-0 overflow-hidden rounded-full" style={{ isolation: "isolate" }}>
        <div
          className="absolute -inset-[12%] transition-[background] duration-1000"
          style={{
            background: cloudLayers(colors),
            filter: "blur(1px) saturate(1.05)",
            animation: animated ? `orb-drift ${driftS}s linear infinite` : undefined,
          }}
        />
        <div className="absolute inset-0 rounded-full" style={{ background: shadingLayers(colors) }} />
        {grain && (
          <div
            className="absolute inset-0 rounded-full opacity-[0.28] mix-blend-overlay"
            style={{ backgroundImage: GRAIN_BG, backgroundSize: "160px 160px" }}
          />
        )}
      </div>
    </div>
  );
}

/** A tiny static orb (crumb trail, tab icons): CSS only, white tip included. */
export function OrbDot({ palette = "ember", className = "size-2.5" }: { palette?: OrbPalette; className?: string }) {
  const c = PALETTES[palette];
  return (
    <span
      aria-hidden="true"
      className={`inline-block shrink-0 rounded-full ${className}`}
      style={{ background: `${shadingLayers(c)}, ${cloudLayers(c)}` }}
    />
  );
}

// ---- State label ----------------------------------------------------------------------------

interface OrbStatusProps {
  label: string;
  detail: string | null;
  mode?: OrbMode;
}

const MODE_DOT: Record<OrbMode, OrbPalette> = {
  idle: "ember",
  listening: "ember",
  warming: "ember",
  crumb: "ember",
  holding: "lilac",
  paused: "sage",
  safety: "cocoa",
};

/** The quiet pill under the orb, so no state is carried by color alone (PRD 14.3). */
export function OrbStatus({ label, detail, mode }: OrbStatusProps) {
  return (
    <div className="inline-flex min-h-10 max-w-[92vw] items-center gap-2.5 rounded-full bg-bg-raised py-2 pl-3 pr-4 text-[15px] font-medium text-ink shadow-pill">
      {mode && <OrbDot palette={MODE_DOT[mode]} className="size-3.5" />}
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={label}
          className="whitespace-nowrap"
          initial={{ opacity: 0, y: 3 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -3 }}
          transition={{ duration: 0.2, ease: "easeOut" }}
        >
          {label}
        </motion.span>
      </AnimatePresence>
      {detail && (
        <>
          <span className="h-4 w-px bg-line" aria-hidden="true" />
          <span className="truncate text-[13px] font-normal text-ink-muted">{detail}</span>
        </>
      )}
    </div>
  );
}
