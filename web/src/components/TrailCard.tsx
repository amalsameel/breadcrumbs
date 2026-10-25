// The trail (PRD 6.8, F17): up to 3 threads to pick up next time, as clean numbered cards.
import { motion, useReducedMotion } from "framer-motion";
import type { TrailItem } from "../lib/types";
import type { OrbPalette } from "./CrumbOrb";
import { OrbDot } from "./ui/Crumbs";

interface TrailCardProps {
  items: TrailItem[];
  title?: string;
}

const DOTS: OrbPalette[] = ["ember", "lilac", "citrus"];

export function TrailCard({ items, title = "Threads to pick up next time" }: TrailCardProps) {
  const reduce = useReducedMotion();
  if (items.length === 0) return null;
  return (
    <section aria-labelledby="trail-title" className="flex flex-col gap-3">
      <h2 id="trail-title" className="font-display text-[24px] font-normal tracking-[-0.02em] text-ink">
        {title}
      </h2>
      <ol className="flex flex-col gap-2.5">
        {items.map((item, i) => (
          <motion.li
            key={`${i}-${item.text}`}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.12 + i * 0.12, duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
            className="flex gap-4 rounded-3xl border border-line bg-bg-raised p-5"
          >
            <span className="flex shrink-0 flex-col items-center gap-2 pt-0.5">
              <OrbDot palette={DOTS[i % DOTS.length]} size={22} />
              <span className="font-mono text-[12px] text-ink-muted" aria-hidden="true">
                {String(i + 1).padStart(2, "0")}
              </span>
            </span>
            <p className="text-[17px] leading-[1.45] text-ink">{item.text}</p>
          </motion.li>
        ))}
      </ol>
    </section>
  );
}
