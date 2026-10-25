// Home (/): one orb, one line, one choice. Pick how the table runs ("Just talk" by default, or "With
// profiles" with its Quick / Full profile option), then "Start a conversation" creates a session and
// opens the Table. Light and minimal: color lives only inside the orb.
import { useEffect, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import CrumbOrb from "../components/CrumbOrb";
import { Button } from "../components/ui/Button";
import { Wordmark } from "../components/ui/Crumbs";
import { Pill } from "../components/ui/Pill";
import { Segmented } from "../components/ui/Segmented";
import { api } from "../lib/api";
import type { AppConfig, Setup } from "../lib/types";

type ProfileLength = "full" | "quick";

const EASE_OUT: [number, number, number, number] = [0.22, 1, 0.36, 1];

/** The two setups, each with its one-line description under the switch. */
const SETUPS: { value: Setup; label: string; line: string }[] = [
  { value: "quick", label: "Just talk", line: "No phones, no forms. It picks things up from your conversation as you go." },
  { value: "profiles", label: "With profiles", line: "Each of you scans a code and answers a few voice questions first." },
];

export default function Home() {
  const navigate = useNavigate();
  const [setup, setSetup] = useState<Setup>("quick");
  const [profileLength, setProfileLength] = useState<ProfileLength>("full");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = SETUPS.find((s) => s.value === setup) ?? SETUPS[0];

  async function start() {
    if (creating) return;
    setCreating(true);
    setError(null);
    try {
      const res = await api.createSession(
        setup === "profiles" ? { setup, quick: profileLength === "quick" } : { setup },
      );
      navigate(`/table/${res.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? `Couldn't start: ${err.message}` : "Couldn't start a conversation.");
      setCreating(false);
    }
  }

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex min-h-dvh flex-col overflow-x-clip bg-bg text-ink">
        <div
          className="mx-auto flex w-full max-w-[1320px] flex-1 flex-col"
          style={{
            paddingLeft: "max(1rem, env(safe-area-inset-left))",
            paddingRight: "max(1rem, env(safe-area-inset-right))",
            paddingBottom: "max(1.5rem, env(safe-area-inset-bottom))",
          }}
        >
          <header className="flex h-20 items-center" style={{ paddingTop: "env(safe-area-inset-top)" }}>
            <a href="/" aria-label="Breadcrumbs home" className="rounded-full">
              <Wordmark size="lg" />
            </a>
          </header>

          <main className="flex flex-1 flex-col items-center justify-center py-6 text-center">
            <motion.div
              className="flex w-full max-w-[720px] flex-col items-center"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, ease: EASE_OUT }}
            >
              <div className="size-[min(32vh,300px,64vw)]">
                <CrumbOrb mode="listening" label="Breadcrumbs" />
              </div>

              <h1 className="mt-7 font-display text-[clamp(40px,min(6vw,7.5vh),64px)] leading-[1.02] font-normal tracking-[-0.04em] text-balance text-ink">
                Never lose the thread.
              </h1>
              <p className="mt-3 max-w-[40ch] text-[17px] leading-[1.5] text-ink-muted sm:text-[18px]">
                A quiet host for two people talking. When a pause runs long, it drops one short spoken crumb.
              </p>

              <div className="mt-7 flex w-full flex-col items-center gap-3">
                <Segmented
                  label="How the table runs"
                  value={setup}
                  onChange={setSetup}
                  describedBy="setup-line"
                  className="bg-bg-sunken"
                  options={SETUPS.map((s) => ({ value: s.value, label: s.label }))}
                />
                <p id="setup-line" className="min-h-[3em] max-w-[40ch] text-balance px-2 text-[15px] leading-[1.5] text-ink-muted" aria-live="polite">
                  {current.line}
                </p>
              </div>

              <AnimatePresence initial={false}>
                {setup === "profiles" && (
                  <motion.div
                    key="profile-length"
                    className="flex w-full flex-col items-center gap-1.5 overflow-hidden"
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.25, ease: EASE_OUT }}
                  >
                    <Segmented
                      label="Profile length"
                      value={profileLength}
                      onChange={setProfileLength}
                      describedBy="profile-length-hint"
                      className="bg-bg-sunken/70"
                      options={[
                        { value: "quick", label: "Quick profile" },
                        { value: "full", label: "Full profile" },
                      ]}
                    />
                    <p id="profile-length-hint" className="pb-1 text-[13px] text-ink-muted">
                      {profileLength === "quick" ? "One question each." : "Three short voice questions each."}
                    </p>
                  </motion.div>
                )}
              </AnimatePresence>

              <Button onClick={() => void start()} loading={creating} className="mt-5 w-full max-w-[320px]">
                Start a conversation
              </Button>
              <p className="mt-4 text-[13px] text-ink-muted">No audio is stored. Transcripts are wiped when you finish.</p>
            </motion.div>
          </main>

          <StatusLine />
        </div>

        {error && (
          <div
            className="fixed inset-x-0 bottom-0 z-40 flex justify-center px-4"
            style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom))" }}
          >
            <div
              role="alert"
              className="flex w-full max-w-md items-start gap-3 rounded-2xl border border-danger/30 bg-bg-raised py-3 pr-2 pl-4 text-[15px] text-ink shadow-card"
            >
              <span aria-hidden="true" className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-danger" />
              <p className="min-w-0 flex-1 py-0.5">{error}</p>
              <button
                type="button"
                onClick={() => setError(null)}
                aria-label="Dismiss"
                className="grid h-9 w-9 shrink-0 cursor-pointer place-items-center rounded-full text-ink-muted hover:bg-ink/5 hover:text-ink"
              >
                <CloseIcon />
              </button>
            </div>
          </div>
        )}
      </div>
    </MotionConfig>
  );
}

function CloseIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
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
    <footer
      className="flex flex-col items-center gap-3 border-t border-line pt-5 sm:flex-row sm:justify-between"
      aria-label="System status"
    >
      <p className="text-[13px] text-ink-muted">Made at HackTX 2026.</p>
      <div className="flex flex-wrap justify-center gap-2" aria-live="polite">
        {offline && <Pill tone="danger">Server not reachable</Pill>}
        {config && (
          <>
            <Pill tone={config.mock.muse ? "amber" : "ok"}>Muse: {config.mock.muse ? "mock" : "live"}</Pill>
            <Pill tone={config.mock.gemini ? "amber" : "ok"}>
              AI: {config.mock.gemini ? "mock" : `${providerName(config.ai_provider)}, live`}
            </Pill>
            {!config.ffmpeg && <Pill tone="danger">ffmpeg missing: voice answers can't be converted</Pill>}
            {config.dev_tools && <Pill>Dev tools on</Pill>}
          </>
        )}
      </div>
    </footer>
  );
}

function providerName(p: AppConfig["ai_provider"]): string {
  return p === "claude" ? "Claude" : p === "gemini" ? "Gemini" : "mock";
}
