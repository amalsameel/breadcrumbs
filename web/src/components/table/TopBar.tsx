// Top bar: wordmark with a small orb, the round countdown and quiet status pills (connection, mic,
// speech service). Every status is spelled out in text, never color alone.
import type { AudioLinkStatus, MicState, MuseStatus } from "../../lib/useTable";
import type { SocketStatus } from "../../lib/ws";
import { OrbDot } from "../CrumbOrb";
import { formatClock } from "./format";
import { Badge } from "./primitives";

interface TopBarProps {
  socket: SocketStatus;
  audioLink: AudioLinkStatus;
  mic: MicState;
  micPaused: boolean;
  showAudio: boolean;
  roundRemainingS: number | null;
  mock: boolean;
  showConnection: boolean;
  muse?: MuseStatus | null;
}

export default function TopBar({
  socket,
  audioLink,
  mic,
  micPaused,
  showAudio,
  roundRemainingS,
  mock,
  showConnection,
  muse = null,
}: TopBarProps) {
  return (
    <header className="relative z-20 mx-auto flex min-h-20 w-full max-w-[1460px] flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-4 sm:px-8">
      <div className="flex min-w-0 items-center gap-3">
        <span className="flex items-center gap-2.5 font-display text-[22px] font-semibold tracking-[-0.035em] text-ink">
          <OrbDot palette="ember" className="size-6" />
          Breadcrumbs
        </span>
        {mock && <Badge className="border-dashed">Mock mode</Badge>}
      </div>

      {roundRemainingS !== null && <RoundTimer remainingS={roundRemainingS} />}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {showAudio && <MicPill mic={mic} paused={micPaused} audioLink={audioLink} />}
        {showAudio && <SpeechPill muse={muse} mock={mock} />}
        {showConnection && <ConnectionPill socket={socket} />}
      </div>
    </header>
  );
}

function RoundTimer({ remainingS }: { remainingS: number }) {
  const urgent = remainingS <= 60;
  return (
    <div
      className={`order-last w-full text-center sm:order-none sm:absolute sm:left-1/2 sm:top-1/2 sm:w-auto sm:-translate-x-1/2 sm:-translate-y-1/2`}
      role="timer"
      aria-label={`Round time left ${formatClock(remainingS)}`}
    >
      <span
        className={`inline-flex min-h-9 items-center gap-2 rounded-full border px-4 font-mono text-[13px] tabular-nums transition-colors ${
          urgent ? "border-amber/40 bg-amber/10 text-amber-ink" : "border-line bg-bg-raised text-ink-muted"
        }`}
      >
        Round {formatClock(remainingS)}
        {urgent && <span className="font-sans text-[12px]">almost up</span>}
      </span>
    </div>
  );
}

type Tone = "ok" | "warn" | "bad" | "off";

function Dot({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  const color = { ok: "bg-ok", warn: "bg-amber", bad: "bg-danger", off: "bg-paused" }[tone];
  return <span className={`size-2 shrink-0 rounded-full ${color} ${pulse ? "animate-pulse" : ""}`} aria-hidden="true" />;
}

function StatusPill({ tone, pulse, children, className = "" }: { tone: Tone; pulse?: boolean; children: string; className?: string }) {
  return (
    <Badge className={`${tone === "bad" ? "border-danger/40 text-ink" : ""} ${className}`}>
      <Dot tone={tone} pulse={pulse} />
      {children}
    </Badge>
  );
}

function ConnectionPill({ socket }: { socket: SocketStatus }) {
  const label = socket === "open" ? "Connected" : socket === "connecting" ? "Connecting" : "Reconnecting";
  return (
    <span role="status">
      <StatusPill tone={socket === "open" ? "ok" : "warn"} pulse={socket !== "open"}>
        {label}
      </StatusPill>
    </span>
  );
}

function SpeechPill({ muse, mock }: { muse: MuseStatus | null; mock: boolean }) {
  const status = muse?.status ?? (mock ? "mock" : "connecting");
  const map: Record<MuseStatus["status"], [Tone, string]> = {
    connected: ["ok", "Speech on"],
    mock: ["ok", "Speech (mock)"],
    connecting: ["warn", "Speech connecting"],
    reconnecting: ["warn", "Speech reconnecting"],
    resyncing: ["warn", "Re-syncing voices"],
    closed: ["off", "Speech off"],
    error: ["bad", "Speech error"],
  };
  const [tone, label] = map[status];
  return (
    <span className="hidden md:inline-flex" title={muse?.detail ?? undefined}>
      <StatusPill tone={tone} pulse={tone === "warn"}>
        {label}
      </StatusPill>
    </span>
  );
}

function MicPill({ mic, paused, audioLink }: { mic: MicState; paused: boolean; audioLink: AudioLinkStatus }) {
  let tone: Tone = "off";
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
  return <StatusPill tone={tone}>{label}</StatusPill>;
}
