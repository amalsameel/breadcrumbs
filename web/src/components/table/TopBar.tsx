// Quiet top strip: wordmark, mode, round countdown and a small connection indicator.
import type { Mode } from "../../lib/types";
import type { AudioLinkStatus, MicState } from "../../lib/useTable";
import type { SocketStatus } from "../../lib/ws";
import { formatClock, MODE_LABEL } from "./format";
import { Badge } from "./primitives";

interface TopBarProps {
  mode: Mode | null;
  socket: SocketStatus;
  audioLink: AudioLinkStatus;
  mic: MicState;
  micPaused: boolean;
  showAudio: boolean;
  roundRemainingS: number | null;
  mock: boolean;
  showConnection: boolean;
}

export default function TopBar({ mode, socket, audioLink, mic, micPaused, showAudio, roundRemainingS, mock, showConnection }: TopBarProps) {
  return (
    <header className="relative z-20 flex items-center justify-between gap-4 px-6 pt-5 sm:px-8">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-2 font-display text-xl text-ink">
          <span className="size-2 rounded-full bg-amber shadow-[0_0_10px_2px_rgba(245,165,36,0.6)]" aria-hidden="true" />
          Breadcrumbs
        </span>
        {mode && <Badge>{MODE_LABEL[mode]}</Badge>}
        {mock && <Badge className="border-dashed">Mock mode</Badge>}
      </div>

      {roundRemainingS !== null && <RoundTimer remainingS={roundRemainingS} />}

      <div className="flex items-center gap-4 text-xs text-ink-faint">
        {showAudio && <MicIndicator mic={mic} paused={micPaused} audioLink={audioLink} />}
        {showConnection && <ConnectionIndicator socket={socket} />}
      </div>
    </header>
  );
}

function RoundTimer({ remainingS }: { remainingS: number }) {
  const urgent = remainingS <= 60;
  return (
    <div
      className={`absolute left-1/2 top-5 -translate-x-1/2 rounded-full border px-4 py-1.5 font-mono text-sm tabular-nums transition-colors ${
        urgent ? "border-crumb/60 text-crumb" : "border-line text-ink-muted"
      }`}
      role="timer"
      aria-label={`Round time left ${formatClock(remainingS)}`}
    >
      Round {formatClock(remainingS)}
    </div>
  );
}

function Dot({ tone, pulse = false }: { tone: "ok" | "warn" | "bad" | "off"; pulse?: boolean }) {
  const color = { ok: "bg-ok", warn: "bg-amber", bad: "bg-danger", off: "bg-paused" }[tone];
  return <span className={`size-2 rounded-full ${color} ${pulse ? "animate-pulse" : ""}`} aria-hidden="true" />;
}

function ConnectionIndicator({ socket }: { socket: SocketStatus }) {
  const label = socket === "open" ? "Connected" : socket === "connecting" ? "Connecting" : "Reconnecting";
  return (
    <span className="flex items-center gap-1.5" role="status">
      <Dot tone={socket === "open" ? "ok" : "warn"} pulse={socket !== "open"} />
      {label}
    </span>
  );
}

function MicIndicator({ mic, paused, audioLink }: { mic: MicState; paused: boolean; audioLink: AudioLinkStatus }) {
  let tone: "ok" | "warn" | "bad" | "off" = "off";
  let label = "Mic off";
  if (mic.status === "error") {
    tone = "bad";
    label = "Mic blocked";
  } else if (mic.status === "starting") {
    tone = "warn";
    label = "Mic starting";
  } else if (mic.status === "on" && paused) {
    tone = "off";
    label = "Mic paused";
  } else if (mic.status === "on") {
    tone = audioLink === "open" ? "ok" : "warn";
    label = audioLink === "open" ? "Mic on" : audioLink === "forbidden" ? "Mic waiting for consent" : "Mic linking";
  }
  return (
    <span className="flex items-center gap-1.5">
      <Dot tone={tone} />
      {label}
    </span>
  );
}
