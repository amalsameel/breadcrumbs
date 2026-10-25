// The Table screen (PRD 6.1, 6.2, 6.5 to 6.7, 14.3): lobby QR codes, calibration, the live orb,
// controls, the crumb trail, the X-ray drawer and the end of the session.
import { AnimatePresence } from "framer-motion";
import { useCallback, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import Controls from "../components/Controls";
import CrumbTrail from "../components/CrumbTrail";
import XRay from "../components/XRay";
import Calibration from "../components/table/Calibration";
import {
  EndedScreen,
  LoadingScreen,
  NotFoundScreen,
  UnreachableScreen,
  WipedScreen,
} from "../components/table/EndScreens";
import { seatNames } from "../components/table/format";
import { useShortcuts } from "../components/table/hooks";
import LiveStage from "../components/table/LiveStage";
import Lobby from "../components/table/Lobby";
import { Backdrop } from "../components/table/primitives";
import TopBar from "../components/table/TopBar";
import { useTable } from "../lib/useTable";

export default function Table() {
  const { sid = "" } = useParams();
  const [view, actions] = useTable(sid);
  const [xrayOpen, setXrayOpen] = useState(false);
  const names = useMemo(() => seatNames(view.session), [view.session]);

  const phase = view.session?.phase ?? null;
  const live = phase === "live" && !view.wiped;
  const xrayAvailable = (phase === "calibrating" || phase === "live") && !view.wiped;
  const toggleXray = useCallback(() => setXrayOpen((o) => !o), []);

  useShortcuts({
    spark: live ? actions.spark : undefined,
    hold: live ? actions.toggleHold : undefined,
    pause: live ? actions.togglePause : undefined,
    xray: xrayAvailable ? toggleXray : undefined,
    escape: xrayOpen ? () => setXrayOpen(false) : undefined,
  });

  const body = (() => {
    if (view.load === "not_found") return <NotFoundScreen />;
    if (view.wiped) return <WipedScreen />;
    if (!view.session) {
      return view.load === "error" ? <UnreachableScreen message={view.loadError} /> : <LoadingScreen />;
    }
    switch (view.session.phase) {
      case "lobby":
      case "profiles":
        return (
          <Lobby session={view.session} joinUrls={view.joinUrls} mic={view.mic} onStartListening={actions.startListening} />
        );
      case "calibrating":
        return <Calibration view={view} names={names} onStartListening={actions.startListening} />;
      case "live":
        return <LiveStage view={view} names={names} onStartListening={actions.startListening} />;
      case "ended":
        return <EndedScreen crumbCount={view.crumbs.length} onWipe={actions.wipe} />;
    }
  })();

  const showDrawer = xrayOpen && xrayAvailable;

  return (
    <div className="relative flex min-h-dvh flex-col overflow-hidden">
      <Backdrop />
      <div
        className={`flex min-h-dvh flex-1 flex-col transition-[padding] duration-300 ease-out ${showDrawer ? "xl:pr-[440px]" : ""}`}
      >
        <TopBar
          mode={view.session?.mode ?? null}
          socket={view.socket}
          audioLink={view.audioLink}
          mic={view.mic}
          micPaused={view.listeningPaused}
          showAudio={phase === "profiles" || phase === "calibrating" || phase === "live"}
          roundRemainingS={live ? (view.engine?.round_remaining_s ?? null) : null}
          mock={Boolean(view.session?.mock.muse)}
          showConnection={view.load !== "not_found" && !view.wiped}
        />

        <main className="flex flex-1 flex-col">{body}</main>

        {live && (
          <footer className="relative z-10 flex flex-col gap-3 px-6 pb-6 sm:px-8">
            <div className="flex justify-center">
              <CrumbTrail crumbs={view.crumbs} />
            </div>
            <Controls
              holding={Boolean(view.engine?.hold)}
              paused={view.listeningPaused}
              ending={view.ending}
              xrayOpen={showDrawer}
              onSpark={actions.spark}
              onSkip={actions.skip}
              onHold={actions.toggleHold}
              onPause={actions.togglePause}
              onEnd={() => void actions.end()}
              onToggleXray={toggleXray}
            />
          </footer>
        )}

        {phase === "calibrating" && !view.wiped && (
          <footer className="flex justify-end px-6 pb-5 sm:px-8">
            <button
              type="button"
              onClick={toggleXray}
              aria-pressed={showDrawer}
              aria-controls="xray-drawer"
              className="min-h-9 cursor-pointer rounded-lg px-3 text-xs text-ink-faint transition-colors hover:bg-bg-raised/60 hover:text-ink"
            >
              X-ray (X)
            </button>
          </footer>
        )}
      </div>

      <AnimatePresence>
        {showDrawer && <XRay view={view} actions={actions} names={names} onClose={() => setXrayOpen(false)} />}
      </AnimatePresence>

      {view.serverError && live && (
        <p role="alert" className="pointer-events-none fixed left-1/2 top-16 z-30 -translate-x-1/2 rounded-full bg-bg-raised px-4 py-1.5 text-xs text-ink-muted">
          {view.serverError}
        </p>
      )}
    </div>
  );
}
