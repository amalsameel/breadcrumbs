// Live conversation (PRD 6.6, 6.7): the orb, its state label, the current crumb and the hints.
import { AnimatePresence, motion } from "framer-motion";
import type { Seat } from "../../lib/types";
import type { TableView } from "../../lib/useTable";
import CrumbOrb, { OrbStatus } from "../CrumbOrb";
import CrumbCaption from "./CrumbCaption";
import { deriveOrb } from "./format";
import { useNow } from "./hooks";
import { isSpeaking, visibleCrumb } from "./tableState";
import { Icon } from "./Icon";

interface LiveStageProps {
  view: TableView;
  names: Record<Seat, string>;
}

export default function LiveStage({ view, names }: LiveStageProps) {
  const now = useNow(500);
  const crumb = visibleCrumb(view.crumbs, now);
  const speaking = crumb ? isSpeaking(crumb, now) : false;
  const orb = deriveOrb(view.engine, speaking);
  const showQuietHint = Boolean(view.engine?.quiet_hint) && !crumb && orb.mode !== "paused";
  const compact = crumb !== null;

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6">
      <motion.div
        layout
        className={compact ? "size-[min(20vh,190px)]" : "size-[min(40vh,340px)]"}
        transition={{ type: "spring", stiffness: 120, damping: 22 }}
      >
        <CrumbOrb mode={orb.mode} pulseKey={speaking ? (crumb?.id ?? null) : null} />
      </motion.div>

      <motion.div layout className="mt-8">
        <OrbStatus label={orb.label} detail={orb.detail} />
      </motion.div>

      <div className="mt-6 flex min-h-[11rem] w-full items-start justify-center">
        <AnimatePresence mode="wait">
          {crumb ? (
            <CrumbCaption
              key={crumb.id}
              id={crumb.id}
              text={crumb.text}
              durationS={crumb.durationS}
              started={crumb.startedAt !== null || crumb.status !== "pending"}
              dimmed={crumb.status === "interrupted"}
              crumb={crumb.crumb}
              names={names}
            />
          ) : showQuietHint ? (
            <motion.p
              key="quiet"
              className="flex items-center gap-2 text-base text-ink-muted"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.8 }}
            >
              <Icon name="spark" size={18} className="text-amber" />
              Quiet for now. Drop a crumb whenever you want one.
            </motion.p>
          ) : orb.mode === "safety" ? (
            <motion.p
              key="safety"
              className="max-w-md text-center text-base text-ink-muted"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              Breadcrumbs stepped back and will stay quiet. Drop a crumb if you want one.
            </motion.p>
          ) : null}
        </AnimatePresence>
      </div>

      {/* Crumbs are always captioned for screen readers too (PRD 14.3). */}
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {crumb ? crumb.text : ""}
      </p>
    </div>
  );
}
