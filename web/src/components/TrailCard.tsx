// The trail (PRD 6.8, F17): up to 3 threads to pick up next time, drawn as crumbs on a little path.
import { motion, useReducedMotion } from "framer-motion";
import type { TrailItem } from "../lib/types";
import { Card } from "./ui/Card";

interface TrailCardProps {
  items: TrailItem[];
  title?: string;
}

export function TrailCard({ items, title = "Threads to pick up next time" }: TrailCardProps) {
  const reduce = useReducedMotion();
  if (items.length === 0) return null;
  return (
    <Card tone="glow" as="section" aria-labelledby="trail-title">
      <h2 id="trail-title" className="mb-4 font-display text-[22px] font-semibold text-ink">
        {title}
      </h2>
      <ol className="relative flex flex-col gap-5">
        {/* the dotted path joining the crumbs */}
        <span
          aria-hidden="true"
          className="absolute top-3 bottom-3 left-[7px] border-l-2 border-dotted border-amber/40"
        />
        {items.map((item, i) => (
          <motion.li
            key={`${i}-${item.text}`}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 + i * 0.18, duration: 0.35, ease: "easeOut" }}
            className="relative flex gap-4"
          >
            <span
              aria-hidden="true"
              className="relative mt-1.5 h-4 w-4 shrink-0 rounded-full bg-crumb shadow-[0_0_12px_rgba(255,210,122,0.9)]"
            />
            <p className="text-[17px] leading-snug text-ink">{item.text}</p>
          </motion.li>
        ))}
      </ol>
    </Card>
  );
}
