// Live Table controls (PRD 6.6): a pill track of toggles on the left, the black "Drop a crumb" pill on
// the right, large touch targets, labels and key hints always visible.
// End uses an inline two-step confirm (no browser dialogs).
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { SegmentTrack, TableButton } from "./table/primitives";

interface ControlsProps {
  holding: boolean;
  paused: boolean;
  ending: boolean;
  xrayOpen: boolean;
  onSpark(): void;
  onSkip(): void;
  onHold(): void;
  onPause(): void;
  onEnd(): void;
  onToggleXray(): void;
}

const CONFIRM_MS = 5000;

export default function Controls(props: ControlsProps) {
  const { holding, paused, ending, xrayOpen } = props;
  return (
    <div className="flex w-full flex-wrap items-center justify-between gap-3">
      <SegmentTrack label="Session controls" className="w-full justify-center sm:w-auto">
        <TableButton
          tone="segment"
          size="md"
          icon={holding ? "play" : "hold"}
          shortcut="H"
          onClick={props.onHold}
          aria-pressed={holding}
        >
          {holding ? "Resume" : "Hold 60 s"}
        </TableButton>
        <TableButton
          tone="segment"
          size="md"
          icon={paused ? "mic" : "micOff"}
          shortcut="P"
          onClick={props.onPause}
          aria-pressed={paused}
        >
          {/* Short label on phones; the accessible name always starts with the visible text. */}
          <span className="sm:hidden">{paused ? "Resume" : "Pause"}</span>
          <span className="hidden sm:inline">{paused ? "Resume listening" : "Pause listening"}</span>
        </TableButton>
        <TableButton
          tone="segment"
          size="md"
          icon="xray"
          shortcut="X"
          onClick={props.onToggleXray}
          aria-pressed={xrayOpen}
          aria-controls="xray-drawer"
        >
          X-ray
        </TableButton>
      </SegmentTrack>
      <div
        className="order-first flex w-full flex-wrap items-center justify-end gap-2.5 sm:order-none sm:w-auto sm:flex-nowrap"
        role="group"
        aria-label="Crumb controls"
      >
        <EndButton ending={ending} onEnd={props.onEnd} />
        <TableButton size="md" icon="skip" onClick={props.onSkip} className="flex-1 sm:flex-none">
          Skip
        </TableButton>
        <TableButton tone="primary" icon="spark" shortcut="Space" onClick={props.onSpark} className="order-first w-full sm:order-none sm:w-auto">
          Drop a crumb
        </TableButton>
      </div>
    </div>
  );
}

function EndButton({ ending, onEnd }: { ending: boolean; onEnd(): void }) {
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!confirming) return;
    const timer = window.setTimeout(() => setConfirming(false), CONFIRM_MS);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setConfirming(false);
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [confirming]);

  return (
    <AnimatePresence mode="wait" initial={false}>
      {confirming ? (
        <motion.div
          key="confirm"
          className="flex items-center gap-2"
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.96 }}
          transition={{ duration: 0.15 }}
        >
          <TableButton
            tone="danger"
            size="md"
            icon="end"
            disabled={ending}
            onClick={() => {
              setConfirming(false);
              onEnd();
            }}
          >
            {ending ? "Ending" : "Yes, end"}
          </TableButton>
          <span className="sr-only" role="status">
            End the session for both of you?
          </span>
          <TableButton tone="ghost" size="md" autoFocus onClick={() => setConfirming(false)}>
            Keep talking
          </TableButton>
        </motion.div>
      ) : (
        <motion.div
          key="end"
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.96 }}
          transition={{ duration: 0.15 }}
        >
          <TableButton size="md" icon="end" disabled={ending} onClick={() => setConfirming(true)}>
            End
          </TableButton>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
