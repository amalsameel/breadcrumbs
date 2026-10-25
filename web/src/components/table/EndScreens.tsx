// Non-live Table screens: loading, unknown session, ended, wiped.
import { motion } from "framer-motion";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import CrumbOrb from "../CrumbOrb";
import type { OrbMode } from "./format";
import { Icon } from "./Icon";
import { TableButton } from "./primitives";

function Centered({ orb, label, children }: { orb: OrbMode; label: string; children: ReactNode }) {
  return (
    <motion.div
      className="mx-auto flex w-full max-w-[1460px] flex-1 flex-col items-center justify-center gap-9 px-4 py-12 text-center sm:px-8"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className="size-[min(30vh,260px,64vw)]">
        <CrumbOrb mode={orb} label={label} />
      </div>
      {children}
    </motion.div>
  );
}

const HOME_LINK =
  "inline-flex min-h-[54px] items-center gap-2 rounded-full bg-ink px-6 text-[17px] font-medium text-on-ink transition-colors hover:bg-ink/85";

const HEADLINE = "font-display font-normal leading-[1.06] tracking-[-0.04em] text-ink text-balance";

export function LoadingScreen() {
  return (
    <Centered orb="idle" label="Breadcrumbs: loading">
      <p className="font-display text-[32px] tracking-[-0.03em] text-ink-muted" role="status">
        Setting the table
      </p>
    </Centered>
  );
}

export function NotFoundScreen() {
  return (
    <Centered orb="paused" label="Breadcrumbs: table cleared">
      <div className="max-w-xl space-y-3">
        <h1 className={`${HEADLINE} text-[clamp(36px,4vw,56px)]`}>This table has been cleared.</h1>
        <p className="text-[18px] leading-[1.5] text-ink-muted">
          The link may be wrong, or the session already ended. Sessions are deleted when they end or after two hours.
        </p>
      </div>
      <Link to="/" className={HOME_LINK}>
        <Icon name="home" size={18} />
        Set a new table
      </Link>
    </Centered>
  );
}

export function UnreachableScreen({ message }: { message: string | null }) {
  return (
    <Centered orb="idle" label="Breadcrumbs: reconnecting">
      <div className="max-w-xl space-y-4">
        <h1 className={`${HEADLINE} text-[clamp(36px,4vw,56px)]`}>Looking for the server.</h1>
        <p className="text-[18px] leading-[1.5] text-ink-muted">Breadcrumbs keeps retrying on its own. {message ? `(${message})` : ""}</p>
      </div>
    </Centered>
  );
}

interface EndedScreenProps {
  crumbCount: number;
  onWipe(): Promise<void>;
}

export function EndedScreen({ crumbCount, onWipe }: EndedScreenProps) {
  const [wiping, setWiping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const wipe = async () => {
    setWiping(true);
    setError(null);
    try {
      await onWipe();
    } catch {
      setError("Could not reach the server to delete the session. It is still deleted automatically within two hours.");
      setWiping(false);
    }
  };

  return (
    <Centered orb="idle" label="Breadcrumbs: session ended">
      <div className="max-w-2xl space-y-4">
        <h1 className={`${HEADLINE} text-[clamp(44px,5vw,68px)]`}>Check your phones.</h1>
        <p className="text-[19px] leading-[1.5] text-ink">
          Each of you can choose to keep in touch. Nothing is shared unless you both say yes.
        </p>
        {crumbCount > 0 && (
          <p className="inline-flex min-h-8 items-center rounded-full border border-line bg-bg-raised px-3 font-mono text-[12px] uppercase tracking-[0.14em] text-ink-muted">
            {crumbCount} {crumbCount === 1 ? "crumb" : "crumbs"} dropped
          </p>
        )}
      </div>
      <div className="flex flex-col items-center gap-3">
        <TableButton icon="trash" onClick={wipe} disabled={wiping}>
          {wiping ? "Deleting" : "Wipe now"}
        </TableButton>
        <p className="max-w-md text-[14px] leading-relaxed text-ink-muted">
          Deletes the transcript, both profiles and the session right away. Phones that have not opened their trail yet will
          not see it.
        </p>
        {error && (
          <p role="alert" className="max-w-md rounded-2xl border border-danger/30 bg-danger/5 px-4 py-2.5 text-[14px] text-ink">
            {error}
          </p>
        )}
      </div>
    </Centered>
  );
}

export function WipedScreen() {
  return (
    <Centered orb="paused" label="Breadcrumbs: session deleted">
      <div className="space-y-4">
        <h1 className={`${HEADLINE} text-[clamp(36px,4.4vw,60px)]`}>Session deleted. Nothing was kept.</h1>
        <p className="text-[18px] text-ink-muted">No audio, no transcript, no profiles.</p>
      </div>
      <Link to="/" className={HOME_LINK}>
        <Icon name="home" size={18} />
        Back home
      </Link>
    </Centered>
  );
}
