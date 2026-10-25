// Phone flow (/join/:sid/:seat): consent -> interview -> profile -> review -> waiting -> live -> opt-in -> trail.
// The step is derived from the server summary plus monotonic local progress, so a reload resumes correctly.
import { useCallback, useEffect, useRef, useState } from "react";
import { MotionConfig } from "framer-motion";
import { useParams } from "react-router-dom";
import { ConsentForm } from "../components/ConsentForm";
import { BuildingStep } from "../components/phone/BuildingStep";
import { loadTrail, OptInStep, saveTrail, TrailStep } from "../components/phone/EndSteps";
import {
  deriveStep,
  interviewDoneOnLoad,
  interviewStartIndex,
  mySeat,
  NO_PROGRESS,
  otherSeat,
  parseSeat,
  partnerName,
  seatKey,
  stepIndex,
} from "../components/phone/flow";
import type { LocalProgress, StepKind } from "../components/phone/flow";
import { InterviewStep } from "../components/phone/InterviewStep";
import { PhoneScreen, StepHeading } from "../components/phone/PhoneScreen";
import { GoneStep, LiveStep, WaitingStep } from "../components/phone/QuietSteps";
import { ReviewStep } from "../components/phone/ReviewStep";
import { useSeatSession } from "../components/phone/useSeatSession";
import { Button } from "../components/ui/Button";
import { MODE_COPY } from "../components/ui/modes";
import { Spinner } from "../components/ui/Spinner";
import { api, ApiError } from "../lib/api";
import type { InterviewQuestion, Profile, Seat, SessionSummary, TrailResponse } from "../lib/types";

export default function Join() {
  const { sid, seat: seatRaw } = useParams();
  const seat = parseSeat(seatRaw);
  if (!sid || !seat) {
    return (
      <PhoneScreen>
        <GoneStep reason="bad_link" />
      </PhoneScreen>
    );
  }
  return (
    <MotionConfig reducedMotion="user">
      <SeatFlow key={`${sid}:${seat}`} sid={sid} seat={seat} />
    </MotionConfig>
  );
}

const MILESTONES = 4; // consent, interview, review, ready

function SeatFlow({ sid, seat }: { sid: string; seat: Seat }) {
  const session = useSeatSession(sid, seat);
  const { summary, refresh } = session;
  const summaryRef = useRef<SessionSummary | null>(summary);
  summaryRef.current = summary;

  const [questions, setQuestions] = useState<InterviewQuestion[] | null>(null);
  const [questionsError, setQuestionsError] = useState<string | null>(null);
  const [questionsAttempt, setQuestionsAttempt] = useState(0);
  const [local, setLocal] = useState<LocalProgress>(NO_PROGRESS);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [trail, setTrail] = useState<TrailResponse | null>(() => loadTrail(sid, seat));

  const mark = useCallback((key: keyof LocalProgress) => {
    setLocal((p) => (p[key] ? p : { ...p, [key]: true }));
  }, []);

  const describeError = useCallback(
    (err: unknown): string => {
      if (err instanceof ApiError) {
        if (err.status === 404) {
          void refresh(); // shows the "cleared" screen if the session itself is gone
          return "This session isn't available anymore.";
        }
        return err.message || "Something went wrong. Try again.";
      }
      return "Can't reach Breadcrumbs. Check your connection and try again.";
    },
    [refresh],
  );

  // Interview questions depend on the session's mode + quick flag.
  const mode = summary?.mode;
  const quick = summary?.quick;
  useEffect(() => {
    if (!mode || quick === undefined) return;
    let cancelled = false;
    setQuestionsError(null);
    api.interview(mode, quick).then(
      (res) => {
        if (cancelled) return;
        setQuestions(res.questions);
        // Resume: every answer was already uploaded before a reload, so go straight to building.
        const s = summaryRef.current;
        if (s && interviewDoneOnLoad(mySeat(s, seat), res.questions.length)) mark("interviewDone");
      },
      (err) => !cancelled && setQuestionsError(describeError(err)),
    );
    return () => {
      cancelled = true;
    };
  }, [mode, quick, seat, mark, describeError, questionsAttempt]);

  const onProfile = useCallback(
    (p: Profile) => {
      setProfile(p);
      mark("hasProfile");
    },
    [mark],
  );
  const onTrail = useCallback(
    (t: TrailResponse) => {
      setTrail(t);
      saveTrail(sid, seat, t);
    },
    [sid, seat],
  );
  const onInterviewDone = useCallback(() => mark("interviewDone"), [mark]);

  const step: StepKind | null = trail
    ? "trail"
    : summary && questions && session.status !== "gone"
      ? deriveStep(summary, seat, local)
      : null;
  useStepFocus(step);

  // The trail outlives the session (it is wiped right after both phones fetch it).
  if (trail) {
    return (
      <PhoneScreen modeLabel={summary ? MODE_COPY[summary.mode].label : undefined}>
        <TrailStep
          sid={sid}
          seat={seat}
          trail={trail}
          trailReadyTick={session.trailReadyTick}
          wiped={session.wiped}
          onTrail={onTrail}
          checkSession={refresh}
        />
      </PhoneScreen>
    );
  }

  if (session.status === "gone") {
    return (
      <PhoneScreen>
        <GoneStep reason="gone" />
      </PhoneScreen>
    );
  }

  if (!step || !summary) {
    const error = session.status === "error" ? session.error : questionsError;
    return (
      <PhoneScreen>
        <LoadingOrError
          error={error}
          onRetry={() => {
            void refresh();
            setQuestionsAttempt((n) => n + 1);
          }}
        />
      </PhoneScreen>
    );
  }

  const partner = partnerName(summary, seat);
  const partnerReady = summary.seats[seatKey(otherSeat(seat))].ready;
  const showProgress = stepIndex(step) < 3 || step === "waiting";
  const idx = stepIndex(step);

  return (
    <PhoneScreen
      modeLabel={MODE_COPY[summary.mode].label}
      progress={showProgress ? { current: idx, total: MILESTONES, label: `Step ${idx + 1} of ${MILESTONES}` } : undefined}
    >
      {step === "consent" && (
        <section aria-labelledby="step-title">
          <StepHeading id="step-title" eyebrow={`Seat ${seat}`} title="Before we listen" />
          <ConsentForm
            mode={summary.mode}
            partnerName={partner}
            onSubmit={async (name) => {
              try {
                await api.consent(sid, seat, name, true);
              } catch (err) {
                throw new Error(describeError(err));
              }
              mark("consented");
              void refresh();
            }}
          />
        </section>
      )}
      {step === "interview" && questions && (
        <InterviewStep
          sid={sid}
          seat={seat}
          questions={questions}
          startIndex={interviewStartIndex(mySeat(summary, seat).answered, questions.length)}
          onDone={onInterviewDone}
          describeError={describeError}
        />
      )}
      {step === "building" && <BuildingStep sid={sid} seat={seat} onBuilt={onProfile} describeError={describeError} />}
      {step === "review" && (
        <ReviewStep
          sid={sid}
          seat={seat}
          profile={profile}
          onProfile={onProfile}
          onReady={() => {
            mark("ready");
            void refresh();
          }}
          describeError={describeError}
        />
      )}
      {step === "waiting" && <WaitingStep partner={partner} partnerReady={partnerReady} />}
      {step === "live" && <LiveStep partner={partner} calibrating={summary.phase === "calibrating"} />}
      {step === "optin" && (
        <OptInStep
          mode={summary.mode}
          partner={partner}
          describeError={describeError}
          onSubmit={async (share, contact) => {
            await api.trailOptIn(sid, seat, share, contact);
            mark("optedIn");
          }}
        />
      )}
      {step === "trail" && (
        <TrailStep
          sid={sid}
          seat={seat}
          trail={trail}
          trailReadyTick={session.trailReadyTick}
          wiped={session.wiped}
          onTrail={onTrail}
          checkSession={refresh}
        />
      )}
    </PhoneScreen>
  );
}

/** Move focus to the new step's heading (screen readers hear the change) and scroll to the top. */
function useStepFocus(step: StepKind | null) {
  const first = useRef(true);
  useEffect(() => {
    if (!step) return;
    if (first.current) {
      first.current = false;
      return;
    }
    window.scrollTo({ top: 0 });
    const id = requestAnimationFrame(() => document.getElementById("step-title")?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(id);
  }, [step]);
}

function LoadingOrError({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  if (!error) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner size={32} label="Finding your table" className="text-ink" />
      </div>
    );
  }
  return (
    <section aria-labelledby="step-title" className="flex flex-1 flex-col justify-center">
      <StepHeading id="step-title" title="Can't reach the table" lead={error} />
      <Button block onClick={onRetry}>
        Try again
      </Button>
    </section>
  );
}
