// Home (/): pick a mode, optionally Quick profile, then "Set the table" creates a session and opens the Table.
// Light, airy landing in the product's own words: a big regular-weight headline, then a rounded showcase
// whose orb carousel is the mode picker. Color lives only inside the orbs.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent, RefObject } from "react";
import { MotionConfig, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import CrumbOrb from "../components/CrumbOrb";
import { Button } from "../components/ui/Button";
import { OrbDot, Wordmark } from "../components/ui/Crumbs";
import { classifyGesture, keepsOrb, nearestPos, SLOT_REACH, wrapIndex } from "../components/ui/carousel";
import { MODE_COPY, MODES } from "../components/ui/modes";
import { Pill } from "../components/ui/Pill";
import { Segmented } from "../components/ui/Segmented";
import { Spinner } from "../components/ui/Spinner";
import { api } from "../lib/api";
import type { AppConfig, Mode } from "../lib/types";

const PROMISES = [
  { title: "No audio is ever stored", body: "Sound is streamed for transcription and never saved anywhere." },
  {
    title: "Transcripts are wiped",
    body: "They live in memory only and are deleted when you finish, or after 2 hours at most.",
  },
  { title: "Only what you share", body: "Breadcrumbs brings up only what each of you approves on your own profile." },
];

const STEPS = [
  {
    title: "Scan",
    body: "Each of you scans a code on the Table, signs the consent and answers a few quick voice questions.",
    palette: "ember",
  },
  {
    title: "Talk",
    body: "Phones face down. Breadcrumbs listens for the lulls and only then drops a short spoken crumb.",
    palette: "lilac",
  },
  {
    title: "Keep the trail",
    body: "At the end, swap contacts if you both say yes, and keep three threads to pick up next time.",
    palette: "citrus",
  },
] as const;

type ProfileLength = "full" | "quick";

export default function Home() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("social");
  const [profileLength, setProfileLength] = useState<ProfileLength>("full");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function setTheTable(forMode: Mode = mode) {
    if (creating) return;
    setCreating(true);
    setError(null);
    try {
      const res = await api.createSession(forMode, profileLength === "quick");
      navigate(`/table/${res.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? `Couldn't set the table: ${err.message}` : "Couldn't set the table.");
      setCreating(false);
    }
  }

  return (
    <MotionConfig reducedMotion="user">
      <div className="min-h-dvh overflow-x-clip bg-bg text-ink">
        <a
          href="#showcase"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-full focus:bg-ink focus:px-4 focus:py-2 focus:text-on-ink"
        >
          Skip to the modes
        </a>
        <div
          className="mx-auto w-full max-w-[1320px]"
          style={{
            paddingLeft: "max(1rem, env(safe-area-inset-left))",
            paddingRight: "max(1rem, env(safe-area-inset-right))",
            paddingBottom: "max(2rem, env(safe-area-inset-bottom))",
          }}
        >
          <TopNav creating={creating} onStart={() => void setTheTable()} />

          <main>
            <Hero creating={creating} onStart={() => void setTheTable()} />

            <Showcase
              mode={mode}
              onModeChange={setMode}
              profileLength={profileLength}
              onProfileLengthChange={setProfileLength}
              creating={creating}
              onStart={(m) => void setTheTable(m)}
            />

            <HowItWorks />
            <PrivacyPromise />
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

// ---- Nav + hero -----------------------------------------------------------------------

function TopNav({ creating, onStart }: { creating: boolean; onStart: () => void }) {
  return (
    <header
      className="flex h-20 items-center justify-between gap-4"
      style={{ paddingTop: "env(safe-area-inset-top)" }}
    >
      <div className="flex items-center gap-10">
        <a href="/" aria-label="Breadcrumbs home" className="rounded-full">
          <Wordmark size="lg" />
        </a>
        <nav aria-label="Page" className="hidden items-center gap-1 md:flex">
          <SectionLink to="how">How it works</SectionLink>
          <SectionLink to="privacy">Privacy</SectionLink>
        </nav>
      </div>
      <Button size="md" onClick={onStart} loading={creating}>
        Set the table
      </Button>
    </header>
  );
}

function SectionLink({ to, children }: { to: string; children: string }) {
  return (
    <a
      href={`#${to}`}
      onClick={(e) => {
        e.preventDefault();
        scrollToSection(to);
      }}
      className="inline-flex min-h-11 items-center rounded-full px-3.5 text-[16px] font-medium text-ink transition-colors hover:bg-ink/5"
    >
      {children}
    </a>
  );
}

/** Scroll to a section (smoothly unless reduced motion) and move focus to its heading. */
function scrollToSection(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  const heading = document.getElementById(`${id}-title`);
  heading?.focus({ preventScroll: true });
  if (window.location.hash !== `#${id}`) window.history.replaceState(null, "", `#${id}`);
}

function Hero({ creating, onStart }: { creating: boolean; onStart: () => void }) {
  return (
    <motion.section
      aria-labelledby="hero-title"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      className="grid gap-6 pt-10 pb-10 sm:pt-16 sm:pb-14 lg:grid-cols-12 lg:gap-12 lg:pt-20"
    >
      <div className="lg:col-span-7">
        <h1
          id="hero-title"
          className="font-display text-[48px] leading-[1.02] font-normal tracking-[-0.04em] text-ink sm:text-[60px] lg:text-[72px]"
        >
          Never lose
          <br />
          the thread.
        </h1>
      </div>
      <div className="flex flex-col gap-7 lg:col-span-5 lg:justify-end lg:pb-2">
        <p className="max-w-[34rem] text-[18px] leading-[1.5] text-ink sm:text-[20px]">
          A quiet host for meeting someone new. Breadcrumbs listens for the lulls, and only when a conversation stalls
          does it drop a crumb: a short spoken prompt drawn from what you both chose to share.
        </p>
      </div>
      <div className="flex flex-wrap gap-3 lg:col-span-12 lg:-mt-4">
        <Button onClick={onStart} loading={creating} className="min-w-[10.5rem]">
          Set the table
        </Button>
        <a
          href="#how"
          onClick={(e) => {
            e.preventDefault();
            scrollToSection("how");
          }}
          className="inline-flex min-h-[54px] cursor-pointer items-center justify-center rounded-full bg-bg-raised px-7 text-[17px] font-medium tracking-[-0.01em] text-ink shadow-pill transition-colors hover:bg-bg"
        >
          How it works
        </a>
      </div>
    </motion.section>
  );
}

// ---- Showcase: mode tabs + orb carousel -----------------------------------------------------

interface ShowcaseProps {
  mode: Mode;
  onModeChange: (m: Mode) => void;
  profileLength: ProfileLength;
  onProfileLengthChange: (p: ProfileLength) => void;
  creating: boolean;
  onStart: (m: Mode) => void;
}

function Showcase({ mode, onModeChange, profileLength, onProfileLengthChange, creating, onStart }: ShowcaseProps) {
  // `pos` is an unbounded index so the carousel can wrap without items jumping across the stage.
  const [pos, setPos] = useState(() => MODES.indexOf(mode));
  const current = MODES[wrapIndex(pos, MODES.length)];

  useEffect(() => {
    if (current !== mode) onModeChange(current);
  }, [current, mode, onModeChange]);

  const step = useCallback((delta: number) => setPos((p) => p + delta), []);
  const select = useCallback((m: Mode) => setPos((p) => nearestPos(p, MODES.indexOf(m), MODES.length)), []);

  return (
    <section
      id="showcase"
      aria-labelledby="showcase-title"
      className="scroll-mt-6 overflow-hidden rounded-[28px] border border-line bg-bg-sunken/55"
    >
      <h2 id="showcase-title" className="sr-only">
        Choose a mode
      </h2>
      <div className="flex justify-center px-2 pt-4 min-[360px]:px-3 sm:pt-6">
        <Segmented
          label="Mode"
          value={current}
          onChange={select}
          size="lg"
          options={MODES.map((m) => ({
            value: m,
            label: MODE_COPY[m].label,
            icon: (
              <span aria-hidden="true" className="hidden sm:inline-flex">
                <OrbDot palette={MODE_COPY[m].palette} size={16} />
              </span>
            ),
          }))}
        />
      </div>

      <OrbCarousel pos={pos} onStep={step} onPick={setPos} creating={creating} onStart={onStart} />

      <div className="flex flex-col gap-4 border-t border-line/70 px-4 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-4">
          <Segmented
            label="Profile length"
            value={profileLength}
            onChange={onProfileLengthChange}
            describedBy="profile-length-hint"
            className="shrink-0 self-start"
            options={[
              { value: "full", label: "Full profile" },
              { value: "quick", label: "Quick profile" },
            ]}
          />
          <p id="profile-length-hint" className="px-1 text-[14px] text-ink-muted">
            {profileLength === "quick" ? "One question each. Great for demos." : "Three short voice questions each."}
          </p>
        </div>
        <Button onClick={() => onStart(current)} loading={creating} className="w-full shrink-0 sm:w-auto">
          Set the table
        </Button>
      </div>
    </section>
  );
}

interface Geometry {
  center: number; // active orb size
  neighbor: number; // neighbor orb size (the active orb scaled down)
  x1: number; // horizontal offset of a neighbor from the stage center
  x2: number; // offset of the faded outer orbs
  captionActive: number; // caption widths, kept clear of the chevrons
  captionNeighbor: number;
  chevronX: number; // offset of each chevron's center from the stage center
  wide: boolean;
}

const CHEVRON = 44;

function geometryFor(width: number): Geometry {
  const wide = width >= 640;
  const center = width >= 1100 ? 320 : wide ? 260 : Math.max(190, Math.min(240, Math.round(width * 0.6)));
  const neighbor = Math.round(center * 0.78);
  const gap = width >= 1100 ? 72 : wide ? 48 : 18;
  const x1 = center / 2 + gap + neighbor / 2;
  const x2 = x1 + neighbor + gap;
  if (!wide) {
    // Narrow: only the active caption shows; the chevrons sit at its sides, inside the stage.
    const chevronX = width / 2 - 8 - CHEVRON / 2;
    return { center, neighbor, x1, x2, captionActive: Math.max(160, width - 2 * (CHEVRON + 16)), captionNeighbor: 0, chevronX, wide };
  }
  const captionActive = Math.min(center, 320);
  const captionNeighbor = neighbor;
  const chevronX = (captionActive / 2 + (x1 - captionNeighbor / 2)) / 2;
  return { center, neighbor, x1, x2, captionActive, captionNeighbor, chevronX, wide };
}

function useWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

interface OrbCarouselProps {
  pos: number;
  onStep: (delta: number) => void;
  onPick: (pos: number) => void;
  creating: boolean;
  onStart: (m: Mode) => void;
}

const CAPTION_GAP = 28;

function OrbCarousel({ pos, onStep, onPick, creating, onStart }: OrbCarouselProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const g = geometryFor(width || 375);
  const swipe = useRef<{ x: number; y: number; id: number } | null>(null);
  // Set when a pointer gesture was a swipe or a drag, so the click the browser fires afterwards on a
  // neighbor orb does not also select it (which would undo or reverse the swipe).
  const suppressClick = useRef(false);
  const current = MODES[wrapIndex(pos, MODES.length)];
  const copy = MODE_COPY[current];
  const stageHeight = g.center + CAPTION_GAP + (g.wide ? 120 : 112);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.target instanceof HTMLElement && e.target.closest('[role="radiogroup"]')) return;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      onStep(-1);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      onStep(1);
    }
  }

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    swipe.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
    suppressClick.current = false;
  }
  function onPointerUp(e: ReactPointerEvent<HTMLDivElement>) {
    const s = swipe.current;
    swipe.current = null;
    if (!s || s.id !== e.pointerId) return;
    const gesture = classifyGesture(e.clientX - s.x, e.clientY - s.y);
    suppressClick.current = gesture.kind !== "tap";
    if (gesture.kind === "swipe") onStep(gesture.delta);
  }
  // Capture phase, so the click is swallowed before any orb or the play button sees it. Keyboard
  // activation (detail 0) is never swallowed.
  function onClickCapture(e: ReactMouseEvent<HTMLDivElement>) {
    if (!suppressClick.current) return;
    suppressClick.current = false;
    if (e.detail === 0) return;
    e.preventDefault();
    e.stopPropagation();
  }

  const slots = [];
  for (let k = pos - SLOT_REACH; k <= pos + SLOT_REACH; k++) slots.push(k);
  const playSize = g.wide ? 72 : 64;

  return (
    <div
      role="group"
      aria-roledescription="carousel"
      aria-label="Modes"
      onKeyDown={onKeyDown}
      className="relative"
    >
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {`${copy.label}, mode ${MODES.indexOf(current) + 1} of ${MODES.length}. ${copy.detail}`}
      </p>
      <div
        ref={ref}
        className="relative mt-6 select-none sm:mt-10"
        style={{ height: stageHeight, touchAction: "pan-y" }}
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerCancel={() => (swipe.current = null)}
        onClickCapture={onClickCapture}
      >
        {width > 0 &&
          slots.map((k) => {
            const off = k - pos;
            const m = MODES[wrapIndex(k, MODES.length)];
            return <CarouselSlot key={k} mode={m} offset={off} geometry={g} onPick={() => onPick(k)} />;
          })}

        {/* One persistent play button over the active orb. It lives outside the keyed slots, so it keeps
            keyboard focus while the orbs slide underneath it (arrow keys can step again and again). */}
        {width > 0 && (
          <div
            className="absolute left-1/2 z-20"
            style={{ top: g.center / 2 - playSize / 2, marginLeft: -playSize / 2, width: playSize, height: playSize }}
          >
            <button
              type="button"
              data-carousel-play=""
              onClick={() => onStart(current)}
              disabled={creating}
              aria-label={`Set the table for ${copy.label}`}
              className={
                "grid h-full w-full cursor-pointer place-items-center rounded-full bg-bg-raised text-ink " +
                "shadow-[0_2px_6px_rgb(12_11_10/0.12),0_12px_32px_-8px_rgb(12_11_10/0.3)] " +
                "transition-transform duration-150 hover:scale-105 active:scale-95 disabled:cursor-wait [touch-action:manipulation]"
              }
            >
              {creating ? <Spinner size={22} /> : <PlayIcon />}
            </button>
          </div>
        )}

        {/* Prev / next sit between the captions, level with the active title. */}
        {width > 0 &&
          (["prev", "next"] as const).map((dir) => (
            <div
              key={dir}
              className="absolute left-1/2 z-20"
              style={{
                top: g.center + CAPTION_GAP - 8,
                marginLeft: (dir === "prev" ? -g.chevronX : g.chevronX) - CHEVRON / 2,
              }}
            >
              <ChevronButton dir={dir} onClick={() => onStep(dir === "prev" ? -1 : 1)} />
            </div>
          ))}
      </div>
    </div>
  );
}

interface CarouselSlotProps {
  mode: Mode;
  offset: number;
  geometry: Geometry;
  onPick: () => void;
}

function CarouselSlot({ mode, offset, geometry: g, onPick }: CarouselSlotProps) {
  const copy = MODE_COPY[mode];
  const dist = Math.abs(offset);
  const active = offset === 0;
  const x = offset === 0 ? 0 : Math.sign(offset) * (dist === 1 ? g.x1 : dist === 2 ? g.x2 : g.x2 + g.neighbor + 80);
  const scale = active ? 1 : g.neighbor / g.center;
  const opacity = dist === 0 ? 1 : dist === 1 ? 1 : dist === 2 ? 0.4 : 0;
  const showCaption = active || (dist === 1 && g.wide);
  const captionWidth = active ? g.captionActive : g.captionNeighbor;

  return (
    <motion.div
      aria-hidden={active ? undefined : true}
      className="absolute top-0 left-1/2"
      style={{ width: g.center, marginLeft: -g.center / 2, zIndex: 10 - dist }}
      initial={false}
      animate={{ x, opacity }}
      transition={{ type: "spring", stiffness: 210, damping: 30, mass: 0.9 }}
    >
      <motion.div
        className="relative"
        style={{ width: g.center, height: g.center }}
        initial={false}
        animate={{ scale }}
        transition={{ type: "spring", stiffness: 210, damping: 30, mass: 0.9 }}
        onClick={active ? undefined : onPick}
      >
        <div className={`absolute inset-0 ${active ? "" : "cursor-pointer"}`}>
          {keepsOrb(dist) && <CrumbOrb mode="listening" palette={copy.palette} still={dist >= 2} />}
        </div>
      </motion.div>

      <motion.div
        className="absolute left-1/2 text-center"
        style={{ top: g.center + CAPTION_GAP, width: captionWidth, marginLeft: -captionWidth / 2 }}
        initial={false}
        animate={{ opacity: showCaption ? 1 : 0 }}
        transition={{ duration: 0.25 }}
      >
        <p
          className={
            "flex items-center justify-center gap-1.5 text-[19px] font-medium tracking-[-0.01em] sm:text-[20px] " +
            (active ? "text-ink" : "text-ink-faint")
          }
        >
          {copy.label}
          {active && <ArrowUpRight />}
        </p>
        <p className={`mx-auto mt-1.5 text-[15px] leading-[1.45] sm:text-[16px] ${active ? "text-ink-muted" : "text-ink-faint"}`}>
          {copy.tagline}
        </p>
      </motion.div>
    </motion.div>
  );
}

function ChevronButton({ dir, onClick }: { dir: "prev" | "next"; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={dir === "prev" ? "Previous mode" : "Next mode"}
      className="grid h-11 w-11 cursor-pointer place-items-center rounded-full bg-bg-raised text-ink shadow-pill transition-colors hover:bg-bg [touch-action:manipulation]"
    >
      <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d={dir === "prev" ? "M15 5l-7 7 7 7" : "M9 5l7 7-7 7"} />
      </svg>
    </button>
  );
}

function PlayIcon() {
  return (
    <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" className="ml-1">
      <path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z" fill="currentColor" />
    </svg>
  );
}

function ArrowUpRight() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M7 17L17 7M9 7h8v8" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

// ---- How it works + privacy -----------------------------------------------------------------

function HowItWorks() {
  return (
    <section id="how" aria-labelledby="how-title" className="scroll-mt-6 pt-20 sm:pt-28">
      <div className="mb-8 flex flex-col gap-3 sm:mb-12 lg:flex-row lg:items-end lg:justify-between">
        <h2
          id="how-title"
          tabIndex={-1}
          className="font-display text-[36px] leading-[1.05] font-normal tracking-[-0.035em] outline-none sm:text-[48px]"
        >
          How it works
        </h2>
        <p className="max-w-md text-[17px] leading-[1.5] text-ink-muted">
          Two phones, one laptop between you, and a host that knows when to stay quiet.
        </p>
      </div>
      <ol className="grid gap-4 md:grid-cols-3">
        {STEPS.map((s, i) => (
          <li key={s.title} className="flex flex-col gap-10 rounded-3xl border border-line bg-bg-raised p-6 sm:p-8">
            <div className="flex items-center justify-between">
              <OrbDot palette={s.palette} size={28} />
              <span className="font-mono text-[13px] text-ink-muted">{String(i + 1).padStart(2, "0")}</span>
            </div>
            <div className="flex flex-col gap-2">
              <h3 className="text-[22px] font-medium tracking-[-0.02em] text-ink">{s.title}</h3>
              <p className="text-[16px] leading-[1.5] text-ink-muted">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function PrivacyPromise() {
  return (
    <section
      id="privacy"
      aria-labelledby="privacy-title"
      className="mt-20 scroll-mt-6 overflow-hidden rounded-[28px] border border-line bg-bg-sunken/55 p-6 sm:mt-28 sm:p-10"
    >
      <div className="grid gap-8 lg:grid-cols-12 lg:items-center">
        <div className="flex items-center gap-5 lg:col-span-4 lg:flex-col lg:items-start">
          <div aria-hidden="true" className="relative h-20 w-20 shrink-0 sm:h-28 sm:w-28">
            <CrumbOrb mode="paused" palette="sage" still />
          </div>
          <h2
            id="privacy-title"
            tabIndex={-1}
            className="font-display text-[32px] leading-[1.05] font-normal tracking-[-0.035em] outline-none sm:text-[40px]"
          >
            Our promise
          </h2>
        </div>
        <ul className="grid gap-6 sm:grid-cols-3 lg:col-span-8">
          {PROMISES.map((p) => (
            <li key={p.title} className="flex flex-col gap-2">
              <span aria-hidden="true" className="grid h-8 w-8 place-items-center rounded-full bg-bg-raised text-ink shadow-pill">
                <svg width="14" height="14" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2.5 6.2l2.3 2.3 4.7-4.9" />
                </svg>
              </span>
              <h3 className="text-[17px] font-medium text-ink">{p.title}</h3>
              <p className="text-[15px] leading-[1.5] text-ink-muted">{p.body}</p>
            </li>
          ))}
        </ul>
      </div>
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
    <footer
      className="mt-16 flex flex-col gap-4 border-t border-line pt-6 sm:flex-row sm:items-center sm:justify-between"
      aria-label="System status"
    >
      <p className="text-[13px] text-ink-muted">Made at HackTX 2026. Once upon a time, two strangers sat down at a table...</p>
      <div className="flex flex-wrap gap-2" aria-live="polite">
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
