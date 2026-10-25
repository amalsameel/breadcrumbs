// X-ray drawer (PRD 6.6, F18, 14.3): the engine's reasoning, live, in monospace readouts.
import { motion } from "framer-motion";
import { useEffect, useRef, type ReactNode } from "react";
import type { EngineSnapshot, Seat } from "../lib/types";
import type { TableActions, TableView } from "../lib/useTable";
import Sparkline from "./Sparkline";
import { Icon } from "./table/Icon";
import { fmt, sourceCaption } from "./table/format";
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
      className="fixed inset-y-0 right-0 z-40 flex w-[min(440px,100vw)] flex-col border-l border-line bg-bg-sunken/95 font-mono text-[12px] text-ink-muted shadow-[-24px_0_60px_-30px_rgba(0,0,0,0.9)] backdrop-blur-md"
      initial={{ x: "100%" }}
      animate={{ x: 0 }}
      exit={{ x: "100%" }}
      transition={{ type: "tween", duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
    >
      <header className="flex items-center justify-between border-b border-line px-5 py-3">
        <div className="flex items-center gap-2 text-ink">
          <Icon name="xray" size={16} />
          <h2 className="text-[13px] font-medium uppercase tracking-[0.2em]">X-ray</h2>
        </div>
        <div className="flex items-center gap-3">
          <MuseBadge view={view} />
          <button
            type="button"
            onClick={onClose}
            className="grid size-9 cursor-pointer place-items-center rounded-lg text-ink-muted transition-colors hover:bg-bg-raised hover:text-ink"
            aria-label="Close X-ray"
          >
            <Icon name="close" size={18} />
          </button>
        </div>
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
        {engine ? (
          <>
            <PauseSection engine={engine} history={view.history.map((p) => p.silence)} />
            <StalenessSection engine={engine} history={view.history.map((p) => p.staleness)} />
            <StateSection engine={engine} />
            <LastCrumbSection view={view} names={names} />
          </>
        ) : (
          <p className="text-ink-faint">Waiting for the engine. It starts once both seats are ready.</p>
        )}
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
        <h3 className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink-faint">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-0.5">
      <dt className="shrink-0 text-ink-faint">{k}</dt>
      <dd className="min-w-0 truncate text-right text-ink">{children}</dd>
    </div>
  );
}

function MuseBadge({ view }: { view: TableView }) {
  const status = view.muse?.status ?? (view.session?.mock.muse ? "mock" : "connecting");
  const ok = status === "connected" || status === "mock";
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-ink-faint" title={view.muse?.detail ?? undefined}>
      <span className={`size-1.5 rounded-full ${ok ? "bg-ok" : status === "error" ? "bg-danger" : "bg-amber"}`} aria-hidden="true" />
      Muse: {status}
    </span>
  );
}

const BAND_COLOR: Record<EngineSnapshot["band"], string> = {
  normal: "var(--color-ink-faint)",
  warming: "var(--color-amber)",
  long: "var(--color-crumb)",
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
      <div
        className="relative h-3 rounded-full bg-bg-raised"
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
      <div className="relative h-4 text-[10px] text-ink-faint" aria-hidden="true">
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
          { value: engine.t_long, label: "t_long", color: "var(--color-crumb)" },
          { value: engine.t_exc, label: "t_exc", color: "var(--color-danger)" },
        ]}
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

function StalenessSection({ engine, history }: { engine: EngineSnapshot; history: number[] }) {
  const parts = Object.entries(engine.parts) as [keyof EngineSnapshot["parts"], number][];
  return (
    <Section
      title="Staleness"
      aside={
        <span className={engine.stale ? "text-crumb" : "text-ink"}>
          <span className="text-lg tabular-nums">{fmt(engine.staleness, 2)}</span> {engine.stale ? "· stale" : "· lively"}
        </span>
      }
    >
      <div className="grid grid-cols-5 gap-2">
        {parts.map(([key, value]) => (
          <div key={key} className="flex flex-col items-center gap-1" title={PART_LABEL[key]}>
            <div className="relative h-12 w-4 overflow-hidden rounded-md bg-bg-raised" aria-hidden="true">
              <div
                className="absolute inset-x-0 bottom-0 rounded-md bg-amber transition-[height] duration-300"
                style={{ height: `${Math.min(1, Math.max(0, value)) * 100}%` }}
              />
            </div>
            <span className="text-ink">{key}</span>
            <span className="tabular-nums text-[10px] text-ink-faint">{fmt(value, 2)}</span>
            <span className="sr-only">{`${PART_LABEL[key]} ${value.toFixed(2)}`}</span>
          </div>
        ))}
      </div>
      <Sparkline
        values={history}
        min={0}
        max={1}
        thresholds={[{ value: 0.6, label: "stale", color: "var(--color-crumb)" }]}
        color="var(--color-crumb)"
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
    <div className="rounded-lg border border-line/70 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.18em] text-ink-faint">cached {label}</div>
      <p className="mt-0.5 font-sans text-[13px] leading-snug text-ink">{text ?? <span className="text-ink-faint">none yet</span>}</p>
    </div>
  );
}

function LastCrumbSection({ view, names }: { view: TableView; names: Record<Seat, string> }) {
  const last = view.crumbs.at(-1);
  if (!last) return null;
  const c = last.crumb;
  return (
    <Section title="Last crumb" aside={<span className="text-ink-faint">{c.id}</span>}>
      <p className="font-sans text-[13px] leading-snug text-ink">{c.text}</p>
      <p className="rounded-lg bg-bg-raised/70 px-3 py-2 font-sans text-[13px] leading-snug text-ink-muted">
        <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">why </span>
        {c.why}
      </p>
      <dl>
        <Row k="kind / format">
          {c.kind} / {c.format}
        </Row>
        <Row k="source">{sourceCaption(c, names)}</Row>
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
    <Section title="Transcript" aside={<span className="text-ink-faint">{view.turns.length} turns</span>}>
      <div ref={boxRef} className="max-h-64 space-y-1.5 overflow-y-auto rounded-lg border border-line/70 p-3" aria-live="off">
        {turns.length === 0 && !partial && <p className="text-ink-faint">No turns yet.</p>}
        {turns.map((t) => (
          <p key={t.turn_id} className={t.dropped_as_bot ? "text-ink-faint/60 line-through decoration-ink-faint/40" : ""}>
            <span className={t.dropped_as_bot ? "" : "text-amber"}>{who(t.seat, t.label)}</span>{" "}
            <span className={`font-sans text-[13px] ${t.dropped_as_bot ? "" : "text-ink"}`}>{t.text}</span>
            {t.dropped_as_bot && <Tag>bot</Tag>}
            {t.hold && <Tag>hold:{t.hold}</Tag>}
            {t.is_question && <Tag>?</Tag>}
          </p>
        ))}
        {partial && (
          <p className="italic text-ink-faint">
            {who(partialSeat, partial.label)} <span className="font-sans text-[13px]">{partial.text}</span>
          </p>
        )}
      </div>
    </Section>
  );
}

function Tag({ children }: { children: ReactNode }) {
  return <span className="ml-1.5 rounded border border-line px-1 text-[10px] not-italic text-ink-faint">{children}</span>;
}

function DecisionSection({ view }: { view: TableView }) {
  const items = view.decisions.slice(0, 40);
  return (
    <Section title="Decisions" aside={<span className="text-ink-faint">newest first</span>}>
      {items.length === 0 ? (
        <p className="text-ink-faint">No decisions yet.</p>
      ) : (
        <ol className="max-h-56 space-y-1 overflow-y-auto">
          {items.map((d, i) => (
            <li key={`${d.at_s}-${d.action}-${i}`} className="grid grid-cols-[52px_80px_1fr] gap-2">
              <span className="tabular-nums text-ink-faint">+{fmt(d.at_s)}</span>
              <span className="text-amber">{d.action}</span>
              <span className="font-sans text-[12px] text-ink-muted">{d.reason}</span>
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
        <p className="text-ink-faint">Not calibrated yet.</p>
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
