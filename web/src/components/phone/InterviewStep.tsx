// Voice profile interview (PRD F3, 6.3, 14.2): one question at a time, 20 s auto-stop, re-record, upload.
import { useCallback, useEffect, useRef, useState } from "react";
import { extForMime, startRecording } from "../../audio/recorder";
import type { RecorderHandle } from "../../audio/recorder";
import { api } from "../../lib/api";
import type { InterviewQuestion, Seat } from "../../lib/types";
import { RecordButton } from "../RecordButton";
import type { RecordButtonState } from "../RecordButton";
import { Waveform } from "../Waveform";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Spinner } from "../ui/Spinner";
import { classifyMicError, MIC_MESSAGES } from "./flow";
import type { MicProblem } from "./flow";
import { StepHeading } from "./PhoneScreen";

const LIMIT_MS = 20_000;
const MIN_MS = 1_200;
const TICK_MS = 100;

type RecState =
  | { k: "idle" }
  | { k: "starting" }
  | { k: "recording"; handle: RecorderHandle; startedAt: number }
  | { k: "uploading" }
  | { k: "heard"; transcript: string }
  | { k: "upload_error"; blob: Blob; mime: string; message: string }
  | { k: "mic_error"; problem: MicProblem }
  | { k: "too_short" };

interface InterviewStepProps {
  sid: string;
  seat: Seat;
  questions: InterviewQuestion[];
  startIndex: number;
  onDone: () => void;
  describeError: (err: unknown) => string; // also routes 404s to the "session gone" screen
}

function micAvailable(): boolean {
  return typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getUserMedia === "function";
}

export function InterviewStep({ sid, seat, questions, startIndex, onDone, describeError }: InterviewStepProps) {
  const [index, setIndex] = useState(startIndex);
  const [rec, setRec] = useState<RecState>(() =>
    micAvailable() ? { k: "idle" } : { k: "mic_error", problem: "insecure" },
  );
  const [elapsed, setElapsed] = useState(0);
  const recRef = useRef<RecState>(rec);
  recRef.current = rec;
  const question = questions[index];
  const isLast = index >= questions.length - 1;

  // Never leave the mic open if the person navigates away mid-recording.
  useEffect(
    () => () => {
      const r = recRef.current;
      if (r.k === "recording") r.handle.cancel();
    },
    [],
  );

  const upload = useCallback(
    async (blob: Blob, mime: string) => {
      setRec({ k: "uploading" });
      try {
        const res = await api.uploadAnswer(sid, seat, question.id, blob, `answer.${extForMime(mime)}`);
        setRec({ k: "heard", transcript: res.transcript.trim() });
      } catch (err) {
        setRec({ k: "upload_error", blob, mime, message: describeError(err) });
      }
    },
    [sid, seat, question, describeError],
  );

  const stop = useCallback(async () => {
    const r = recRef.current;
    if (r.k !== "recording") return;
    const duration = performance.now() - r.startedAt;
    recRef.current = { k: "uploading" }; // guard against a second auto-stop tick before re-render
    if (duration < MIN_MS) {
      r.handle.cancel();
      setRec({ k: "too_short" });
      return;
    }
    setRec({ k: "uploading" });
    try {
      const blob = await r.handle.stop();
      await upload(blob, r.handle.mimeType);
    } catch (err) {
      setRec({ k: "upload_error", blob: new Blob(), mime: r.handle.mimeType, message: describeError(err) });
    }
  }, [upload, describeError]);

  // Countdown tick + auto-stop at 20 s.
  useEffect(() => {
    if (rec.k !== "recording") return;
    const id = window.setInterval(() => {
      const ms = performance.now() - rec.startedAt;
      setElapsed(ms);
      if (ms >= LIMIT_MS) void stop();
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [rec, stop]);

  async function start() {
    if (!micAvailable()) {
      setRec({ k: "mic_error", problem: "insecure" });
      return;
    }
    setRec({ k: "starting" });
    setElapsed(0);
    try {
      const handle = await startRecording();
      setRec({ k: "recording", handle, startedAt: performance.now() });
    } catch (err) {
      setRec({ k: "mic_error", problem: classifyMicError(err) });
    }
  }

  function next() {
    if (isLast) {
      onDone();
      return;
    }
    setIndex((i) => i + 1);
    setRec({ k: "idle" });
    setElapsed(0);
  }

  const buttonState: RecordButtonState =
    rec.k === "recording"
      ? "recording"
      : rec.k === "starting"
        ? "starting"
        : rec.k === "uploading"
          ? "busy"
          : rec.k === "mic_error" && rec.problem === "insecure"
            ? "disabled"
            : "idle";
  const showRecorder = rec.k !== "heard" && rec.k !== "upload_error";

  if (!question) {
    return (
      <section aria-labelledby="step-title" className="flex flex-1 flex-col justify-center">
        <StepHeading id="step-title" title="No questions this time" lead="Let's go straight to your profile." />
        <Button block onClick={onDone}>
          Continue
        </Button>
      </section>
    );
  }

  return (
    <section aria-labelledby="step-title" className="flex flex-1 flex-col">
      <StepHeading
        id="step-title"
        eyebrow={`Question ${index + 1} of ${questions.length}`}
        title={question.text}
        lead="About 20 seconds. Speak naturally, there are no wrong answers."
      />

      <p className="sr-only" aria-live="polite">
        {rec.k === "recording"
          ? "Recording"
          : rec.k === "uploading"
            ? "Listening back to your answer"
            : rec.k === "heard"
              ? "Answer received"
              : ""}
      </p>

      {showRecorder && (
        <div className="flex flex-1 flex-col items-center justify-center gap-6 py-4">
          <div className="w-full max-w-xs">
            <Waveform analyser={rec.k === "recording" ? rec.handle.analyser : null} />
          </div>
          <RecordButton
            state={buttonState}
            elapsedMs={elapsed}
            limitMs={LIMIT_MS}
            onPress={() => (rec.k === "recording" ? void stop() : void start())}
            idleLabel={idleLabelFor(rec)}
          />
          {rec.k === "uploading" && (
            <p className="flex items-center gap-2 text-[16px] text-ink-muted">
              <Spinner size={18} /> Listening back...
            </p>
          )}
          {rec.k === "too_short" && (
            <p role="status" className="text-center text-[16px] text-ink-muted">
              That was a little quick. Tap and tell us a bit more.
            </p>
          )}
          {rec.k === "mic_error" && <MicNotice problem={rec.problem} />}
        </div>
      )}

      {rec.k === "heard" && (
        <HeardCard
          transcript={rec.transcript}
          isLast={isLast}
          onConfirm={next}
          onRetry={() => {
            setRec({ k: "idle" });
            setElapsed(0);
          }}
        />
      )}

      {rec.k === "upload_error" && (
        <Card tone="sunken" className="flex flex-col gap-4" role="alert">
          <p className="text-[17px] font-semibold text-ink">We couldn't save that answer</p>
          <p className="text-[15px] text-ink-muted">{rec.message}</p>
          <div className="flex flex-col gap-2">
            {rec.blob.size > 0 && (
              <Button block onClick={() => void upload(rec.blob, rec.mime)}>
                Try sending again
              </Button>
            )}
            <Button block variant="secondary" onClick={() => setRec({ k: "idle" })}>
              Record again
            </Button>
          </div>
        </Card>
      )}
    </section>
  );
}

function HeardCard({
  transcript,
  isLast,
  onConfirm,
  onRetry,
}: {
  transcript: string;
  isLast: boolean;
  onConfirm: () => void;
  onRetry: () => void;
}) {
  const empty = transcript.length === 0;
  return (
    <div className="flex flex-col gap-4">
      <Card tone="glow">
        <p className="mb-2 text-[14px] font-medium tracking-wide text-amber uppercase">We heard</p>
        {empty ? (
          <p className="text-[17px] text-ink-muted">We couldn't quite catch that. Try once more, a little closer to the phone?</p>
        ) : (
          <blockquote className="font-display text-[20px] leading-snug text-ink">"{truncate(transcript, 240)}"</blockquote>
        )}
      </Card>
      {empty ? (
        <>
          <Button block onClick={onRetry}>
            Record again
          </Button>
          <Button block variant="ghost" onClick={onConfirm}>
            Keep going anyway
          </Button>
        </>
      ) : (
        <>
          <Button block onClick={onConfirm}>
            {isLast ? "Sounds right, build my profile" : "Sounds right, next"}
          </Button>
          <Button block variant="secondary" onClick={onRetry}>
            Re-record
          </Button>
        </>
      )}
    </div>
  );
}

function MicNotice({ problem }: { problem: MicProblem }) {
  const msg = MIC_MESSAGES[problem];
  return (
    <Card tone="sunken" role="alert" className="w-full">
      <p className="text-[17px] font-semibold text-ink">{msg.title}</p>
      <p className="mt-1 text-[15px] leading-relaxed text-ink-muted">{msg.body}</p>
    </Card>
  );
}

function idleLabelFor(rec: RecState): string {
  if (rec.k === "mic_error") return rec.problem === "insecure" ? "Microphone unavailable" : "Tap to try again";
  if (rec.k === "too_short") return "Tap to try again";
  return "Tap to record";
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}...`;
}
