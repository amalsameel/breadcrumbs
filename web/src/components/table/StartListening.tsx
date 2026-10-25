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
}

export default function StartListening({ mic, onStart, hint, onMessage }: StartListeningProps) {
  if (mic.status === "on") {
    return (
      <p className="flex items-center justify-center gap-2 text-sm text-ink-muted" role="status">
        <span className="size-2 animate-pulse rounded-full bg-ok" aria-hidden="true" />
        {onMessage}
      </p>
    );
  }
  return (
    <div className="flex flex-col items-center gap-3">
      <TableButton tone="primary" icon="mic" onClick={onStart} disabled={mic.status === "starting"}>
        {mic.status === "starting" ? "Starting the mic" : mic.status === "error" ? "Try the mic again" : "Start listening"}
      </TableButton>
      {mic.status === "error" ? (
        <p
          role="alert"
          className="flex max-w-md items-start gap-2 rounded-xl border border-danger/50 bg-danger/10 px-4 py-2.5 text-sm text-ink"
        >
          <Icon name="micOff" size={18} className="mt-px shrink-0 text-danger" />
          {mic.message}
        </p>
      ) : (
        hint && <p className="text-sm text-ink-faint">{hint}</p>
      )}
    </div>
  );
}
