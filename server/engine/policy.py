"""Decision policy (PRD 8.4, 8.7, 8.8): thresholds, bands, cooldowns and the 10 Hz tick decision.

`evaluate` mirrors the PRD 8.7 pseudocode line by line; the engine gathers its inputs into `TickInputs`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from config import MODE_MULTIPLIER, WILDCARD_MIN_SILENCE_S, Params
from models import Band, CrumbKind, HoldKind, Mode
from engine.staleness import clamp

EPS = 1e-6  # float slack for threshold comparisons on the fake clock
RATE_WINDOW_S = 600.0  # rolling 10 minutes
ANSWER_WINDOW_S = 60.0  # PRD 16.3: success = 3+ human turns within 60 s
ANSWER_MIN_TURNS = 3
STEER_LANDED_MIN_TURNS = 3  # a steer followed by 2 or fewer human turns did not land (PRD 8.7)

AutoAction = Literal["steer", "switch", "wildcard"]


# --- Thresholds and bands (PRD 8.4) ----------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    t_normal: float
    t_long: float
    t_exc: float

    def doubled(self) -> Thresholds:
        """Thinking hold: both intervention thresholds double (10 s / 13 s by default)."""
        return replace(self, t_long=self.t_long * 2, t_exc=self.t_exc * 2)


def thresholds(params: Params, mode: Mode, gap_ref: float | None = None) -> Thresholds:
    """Base thresholds, optionally nudged by the pair's median gap g (PRD 8.4 adaptive formula), then
    scaled by MODE_MULTIPLIER. With default params the adaptive formula is exactly the PRD's."""
    t_normal, t_long, t_exc = params.T_NORMAL_S, params.T_LONG_S, params.T_EXC_S
    if params.ADAPTIVE_THRESHOLDS and gap_ref is not None:
        delta = gap_ref - params.DEFAULT_GAP_S
        normal_offset = params.T_LONG_S - params.T_NORMAL_S
        t_long = clamp(params.T_LONG_S + delta, params.T_LONG_S - 0.5, params.T_LONG_S + 1.0)
        t_exc = max(t_long + 1.0, clamp(params.T_EXC_S + delta, params.T_EXC_S - 0.5, params.T_EXC_S + 1.0))
        t_normal = t_long - normal_offset
    m = MODE_MULTIPLIER.get(mode, 1.0)
    return Thresholds(t_normal * m, t_long * m, t_exc * m)


def effective_thresholds(base: Thresholds, hold: HoldKind | None) -> Thresholds:
    return base.doubled() if hold == "think" else base


def band(silence_s: float, th: Thresholds) -> Band:
    if silence_s + EPS >= th.t_exc:
        return "exceptional"
    if silence_s + EPS >= th.t_long:
        return "long"
    if silence_s + EPS >= th.t_normal:
        return "warming"
    return "normal"


# --- Crumb ledger and cooldowns (PRD 8.8) ---------------------------------------


@dataclass
class CrumbRecord:
    crumb_id: str
    kind: CrumbKind
    auto: bool
    dispatched_at: float
    latency_ms: float = 0.0
    cache_hit: bool = False
    play_started_at: float | None = None
    ended_at: float | None = None
    interrupted: bool = False
    skipped: bool = False
    cancelled: bool = False  # cancelled while generating but sent anyway: faded at playback start
    human_turns_after: int = 0
    human_turns_within_window: int = 0

    @property
    def answered(self) -> bool:
        """Barge-in counts as success (PRD 6.7); otherwise 3+ human turns within 60 s."""
        return self.interrupted or self.human_turns_within_window >= ANSWER_MIN_TURNS

    def end_ref(self, now: float) -> float:
        """When the crumb finished: audio end, `now` while still playing, dispatch time if it never played."""
        if self.ended_at is not None:
            return self.ended_at
        if self.play_started_at is not None:
            return now
        return self.dispatched_at


@dataclass
class CrumbLedger:
    records: list[CrumbRecord] = field(default_factory=list)
    auto_decisions: list[float] = field(default_factory=list)

    def add(self, rec: CrumbRecord) -> None:
        self.records.append(rec)

    def get(self, crumb_id: str) -> CrumbRecord | None:
        for rec in reversed(self.records):
            if rec.crumb_id == crumb_id:
                return rec
        return None

    @property
    def last(self) -> CrumbRecord | None:
        """The last crumb that was actually delivered (cooldowns, steer landing). A crumb cancelled while it
        was generated never reached the room, so it does not restart the cooldown."""
        for rec in reversed(self.records):
            if not rec.cancelled:
                return rec
        return None

    def note_auto_decision(self, now: float) -> None:
        self.auto_decisions.append(now)

    def cancel_auto_decision(self) -> None:
        if self.auto_decisions:
            self.auto_decisions.pop()

    def auto_count(self, now: float) -> int:
        return sum(1 for t in self.auto_decisions if now - t < RATE_WINDOW_S)

    def note_human_turn(self, started_at: float) -> None:
        for rec in self.records:
            if started_at + EPS >= rec.dispatched_at:
                rec.human_turns_after += 1
                if started_at <= rec.dispatched_at + ANSWER_WINDOW_S:
                    rec.human_turns_within_window += 1

    def since_last_end(self, now: float) -> float | None:
        last = self.last
        return None if last is None else max(0.0, now - last.end_ref(now))

    def human_turns_since_last(self) -> int | None:
        last = self.last
        return None if last is None else last.human_turns_after

    def regular_cooldown_ok(self, now: float, params: Params) -> bool:
        """30 s since the last crumb ended AND 2+ human turns since."""
        last = self.last
        if last is None:
            return True
        since = now - last.end_ref(now)
        return since + EPS >= params.MIN_GAP_BETWEEN_CRUMBS_S and last.human_turns_after >= params.MIN_HUMAN_TURNS_BETWEEN

    def exceptional_cooldown_ok(self, now: float, params: Params) -> bool:
        """15 s since the last crumb ended (human-turn requirement waived)."""
        last = self.last
        return last is None or now - last.end_ref(now) + EPS >= params.EXC_SWITCH_MIN_GAP_S

    def regular_cooldown_remaining(self, now: float, params: Params) -> float:
        last = self.last
        if last is None:
            return 0.0
        return max(0.0, params.MIN_GAP_BETWEEN_CRUMBS_S - (now - last.end_ref(now)))

    def last_steer_unlanded(self) -> bool:
        last = self.last
        return last is not None and last.kind == "steer" and last.human_turns_after < STEER_LANDED_MIN_TURNS


# --- Tick decision (PRD 8.7) ----------------------------------------------------------


@dataclass(frozen=True)
class TickInputs:
    live: bool
    listening_paused: bool
    seats_unmapped: bool
    auto_paused: bool
    turn_active: bool
    vad_active: bool
    tts_playing: bool
    hold: HoldKind | None  # active, unexpired hold
    silence_s: float
    base: Thresholds
    staleness: float
    crumbs_this_silence: int
    pending_delivery: bool = False
    in_warmup: bool = False
    rate_ok: bool = True
    exceptional_ok: bool = True
    regular_ok: bool = True
    steer_unlanded: bool = False
    wildcard_ok: bool = False


@dataclass(frozen=True)
class Outcome:
    thresholds: Thresholds
    band: Band
    action: AutoAction | None = None
    reason: str = ""
    quiet: bool = False
    blocked: str | None = None


def blocking_reason(inp: TickInputs) -> str | None:
    """Gates that mean "no automatic crumb, and no point showing a next action" (snapshot `next`)."""
    if not inp.live:
        return "not live"
    if inp.listening_paused:
        return "listening paused"
    if inp.seats_unmapped:
        return "voices not mapped to seats"
    if inp.auto_paused:
        return "safety pause"
    if inp.hold in ("show", "external"):
        return f"{inp.hold} hold"
    return None


def evaluate(inp: TickInputs, params: Params) -> Outcome:
    th = effective_thresholds(inp.base, inp.hold)
    s = inp.silence_s
    b = band(s, th)

    def out(action: AutoAction | None = None, reason: str = "", *, quiet: bool = False, blocked: str | None = None) -> Outcome:
        return Outcome(thresholds=th, band=b, action=action, reason=reason, quiet=quiet, blocked=blocked)

    gate = blocking_reason(inp)
    if gate in ("not live", "listening paused", "voices not mapped to seats", "safety pause"):
        return out(blocked=gate)
    if inp.turn_active or inp.vad_active or inp.tts_playing:
        return out(blocked="someone is talking" if not inp.tts_playing else "bot speaking")
    if gate is not None:
        return out(blocked=gate)

    if inp.crumbs_this_silence >= params.MAX_UNANSWERED_CRUMBS:
        return out(quiet=True, blocked="quiet after unanswered crumbs")
    if inp.pending_delivery:
        return out(blocked="delivery in flight")
    if inp.in_warmup:
        return out(blocked="warm-up after the opener")
    if not inp.rate_ok:
        return out(blocked="max automatic crumbs per 10 minutes")

    if inp.crumbs_this_silence >= 1:
        if s + EPS >= th.t_exc:
            return out("switch", "last crumb got no reply (exceptionally long pause)")
        return out(blocked="waiting for a reply")

    if s + EPS >= th.t_exc and inp.exceptional_ok:
        return out("switch", "exceptionally long pause")
    if s + EPS >= th.t_long and inp.regular_ok:
        if inp.staleness + EPS >= params.STALE_SWITCH:
            return out("switch", "unusually long pause + stale conversation")
        if inp.steer_unlanded:
            return out("switch", "unusually long pause, previous steer did not land (2 or fewer replies)")
        return out("steer", "unusually long pause, conversation still lively")
    if inp.wildcard_ok and inp.regular_ok and s + EPS >= WILDCARD_MIN_SILENCE_S and s + EPS < th.t_long:
        return out("wildcard", "natural pause of 3 s or more, wildcard drop")
    if s + EPS >= th.t_long:
        return out(blocked="cooldown")
    return out()


def next_action(inp: TickInputs, params: Params) -> Literal["none", "steer", "switch"]:
    """What the next automatic crumb would be (snapshot `next`)."""
    if blocking_reason(inp) is not None or inp.crumbs_this_silence >= params.MAX_UNANSWERED_CRUMBS:
        return "none"
    stale = inp.staleness + EPS >= params.STALE_SWITCH
    if stale or inp.crumbs_this_silence >= 1 or inp.steer_unlanded:
        return "switch"
    return "steer"
