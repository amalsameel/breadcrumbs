// Lobby + profiles phase (PRD 6.2, F1): a big headline, then one rounded showcase that holds the
// table's mode, the two seat QR cards around the orb, the setup steps and the mic warm-up.
import { motion } from "framer-motion";
import type { Seat, SessionSummary } from "../../lib/types";
import type { MicState } from "../../lib/useTable";
import CrumbOrb, { OrbDot } from "../CrumbOrb";
import SeatQr from "../SeatQr";
import { isInsecureUrl, isLocalUrl, lobbyStep, micOnMessage, MODE_LABEL, MODE_PALETTE, MODES, type LobbyStep } from "./format";
import { Icon } from "./Icon";
import { SegmentTrack } from "./primitives";
import StartListening from "./StartListening";

interface LobbyProps {
  session: SessionSummary;
  joinUrls: Record<Seat, string>;
  mic: MicState;
  onStartListening(): void;
}

const EASE_OUT: [number, number, number, number] = [0.22, 1, 0.36, 1];

const STEP_LABEL: Record<LobbyStep, string> = {
  1: "Scan a code",
  2: "Consent and a quick profile",
  3: "Say hi to the table",
};

export default function Lobby({ session, joinUrls, mic, onStartListening }: LobbyProps) {
  const local = isLocalUrl(joinUrls[1]);
  const insecure = !local && isInsecureUrl(joinUrls[1]);
  const anyoneJoined = session.seats["1"].joined || session.seats["2"].joined;
  const bothConsented = session.seats["1"].consented && session.seats["2"].consented;
  const step = lobbyStep(session);

  return (
    <div className="mx-auto flex w-full max-w-[1460px] flex-1 flex-col gap-8 px-4 pb-10 pt-4 sm:px-8 lg:pt-6">
      <motion.section
        className="grid items-end gap-5 lg:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)] lg:gap-14"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: EASE_OUT }}
      >
        <h1 className="font-display text-[clamp(38px,min(4.5vw,7vh),68px)] font-normal leading-[1.04] tracking-[-0.04em] text-ink text-balance">
          Pull up a chair.
          <br />
          The table keeps the thread.
        </h1>
        <p className="max-w-xl text-[18px] leading-[1.5] text-ink lg:text-[20px]">
          {anyoneJoined
            ? "Finish on your phones. The table wakes up the moment you are both ready."
            : "Each of you scan a code with your own phone. It takes about a minute, then you just talk. When a pause runs long, the table drops one short crumb to pick the thread back up."}
        </p>
      </motion.section>

      <motion.section
        aria-label="Join this table"
        className="flex flex-col gap-6 rounded-[28px] border border-line bg-well p-3 sm:p-5 lg:p-6"
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.55, delay: 0.06, ease: EASE_OUT }}
      >
        <div className="flex justify-center">
          <SegmentTrack as="ul" label="Conversation mode" className="bg-bg-sunken/60">
            {MODES.map((m) => {
              const active = m === session.mode;
              return (
                <li
                  key={m}
                  aria-current={active ? "true" : undefined}
                  className={`inline-flex min-h-10 items-center gap-2 rounded-full px-4 text-[15px] font-medium ${
                    active ? "bg-bg-raised text-ink shadow-raised" : "text-ink-muted"
                  }`}
                >
                  <OrbDot palette={MODE_PALETTE[m]} className="size-4" />
                  {MODE_LABEL[m]}
                  {active && <span className="sr-only">(this table)</span>}
                </li>
              );
            })}
          </SegmentTrack>
        </div>

        <div className="grid items-center justify-items-center gap-6 md:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] lg:gap-8">
          <SeatQr seat={1} url={joinUrls[1]} summary={session.seats["1"]} />
          <figure className="order-first flex flex-col items-center gap-4 text-center md:col-span-2 lg:order-none lg:col-span-1 lg:w-[min(22vw,300px)]">
            <div className="size-[min(40vw,190px)] lg:size-[min(20vw,260px)]">
              <CrumbOrb mode={mic.status === "on" ? "listening" : "idle"} label="The table's orb" />
            </div>
            <figcaption className="space-y-1.5">
              <p className="text-[19px] font-medium tracking-[-0.01em] text-ink">This is the table</p>
              <p className="mx-auto max-w-[30ch] text-[15px] leading-snug text-ink-muted">
                It only listens once you have both said yes, and nothing you say is stored.
              </p>
            </figcaption>
          </figure>
          <SeatQr seat={2} url={joinUrls[2]} summary={session.seats["2"]} />
        </div>

        <div className="flex flex-col items-center justify-between gap-4 border-t border-line pt-5 sm:flex-row">
          <SegmentTrack as="ol" label="Setup steps" className="bg-bg-sunken/60">
            {([1, 2, 3] as const).map((n) => {
              const done = n < step;
              const active = n === step;
              return (
                <li
                  key={n}
                  aria-current={active ? "step" : undefined}
                  className={`inline-flex min-h-10 items-center gap-2 rounded-full px-4 text-[14px] font-medium ${
                    active ? "bg-bg-raised text-ink shadow-raised" : done ? "text-ink" : "text-ink-muted"
                  }`}
                >
                  {done ? (
                    <Icon name="check" size={15} strokeWidth={2.4} className="text-ok" />
                  ) : (
                    <span className="font-mono text-[12px] tabular-nums" aria-hidden="true">
                      {n}
                    </span>
                  )}
                  {STEP_LABEL[n]}
                  <span className="sr-only">{done ? "(done)" : active ? "(now)" : "(next)"}</span>
                </li>
              );
            })}
          </SegmentTrack>

          {session.phase === "profiles" ? (
            <StartListening
              mic={mic}
              onStart={onStartListening}
              align="end"
              hint="Start the mic now so it is ready the moment you both are. Nothing is sent until you have both consented."
              onMessage={micOnMessage(session.phase, bothConsented)}
            />
          ) : (
            <p className="text-[15px] text-ink-muted" role="status">
              Waiting for both phones
            </p>
          )}
        </div>
      </motion.section>

      <div className="flex flex-col items-center gap-2 text-center">
        <p className="flex flex-wrap justify-center gap-x-5 gap-y-1 break-all font-mono text-[12px] text-ink-muted">
          <span>{joinUrls[1]}</span>
          <span>{joinUrls[2]}</span>
        </p>
        {(local || insecure) && (
          <p className="max-w-lg rounded-full border border-amber/30 bg-amber/10 px-4 py-1.5 text-[13px] text-ink">
            {local
              ? "Phones need the HTTPS tunnel URL, set PUBLIC_BASE_URL."
              : "Phones need HTTPS for the mic. Use the tunnel URL in PUBLIC_BASE_URL."}
          </p>
        )}
      </div>
    </div>
  );
}
