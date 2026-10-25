// Lobby + profiles phase (PRD 6.2, F1): two seat QR cards and the mic warm-up.
import { motion } from "framer-motion";
import type { Seat, SessionSummary } from "../../lib/types";
import type { MicState } from "../../lib/useTable";
import SeatQr from "../SeatQr";
import { isInsecureUrl, isLocalUrl, micOnMessage } from "./format";
import StartListening from "./StartListening";

interface LobbyProps {
  session: SessionSummary;
  joinUrls: Record<Seat, string>;
  mic: MicState;
  onStartListening(): void;
}

export default function Lobby({ session, joinUrls, mic, onStartListening }: LobbyProps) {
  const local = isLocalUrl(joinUrls[1]);
  const insecure = !local && isInsecureUrl(joinUrls[1]);
  const anyoneJoined = session.seats["1"].joined || session.seats["2"].joined;
  const bothConsented = session.seats["1"].consented && session.seats["2"].consented;

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col items-center justify-center gap-7 px-6 py-6">
      <motion.div
        className="text-center"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
      >
        <h1 className="font-display text-[clamp(34px,3.6vw,52px)] leading-tight text-ink">Pull up a chair.</h1>
        <p className="mt-2 text-base text-ink-muted">
          {anyoneJoined
            ? "Finish on your phones. The table wakes up when you are both ready."
            : "Each of you scan a code with your own phone. It takes about a minute."}
        </p>
      </motion.div>

      <div className="flex w-full flex-wrap items-stretch justify-center gap-6 lg:gap-10">
        <SeatQr seat={1} url={joinUrls[1]} summary={session.seats["1"]} />
        <SeatQr seat={2} url={joinUrls[2]} summary={session.seats["2"]} />
      </div>

      <div className="flex flex-col items-center gap-2 text-center">
        <p className="flex flex-wrap justify-center gap-x-5 gap-y-1 font-mono text-xs text-ink-faint">
          <span>{joinUrls[1]}</span>
          <span>{joinUrls[2]}</span>
        </p>
        {(local || insecure) && (
          <p className="max-w-lg rounded-lg border border-amber/40 bg-amber/10 px-3 py-1.5 text-xs text-ink-muted">
            {local
              ? "Phones need the HTTPS tunnel URL, set PUBLIC_BASE_URL."
              : "Phones need HTTPS for the mic. Use the tunnel URL in PUBLIC_BASE_URL."}
          </p>
        )}
      </div>

      {session.phase === "profiles" && (
        <StartListening
          mic={mic}
          onStart={onStartListening}
          hint="Start the mic now so it is ready the moment you both are. Nothing is sent until you have both consented."
          onMessage={micOnMessage(session.phase, bothConsented)}
        />
      )}
    </div>
  );
}
