// The Table screen (PRD 6.1, 6.2, 6.5 to 6.7, 14.3). Two setups:
// - "Just talk" (quick): one consent card on the laptop, then straight to the live orb, and the trail
//   on the laptop at the end (QuickSetup.tsx). No phones, no calibration screen.
// - "With profiles": lobby QR codes, calibration, the live orb and the phones' trail.
// Both share the live stage, controls, the crumb trail dots and the X-ray drawer.
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
import { Backdrop, TableButton } from "../components/table/primitives";
import { QuickConsent, QuickStarting, QuickTrail } from "../components/table/QuickSetup";
import { isQuick } from "../components/table/tableState";
import TopBar from "../components/table/TopBar";
import { useTable } from "../lib/useTable";

export default function Table() {
  const { sid = "" } = useParams();
  const [view, actions] = useTable(sid);
  const [xrayOpen, setXrayOpen] = useState(false);
  const names = useMemo(() => seatNames(view.session), [view.session]);

  const phase = view.session?.phase ?? null;
  const quick = isQuick(view.session);
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
    if (quick) {
      switch (view.session.phase) {
        case "lobby":
        case "profiles":
          return <QuickConsent onStart={actions.startQuick} />;
        case "calibrating":
          return <QuickStarting view={view} onStartListening={actions.startListening} />;
        case "live":
          return <LiveStage view={view} names={names} onStartListening={actions.startListening} />;
        case "ended":
          return <QuickTrail trail={view.tableTrail} names={names} crumbCount={view.crumbs.length} onDone={actions.wipe} />;
      }
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
    <div className="relative flex min-h-dvh flex-col overflow-x-clip bg-bg text-ink">
      <Backdrop />
      <div
        className={`flex min-h-dvh flex-1 flex-col transition-[padding] duration-300 ease-out ${showDrawer ? "xl:pr-[464px]" : ""}`}
      >
        <TopBar
          socket={view.socket}
          audioLink={view.audioLink}
          mic={view.mic}
          micPaused={view.listeningPaused}
          showAudio={phase === "profiles" || phase === "calibrating" || phase === "live"}
          roundRemainingS={live ? (view.engine?.round_remaining_s ?? null) : null}
          mock={Boolean(view.session?.mock.muse)}
          showConnection={view.load !== "not_found" && !view.wiped}
          muse={view.muse}
        />

        <main className="flex flex-1 flex-col">{body}</main>

        {live && (
          <footer className="relative z-10 mx-auto flex w-full max-w-[1460px] flex-col gap-4 px-4 pb-6 sm:px-8 sm:pb-8">
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
          <footer className="mx-auto flex w-full max-w-[1460px] justify-end px-4 pb-6 sm:px-8">
            <TableButton
              size="sm"
              icon="xray"
              shortcut="X"
              onClick={toggleXray}
              aria-pressed={showDrawer}
              aria-controls="xray-drawer"
            >
              X-ray
            </TableButton>
          </footer>
        )}
      </div>

      <AnimatePresence>
        {showDrawer && <XRay view={view} actions={actions} names={names} onClose={() => setXrayOpen(false)} />}
      </AnimatePresence>

      {view.serverError && live && (
        <p
          role="alert"
          className="pointer-events-none fixed left-1/2 top-20 z-30 max-w-[92vw] -translate-x-1/2 rounded-full border border-line bg-bg-raised px-4 py-2 text-[13px] text-ink shadow-card"
        >
          {view.serverError}
        </p>
      )}
    </div>
  );
}
