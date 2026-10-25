// "Start listening": the user gesture that opens the mic and unlocks crumb playback.
import type { MicState } from "../../lib/useTable";
import { Icon } from "./Icon";
import { TableButton } from "./primitives";

interface StartListeningProps {
  mic: MicState;
  onStart(): void;
  hint?: string;
  /** Shown once the mic is open; see micOnMessage (only claims transcription once it is happening). */
  onMessage: string;
  align?: "center" | "end";
}

export default function StartListening({ mic, onStart, hint, onMessage, align = "center" }: StartListeningProps) {
  const items = align === "end" ? "items-center sm:items-end sm:text-right" : "items-center text-center";
  if (mic.status === "on") {
    return (
      <p className={`flex items-center gap-2 text-[15px] text-ink-muted ${align === "end" ? "justify-end" : "justify-center"}`} role="status">
        <span className="size-2 shrink-0 animate-pulse rounded-full bg-ok" aria-hidden="true" />
        {onMessage}
      </p>
    );
  }
  return (
    <div className={`flex flex-col gap-3 ${items}`}>
      <TableButton tone="primary" icon="mic" onClick={onStart} disabled={mic.status === "starting"}>
        {mic.status === "starting" ? "Starting the mic" : mic.status === "error" ? "Try the mic again" : "Start listening"}
      </TableButton>
      {mic.status === "error" ? (
        <p
          role="alert"
          className="flex max-w-md items-start gap-2 rounded-2xl border border-danger/30 bg-danger/5 px-4 py-2.5 text-left text-[14px] text-ink"
        >
          <Icon name="micOff" size={18} className="mt-px shrink-0 text-danger" />
          {mic.message}
        </p>
      ) : (
        hint && <p className="max-w-md text-[14px] leading-relaxed text-ink-muted">{hint}</p>
      )}
    </div>
  );
}
