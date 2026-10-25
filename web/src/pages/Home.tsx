// Home (/): pick a mode, optionally Quick profile, then "Set the table" creates a session and opens the Table.
import { useEffect, useState } from "react";
import { MotionConfig, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/ui/Button";
import { CrumbMark } from "../components/ui/Crumbs";
import { MODE_COPY, MODES } from "../components/ui/modes";
import { Pill } from "../components/ui/Pill";
import { Switch } from "../components/ui/Switch";
import { api } from "../lib/api";
import type { AppConfig, Mode } from "../lib/types";

const PROMISES = [
  "No audio is ever stored.",
  "Transcripts live in memory and are wiped when you finish, or after 2 hours.",
  "Breadcrumbs only brings up what each of you chooses to share.",
];

export default function Home() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("social");
  const [quick, setQuick] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function setTheTable() {
    setCreating(true);
    setError(null);
    try {
      const res = await api.createSession(mode, quick);
      navigate(`/table/${res.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? `Couldn't set the table: ${err.message}` : "Couldn't set the table.");
      setCreating(false);
    }
  }

  return (
    <MotionConfig reducedMotion="user">
      <div className="relative min-h-dvh overflow-x-hidden bg-bg text-ink">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 h-[520px] bg-[radial-gradient(55%_80%_at_50%_0%,rgba(245,165,36,0.16),transparent)]"
        />
        <TrailDecoration />
        <main
          className="relative mx-auto flex w-full max-w-xl flex-col gap-10 px-5"
          style={{
            paddingTop: "max(2.5rem, env(safe-area-inset-top))",
            paddingBottom: "max(2rem, env(safe-area-inset-bottom))",
            paddingLeft: "max(1.25rem, env(safe-area-inset-left))",
            paddingRight: "max(1.25rem, env(safe-area-inset-right))",
          }}
        >
          <Hero />

          <section aria-labelledby="mode-title" className="flex flex-col gap-4">
            <h2 id="mode-title" className="font-display text-[22px] font-semibold">
              Who's sitting down?
            </h2>
            <ModePicker value={mode} onChange={setMode} />
            <Switch
              checked={quick}
              onChange={setQuick}
              label="Quick profile"
              description="One question instead of three. Great for demos."
            />
          </section>

          <section className="flex flex-col gap-3">
            <Button block onClick={() => void setTheTable()} loading={creating}>
              Set the table
            </Button>
            <p className="text-center text-[15px] text-ink-muted">
              Opens the Table on this screen, with a QR code for each person.
            </p>
            {error && (
              <p role="alert" className="rounded-2xl border border-danger/50 bg-danger/10 px-4 py-3 text-[15px] text-ink">
                {error}
              </p>
            )}
          </section>

          <PrivacyPromise />
          <StatusLine />
        </main>
      </div>
    </MotionConfig>
  );
}

function Hero() {
  return (
    <motion.header
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
      className="flex flex-col items-start gap-4 pt-4"
    >
      <div className="flex items-center gap-2.5">
        <CrumbMark size={34} className="text-ink" />
        <span className="font-display text-[22px] font-semibold tracking-tight">Breadcrumbs</span>
      </div>
      <h1 className="font-display text-[44px] leading-[1.05] font-semibold tracking-tight text-balance sm:text-[56px]">
        Never lose the <span className="text-crumb">thread.</span>
      </h1>
      <p className="max-w-md text-[18px] leading-relaxed text-ink-muted">
        A quiet host for meeting someone new. It listens for the pauses, and only when a conversation stalls does it
        drop a crumb: a short spoken prompt drawn from what you both shared.
      </p>
    </motion.header>
  );
}

function ModePicker({ value, onChange }: { value: Mode; onChange: (m: Mode) => void }) {
  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="sr-only">Mode</legend>
      {MODES.map((m) => {
        const copy = MODE_COPY[m];
        const selected = value === m;
        return (
          <label
            key={m}
            className={
              "relative flex min-h-[76px] cursor-pointer items-start gap-4 rounded-3xl border p-4 transition-[border-color,background-color,box-shadow] duration-200 " +
              "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-crumb " +
              (selected
                ? "border-amber/70 bg-bg-raised shadow-[0_0_36px_-16px_rgba(245,165,36,0.8)]"
                : "border-line bg-bg-raised/50 hover:border-ink-faint")
            }
          >
            <input
              type="radio"
              name="mode"
              value={m}
              checked={selected}
              onChange={() => onChange(m)}
              className="sr-only"
            />
            <span
              aria-hidden="true"
              className={
                "mt-1 grid h-5 w-5 shrink-0 place-items-center rounded-full border-2 transition-colors " +
                (selected ? "border-crumb" : "border-ink-faint")
              }
            >
              <span className={`h-2.5 w-2.5 rounded-full bg-crumb transition-transform ${selected ? "scale-100" : "scale-0"}`} />
            </span>
            <span className="flex min-w-0 flex-col gap-1">
              <span className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-[18px] font-semibold text-ink">{copy.label}</span>
                <span className="text-[15px] text-ink-muted">{copy.blurb}</span>
                {m === "social" && <span className="text-[13px] text-ink-muted">(default)</span>}
              </span>
              <span className="text-[15px] leading-snug text-ink-muted">{copy.detail}</span>
            </span>
          </label>
        );
      })}
    </fieldset>
  );
}

function PrivacyPromise() {
  return (
    <section aria-labelledby="privacy-title" className="rounded-3xl border border-line bg-bg-sunken p-5">
      <h2 id="privacy-title" className="mb-3 text-[16px] font-semibold text-ink">
        Our promise
      </h2>
      <ul className="flex flex-col gap-2.5">
        {PROMISES.map((p) => (
          <li key={p} className="flex gap-3 text-[15px] leading-snug text-ink-muted">
            <span aria-hidden="true" className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-amber" />
            {p}
          </li>
        ))}
      </ul>
    </section>
  );
}

function StatusLine() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.config().then(
      (c) => !cancelled && setConfig(c),
      () => !cancelled && setOffline(true),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <footer className="flex flex-col gap-3 border-t border-line/60 pt-5" aria-label="System status">
      <div className="flex flex-wrap gap-2" aria-live="polite">
        {offline && <Pill tone="danger">Server not reachable</Pill>}
        {config && (
          <>
            <Pill tone={config.mock.muse ? "amber" : "ok"}>Muse: {config.mock.muse ? "mock mode" : "live"}</Pill>
            <Pill tone={config.mock.gemini ? "amber" : "ok"}>Gemini: {config.mock.gemini ? "mock mode" : "live"}</Pill>
            {!config.ffmpeg && <Pill tone="danger">ffmpeg missing: voice answers can't be converted</Pill>}
            {config.dev_tools && <Pill>Dev tools on</Pill>}
          </>
        )}
      </div>
      <p className="text-[13px] text-ink-muted">Made at HackTX 2026. Once upon a time, two strangers sat down at a table...</p>
    </footer>
  );
}

/** A faint dotted trail of crumbs across the top of the page. Decorative only. */
function TrailDecoration() {
  return (
    <svg
      aria-hidden="true"
      className="pointer-events-none absolute top-6 right-0 w-[70%] max-w-[520px] opacity-50"
      viewBox="0 0 400 120"
      fill="none"
    >
      <path
        d="M0 100 C 80 90, 110 40, 190 50 S 320 20, 400 8"
        stroke="#F5A524"
        strokeOpacity="0.35"
        strokeWidth="1.5"
        strokeDasharray="1 9"
        strokeLinecap="round"
      />
      {[
        [40, 95, 2],
        [120, 66, 2.5],
        [200, 49, 3],
        [280, 34, 3.5],
        [360, 16, 4],
      ].map(([x, y, r]) => (
        <circle key={x} cx={x} cy={y} r={r} fill="#FFD27A" fillOpacity="0.7" />
      ))}
    </svg>
  );
}
