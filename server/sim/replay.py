"""Engine simulator (PRD 16.1, docs/CONTRACTS.md section 1).

Feeds a scripted conversation into `Engine` on a fake clock at 10 Hz: Muse-shaped events (speechStart,
transcript partials, speaker, speechEnd, speechComplete), local VAD frames, and simulated TTS playback
(`tts_state` start/end; barge-in and skip fade out after 250 ms). Records the engine's decisions.

Before script time 0 the simulator auto-completes calibration (label A -> seat 1, B -> seat 2) and plays
the opener. By default it then fast-forwards, without ticking, past the opener's warm-up and cooldowns so
script time 0 starts clean (`"skip_warmup": false` keeps the warm-up: script time 0 = opener end).
Scripts should start with a turn at 0.0.

Script keys: name, mode, turns[{seat, text, start, end, label?, echo?, complete_delay?}],
expect[{between: [a, b], action, kind?, count?, at?, tolerance?}], and optional duration, prior_crumb_at,
controls[{t, action}], hold_results{"<turn index>": HoldDecision fields + delay_s?}, energy_results[...],
tts_seconds (3.0), gen_latency_s (0), skip_warmup (true), names.

CLI: python -m sim.replay sim/scripts/<name>.json [more.json ...]
"""
from __future__ import annotations

import heapq
import itertools
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import BARGE_IN_FADE_MS, Params
from engine.core import Effect, Engine
from engine.hold import match_hold
from models import Crumb, CrumbKind, EnergyTag, HoldDecision

TICK_S = 0.1
TIME_EPS = 1e-9
SYS_TTS_S = 2.0
CALIBRATION_REPLY_DELAY_S = 0.6
CALIBRATION_TURN_S = 1.6
COMPLETE_DELAY_S = 0.3
CLASSIFY_DELAY_S = 1.0
ENERGY_DELAY_S = 1.0
PARTIAL_EVERY_S = 0.5
PREROLL_LIMIT_S = 120.0
CRUMB_ACTIONS = frozenset({"opener", "steer", "switch", "manual", "wildcard"})
DEFAULT_NAMES = {1: "Maya", 2: "Dev"}
SEAT_LABELS = {1: "A", 2: "B"}
CALIBRATION_LINES = {"seat1": (1, "Hi, I'm from Austin."), "seat2": (2, "Hey, I'm from Houston, nice to meet you.")}
DECISION_ACTIONS = {
    "hold": "hold",
    "release": "release",
    "quiet": "quiet",
    "safety_pause": "safety_pause",
    "dropped_bot_turn": "dropped_as_bot",
}
SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


@dataclass
class SimResult:
    name: str
    actions: list[dict] = field(default_factory=list)  # {t, action, kind, reason, ...} in script time
    decisions: list[dict] = field(default_factory=list)  # every decision effect, script time
    turns: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    final_snapshot: dict = field(default_factory=dict)


class _Sim:
    def __init__(self, script: dict, params: Params | None):
        self.script = script
        self.params = params or Params()
        names = {int(k): v for k, v in (script.get("names") or DEFAULT_NAMES).items()}
        self.engine = Engine(script.get("mode", "social"), names, self.params)
        self.tts_seconds = float(script.get("tts_seconds", 3.0))
        self.gen_latency_s = float(script.get("gen_latency_s", 0.0))
        self.energy_results = list(script.get("energy_results") or [])

        self._heap: list[tuple[float, int, Callable[..., None], tuple]] = []
        self._seq = itertools.count()
        self._muse_ids = itertools.count(1)
        self._crumb_n = 0
        self._tts_token: dict[str, int] = {}
        self._playing: set[str] = set()
        self._vad_spans: list[tuple[float, float]] = []
        self._turn_index_by_start: dict[float, int] = {}
        self._opener_id: str | None = None
        self.opener_end: float | None = None
        self.now = 0.0
        self.base = 0.0
        self.stopped = False
        self.actions: list[dict] = []
        self.decisions: list[dict] = []

    # --- scheduling -----------------------------------------------------------------

    def at(self, t: float, fn: Callable[..., None], *args: Any) -> None:
        heapq.heappush(self._heap, (t, next(self._seq), fn, args))

    def _drain(self, limit: float, *, inclusive: bool) -> None:
        while self._heap and not self.stopped:
            t = self._heap[0][0]
            if (t > limit + TIME_EPS) if inclusive else (t >= limit - TIME_EPS):
                return
            _, _, fn, args = heapq.heappop(self._heap)
            self.now = max(self.now, t)
            fn(*args)

    def run_window(self, t_start: float, t_end: float, until: Callable[[], bool] | None = None) -> None:
        for k in range(math.ceil(t_start / TICK_S - 1e-6), math.floor(t_end / TICK_S + 1e-6) + 1):
            g = k / 10
            self._drain(g, inclusive=False)
            self.now = g
            self._vad_frame(g)
            self._drain(g, inclusive=True)
            if self.stopped:
                return
            self.handle(self.engine.tick(g))
            if self.stopped or (until is not None and until()):
                return

    def _vad_frame(self, g: float) -> None:
        active = any(s - TIME_EPS <= g < e - TIME_EPS for s, e in self._vad_spans)
        self.handle(self.engine.on_vad(active, 0.08 if active else 0.004, g))

    # --- inputs ------------------------------------------------------------------------

    def schedule_turn(self, label: str | None, text: str, start: float, end: float, *, echo: bool = False,
                      complete_delay: float = COMPLETE_DELAY_S) -> None:
        muse_id = next(self._muse_ids)
        words = text.split()
        self.at(start, self._muse, {"type": "speechStart", "turnId": muse_id, "audioProcessedMs": 0})
        j = 1
        while start + PARTIAL_EVERY_S * j < end - 0.1:
            t = start + PARTIAL_EVERY_S * j
            n = max(1, math.ceil(len(words) * (t - start) / (end - start)))
            self.at(t, self._muse, {"type": "transcript", "transcript": " ".join(words[:n]), "final": False})
            j += 1
        self.at(max(start, end - 0.1), self._muse, {"type": "transcript", "transcript": text, "final": False})
        if label is not None:
            self.at(max(start, end - 0.05), self._muse, {"type": "speaker", "label": label})
        self.at(end, self._muse, {"type": "speechEnd", "turnId": muse_id})
        self.at(end + complete_delay, self._muse, {"type": "speechComplete", "turnId": muse_id, "transcript": text})
        if not echo:
            self._vad_spans.append((start, end))

    def _muse(self, evt: dict) -> None:
        self.handle(self.engine.on_muse_event(evt, self.now))

    def _control(self, action: str) -> None:
        self.handle(self.engine.on_control(action, self.now))

    def _apply_hold(self, turn_id: int, d: HoldDecision) -> None:
        self.handle(self.engine.apply_hold_decision(turn_id, d, self.now))

    def _apply_energy(self, tag: EnergyTag) -> None:
        self.handle(self.engine.apply_energy_tag(tag, self.now))

    # --- TTS -----------------------------------------------------------------------------

    def _play(self, speech_id: str, duration: float) -> None:
        token = self._tts_token.get(speech_id, 0) + 1
        self._tts_token[speech_id] = token
        self._playing.add(speech_id)
        self.handle(self.engine.on_tts_state(speech_id, True, self.now))
        self.at(self.now + duration, self._end_play, speech_id, token)

    def _end_play(self, speech_id: str, token: int) -> None:
        if self._tts_token.get(speech_id) != token or speech_id not in self._playing:
            return
        self._playing.discard(speech_id)
        self.handle(self.engine.on_tts_state(speech_id, False, self.now))
        if speech_id == self._opener_id:
            self.opener_end = self.now

    def _fade(self, speech_id: str | None) -> None:
        if speech_id is None or speech_id not in self._playing:
            return
        token = self._tts_token.get(speech_id, 0) + 1
        self._tts_token[speech_id] = token
        self.at(self.now + BARGE_IN_FADE_MS / 1000.0, self._end_play, speech_id, token)

    def _generate(self, kind: CrumbKind) -> None:
        """A `deliver` (or `round_warning`) effect: the crumb is ready after `gen_latency_s`."""
        if self.gen_latency_s > 0:
            self.at(self.now + self.gen_latency_s, self._dispatch, kind)
        else:
            self._dispatch(kind)

    def _dispatch(self, kind: CrumbKind) -> None:
        if self.engine.pending_cancelled:  # PRD 8.1: speech or Skip arrived while it was generated
            self._record("crumb_cancelled", kind, "cancelled while generating")
            self.handle(self.engine.crumb_cancelled(self.now))
            return
        self._crumb_n += 1
        crumb = Crumb(
            id=f"c_{self._crumb_n}", kind=kind, format="question", text="Simulated crumb.",
            addressed_to="both", source="conversation", topic=f"sim topic {self._crumb_n}", depth=1,
            why="Simulator crumb.",
        )
        if kind == "opener":
            self._opener_id = crumb.id
        self.engine.crumb_dispatched(crumb, self.now, latency_ms=self.gen_latency_s * 1000.0, cache_hit=True)
        self._play(crumb.id, self.tts_seconds)

    # --- effects --------------------------------------------------------------------------

    def handle(self, effects: list[Effect]) -> None:
        for eff in effects:
            self._effect(eff)

    def _record(self, action: str, kind: str | None, reason: str, **extra: Any) -> None:
        self.actions.append({"t": self.now, "action": action, "kind": kind, "reason": reason, **extra})

    def _effect(self, eff: Effect) -> None:
        typ = eff.get("type")
        if typ == "deliver":
            self._record(eff["kind"], eff["kind"], eff.get("reason", ""))
            self._generate(eff["kind"])
        elif typ == "barge_in":
            self._record("crumb_interrupted", None, "barge-in", crumb_id=eff.get("crumb_id"))
            self._fade(eff.get("crumb_id"))
        elif typ == "skip":
            self._record("skip", None, "skip", crumb_id=eff.get("crumb_id"))
            self._fade(eff.get("crumb_id"))
        elif typ == "say":
            self._play(eff["id"], SYS_TTS_S)
        elif typ == "calibration":
            self._on_calibration_step(eff.get("step"))
        elif typ == "classify_hold":
            self._on_classify(int(eff["turn_id"]))
        elif typ == "tag_energy" and self.energy_results:
            self.at(self.now + ENERGY_DELAY_S, self._apply_energy, EnergyTag(**self.energy_results.pop(0)))
        elif typ == "decision":
            self._on_decision(eff["decision"])
        elif typ in ("round_warning", "round_end"):
            self._record(typ, None, typ)
            if typ == "round_warning":  # the runner turns it into a switch crumb with a time instruction
                self._generate("switch")
            if typ == "round_end":
                self.handle(self.engine.end(self.now))
                self.stopped = True

    def _on_decision(self, d: dict) -> None:
        self.decisions.append({"t": self.now, **d})
        action = DECISION_ACTIONS.get(d.get("action", ""))
        if action is not None:
            inputs = d.get("inputs") or {}
            self._record(action, inputs.get("kind"), d.get("reason", ""), change=inputs.get("change"))

    def _on_calibration_step(self, step: str | None) -> None:
        if step not in CALIBRATION_LINES or not self.script.get("auto_calibrate", True):
            return
        seat, text = CALIBRATION_LINES[step]
        start = round(self.now + SYS_TTS_S + CALIBRATION_REPLY_DELAY_S, 6)
        self.schedule_turn(SEAT_LABELS[seat], text, start, start + CALIBRATION_TURN_S)

    def _on_classify(self, turn_id: int) -> None:
        turn = next((t for t in self.engine.turns if t.turn_id == turn_id), None)
        if turn is None:
            return
        idx = self._turn_index_by_start.get(round(turn.started_at - self.base, 3))
        spec = (self.script.get("hold_results") or {}).get(str(idx)) if idx is not None else None
        if spec:
            d = HoldDecision(**{k: spec[k] for k in ("hold", "kind", "expected_seconds", "confidence")})
            delay = float(spec.get("delay_s", CLASSIFY_DELAY_S))
        else:  # default classifier: agrees with the lexicon (deterministic)
            kind = match_hold(turn.text, self.params.HOLD_TAIL_WORDS)
            seconds = int(self.params.HOLD_MAX_S.get(kind, 0)) if kind else 0
            d = HoldDecision(hold=kind is not None, kind=kind or "none", expected_seconds=seconds, confidence=0.9)
            delay = CLASSIFY_DELAY_S
        self.at(self.now + delay, self._apply_hold, turn_id, d)

    # --- driver -----------------------------------------------------------------------------

    def preroll(self) -> None:
        self.at(0.0, lambda: self.handle(self.engine.start_calibration(self.now)))
        self.run_window(0.0, PREROLL_LIMIT_S, until=self._preroll_done)
        if self.engine.phase != "live" or self.opener_end is None:
            raise RuntimeError("simulator: calibration or opener did not complete")

    def _preroll_done(self) -> bool:
        if self.opener_end is None and self.engine.phase == "live" and self._opener_id is None:
            snap = self.engine.snapshot(self.now)
            if not snap["pending_delivery"] and not snap["tts_playing"]:
                self.opener_end = self.now  # no opener (safety pause during calibration): live starts now
        return self.opener_end is not None

    def run_script(self) -> None:
        skip = bool(self.script.get("skip_warmup", True))
        gap = (
            max(self.params.WARMUP_S, self.params.MIN_GAP_BETWEEN_CRUMBS_S, self.params.EXC_SWITCH_MIN_GAP_S) + 1.0
            if skip else 0.0
        )
        assert self.opener_end is not None
        self.base = math.ceil(self.opener_end + gap) if skip else math.ceil(self.opener_end * 10 - 1e-6) / 10
        turns = self.script.get("turns", [])
        for i, turn in enumerate(turns):
            seat = turn.get("seat")
            label = turn.get("label", SEAT_LABELS.get(seat) if seat is not None else None)
            start, end = self.base + float(turn["start"]), self.base + float(turn["end"])
            self._turn_index_by_start[round(float(turn["start"]), 3)] = i
            self.schedule_turn(label, turn["text"], start, end, echo=bool(turn.get("echo", False)),
                               complete_delay=float(turn.get("complete_delay", COMPLETE_DELAY_S)))
        controls = list(self.script.get("controls") or [])
        if "prior_crumb_at" in self.script:
            controls.append({"t": self.script["prior_crumb_at"], "action": "spark"})
        for c in controls:
            self.at(self.base + float(c["t"]), self._control, c["action"])
        first = min([float(t["start"]) for t in turns] + [float(c["t"]) for c in controls] + [0.0]) if skip else 0.0
        self.run_window(self.base + first, self.base + script_duration(self.script))


def script_duration(script: dict) -> float:
    if "duration" in script:
        return float(script["duration"])
    ends = [float(t["end"]) for t in script.get("turns", [])]
    ends += [float(e["between"][1]) for e in script.get("expect", [])]
    ends += [float(c["t"]) for c in script.get("controls", []) or []]
    return (max(ends) if ends else 0.0) + 1.0


def run_script(script: dict, params: Params | None = None) -> SimResult:
    sim = _Sim(script, params)
    sim.preroll()
    sim.run_script()

    def rel(t: float) -> float:
        return round(t - sim.base, 3)

    result = SimResult(name=script.get("name", "script"))
    result.actions = [{**a, "t": rel(a["t"])} for a in sim.actions]
    result.decisions = [{**d, "t": rel(d["t"])} for d in sim.decisions]
    result.turns = [
        {**t.model_dump(), "started_at": rel(t.started_at), "ended_at": rel(t.ended_at)} for t in sim.engine.turns
    ]
    result.metrics = sim.engine.metrics()
    result.final_snapshot = sim.engine.snapshot(sim.now)
    return result


def check_expectations(script: dict, result: SimResult) -> list[str]:
    """Returns human-readable failures (empty list = every expectation holds)."""
    failures: list[str] = []
    for exp in script.get("expect", []):
        a, b = float(exp["between"][0]), float(exp["between"][1])
        action = exp["action"]
        window = [x for x in result.actions if a - 1e-6 <= x["t"] <= b + 1e-6]
        if action == "none":
            fired = [x for x in window if x["action"] in CRUMB_ACTIONS]
            if fired:
                failures.append(f"[{a}, {b}] expected no crumb, got " + ", ".join(f"{x['action']}@{x['t']}" for x in fired))
            continue
        matching = [x for x in window if x["action"] == action and ("kind" not in exp or x["kind"] == exp["kind"])]
        want = int(exp.get("count", 1))
        label = action + (f"({exp['kind']})" if "kind" in exp else "")
        if len(matching) != want:
            got = ", ".join(f"{x['action']}@{x['t']}" for x in matching) or "nothing"
            failures.append(f"[{a}, {b}] expected {want} x {label}, got {len(matching)}: {got}")
        if "at" in exp:
            tol = float(exp.get("tolerance", 0.15))
            if not any(abs(x["t"] - float(exp["at"])) <= tol + 1e-6 for x in matching):
                failures.append(f"[{a}, {b}] expected {label} at {exp['at']} (+/- {tol}), got {[x['t'] for x in matching]}")
    return failures


def format_timeline(result: SimResult) -> str:
    lines = [f"== {result.name} =="]
    keys = ("silence_s", "t_long", "t_exc", "staleness", "since_last_crumb_s", "crumbs_this_silence", "kind", "overlap_ratio")
    for d in result.decisions:
        inputs = d.get("inputs") or {}
        shown = " ".join(f"{k}={inputs[k]}" for k in keys if inputs.get(k) is not None)
        lines.append(f"{d['t']:9.2f}  {d['action']:<17} {d['reason']}" + (f"  [{shown}]" if shown else ""))
    m = result.metrics
    lines.append(f"metrics: crumbs={m['crumbs']} interrupted={m['interrupted']} answered={m['answered']} "
                 f"false_interruptions={m['false_interruptions']}")
    return "\n".join(lines)


def load_script(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str]) -> int:
    paths = argv or sorted(str(p) for p in SCRIPTS_DIR.glob("*.json"))
    failed = 0
    for path in paths:
        script = load_script(path)
        result = run_script(script)
        print(format_timeline(result))
        failures = check_expectations(script, result)
        for f in failures:
            print(f"  FAIL {f}")
        print("  PASS" if not failures else f"  {len(failures)} expectation(s) failed")
        print()
        failed += bool(failures)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
