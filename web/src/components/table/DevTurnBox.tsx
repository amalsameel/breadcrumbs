// "Dev: inject turn" (DEV_TOOLS only): sends {type:"dev_turn", seat, text} so the engine can be
// exercised without a microphone.
import { useState, type FormEvent } from "react";
import type { Seat } from "../../lib/types";

interface DevTurnBoxProps {
  names: Record<Seat, string>;
  onSend(seat: Seat, text: string): void;
}

export function DevTurnBox({ names, onSend }: DevTurnBoxProps) {
  const [seat, setSeat] = useState<Seat>(1);
  const [text, setText] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    onSend(seat, text);
    setText("");
    setSeat((s) => (s === 1 ? 2 : 1)); // alternate speakers, like a real exchange
  };

  return (
    <form onSubmit={submit} className="space-y-2">
      <fieldset className="flex gap-2">
        <legend className="sr-only">Speaker</legend>
        {([1, 2] as const).map((s) => (
          <label
            key={s}
            className={`flex min-h-10 flex-1 cursor-pointer items-center justify-center rounded-full border px-3 transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ink ${
              seat === s ? "border-ink bg-ink text-on-ink" : "border-line bg-bg-raised text-ink-muted hover:border-line-strong hover:text-ink"
            }`}
          >
            <input type="radio" name="dev-seat" value={s} checked={seat === s} onChange={() => setSeat(s)} className="sr-only" />
            {names[s]} <span className={`ml-1 ${seat === s ? "text-on-ink/70" : "text-ink-muted"}`}>({s})</span>
          </label>
        ))}
      </fieldset>
      <div className="flex gap-2">
        <label htmlFor="dev-turn-text" className="sr-only">
          Turn text
        </label>
        <input
          id="dev-turn-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="e.g. wait, let me show you this photo"
          autoComplete="off"
          className="min-h-10 min-w-0 flex-1 rounded-full border border-line bg-bg-raised px-4 font-sans text-[13px] text-ink placeholder:text-ink-muted/80 focus:border-ink"
        />
        <button
          type="submit"
          disabled={!text.trim()}
          className="min-h-10 cursor-pointer rounded-full bg-ink px-4 font-sans text-[13px] font-medium text-on-ink transition-colors hover:bg-ink/85 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Send
        </button>
      </div>
    </form>
  );
}
