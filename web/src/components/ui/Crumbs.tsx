import { motion, useReducedMotion } from "framer-motion";

/** Brand mark: three crumbs on a little winding trail. Decorative. */
export function CrumbMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 32 32" className={className}>
      <path
        d="M4 26c5-1 6-6 11-7s7-6 13-8"
        fill="none"
        stroke="currentColor"
        strokeOpacity="0.28"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="0.5 4"
      />
      <circle cx="6" cy="25" r="2.2" fill="#F5A524" />
      <circle cx="15.5" cy="18.8" r="2.8" fill="#FFD27A" />
      <circle cx="26" cy="11.5" r="3.4" fill="#FFD27A" />
    </svg>
  );
}

/** A single softly glowing crumb that breathes slowly (about 0.2 Hz, matching the Table orb). */
export function BreathingCrumb({ size = 120, tone = "amber" }: { size?: number; tone?: "amber" | "hold" }) {
  const reduce = useReducedMotion();
  const color = tone === "hold" ? "#5B8DEF" : "#F5A524";
  const core = tone === "hold" ? "#A9C3F7" : "#FFD27A";
  return (
    <div aria-hidden="true" className="relative grid place-items-center" style={{ width: size, height: size }}>
      <motion.div
        className="absolute inset-0 rounded-full"
        style={{ background: `radial-gradient(circle, ${color}55 0%, ${color}00 70%)` }}
        animate={reduce ? undefined : { scale: [0.85, 1.12, 0.85], opacity: [0.55, 1, 0.55] }}
        transition={{ duration: 5, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="rounded-full"
        style={{
          width: size * 0.26,
          height: size * 0.26,
          background: `radial-gradient(circle at 35% 35%, #FFF6E0 0%, ${core} 45%, ${color} 100%)`,
          boxShadow: `0 0 ${size * 0.25}px ${color}`,
        }}
        animate={reduce ? undefined : { scale: [1, 1.08, 1] }}
        transition={{ duration: 5, repeat: Infinity, ease: "easeInOut" }}
      />
    </div>
  );
}

/** Row of small crumbs used as a step indicator: filled for done, glowing for current. */
export function CrumbSteps({ total, current, label }: { total: number; current: number; label: string }) {
  return (
    <div role="img" aria-label={label} className="flex items-center gap-1.5">
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={
            "h-2 rounded-full transition-all duration-300 " +
            (i < current
              ? "w-2 bg-amber"
              : i === current
                ? "w-5 bg-crumb shadow-[0_0_10px_rgba(255,210,122,0.8)]"
                : "w-2 bg-line")
          }
        />
      ))}
    </div>
  );
}
