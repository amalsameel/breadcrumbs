// Live conversation (PRD 6.6, 6.7): the orb as the hero, its state pill, the current crumb and hints.
// A crumb's caption appears the moment it arrives, stays while it is voiced and for a reading time
// after, then shrinks into a small "last crumb" line (captionStage in format.ts).
import { AnimatePresence, motion } from "framer-motion";
import type { ReactNode } from "react";
import type { Seat } from "../../lib/types";
import type { TableView } from "../../lib/useTable";
import CrumbOrb, { OrbStatus } from "../CrumbOrb";
import CrumbCaption from "./CrumbCaption";
import {
  CAPTION_ENTER_S,
  CAPTION_EXIT_S,
  CAPTION_SLOT,
  CAPTION_SLOT_ITEM,
  captionStage,
  deriveOrb,
  isResyncStep,
  micOnMessage,
} from "./format";
import { useNow } from "./hooks";
import { isSpeaking, recapCrumb, visibleCrumb } from "./tableState";
import { Icon } from "./Icon";
import StartListening from "./StartListening";

interface LiveStageProps {
  view: TableView;
  names: Record<Seat, string>;
  onStartListening(): void;
}

export default function LiveStage({ view, names, onStartListening }: LiveStageProps) {
  const now = useNow(250);
  // A reconnect re-maps the voices while the phase stays live: the spoken prompt is captioned here.
  const resyncing = isResyncStep(view.calibrationStep);
  const resyncLine = resyncing && view.systemLine && captionStage(view.systemLine, now) === "full" ? view.systemLine : null;
  const crumb = resyncLine ? null : visibleCrumb(view.crumbs, now);
  const recap = resyncLine || crumb ? null : recapCrumb(view.crumbs, now);
  const last = view.crumbs.at(-1) ?? null;
  const speaking = last ? isSpeaking(last, now) : false;
  const orb = deriveOrb(view.engine, speaking, resyncing);
  const showQuietHint = Boolean(view.engine?.quiet_hint) && !crumb && !resyncLine && orb.mode !== "paused";
  const showSafety = orb.mode === "safety" && !crumb && !resyncLine;
  const compact = crumb !== null || resyncLine !== null;
  // After a Table reload or a lost mic there is no capture: offer the same gesture again (PRD 14.1).
  const micOff = view.mic.status !== "on";
  const live = resyncLine ? resyncLine.text : crumb ? crumb.text : "";

  return (
    <div className="mx-auto flex w-full max-w-[1460px] flex-1 flex-col items-center justify-center px-4 py-6 sm:px-8">
      <motion.div
        layout
        className={compact ? "size-[min(26vh,230px)]" : "size-[min(46vh,400px,78vw)]"}
        transition={{ type: "spring", stiffness: 110, damping: 22 }}
      >
        <CrumbOrb mode={orb.mode} pulseKey={speaking ? (last?.id ?? null) : null} label={`Breadcrumbs: ${orb.label}`} />
      </motion.div>

      <motion.div layout="position" className="mt-7" transition={{ type: "spring", stiffness: 110, damping: 22 }}>
        <OrbStatus label={orb.label} detail={orb.detail} mode={orb.mode} />
      </motion.div>

      {/* No mode="wait": a new crumb starts its entrance the moment it arrives, over the leaving line. */}
      <div className={`mt-8 min-h-[12rem] ${CAPTION_SLOT}`}>
        <AnimatePresence initial={false}>
          {resyncLine ? (
            <CrumbCaption
              key={resyncLine.id}
              id={resyncLine.id}
              text={resyncLine.text}
              size="prompt"
              className={CAPTION_SLOT_ITEM}
            />
          ) : crumb ? (
            <CrumbCaption
              key={crumb.id}
              id={crumb.id}
              text={crumb.text}
              dimmed={crumb.status === "interrupted"}
              crumb={crumb.crumb}
              names={names}
              className={CAPTION_SLOT_ITEM}
            />
          ) : showQuietHint ? (
            <Hint key="quiet">
              <Icon name="spark" size={18} className="text-amber-ink" />
              Quiet for now. Drop a crumb whenever you want one.
            </Hint>
          ) : showSafety ? (
            <Hint key="safety">Breadcrumbs stepped back and will stay quiet. Drop a crumb if you want one.</Hint>
          ) : recap ? (
            <motion.div
              key={`recap-${recap.id}`}
              className={`flex max-w-[min(720px,92vw)] flex-col items-center gap-1.5 text-center ${CAPTION_SLOT_ITEM}`}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, transition: { duration: CAPTION_EXIT_S } }}
              transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
            >
              <span className="text-[12px] font-medium uppercase tracking-[0.16em] text-ink-muted">Last crumb</span>
              <p className="line-clamp-2 font-display text-[19px] leading-snug tracking-[-0.015em] text-ink-muted text-balance">
                {recap.text}
              </p>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>

      {micOff && (
        <div className="mt-2">
          <StartListening
            mic={view.mic}
            onStart={onStartListening}
            hint="The mic is off, so nothing is being heard. Turn it back on to keep the conversation going."
            onMessage={micOnMessage("live", view.bothConsented)}
          />
        </div>
      )}

      {/* Crumbs and voice prompts are always captioned for screen readers too (PRD 14.3). */}
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {live}
      </p>
    </div>
  );
}

function Hint({ children }: { children: ReactNode }) {
  return (
    <motion.p
      className={`flex max-w-md items-center justify-center gap-2 text-center text-[17px] text-ink-muted ${CAPTION_SLOT_ITEM}`}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, transition: { duration: CAPTION_EXIT_S } }}
      transition={{ duration: CAPTION_ENTER_S + 0.1 }}
    >
      {children}
    </motion.p>
  );
}
