// The calm screens: waiting for the partner, "phones face down" during the conversation, and "gone".
import { Link } from "react-router-dom";
import { OrbFigure } from "../ui/Crumbs";
import { waitingLine } from "./flow";
import { StepHeading } from "./PhoneScreen";

export function WaitingStep({ partner, partnerReady }: { partner: string | null; partnerReady: boolean }) {
  return (
    <section aria-labelledby="step-title" className="flex flex-1 flex-col items-center justify-center text-center">
      <OrbFigure size={200} mode={partnerReady ? "warming" : "idle"} />
      <div className="mt-10" aria-live="polite">
        <StepHeading
          id="step-title"
          title={waitingLine(partner, partnerReady)}
          lead="Breadcrumbs starts as soon as you're both ready. Keep this page open."
        />
      </div>
    </section>
  );
}

export function LiveStep({ partner, calibrating }: { partner: string | null; calibrating: boolean }) {
  return (
    <section aria-labelledby="step-title" className="flex flex-1 flex-col items-center justify-center text-center">
      <OrbFigure size={240} mode="listening" />
      <div className="mt-10">
        <StepHeading
          id="step-title"
          title="You're all set."
          lead={
            <>
              Put your phone face down and enjoy the conversation
              {partner ? (
                <>
                  {" "}
                  with <span className="text-ink">{partner}</span>
                </>
              ) : null}
              .
            </>
          }
        />
        <p
          className="mx-auto -mt-2 inline-flex max-w-sm rounded-2xl bg-bg-raised px-4 py-3 text-[15px] leading-relaxed text-ink-muted shadow-pill"
          aria-live="polite"
        >
          {calibrating
            ? "First, the Table will ask you each to say hi, so it can tell your voices apart."
            : "Everything happens on the Table now. This page will wake up when you're done."}
        </p>
      </div>
    </section>
  );
}

export function GoneStep({ reason }: { reason: "gone" | "bad_link" }) {
  const badLink = reason === "bad_link";
  return (
    <section aria-labelledby="step-title" className="flex flex-1 flex-col items-center justify-center text-center">
      <OrbFigure size={150} mode="paused" palette="sage" still />
      <div className="mt-10">
        <StepHeading
          id="step-title"
          title={badLink ? "This link doesn't look right" : "This table has been cleared"}
          lead={
            badLink
              ? "Scan the QR code on the Table again to join."
              : "The session ended or expired, and everything from it was deleted. No audio was ever stored."
          }
        />
      </div>
      <Link
        to="/"
        className="inline-flex min-h-[54px] items-center rounded-full bg-bg-raised px-7 text-[17px] font-medium text-ink shadow-pill transition-colors hover:bg-bg"
      >
        Go to Breadcrumbs
      </Link>
    </section>
  );
}

/** A phone opened the link of a "Just talk" table: everything happens on the laptop. */
export function NoPhonesStep() {
  return (
    <section aria-labelledby="step-title" className="flex flex-1 flex-col items-center justify-center text-center">
      <OrbFigure size={180} mode="listening" />
      <div className="mt-10">
        <StepHeading
          id="step-title"
          title="This table doesn't need phones"
          lead="It runs on the laptop. Put your phone away and just talk."
        />
      </div>
    </section>
  );
}
