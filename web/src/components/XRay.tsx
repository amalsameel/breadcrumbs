// X-ray drawer (PRD 6.6, F18, 14.3): the engine's reasoning, live, in monospace readouts.
import { motion } from "framer-motion";
import { useEffect, useRef, type ReactNode } from "react";
import type { EngineSnapshot, Memory, Seat } from "../lib/types";
import type { TableActions, TableView } from "../lib/useTable";
import Sparkline from "./Sparkline";
import { Icon } from "./table/Icon";
import { fmt, noiseSuspected, sourceCaption, staleThreshold } from "./table/format";
import { isQuick } from "./table/tableState";
import { TuningPanel } from "./table/TuningPanel";
import { DevTurnBox } from "./table/DevTurnBox";

interface XRayProps {
  view: TableView;
  actions: TableActions;
  names: Record<Seat, string>;
  onClose(): void;
}

export default function XRay({ view, actions, names, onClose }: XRayProps) {
  const engine = view.engine;
  return (
    <motion.aside
      id="xray-drawer"
      aria-label="X-ray: how Breadcrumbs is thinking"
      className="fixed inset-y-0 right-0 z-40 flex w-[min(440px,100vw)] flex-col overflow-hidden border-l border-line bg-bg-raised font-mono text-[12px] text-ink-muted shadow-drawer sm:inset-y-3 sm:right-3 sm:rounded-[24px] sm:border"
      initial={{ x: "105%" }}
      animate={{ x: 0 }}
      exit={{ x: "105%" }}
      transition={{ type: "tween", duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
    >
      <header className="flex items-center justify-between border-b border-line px-5 py-3.5">
        <div className="flex items-center gap-2 text-ink">
          <Icon name="xray" size={16} />
          <h2 className="font-sans text-[15px] font-semibold tracking-[-0.01em]">X-ray</h2>
        </div>
        <div className="flex items-center gap-3">
          <MuseBadge view={view} />
          <button
            type="button"
            onClick={onClose}
            className="grid size-10 cursor-pointer place-items-center rounded-full border border-line bg-bg-raised text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
            aria-label="Close X-ray"
          >
            <Icon name="close" size={18} />
          </button>
        </div>
      </header>

      <div className="flex-1 divide-y divide-line overflow-y-auto px-5 [&>*]:py-4">
        {engine ? (
          <>
            <PauseSection engine={engine} history={view.history.map((p) => p.silence)} />
            <StalenessSection
              engine={engine}
              history={view.history.map((p) => p.staleness)}
              staleSwitch={staleThreshold(view.params)}
            />
            <StateSection engine={engine} />
            <LastCrumbSection view={view} names={names} />
          </>
        ) : (
          <p className="text-ink-muted">Waiting for the engine. It starts once both seats are ready.</p>
        )}
        <MemorySection memory={view.memory} names={names} quick={isQuick(view.session)} />
        <TranscriptSection view={view} names={names} />
        <DecisionSection view={view} />
        <LabelMapSection labelMap={view.labelMap} names={names} />
        {engine && <MetricsSection engine={engine} />}
        <Section title="Tuning">
          <TuningPanel params={view.params} onChange={actions.setParams} />
        </Section>
        {view.session?.dev_tools && (
          <Section title="Dev: inject turn">
            <DevTurnBox names={names} onSend={actions.devTurn} />
          </Section>
        )}
      </div>
    </motion.aside>
  );
}

// ---- Building blocks ---------------------------------------------------------------

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h3 className="font-mono text-[11px] font-medium uppercase tracking-[0.16em] text-ink-muted">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-0.5">
      <dt className="shrink-0 text-ink-muted">{k}</dt>
      <dd className="min-w-0 truncate text-right text-ink">{children}</dd>
    </div>
  );
}

function MuseBadge({ view }: { view: TableView }) {
  const status = view.muse?.status ?? (view.session?.mock.muse ? "mock" : "connecting");
  const ok = status === "connected" || status === "mock";
  return (
    <span
      className="inline-flex min-h-7 items-center gap-1.5 rounded-full border border-line px-2.5 text-[11px] text-ink-muted"
      title={view.muse?.detail ?? undefined}
    >
      <span className={`size-1.5 rounded-full ${ok ? "bg-ok" : status === "error" ? "bg-danger" : "bg-amber"}`} aria-hidden="true" />
      Muse: {status}
    </span>
  );
}

const BAND_COLOR: Record<EngineSnapshot["band"], string> = {
  normal: "var(--color-ink-muted)",
  warming: "var(--color-crumb)",
  long: "var(--color-amber)",
  exceptional: "var(--color-danger)",
};

function PauseSection({ engine, history }: { engine: EngineSnapshot; history: number[] }) {
  const scale = Math.max(engine.t_exc * 1.35, engine.silence_s, 1);
  const pct = (v: number) => `${Math.min(100, (v / scale) * 100)}%`;
  const markers = [
    { v: engine.t_normal, label: "normal" },
    { v: engine.t_long, label: "long" },
    { v: engine.t_exc, label: "exc" },
  ];
  return (
    <Section
      title="Pause timer"
      aside={
        <span className="text-ink">
          <span className="text-lg tabular-nums">{fmt(engine.silence_s)}</span> s · {engine.band}
        </span>
      }
    >
      {noiseSuspected(engine) && (
        <p className="inline-flex min-h-7 items-center gap-1.5 rounded-full bg-well px-2.5 text-[11px] text-ink" role="status">
          <span className="size-1.5 rounded-full bg-paused" aria-hidden="true" />
          Background noise ignored
        </p>
      )}
      <div
        className="relative h-3 rounded-full bg-bg-sunken"
        role="meter"
        aria-label="Silence against thresholds"
        aria-valuemin={0}
        aria-valuemax={Number(scale.toFixed(1))}
        aria-valuenow={Number(engine.silence_s.toFixed(1))}
        aria-valuetext={`${engine.silence_s.toFixed(1)} seconds, band ${engine.band}`}
      >
        <div
          className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-200 ease-linear"
          style={{ width: pct(engine.silence_s), background: BAND_COLOR[engine.band] }}
        />
        {markers.map((m) => (
          <div key={m.label} className="absolute -inset-y-1 w-px bg-ink/70" style={{ left: pct(m.v) }} aria-hidden="true" />
        ))}
      </div>
      <div className="relative h-4 text-[10px] text-ink-muted" aria-hidden="true">
        {markers.map((m) => (
          <span key={m.label} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: pct(m.v) }}>
            {m.label} {fmt(m.v)}
          </span>
        ))}
      </div>
      <Sparkline
        values={history}
        min={0}
        max={engine.t_exc * 1.2}
        thresholds={[
          { value: engine.t_long, label: "t_long", color: "var(--color-amber)" },
          { value: engine.t_exc, label: "t_exc", color: "var(--color-danger)" },
        ]}
        color="var(--color-ink)"
        label="Silence over the last 60 seconds"
      />
    </Section>
  );
}

const PART_LABEL: Record<keyof EngineSnapshot["parts"], string> = {
  L: "length decay",
  R: "short replies",
  G: "gap growth",
  Q: "few questions",
  E: "low energy",
};

function StalenessSection({
  engine,
  history,
  staleSwitch,
}: {
  engine: EngineSnapshot;
  history: number[];
  staleSwitch: number;
}) {
  const parts = Object.entries(engine.parts) as [keyof EngineSnapshot["parts"], number][];
  return (
    <Section
      title="Staleness"
      aside={
        <span className={engine.stale ? "text-amber-ink" : "text-ink"}>
          <span className="text-lg tabular-nums">{fmt(engine.staleness, 2)}</span> {engine.stale ? "· stale" : "· lively"}
        </span>
      }
    >
      <div className="grid grid-cols-5 gap-2">
        {parts.map(([key, value]) => (
          <div key={key} className="flex flex-col items-center gap-1" title={PART_LABEL[key]}>
            <div className="relative h-12 w-4 overflow-hidden rounded-md bg-bg-sunken" aria-hidden="true">
              <div
                className="absolute inset-x-0 bottom-0 rounded-md bg-amber transition-[height] duration-300"
                style={{ height: `${Math.min(1, Math.max(0, value)) * 100}%` }}
              />
            </div>
            <span className="text-ink">{key}</span>
            <span className="tabular-nums text-[10px] text-ink-muted">{fmt(value, 2)}</span>
            <span className="sr-only">{`${PART_LABEL[key]} ${value.toFixed(2)}`}</span>
          </div>
        ))}
      </div>
      <Sparkline
        values={history}
        min={0}
        max={1}
        thresholds={[{ value: staleSwitch, label: "stale", color: "var(--color-ink-muted)" }]}
        color="var(--color-amber)"
        label="Staleness over the last 60 seconds"
      />
    </Section>
  );
}

function StateSection({ engine }: { engine: EngineSnapshot }) {
  const hold = engine.hold ? `${engine.hold.kind} (${engine.hold.source}) ${Math.ceil(engine.hold.remaining_s)} s` : "none";
  return (
    <Section title="Engine">
      <dl>
        <Row k="next">{engine.next}</Row>
        <Row k="hold">{hold}</Row>
        <Row k="cooldown">{engine.cooldown_s > 0 ? `${fmt(engine.cooldown_s)} s` : "ready"}</Row>
        <Row k="pending">{engine.pending_delivery ? "generating" : "no"}</Row>
        <Row k="crumbs this silence">{engine.crumbs_this_silence}</Row>
        <Row k="crumbs / 10 min">{engine.crumbs_last_10min}</Row>
        <Row k="topic">{engine.topic ?? "--"}</Row>
        <Row k="energy">{engine.energy ?? "--"}</Row>
        <Row k="depth max">{engine.depth_max}</Row>
        <Row k="signals">
          {[engine.turn_active && "turn", engine.vad_active && "vad", engine.tts_playing && "tts"].filter(Boolean).join(" ") || "quiet"}
        </Row>
        <Row k="background noise">{noiseSuspected(engine) ? "ignored (steady noise)" : "clear"}</Row>
        <Row k="defer">{engine.defer ?? "none"}</Row>
        <Row k="seated level">
          {engine.seat_level_db === null || engine.seat_level_db === undefined ? "learning" : `${fmt(engine.seat_level_db)} dB`}
          {engine.background_turns ? ` · ${engine.background_turns} away` : ""}
        </Row>
        <Row k="flags">
          {[engine.listening_paused && "paused", engine.auto_paused && "safety", engine.quiet_hint && "quiet_hint"]
            .filter(Boolean)
            .join(" ") || "none"}
        </Row>
      </dl>
      <div className="space-y-1.5 pt-1">
        <Cached label="steer" text={engine.cached.steer} />
        <Cached label="switch" text={engine.cached.switch} />
      </div>
    </Section>
  );
}

function Cached({ label, text }: { label: string; text: string | null }) {
  return (
    <div className="rounded-xl border border-line px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.16em] text-ink-muted">cached {label}</div>
      <p className="mt-0.5 font-sans text-[13px] leading-snug text-ink">{text ?? <span className="text-ink-muted">none yet</span>}</p>
    </div>
  );
}

function LastCrumbSection({ view, names }: { view: TableView; names: Record<Seat, string> }) {
  const last = view.crumbs.at(-1);
  if (!last) return null;
  const c = last.crumb;
  return (
    <Section title="Last crumb" aside={<span className="text-ink-muted">{c.id}</span>}>
      <p className="font-sans text-[13px] leading-snug text-ink">{c.text}</p>
      <p className="rounded-xl bg-well px-3 py-2 font-sans text-[13px] leading-snug text-ink-muted">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-muted">why </span>
        {c.why}
      </p>
      <dl>
        <Row k="kind / format">
          {c.kind} / {c.format}
        </Row>
        <Row k="source">{sourceCaption(c, names, isQuick(view.session))}</Row>
        <Row k="depth">{c.depth}</Row>
        <Row k="topic">{c.topic}</Row>
        <Row k="latency">
          {Math.round(last.latencyMs)} ms {last.cacheHit ? "(cache hit)" : "(fresh)"}
        </Row>
        <Row k="playback">{last.status}</Row>
      </dl>
    </Section>
  );
}

/** What the table learned from the conversation (WS `memory`). In a quick table it stands in for profiles. */
function MemorySection({ memory, names, quick }: { memory: Memory | null; names: Record<Seat, string>; quick: boolean }) {
  const groups: { key: keyof Memory; label: string }[] = [
    { key: "1", label: names[1] },
    { key: "2", label: names[2] },
    { key: "shared", label: "shared" },
    { key: "threads", label: "open threads" },
  ];
  const count = memory ? groups.reduce((n, g) => n + (memory[g.key]?.length ?? 0), 0) : 0;
  return (
    <Section title="Memory" aside={<span className="text-ink-muted">{count} items</span>}>
      {!memory || count === 0 ? (
        <p className="text-ink-muted">
          {quick ? "Nothing learned yet. It fills in from the conversation." : "Nothing learned from the conversation yet."}
        </p>
      ) : (
        <dl className="space-y-2.5">
          {groups.map(({ key, label }) =>
            memory[key]?.length ? (
              <div key={key}>
                <dt className="text-[10px] uppercase tracking-[0.16em] text-ink-muted">{label}</dt>
                <dd className="mt-1 flex flex-wrap gap-1.5">
                  {memory[key].map((item, i) => (
                    <span
                      key={`${i}-${item}`}
                      className="rounded-full border border-line bg-bg px-2.5 py-0.5 font-sans text-[12px] leading-snug text-ink"
                    >
                      {item}
                    </span>
                  ))}
                </dd>
              </div>
            ) : null,
          )}
        </dl>
      )}
    </Section>
  );
}

function TranscriptSection({ view, names }: { view: TableView; names: Record<Seat, string> }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const turns = view.turns.slice(-60);
  const partial = view.partial;

  useEffect(() => {
    const box = boxRef.current;
    if (box && box.scrollHeight - box.scrollTop - box.clientHeight < 80) box.scrollTop = box.scrollHeight;
  }, [turns.length, partial?.text]);

  const who = (seat: Seat | null, label: string | null) =>
    seat ? `${names[seat]}${label ? ` (${label})` : ""}` : label ? `voice ${label}` : "unknown";
  const partialSeat = partial?.label ? (view.labelMap[partial.label] ?? null) : null;

  return (
    <Section title="Transcript" aside={<span className="text-ink-muted">{view.turns.length} turns</span>}>
      <div ref={boxRef} className="max-h-64 space-y-1.5 overflow-y-auto rounded-xl border border-line bg-bg p-3" aria-live="off">
        {turns.length === 0 && !partial && <p className="text-ink-muted">No turns yet.</p>}
        {turns.map((t) => (
          <p
            key={t.turn_id}
            className={
              t.dropped_as_bot ? "text-ink-muted/70 line-through decoration-ink-muted/40" : t.background ? "text-ink-muted/70" : ""
            }
          >
            <span className={t.dropped_as_bot || t.background ? "" : "font-medium text-amber-ink"}>{who(t.seat, t.label)}</span>{" "}
            <span className={`font-sans text-[13px] ${t.dropped_as_bot || t.background ? "" : "text-ink"}`}>{t.text}</span>
            {t.dropped_as_bot && <Tag>bot</Tag>}
            {t.background && <Tag>away</Tag>}
            {t.hold && <Tag>hold:{t.hold}</Tag>}
            {t.is_question && <Tag>?</Tag>}
          </p>
        ))}
        {partial && (
          <p className="italic text-ink-muted">
            {who(partialSeat, partial.label)} <span className="font-sans text-[13px]">{partial.text}</span>
          </p>
        )}
      </div>
    </Section>
  );
}

function Tag({ children }: { children: ReactNode }) {
  return <span className="ml-1.5 rounded border border-line px-1 text-[10px] not-italic text-ink-muted">{children}</span>;
}

/** The numbers behind a background_turn decision (for the live tuning of the near / far margins). */
function backgroundNumbers(inputs: Record<string, unknown>): string {
  const n = (k: string, unit = "") => (typeof inputs[k] === "number" ? `${k} ${inputs[k]}${unit}` : null);
  return [n("words"), n("active_frac"), n("level_db", " dB"), n("seat_db", " dB"), n("duration_s", " s")]
    .filter(Boolean)
    .join(" · ");
}

function DecisionSection({ view }: { view: TableView }) {
  const items = view.decisions.slice(0, 40);
  return (
    <Section title="Decisions" aside={<span className="text-ink-muted">newest first</span>}>
      {items.length === 0 ? (
        <p className="text-ink-muted">No decisions yet.</p>
      ) : (
        <ol className="max-h-56 space-y-1 overflow-y-auto">
          {items.map((d, i) => (
            <li key={`${d.at_s}-${d.action}-${i}`} className="grid grid-cols-[52px_80px_1fr] gap-2">
              <span className="tabular-nums text-ink-muted">+{fmt(d.at_s)}</span>
              <span className="font-medium text-amber-ink">{d.action}</span>
              <span className="font-sans text-[12px] text-ink-muted">
                {d.reason}
                {d.action === "background_turn" && d.inputs && (
                  <span className="block font-mono text-[10px] text-ink-muted/80">{backgroundNumbers(d.inputs)}</span>
                )}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Section>
  );
}

function LabelMapSection({ labelMap, names }: { labelMap: Record<string, Seat>; names: Record<Seat, string> }) {
  const entries = Object.entries(labelMap);
  return (
    <Section title="Voice labels">
      {entries.length === 0 ? (
        <p className="text-ink-muted">Not calibrated yet.</p>
      ) : (
        <dl>
          {entries.map(([label, seat]) => (
            <Row key={label} k={`voice ${label}`}>
              {names[seat]} (seat {seat})
            </Row>
          ))}
        </dl>
      )}
    </Section>
  );
}

function MetricsSection({ engine }: { engine: EngineSnapshot }) {
  const m = engine.metrics;
  return (
    <Section title="Metrics">
      <dl>
        <Row k="crumbs">{m.crumbs}</Row>
        <Row k="answered">{m.answered}</Row>
        <Row k="success rate">{m.success_rate === null ? "--" : `${Math.round(m.success_rate * 100)}%`}</Row>
        <Row k="interrupted (barge-in)">{m.interrupted}</Row>
        <Row k="false interruptions">{m.false_interruptions}</Row>
        <Row k="latency p50 / p95">
          {m.latency_ms_p50 === null ? "--" : Math.round(m.latency_ms_p50)} /{" "}
          {m.latency_ms_p95 === null ? "--" : Math.round(m.latency_ms_p95)} ms
        </Row>
      </dl>
    </Section>
  );
}
