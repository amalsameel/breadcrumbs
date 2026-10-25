// The trail of crumbs (PRD 14.3): one tiny orb per crumb along the bottom edge.
// Tapping a dot shows that crumb's text; interrupted crumbs are drawn fainter and say so.
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import type { CrumbRecord } from "../lib/useTable";
import { OrbDot } from "./CrumbOrb";

const MAX_DOTS = 24;
const PEEK_MS = 9000;

export default function CrumbTrail({ crumbs }: { crumbs: CrumbRecord[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const shown = crumbs.slice(-MAX_DOTS);
  const hidden = crumbs.length - shown.length;
  const open = shown.find((c) => c.id === openId) ?? null;

  useEffect(() => {
    if (!openId) return;
    const timer = window.setTimeout(() => setOpenId(null), PEEK_MS);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpenId(null);
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [openId]);

  if (crumbs.length === 0) return <div className="h-8" aria-hidden="true" />;

  return (
    <nav aria-label="Crumbs dropped so far" className="relative flex flex-col items-center">
      <AnimatePresence>
        {open && (
          <motion.div
            key={open.id}
            role="status"
            className="absolute bottom-full z-20 mb-3 w-[min(440px,86vw)] rounded-[20px] border border-line bg-bg-raised px-5 py-4 text-center shadow-card"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.2 }}
          >
            <p className="font-display text-[19px] leading-snug tracking-[-0.015em] text-ink">{open.text}</p>
            <p className="mt-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-muted">
              {open.crumb.kind}
              {open.status === "interrupted" ? " · interrupted" : ""}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
      <ol className="flex items-center gap-0.5">
        {hidden > 0 && <li className="px-1 font-mono text-[12px] text-ink-muted">+{hidden}</li>}
        {shown.map((c, i) => {
          const dim = c.status === "interrupted";
          const isOpen = c.id === openId;
          return (
            <li key={c.id}>
              <button
                type="button"
                className="group grid size-8 cursor-pointer place-items-center rounded-full"
                aria-label={`Crumb ${hidden + i + 1}${dim ? ", interrupted" : ""}: show text`}
                aria-pressed={isOpen}
                onClick={(e) => {
                  if (e.detail > 0) e.currentTarget.blur();
                  setOpenId(isOpen ? null : c.id);
                }}
              >
                <span
                  className="block rounded-full transition-transform duration-200 group-hover:scale-125"
                  style={{ outline: isOpen ? "1.5px solid var(--color-ink)" : "none", outlineOffset: 3 }}
                >
                  <motion.span
                    className="block"
                    initial={{ scale: 0, opacity: 0 }}
                    animate={{ scale: 1, opacity: dim ? 0.4 : 1 }}
                    transition={{ type: "spring", stiffness: 260, damping: 18 }}
                  >
                    <OrbDot palette="ember" className="block size-3" />
                  </motion.span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
