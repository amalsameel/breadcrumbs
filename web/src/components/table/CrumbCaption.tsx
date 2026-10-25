// The big crumb caption (PRD 14.3, issue 2): the whole line lands at once in one quick blur-to-sharp
// motion (a ~20 ms hint of left-to-right flow), then simply stays readable. How long it stays is
// decided by the caller (captionStage in format.ts), never by the audio pacing.
// The slot that hosts it must not use AnimatePresence mode="wait": an arriving crumb never waits for
// the previous line to leave. The slot is a one-cell grid instead (CAPTION_SLOT and CAPTION_SLOT_ITEM
// in format.ts), so the leaving and arriving lines overlap and crossfade.
import { motion, useReducedMotion } from "framer-motion";
import type { Crumb, Seat } from "../../lib/types";
import { CAPTION_ENTER_S, CAPTION_EXIT_S, captionStaggerS, sourceCaption, wordsOf } from "./format";

interface CrumbCaptionProps {
  id: string;
  text: string;
  /** The voice was cut short: the text stays, in a slightly softer ink. */
  dimmed?: boolean;
  crumb?: Crumb;
  names?: Record<Seat, string>;
  /** A quick table (no profiles): the source line says what someone said, never "profile". */
  quick?: boolean;
  /** Smaller type for calibration prompts. */
  size?: "hero" | "prompt";
  /** Placement from the caption slot (CAPTION_SLOT_ITEM stacks it over a leaving line). */
  className?: string;
}

const EASE_OUT: [number, number, number, number] = [0.22, 1, 0.36, 1];

export default function CrumbCaption({
  id,
  text,
  dimmed = false,
  crumb,
  names,
  quick = false,
  size = "hero",
  className = "",
}: CrumbCaptionProps) {
  const reduce = useReducedMotion() ?? false;
  const words = wordsOf(text);
  const stagger = captionStaggerS(words.length);
  const type =
    size === "hero"
      ? "text-[clamp(30px,min(3.6vw,6.4vh),60px)] leading-[1.12]"
      : "text-[clamp(26px,min(2.8vw,5vh),44px)] leading-[1.18]";

  return (
    <motion.figure
      key={id}
      className={`mx-auto flex w-full max-w-[min(1100px,92vw)] flex-col items-center gap-5 text-center ${className}`}
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{
        opacity: 0,
        ...(reduce ? {} : { y: -6, filter: "blur(4px)" }),
        transition: { duration: CAPTION_EXIT_S, ease: "easeIn" },
      }}
      transition={{ duration: CAPTION_ENTER_S, ease: EASE_OUT }}
    >
      <blockquote
        className={`font-display font-normal tracking-[-0.03em] text-balance transition-colors duration-500 ${type} ${
          dimmed ? "text-ink/75" : "text-ink"
        }`}
        aria-hidden="true"
      >
        {words.map((word, i) => (
          <motion.span
            key={`${i}-${word}`}
            className="inline-block whitespace-pre"
            initial={reduce ? false : { opacity: 0, filter: "blur(8px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            transition={{ delay: reduce ? 0 : i * stagger, duration: CAPTION_ENTER_S, ease: EASE_OUT }}
          >
            {i < words.length - 1 ? `${word} ` : word}
          </motion.span>
        ))}
      </blockquote>
      {crumb && names && <SourceLine crumb={crumb} names={names} quick={quick} delayS={reduce ? 0 : 0.18} />}
    </motion.figure>
  );
}

interface SourceLineProps {
  crumb: Crumb;
  names: Record<Seat, string>;
  quick: boolean;
  delayS: number;
}

function SourceLine({ crumb, names, quick, delayS }: SourceLineProps) {
  const caption = sourceCaption(crumb, names, quick);
  return (
    <motion.figcaption
      className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-[15px] text-ink-muted"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: delayS, duration: 0.35 }}
    >
      <span className="size-1.5 rounded-full bg-amber" aria-hidden="true" />
      <span>{caption}</span>
      {crumb.source === "outside" && crumb.citation_url && (
        <a
          href={crumb.citation_url}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex min-h-8 items-center gap-1 rounded-full px-1 text-ink underline decoration-line-strong underline-offset-4 transition-colors hover:decoration-ink"
        >
          {crumb.citation_title || "source"}
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      )}
    </motion.figcaption>
  );
}
