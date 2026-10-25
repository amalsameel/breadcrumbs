// Non-live Table screens: loading, unknown session, ended, wiped.
import { motion } from "framer-motion";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import CrumbOrb from "../CrumbOrb";
import type { OrbMode } from "./format";
import { Icon } from "./Icon";
import { TableButton } from "./primitives";

function Centered({ orb, children }: { orb: OrbMode; children: ReactNode }) {
  return (
    <motion.div
      className="flex flex-1 flex-col items-center justify-center gap-8 px-6 py-10 text-center"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.6 }}
    >
      <div className="size-[min(22vh,180px)]">
        <CrumbOrb mode={orb} />
      </div>
      {children}
    </motion.div>
  );
}

const HOME_LINK =
  "inline-flex min-h-12 items-center gap-2 rounded-2xl border border-line bg-bg-raised/80 px-5 font-medium text-ink transition-colors hover:border-ink-faint";

export function LoadingScreen() {
  return (
    <Centered orb="idle">
      <p className="font-display text-3xl text-ink-muted" role="status">
        Setting the table
      </p>
    </Centered>
  );
}

export function NotFoundScreen() {
  return (
    <Centered orb="paused">
      <div className="max-w-xl space-y-3">
        <h1 className="font-display text-[clamp(32px,3vw,44px)] leading-tight text-ink">This table has been cleared.</h1>
        <p className="text-base text-ink-muted">
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
    <Centered orb="idle">
      <div className="max-w-xl space-y-3">
        <h1 className="font-display text-[clamp(30px,3vw,42px)] leading-tight text-ink">Looking for the server.</h1>
        <p className="text-base text-ink-muted">Breadcrumbs keeps retrying on its own. {message ? `(${message})` : ""}</p>
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
    <Centered orb="idle">
      <div className="max-w-2xl space-y-4">
        <h1 className="font-display text-[clamp(44px,4.4vw,64px)] leading-tight text-ink">Check your phones.</h1>
        <p className="text-lg text-ink-muted">
          Each of you can choose to keep in touch. Nothing is shared unless you both say yes.
        </p>
        {crumbCount > 0 && (
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-ink-faint">
            {crumbCount} {crumbCount === 1 ? "crumb" : "crumbs"} dropped
          </p>
        )}
      </div>
      <div className="flex flex-col items-center gap-3">
        <TableButton icon="trash" onClick={wipe} disabled={wiping}>
          {wiping ? "Deleting" : "Wipe now"}
        </TableButton>
        <p className="max-w-md text-sm text-ink-faint">
          Deletes the transcript, both profiles and the session right away. Phones that have not opened their trail yet will
          not see it.
        </p>
        {error && (
          <p role="alert" className="max-w-md text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Centered>
  );
}

export function WipedScreen() {
  return (
    <Centered orb="paused">
      <div className="space-y-3">
        <h1 className="font-display text-[clamp(36px,3.6vw,52px)] leading-tight text-ink">Session deleted. Nothing was kept.</h1>
        <p className="text-base text-ink-muted">No audio, no transcript, no profiles.</p>
      </div>
      <Link to="/" className={HOME_LINK}>
        <Icon name="home" size={18} />
        Back home
      </Link>
    </Centered>
  );
}
