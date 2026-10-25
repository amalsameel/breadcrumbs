// Large crumb caption that fades in word by word, paced to the spoken audio (PRD 14.3).
import { motion, useReducedMotion } from "framer-motion";
import type { Crumb, Seat } from "../../lib/types";
import { sourceCaption, wordsOf } from "./format";

interface CrumbCaptionProps {
  id: string;
  text: string;
  durationS: number;
  started: boolean;
  dimmed?: boolean;
  crumb?: Crumb;
  names?: Record<Seat, string>;
}

const MAX_WORD_STEP_S = 0.55;

export default function CrumbCaption({ id, text, durationS, started, dimmed = false, crumb, names }: CrumbCaptionProps) {
  const reduce = useReducedMotion() ?? false;
  const words = wordsOf(text);
  // Finish the reveal slightly before the voice does so the eye never trails the ear.
  const step = Math.min(MAX_WORD_STEP_S, (durationS * 0.85) / Math.max(1, words.length));

  return (
    <motion.figure
      key={id}
      className="mx-auto flex max-w-[min(1120px,90vw)] flex-col items-center gap-5 text-center"
      initial={{ opacity: 0 }}
      animate={{ opacity: dimmed ? 0.45 : 1 }}
      exit={{ opacity: 0, y: -8, transition: { duration: 0.5 } }}
      transition={{ duration: 0.4 }}
    >
      <blockquote
        className="font-display text-[clamp(40px,3.5vw,56px)] font-normal leading-[1.18] tracking-[-0.01em] text-ink text-balance"
        aria-hidden="true"
      >
        {words.map((word, i) => (
          <motion.span
            key={`${i}-${word}`}
            className="inline-block whitespace-pre"
            initial={reduce ? false : { opacity: 0, y: 10, filter: "blur(6px)" }}
            animate={started || reduce ? { opacity: 1, y: 0, filter: "blur(0px)" } : undefined}
            transition={{ delay: reduce ? 0 : i * step, duration: 0.45, ease: "easeOut" }}
          >
            {i < words.length - 1 ? `${word} ` : word}
          </motion.span>
        ))}
      </blockquote>
      {crumb && names && <SourceLine crumb={crumb} names={names} visible={started || reduce} delayS={words.length * step} />}
    </motion.figure>
  );
}

function SourceLine({
  crumb,
  names,
  visible,
  delayS,
}: {
  crumb: Crumb;
  names: Record<Seat, string>;
  visible: boolean;
  delayS: number;
}) {
  const caption = sourceCaption(crumb, names);
  return (
    <motion.figcaption
      className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-sm tracking-wide text-ink-faint"
      initial={{ opacity: 0 }}
      animate={visible ? { opacity: 1 } : { opacity: 0 }}
      transition={{ delay: Math.min(delayS, 4), duration: 0.6 }}
    >
      <span className="size-1.5 rounded-full bg-crumb/70" aria-hidden="true" />
      <span>{caption}</span>
      {crumb.source === "outside" && crumb.citation_url && (
        <a
          href={crumb.citation_url}
          target="_blank"
          rel="noreferrer noopener"
          className="rounded text-ink-muted underline decoration-crumb/50 underline-offset-4 transition-colors hover:text-crumb"
        >
          {crumb.citation_title || "source"}
        </a>
      )}
    </motion.figcaption>
  );
}
