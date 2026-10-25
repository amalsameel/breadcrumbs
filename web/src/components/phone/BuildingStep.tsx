// "Gathering your crumbs...": POST profile once (deduped across StrictMode remounts and quick reloads).
import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { Profile, Seat } from "../../lib/types";
import { Button } from "../ui/Button";
import { OrbFigure } from "../ui/Crumbs";
import { StepHeading } from "./PhoneScreen";

const inflight = new Map<string, Promise<Profile>>();

function buildOnce(sid: string, seat: Seat): Promise<Profile> {
  const key = `${sid}:${seat}`;
  let p = inflight.get(key);
  if (!p) {
    p = api.buildProfile(sid, seat).finally(() => inflight.delete(key));
    inflight.set(key, p);
  }
  return p;
}

interface BuildingStepProps {
  sid: string;
  seat: Seat;
  onBuilt: (profile: Profile) => void;
  describeError: (err: unknown) => string;
}

export function BuildingStep({ sid, seat, onBuilt, describeError }: BuildingStepProps) {
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const run = useCallback(() => {
    let cancelled = false;
    setError(null);
    buildOnce(sid, seat).then(
      (profile) => !cancelled && onBuilt(profile),
      (err) => !cancelled && setError(describeError(err)),
    );
    return () => {
      cancelled = true;
    };
  }, [sid, seat, onBuilt, describeError]);

  useEffect(() => run(), [run, attempt]);

  return (
    <section aria-labelledby="step-title" className="flex flex-1 flex-col items-center justify-center text-center">
      <OrbFigure size={200} mode={error ? "paused" : "warming"} />
      <div className="mt-10 w-full">
        <StepHeading
          id="step-title"
          title={error ? "That didn't work" : "Gathering your crumbs..."}
          lead={error ?? "Turning your answers into a few things worth talking about. This takes a few seconds."}
        />
      </div>
      <p className="sr-only" aria-live="polite">
        {error ? "" : "Building your profile"}
      </p>
      {error && (
        <Button block onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </Button>
      )}
    </section>
  );
}
