// Live Table controls (PRD 6.6): bottom corners, large touch targets, labels always visible.
// End uses an inline two-step confirm (no browser dialogs).
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { TableButton } from "./table/primitives";

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
    <div className="pointer-events-none flex w-full flex-wrap items-end justify-between gap-3">
      <div className="pointer-events-auto flex flex-wrap items-center gap-3" role="group" aria-label="Crumb controls">
        <TableButton tone="primary" icon="spark" shortcut="Space" onClick={props.onSpark}>
          Drop a crumb
        </TableButton>
        <TableButton icon="skip" onClick={props.onSkip}>
          Skip
        </TableButton>
      </div>
      <div className="pointer-events-auto flex flex-wrap items-center justify-end gap-3" role="group" aria-label="Session controls">
        <TableButton icon={holding ? "play" : "hold"} shortcut="H" onClick={props.onHold} aria-pressed={holding}>
          {holding ? "Resume" : "Hold 60 s"}
        </TableButton>
        <TableButton icon={paused ? "mic" : "micOff"} shortcut="P" onClick={props.onPause} aria-pressed={paused}>
          {paused ? "Resume listening" : "Pause listening"}
        </TableButton>
        <EndButton ending={ending} onEnd={props.onEnd} />
        <TableButton
          tone="ghost"
          size="md"
          icon="xray"
          shortcut="X"
          onClick={props.onToggleXray}
          aria-pressed={xrayOpen}
          aria-controls="xray-drawer"
        >
          X-ray
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
            icon="end"
            disabled={ending}
            onClick={() => {
              setConfirming(false);
              onEnd();
            }}
          >
            {ending ? "Ending" : "Yes, end"}
          </TableButton>
          <TableButton tone="ghost" size="md" onClick={() => setConfirming(false)}>
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
          <TableButton icon="end" disabled={ending} onClick={() => setConfirming(true)}>
            End
          </TableButton>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
