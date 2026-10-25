// The calm screens: waiting for the partner, "phones face down" during the conversation, and "gone".
import { Link } from "react-router-dom";
import { BreathingCrumb } from "../ui/Crumbs";
import { waitingLine } from "./flow";
import { StepHeading } from "./PhoneScreen";

export function WaitingStep({ partner, partnerReady }: { partner: string | null; partnerReady: boolean }) {
  return (
    <section aria-labelledby="step-title" className="flex flex-1 flex-col items-center justify-center text-center">
      <BreathingCrumb size={150} />
      <div className="mt-8" aria-live="polite">
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
      <BreathingCrumb size={190} />
      <div className="mt-8">
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
        <p className="text-[15px] leading-relaxed text-ink-muted" aria-live="polite">
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
      <div className="opacity-60">
        <BreathingCrumb size={110} />
      </div>
      <div className="mt-6">
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
        className="inline-flex min-h-12 items-center rounded-2xl border border-line bg-bg-raised px-5 text-[16px] font-semibold text-ink hover:border-ink-faint"
      >
        Go to Breadcrumbs
      </Link>
    </section>
  );
}
