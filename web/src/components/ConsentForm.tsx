// Express written consent (PRD F2, 15.2): typed name + required checkbox before any audio is captured.
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { OrbDot } from "./ui/Crumbs";
import { TextField } from "./ui/TextField";
import { ErrorNote } from "./phone/PhoneScreen";

interface ConsentFormProps {
  partnerName: string | null;
  onSubmit: (typedName: string) => Promise<void>; // throws with a user-facing message on failure
}

const PROMISES: { title: string; body: string }[] = [
  { title: "A laptop listens", body: "The Table's mic hears your conversation so it knows when a pause needs a nudge." },
  { title: "Audio is never stored", body: "Sound is streamed for transcription and never saved anywhere." },
  {
    title: "Transcripts are temporary",
    body: "They live only in memory and are wiped when you finish, or after 2 hours at most.",
  },
  { title: "You approve everything", body: "Breadcrumbs only mentions what's on the profile you approve. You can remove anything." },
];

export function ConsentForm({ partnerName, onSubmit }: ConsentFormProps) {
  const [name, setName] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const [agreeError, setAgreeError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const agreeId = useId();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.replace(/\s+/g, " ").trim();
    const nErr = trimmed ? null : "Type your name to sign.";
    const aErr = agreed ? null : "Check the box to agree.";
    setNameError(nErr);
    setAgreeError(aErr);
    if (nErr || aErr) return;
    setSubmitting(true);
    setFormError(null);
    try {
      await onSubmit(trimmed);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Something went wrong. Try again.");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-6">
      <p className="text-[17px] leading-relaxed text-ink-muted">
        You're joining a conversation
        {partnerName ? (
          <>
            {" "}
            with <span className="font-medium text-ink">{partnerName}</span>
          </>
        ) : null}
        . Here's exactly what happens:
      </p>

      <Card as="section" aria-label="What happens" className="p-5">
        <ul className="flex flex-col gap-5">
          {PROMISES.map((p) => (
            <li key={p.title} className="flex gap-3">
              <OrbDot size={14} className="mt-1" />
              <span>
                <span className="block text-[16px] font-medium text-ink">{p.title}</span>
                <span className="block text-[15px] leading-snug text-ink-muted">{p.body}</span>
              </span>
            </li>
          ))}
        </ul>
      </Card>

      <TextField
        label="Your name"
        hint="This is how Breadcrumbs will address you out loud."
        error={nameError}
        value={name}
        onChange={(e) => {
          setName(e.target.value);
          if (nameError) setNameError(null);
        }}
        maxLength={60}
        autoComplete="given-name"
        autoCapitalize="words"
        enterKeyHint="done"
        placeholder="e.g. Maya"
      />

      <div className="flex flex-col gap-1.5">
        <label
          htmlFor={agreeId}
          className={
            "flex min-h-14 cursor-pointer items-start gap-3 rounded-3xl border bg-bg-raised p-4 transition-[border-color,box-shadow] duration-150 " +
            "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ink " +
            (agreeError ? "border-danger" : agreed ? "border-ink shadow-card" : "border-line")
          }
        >
          <input
            id={agreeId}
            type="checkbox"
            checked={agreed}
            onChange={(e) => {
              setAgreed(e.target.checked);
              if (agreeError) setAgreeError(null);
            }}
            aria-invalid={agreeError ? true : undefined}
            aria-describedby={agreeError ? `${agreeId}-err` : undefined}
            className="mt-0.5 h-6 w-6 shrink-0 cursor-pointer accent-ink"
          />
          <span className="text-[16px] leading-snug text-ink">
            <span className="font-medium">I agree.</span> I consent to Breadcrumbs transcribing my voice answers and
            listening to this conversation as described above. Typing my name above is my signature.
          </span>
        </label>
        {agreeError && (
          <p id={`${agreeId}-err`} role="alert" className="flex items-start gap-2 text-[14px] leading-snug text-ink">
            <span aria-hidden="true" className="mt-[5px] h-2 w-2 shrink-0 rounded-full bg-danger" />
            <span className="min-w-0">{agreeError}</span>
          </p>
        )}
      </div>

      {formError && <ErrorNote>{formError}</ErrorNote>}

      <Button type="submit" block loading={submitting}>
        I agree, let's begin
      </Button>
    </form>
  );
}
