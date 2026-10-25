// Profile review (PRD F4, 6.4): remove chips, set off-limits topics, then "I'm ready".
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../../lib/api";
import type { Profile, Seat } from "../../lib/types";
import { ProfileChips } from "../ProfileChips";
import { Button } from "../ui/Button";
import { TogglePill } from "../ui/Pill";
import { FIELD_CLASS } from "../ui/TextField";
import { Spinner } from "../ui/Spinner";
import { addTopic, OFF_LIMITS_PRESETS, toggleTopic } from "./flow";
import { ErrorNote, StepHeading } from "./PhoneScreen";

interface ReviewStepProps {
  sid: string;
  seat: Seat;
  profile: Profile | null; // null after a reload: fetched here
  onProfile: (p: Profile) => void;
  onReady: () => void;
  describeError: (err: unknown) => string;
}

export function ReviewStep({ sid, seat, profile, onProfile, onReady, describeError }: ReviewStepProps) {
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (profile) return;
    let cancelled = false;
    api.getProfile(sid, seat).then(
      (p) => !cancelled && onProfile(p),
      (err) => !cancelled && setLoadError(describeError(err)),
    );
    return () => {
      cancelled = true;
    };
  }, [sid, seat, profile, onProfile, describeError]);

  if (!profile) {
    return (
      <section aria-labelledby="step-title" className="flex flex-1 flex-col items-center justify-center gap-4 text-center">
        <h1 id="step-title" tabIndex={-1} className="sr-only">
          Loading your profile
        </h1>
        {loadError ? <ErrorNote>{loadError}</ErrorNote> : <Spinner size={28} label="Loading your profile" className="text-ink" />}
      </section>
    );
  }
  return <ReviewForm sid={sid} seat={seat} profile={profile} onReady={onReady} describeError={describeError} />;
}

function ReviewForm({
  sid,
  seat,
  profile,
  onReady,
  describeError,
}: Omit<ReviewStepProps, "profile" | "onProfile"> & { profile: Profile }) {
  const [removed, setRemoved] = useState<string[]>(() => [...profile.removed_item_ids]);
  const [offLimits, setOffLimits] = useState<string[]>(() => [...profile.off_limits]);
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const custom = offLimits.filter((t) => !(OFF_LIMITS_PRESETS as readonly string[]).includes(t));

  function addDraft(e: FormEvent) {
    e.preventDefault();
    setOffLimits((list) => addTopic(list, draft));
    setDraft("");
  }

  async function ready() {
    setSubmitting(true);
    setError(null);
    try {
      // A typed but un-added topic still counts: people often forget to tap Add.
      const finalOffLimits = draft.trim() ? addTopic(offLimits, draft) : offLimits;
      await api.patchProfile(sid, seat, removed, finalOffLimits);
      await api.ready(sid, seat);
      onReady();
    } catch (err) {
      setError(describeError(err));
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="step-title" className="flex flex-col gap-8">
      <StepHeading
        id="step-title"
        eyebrow={`Hi ${profile.display_name}`}
        title="Here's what you shared"
        lead="Breadcrumbs will only bring up what's on this list. You can remove anything."
      />

      {profile.vibe && (
        <p className="-mt-4 inline-flex items-center gap-2 self-start rounded-full bg-bg-raised px-4 py-2 text-[15px] text-ink-muted shadow-pill">
          Your vibe: <span className="font-medium text-ink">{profile.vibe}</span>
        </p>
      )}

      <ProfileChips
        items={profile.items}
        removedIds={removed}
        onRemove={(id) => setRemoved((r) => (r.includes(id) ? r : [...r, id]))}
        onUndo={() => setRemoved((r) => r.slice(0, -1))}
      />

      <section aria-labelledby="offlimits-title" className="flex flex-col gap-3">
        <div>
          <h2 id="offlimits-title" className="font-display text-[24px] font-normal tracking-[-0.02em] text-ink">
            Off-limits
          </h2>
          <p className="mt-1.5 text-[15px] text-ink-muted">Topics Breadcrumbs will never raise, even as a fun fact.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {OFF_LIMITS_PRESETS.map((topic) => (
            <TogglePill
              key={topic}
              pressed={offLimits.includes(topic)}
              onPressedChange={(on) => setOffLimits((list) => toggleTopic(list, topic, on))}
            >
              {topic}
            </TogglePill>
          ))}
          {custom.map((topic) => (
            <TogglePill
              key={topic}
              pressed
              aria-label={`${topic}, off-limits. Tap to remove.`}
              onPressedChange={() => setOffLimits((list) => toggleTopic(list, topic, false))}
            >
              {topic}
            </TogglePill>
          ))}
        </div>
        <form onSubmit={addDraft} className="flex gap-2">
          <label htmlFor="offlimits-add" className="sr-only">
            Add an off-limits topic
          </label>
          <input
            id="offlimits-add"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={40}
            placeholder="Add a topic, e.g. my ex"
            enterKeyHint="done"
            className={`${FIELD_CLASS} min-h-12 min-w-0 flex-1 border-line`}
          />
          <Button type="submit" variant="secondary" size="md" className="min-h-12 shrink-0" disabled={!draft.trim()}>
            Add
          </Button>
        </form>
      </section>

      {error && <ErrorNote>{error}</ErrorNote>}

      <Button block onClick={() => void ready()} loading={submitting}>
        I'm ready
      </Button>
    </section>
  );
}
