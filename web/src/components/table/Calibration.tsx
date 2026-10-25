// Calibration (PRD 6.5): each seat says hi so voice labels map to seats.
import { AnimatePresence, motion } from "framer-motion";
import type { Seat } from "../../lib/types";
import type { CalibrationStep, TableView } from "../../lib/useTable";
import CrumbOrb from "../CrumbOrb";
import CrumbCaption from "./CrumbCaption";
import { StatusChip, type ChipState } from "./primitives";
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

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-8 px-6 py-6">
      <div className="size-[min(24vh,210px)]">
        <CrumbOrb mode={speaking ? "crumb" : micOn ? "listening" : "idle"} pulseKey={speaking ? (line?.id ?? null) : null} />
      </div>

      <AnimatePresence>
        {resyncing && (
          <motion.p
            role="status"
            className="rounded-full border border-hold/50 bg-hold/10 px-4 py-1.5 text-sm text-ink"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            Re-syncing voices
          </motion.p>
        )}
      </AnimatePresence>

      <div className="min-h-[9rem]" aria-live="polite">
        <AnimatePresence mode="wait">
          {line ? (
            <CrumbCaption
              key={line.id}
              id={line.id}
              text={line.text}
              durationS={line.durationS}
              started={line.startedAt !== null || line.status !== "pending"}
            />
          ) : (
            <motion.p
              key="waiting"
              className="max-w-xl text-center font-display text-[clamp(28px,2.6vw,40px)] leading-snug text-ink-muted"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              {micOn ? "Getting to know your voices." : `${names[1]} and ${names[2]} are ready. Turn on the mic to begin.`}
            </motion.p>
          )}
        </AnimatePresence>
        {line && <span className="sr-only">{line.text}</span>}
      </div>

      <ol className="flex flex-wrap justify-center gap-2" aria-label="Calibration steps">
        <li>
          <StatusChip state={s1}>{names[1]} says hi</StatusChip>
        </li>
        <li>
          <StatusChip state={s2}>{names[2]} says hi</StatusChip>
        </li>
        <li>
          <StatusChip state={done}>Voices matched</StatusChip>
        </li>
      </ol>

      {!micOn && <StartListening mic={view.mic} onStart={onStartListening} />}
      {micOn && view.audioLink === "forbidden" && (
        <p className="text-sm text-ink-faint" role="status">
          Waiting for both consents before any audio is sent.
        </p>
      )}
    </div>
  );
}
