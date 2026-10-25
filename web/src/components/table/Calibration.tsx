// Calibration (PRD 6.5): each seat says hi so voice labels map to seats. The orb leads, the spoken
// prompt is captioned in full the moment it arrives, and the three steps sit in a pill track.
import { AnimatePresence, motion } from "framer-motion";
import type { Seat } from "../../lib/types";
import type { CalibrationStep, TableView } from "../../lib/useTable";
import CrumbOrb from "../CrumbOrb";
import CrumbCaption from "./CrumbCaption";
import {
  CAPTION_ENTER_S,
  CAPTION_EXIT_S,
  CAPTION_SLOT,
  CAPTION_SLOT_ITEM,
  micOnMessage,
  speechServiceProblem,
} from "./format";
import { SegmentTrack, StatusChip, type ChipState } from "./primitives";
import StartListening from "./StartListening";

interface CalibrationProps {
  view: TableView;
  names: Record<Seat, string>;
  onStartListening(): void;
}

function stepStates(step: CalibrationStep | null): [ChipState, ChipState, ChipState] {
  if (step === "done") return ["done", "done", "done"];
  if (step === "seat2") return ["done", "active", "pending"];
  if (step === "seat1") return ["active", "pending", "pending"];
  return ["pending", "pending", "pending"];
}

export default function Calibration({ view, names, onStartListening }: CalibrationProps) {
  const line = view.systemLine;
  const speaking = line?.status === "playing" || line?.status === "pending";
  const [s1, s2, done] = stepStates(view.calibrationStep);
  const resyncing = view.muse?.status === "resyncing";
  const micOn = view.mic.status === "on";
  const speechProblem = micOn ? speechServiceProblem(view.muse?.status, view.muse?.detail) : null;
  const mode = speaking ? "crumb" : micOn ? "listening" : "idle";
  const orbLabel = speaking ? "Speaking a prompt" : micOn ? "Listening for your voices" : "Waiting for the mic";

  return (
    <div className="mx-auto flex w-full max-w-[1460px] flex-1 flex-col items-center justify-center gap-8 px-4 py-8 sm:px-8">
      <div className="size-[min(34vh,320px,72vw)]">
        <CrumbOrb mode={mode} pulseKey={speaking ? (line?.id ?? null) : null} label={`Breadcrumbs: ${orbLabel}`} />
      </div>

      <AnimatePresence>
        {resyncing && (
          <motion.p
            key="resync"
            role="status"
            className="inline-flex min-h-9 items-center gap-2 rounded-full border border-line bg-bg-raised px-4 text-[14px] font-medium text-ink shadow-pill"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            <span className="size-2 animate-pulse rounded-full bg-hold" aria-hidden="true" />
            Re-syncing voices
          </motion.p>
        )}
        {speechProblem && (
          <motion.p
            key="problem"
            role="alert"
            className="max-w-xl rounded-2xl border border-danger/30 bg-danger/5 px-4 py-2.5 text-center text-[14px] text-ink"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            {speechProblem}
          </motion.p>
        )}
      </AnimatePresence>

      {/* No mode="wait": a spoken prompt's caption appears at once, over the line it replaces. */}
      <div className={`min-h-[8.5rem] ${CAPTION_SLOT}`} aria-live="polite">
        <AnimatePresence initial={false}>
          {line ? (
            <CrumbCaption key={line.id} id={line.id} text={line.text} size="prompt" className={CAPTION_SLOT_ITEM} />
          ) : (
            <motion.p
              key="waiting"
              className={`max-w-2xl text-center font-display text-[clamp(26px,2.6vw,40px)] font-normal leading-snug tracking-[-0.025em] text-ink text-balance ${CAPTION_SLOT_ITEM}`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, transition: { duration: CAPTION_EXIT_S } }}
              transition={{ duration: CAPTION_ENTER_S }}
            >
              {micOn ? "Getting to know your voices." : `${names[1]} and ${names[2]} are ready. Turn on the mic to begin.`}
            </motion.p>
          )}
        </AnimatePresence>
        {line && <span className="sr-only">{line.text}</span>}
      </div>

      <SegmentTrack as="ol" label="Calibration steps">
        <li>
          <StatusChip state={s1}>{names[1]} says hi</StatusChip>
        </li>
        <li>
          <StatusChip state={s2}>{names[2]} says hi</StatusChip>
        </li>
        <li>
          <StatusChip state={done}>Voices matched</StatusChip>
        </li>
      </SegmentTrack>

      {!micOn && (
        <StartListening mic={view.mic} onStart={onStartListening} onMessage={micOnMessage("calibrating", view.bothConsented)} />
      )}
      {micOn && view.audioLink === "forbidden" && (
        <p className="text-[14px] text-ink-muted" role="status">
          Waiting for both consents before any audio is sent.
        </p>
      )}
    </div>
  );
}
