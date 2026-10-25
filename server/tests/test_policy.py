"""Decision policy, cooldowns and engine behaviors (PRD 6.5, 8.3 to 8.11, 15.3, F21, F22)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from config import Params
from engine import core as core_mod
from engine import policy as policy_mod
from engine.core import Engine
from engine.policy import (
    CrumbLedger,
    CrumbRecord,
    Thresholds,
    TickInputs,
    band,
    effective_thresholds,
    evaluate,
    next_action,
    thresholds,
    unanswered_switch_s,
)
from models import Crumb, EnergyTag, HoldDecision
from sim.replay import check_expectations, run_script

NAMES = {1: "Maya", 2: "Dev"}
TYPES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "types.ts"
LINES = [
    "So I just got back from a climbing trip in Colorado last weekend.",
    "No way, I have always wanted to go climbing out there, which area?",
    "We were near Boulder, mostly the Flatirons, the views are unreal.",
    "That sounds amazing, I mostly climb indoors at the gym near campus.",
    "Indoor is great for training though, do you boulder or top rope?",
    "Mostly bouldering, my friends dragged me there and I got hooked.",
]
LONG = (
    "So the whole story is that we drove out before sunrise, got completely lost twice, found a tiny diner "
    "with the best pancakes I have ever had, and then finally made it to the trailhead around noon."
)


# --- helpers -------------------------------------------------------------------------------


def lively(start: float, n: int, *, first_seat: int = 1, dur: float = 2.5, gap: float = 0.7) -> tuple[list[dict], float]:
    turns, t = [], start
    for i in range(n):
        turns.append({"seat": 1 + (i + first_seat - 1) % 2, "text": LINES[i % len(LINES)], "start": round(t, 1),
                      "end": round(t + dur, 1)})
        t = round(t + dur + gap, 1)
    return turns, turns[-1]["end"]


# The inline scripts below were written for the pre-3 s thresholds (3.0 / 5.0 / 6.5 s); they keep that timing
# math through script params, like the pinned sim scripts. The 3 s defaults have their own tests and scripts.
LEGACY = {"T_NORMAL_S": 3.0, "T_LONG_S": 5.0, "T_EXC_S": 6.5, "UNANSWERED_SWITCH_S": 6.5,
          "HOLD_CHECK_SILENCE_S": 1.0, "VAD_DEFER_S": 2.5}


def legacy(**kw) -> Params:
    p = Params()
    p.update({**LEGACY, **kw})
    return p


def script(turns: list[dict], expect: list[dict], **extra) -> dict:
    return {"name": "inline", "mode": "social", "params": dict(LEGACY), "turns": turns, "expect": expect, **extra}


def assert_script(s: dict, params: Params | None = None):
    result = run_script(s, params)
    failures = check_expectations(s, result)
    assert not failures, "\n".join(failures)
    return result


def crumb(cid: str, kind: str = "steer") -> Crumb:
    return Crumb(id=cid, kind=kind, format="question", text="A crumb.", addressed_to="both",
                 source="conversation", topic="t", depth=1, why="test")


def speak(e: Engine, mid: int, label: str | None, text: str, start: float, end: float,
          complete: float | None = 0.0) -> list[dict]:
    fx = e.on_muse_event({"type": "speechStart", "turnId": mid, "audioProcessedMs": 0}, start)
    fx += e.on_muse_event({"type": "transcript", "transcript": text, "final": False}, max(start, end - 0.1))
    if label is not None:
        fx += e.on_muse_event({"type": "speaker", "label": label}, max(start, end - 0.05))
    fx += e.on_muse_event({"type": "speechEnd", "turnId": mid}, end)
    if complete is not None:
        fx += e.on_muse_event({"type": "speechComplete", "turnId": mid, "transcript": text}, end + complete)
    return fx


def calibrated(mode: str = "social", params: Params | None = None) -> tuple[Engine, list[dict]]:
    """A live engine at t = 9.5 s with the opener (c_0) already played."""
    e = Engine(mode, NAMES, params or Params())  # type: ignore[arg-type]
    fx = e.start_calibration(0.0)
    e.on_tts_state("sys_cal1", True, 0.0)
    e.on_tts_state("sys_cal1", False, 2.0)
    fx += speak(e, 1, "A", "Hi, I'm from Austin.", 3.0, 4.0)
    e.on_tts_state("sys_cal2", True, 4.0)
    e.on_tts_state("sys_cal2", False, 5.0)
    fx += speak(e, 2, "B", "Hey, I'm from Houston.", 5.5, 6.5)
    assert e.phase == "live"
    e.crumb_dispatched(crumb("c_0", "opener"), 6.5, 120.0, True)
    e.on_tts_state("c_0", True, 6.5)
    e.on_tts_state("c_0", False, 9.5)
    return e, fx


def ticks(e: Engine, t0: float, t1: float) -> list[dict]:
    fx: list[dict] = []
    for k in range(round(t0 * 10), round(t1 * 10) + 1):
        fx += e.tick(k / 10)
    return fx


def of_type(fx: list[dict], typ: str) -> list[dict]:
    return [f for f in fx if f["type"] == typ]


def decisions(fx: list[dict], action: str | None = None) -> list[dict]:
    ds = [f["decision"] for f in fx if f["type"] == "decision"]
    return [d for d in ds if action is None or d["action"] == action]


def base_inputs(**kw) -> TickInputs:
    defaults = dict(live=True, listening_paused=False, seats_unmapped=False, auto_paused=False, turn_active=False,
                    vad_active=False, tts_playing=False, hold=None, silence_s=0.0,
                    base=Thresholds(3.0, 5.0, 6.5), staleness=0.1, crumbs_this_silence=0)
    defaults.update(kw)
    return TickInputs(**defaults)


P = Params()


# --- thresholds and bands --------------------------------------------------------------


def test_default_thresholds_and_bands() -> None:
    th = thresholds(P, "social")
    assert (th.t_normal, th.t_long, th.t_exc) == (1.5, 3.0, 4.5)
    assert [band(s, th) for s in (0.0, 1.4, 1.5, 2.9, 3.0, 4.4, 4.5, 30.0)] == [
        "normal", "normal", "warming", "warming", "long", "long", "exceptional", "exceptional"]
    think = effective_thresholds(th, "think")
    assert (think.t_normal, think.t_long, think.t_exc) == (1.5, 6.0, 9.0)


def test_unanswered_follow_up_waits_for_the_unanswered_switch() -> None:
    th = thresholds(P, "social")
    assert unanswered_switch_s(th, P, None) == 6.0  # max(t_exc 4.5, 6.0)
    assert unanswered_switch_s(effective_thresholds(th, "think"), P, "think") == 12.0
    base = Thresholds(1.5, 3.0, 4.5)
    assert evaluate(base_inputs(base=base, silence_s=5.9, crumbs_this_silence=1), P).action is None
    assert evaluate(base_inputs(base=base, silence_s=6.0, crumbs_this_silence=1), P).action == "switch"


def test_renamed_vad_param_still_tunes_its_replacement() -> None:
    p = Params()
    p.update({"VAD_UNCONFIRMED_S": 2.0})
    assert p.VAD_DEFER_S == 2.0 and not hasattr(p, "VAD_UNCONFIRMED_S")


@pytest.mark.parametrize(
    "g, expected",
    [
        (1.2, (3.0, 5.0, 6.5)),
        (0.4, (2.5, 4.5, 6.0)),  # T_LONG clamp 4.5; T_EXC = max(5.5, clamp(5.7, 6.0, 7.5))
        (2.5, (4.0, 6.0, 7.5)),  # T_LONG clamp 6.0; T_EXC = max(7.0, clamp(7.8, 6.0, 7.5))
        (1.7, (3.5, 5.5, 7.0)),
    ],
)
def test_adaptive_threshold_clamping(g: float, expected: tuple) -> None:
    p = legacy(ADAPTIVE_THRESHOLDS=True)
    th = thresholds(p, "social", g)
    assert (th.t_normal, th.t_long, th.t_exc) == pytest.approx(expected)
    assert thresholds(Params(), "social", g) == Thresholds(1.5, 3.0, 4.5)  # off by default


def test_mode_multiplier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(policy_mod.MODE_MULTIPLIER, "speed_dating", 0.8)
    th = thresholds(P, "speed_dating")
    assert (th.t_normal, th.t_long, th.t_exc) == pytest.approx((1.2, 2.4, 3.6))


def test_think_hold_doubles_long_and_exceptional() -> None:
    th = Thresholds(3.0, 5.0, 6.5).doubled()
    assert (th.t_normal, th.t_long, th.t_exc) == (3.0, 10.0, 13.0)
    assert evaluate(base_inputs(silence_s=8.0, hold="think"), P).action is None
    out = evaluate(base_inputs(silence_s=10.0, hold="think"), P)
    assert out.action == "steer" and out.thresholds.t_long == 10.0


# --- PRD 8.7 branches --------------------------------------------------------------------


def test_policy_branches() -> None:
    assert evaluate(base_inputs(silence_s=4.9), P).action is None
    assert evaluate(base_inputs(silence_s=5.0), P).action == "steer"
    assert evaluate(base_inputs(silence_s=5.0, staleness=0.6), P).action == "switch"
    assert evaluate(base_inputs(silence_s=5.0, steer_unlanded=True), P).action == "switch"
    blocked = evaluate(base_inputs(silence_s=5.0, regular_ok=False), P)
    assert blocked.action is None and blocked.blocked == "cooldown"
    assert evaluate(base_inputs(silence_s=6.5, regular_ok=False), P).action == "switch"
    assert evaluate(base_inputs(silence_s=6.5, regular_ok=False, exceptional_ok=False), P).action is None
    # a crumb already played this silence: only the exceptional switch, no cooldown check
    assert evaluate(base_inputs(silence_s=5.0, crumbs_this_silence=1), P).action is None
    out = evaluate(base_inputs(silence_s=6.5, crumbs_this_silence=1, regular_ok=False, exceptional_ok=False), P)
    assert out.action == "switch" and "no reply" in out.reason
    quiet = evaluate(base_inputs(silence_s=60.0, crumbs_this_silence=2), P)
    assert quiet.action is None and quiet.quiet


@pytest.mark.parametrize(
    "kw",
    [
        {"live": False}, {"listening_paused": True}, {"seats_unmapped": True}, {"auto_paused": True},
        {"turn_active": True}, {"vad_active": True}, {"tts_playing": True}, {"hold": "show"},
        {"hold": "external"}, {"pending_delivery": True}, {"in_warmup": True}, {"rate_ok": False},
    ],
)
def test_policy_gates_block_everything(kw: dict) -> None:
    assert evaluate(base_inputs(silence_s=30.0, **kw), P).action is None


def test_wildcard_gating_in_evaluate() -> None:
    # The window is the last second under the long threshold: [max(t_normal, t_long - 1), t_long).
    on = Params(WILDCARD_ENABLED=True)
    base = Thresholds(1.5, 3.0, 4.5)
    assert evaluate(base_inputs(base=base, silence_s=2.5, wildcard_ok=True), on).action == "wildcard"
    assert evaluate(base_inputs(base=base, silence_s=2.0, wildcard_ok=True), on).action == "wildcard"
    assert evaluate(base_inputs(base=base, silence_s=1.9, wildcard_ok=True), on).action is None
    assert evaluate(base_inputs(base=base, silence_s=2.5, wildcard_ok=False), on).action is None
    assert evaluate(base_inputs(base=base, silence_s=2.5, wildcard_ok=True, regular_ok=False), on).action is None
    assert evaluate(base_inputs(base=base, silence_s=2.5, wildcard_ok=True, crumbs_this_silence=1), on).action is None
    assert evaluate(base_inputs(silence_s=4.0, wildcard_ok=True), on).action == "wildcard"  # 3 / 5 / 6.5: [4, 5)
    assert evaluate(base_inputs(silence_s=3.9, wildcard_ok=True), on).action is None


def test_next_action() -> None:
    assert next_action(base_inputs(), P) == "steer"
    assert next_action(base_inputs(staleness=0.7), P) == "switch"
    assert next_action(base_inputs(crumbs_this_silence=1), P) == "switch"
    assert next_action(base_inputs(crumbs_this_silence=2), P) == "none"
    assert next_action(base_inputs(hold="show"), P) == "none"
    assert next_action(base_inputs(hold="think"), P) == "steer"
    assert next_action(base_inputs(listening_paused=True), P) == "none"
    assert next_action(base_inputs(live=False), P) == "none"
    assert next_action(base_inputs(auto_paused=True), P) == "none"


# --- cooldown math (PRD 8.8) -----------------------------------------------------------------


def test_cooldown_math() -> None:
    ledger = CrumbLedger()
    assert ledger.regular_cooldown_ok(0.0, P) and ledger.exceptional_cooldown_ok(0.0, P)
    rec = CrumbRecord(crumb_id="c_1", kind="steer", auto=True, dispatched_at=100.0, play_started_at=100.0)
    ledger.add(rec)
    assert ledger.since_last_end(102.0) == 0.0  # still playing
    rec.ended_at = 103.0
    assert ledger.regular_cooldown_remaining(113.0, P) == pytest.approx(20.0)
    assert not ledger.exceptional_cooldown_ok(117.9, P) and ledger.exceptional_cooldown_ok(118.0, P)
    assert not ledger.regular_cooldown_ok(133.0, P)  # 30 s elapsed, but no human turns yet
    ledger.note_human_turn(104.0)
    ledger.note_human_turn(107.0)
    assert not ledger.regular_cooldown_ok(132.9, P) and ledger.regular_cooldown_ok(133.0, P)
    assert ledger.last_steer_unlanded()  # 2 replies: did not land
    ledger.note_human_turn(110.0)
    assert not ledger.last_steer_unlanded()
    ledger.note_human_turn(99.0)  # started before the crumb: not a reply
    assert rec.human_turns_after == 3


def test_rolling_rate_window() -> None:
    ledger = CrumbLedger()
    for t in (0.0, 100.0, 200.0, 300.0, 400.0, 500.0):
        ledger.note_auto_decision(t)
    assert ledger.auto_count(599.0) == 6
    assert ledger.auto_count(600.0) == 5
    ledger.cancel_auto_decision()
    assert ledger.auto_count(600.0) == 4


def test_crumb_answered_rule() -> None:
    rec = CrumbRecord(crumb_id="c", kind="switch", auto=True, dispatched_at=0.0)
    ledger = CrumbLedger(records=[rec])
    for t in (5.0, 20.0, 61.0):
        ledger.note_human_turn(t)
    assert not rec.answered  # only 2 within 60 s
    rec.interrupted = True
    assert rec.answered


# --- engine through the simulator ---------------------------------------------------------


def test_max_crumbs_per_rolling_10_minutes() -> None:
    params = legacy(MAX_CRUMBS_PER_10_MIN=2, MIN_GAP_BETWEEN_CRUMBS_S=5)
    turns, ends, t = [], [], 0.0
    for _ in range(3):
        block, end = lively(t, 4)
        turns += block
        ends.append(end)
        t = round(end + 5.5, 1)
    turns += lively(t, 1)[0]
    expect = [
        {"between": [ends[0], ends[0] + 5.5], "action": "steer", "count": 1, "at": round(ends[0] + 5.0, 1)},
        {"between": [ends[1], ends[1] + 5.5], "action": "steer", "count": 1, "at": round(ends[1] + 5.0, 1)},
        {"between": [ends[2], ends[2] + 5.5], "action": "none"},
    ]
    result = assert_script(script(turns, expect), params)
    assert result.final_snapshot["crumbs_last_10min"] == 2


def test_quiet_after_two_unanswered_then_cleared_by_speech() -> None:
    turns, end = lively(0.0, 4)
    s = script(turns, [{"between": [end, end + 30], "action": "quiet", "count": 1}], duration=end + 30)
    snap = assert_script(s).final_snapshot
    assert snap["quiet_hint"] and snap["next"] == "none" and snap["crumbs_this_silence"] == 2
    turns2 = turns + [{"seat": 1, "text": "Sorry, I zoned out, what were we talking about?", "start": end + 25,
                       "end": end + 27}]
    snap2 = run_script(script(turns2, [], duration=end + 28)).final_snapshot
    assert not snap2["quiet_hint"] and snap2["crumbs_this_silence"] == 0


def test_spark_queued_while_talking_and_ignores_cooldown() -> None:
    turns, end = lively(0.0, 2)
    turns.append({"seat": 1, "text": LONG, "start": end + 0.6, "end": end + 6.6})
    talk_end = round(end + 6.6, 1)
    turns.append({"seat": 2, "text": "Pancakes before a hike is the correct order of operations.", "start": talk_end + 6,
                  "end": talk_end + 8})
    controls = [{"t": round(end + 2.0, 1), "action": "spark"}, {"t": round(talk_end + 9.0, 1), "action": "spark"}]
    expect = [
        {"between": [end + 2.0, talk_end - 0.1], "action": "none"},
        {"between": [end, talk_end + 5.0], "action": "manual", "count": 1, "at": talk_end},
        {"between": [talk_end + 8.5, talk_end + 9.5], "action": "manual", "count": 1, "at": round(talk_end + 9.0, 1)},
    ]
    assert_script(script(turns, expect, controls=controls))


def test_skip_interrupts_and_delivers_a_switch() -> None:
    turns, end = lively(0.0, 4)
    skip_at = round(end + 6.0, 1)
    expect = [
        {"between": [end, end + 5.5], "action": "steer", "count": 1, "at": round(end + 5.0, 1)},
        {"between": [skip_at, skip_at], "action": "skip", "count": 1},
        {"between": [skip_at, skip_at + 1.0], "action": "switch", "count": 1, "at": round(skip_at + 0.3, 1)},
    ]
    result = assert_script(script(turns, expect, controls=[{"t": skip_at, "action": "skip"}], duration=skip_at + 2))
    skip = next(a for a in result.actions if a["action"] == "skip")
    steer_id = [d for d in result.decisions if d["action"] == "steer"]
    assert skip["crumb_id"] and steer_id


def test_skip_without_a_playing_crumb() -> None:
    e, _ = calibrated()
    fx = e.on_control("skip", 20.0)
    assert of_type(fx, "skip") == [{"type": "skip", "crumb_id": None}]
    assert of_type(fx, "deliver")[0]["kind"] == "switch"


@pytest.mark.parametrize("replies, kind", [(2, "switch"), (3, "steer")])
def test_steer_after_steer_upgrade(replies: int, kind: str) -> None:
    turns, end = lively(0.0, 4)
    first_steer = round(end + 5.0, 1)
    t = round(end + 5.5, 1)
    span = 27.5 / replies
    for i in range(replies):
        turns.append({"seat": 1 + i % 2, "text": LONG, "start": round(t, 1), "end": round(t + span - 0.8, 1)})
        t += span
    second_pause = turns[-1]["end"]
    expect = [
        {"between": [end, end + 5.5], "action": "steer", "count": 1, "at": first_steer},
        {"between": [second_pause, second_pause + 5.5], "action": kind, "count": 1, "at": round(second_pause + 5.0, 1)},
    ]
    result = assert_script(script(turns, expect, duration=second_pause + 5.5))
    if kind == "switch":
        assert any("did not land" in d["reason"] for d in result.decisions if d["action"] == "switch")


def test_thinking_hold_steers_at_doubled_threshold() -> None:
    turns, end = lively(0.0, 4)
    turns.append({"seat": 1, "text": "Hmm, good question.", "start": end + 0.7, "end": end + 1.9})
    hold_end = round(end + 1.9, 1)
    expect = [
        {"between": [hold_end, hold_end + 9.9], "action": "none"},
        {"between": [hold_end, hold_end + 10.5], "action": "steer", "count": 1, "at": round(hold_end + 10.0, 1)},
    ]
    result = assert_script(script(turns, expect, duration=hold_end + 10.5))
    steer = next(d for d in result.decisions if d["action"] == "steer")
    assert steer["inputs"]["t_long"] == 10.0 and steer["inputs"]["t_exc"] == 13.0


def test_wildcard_gating_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """On the 3 s defaults the wildcard window is 2.0 to 3.0 s of silence."""
    monkeypatch.setattr(core_mod, "WILDCARD_MIN_GAP_S", 40)
    turns, e1 = lively(0.0, 3)
    block, e2 = lively(round(e1 + 2.6, 1), 4)
    turns += block
    block, e3 = lively(round(e2 + 2.6, 1), 10)
    turns += block
    turns += lively(round(e3 + 2.6, 1), 1)[0]
    expect = [
        {"between": [e1, e1 + 2.6], "action": "wildcard", "count": 1, "at": round(e1 + 2.0, 1)},
        {"between": [e2, e2 + 2.6], "action": "none"},  # under 40 s since the last wildcard
        {"between": [e3, e3 + 2.6], "action": "wildcard", "count": 1, "at": round(e3 + 2.0, 1)},
    ]
    s = {**script(turns, expect), "params": {}}
    assert_script(s, Params(WILDCARD_ENABLED=True))
    off = run_script(s, Params())
    assert not [a for a in off.actions if a["action"] == "wildcard"]


def test_warmup_after_opener() -> None:
    turns = [
        {"seat": 1, "text": "Oh that is a fun one, we both love ramen apparently.", "start": 0.5, "end": 2.5},
        {"seat": 2, "text": "Ramen is the best food on the planet, no contest.", "start": 3.1, "end": 5.0},
    ]
    # Warm-up: 20 s after the opener ends (script t = 0). The 5 s steer at 10.0 is blocked; at 20.0 the
    # pause is past the exceptional threshold and the exceptional cooldown (15 s) has cleared.
    expect = [
        {"between": [0.0, 19.9], "action": "none"},
        {"between": [19.9, 21.0], "action": "switch", "count": 1, "at": 20.0},
    ]
    assert_script(script(turns, expect, skip_warmup=False, duration=21.0))


def test_classifier_cancels_lexical_hold() -> None:
    turns, end = lively(0.0, 4)
    turns.append({"seat": 1, "text": "It was honestly so good, let me think", "start": end + 0.7, "end": end + 2.5})
    t = round(end + 2.5, 1)
    hold_results = {"4": {"hold": False, "kind": "none", "expected_seconds": 0, "confidence": 0.9}}
    expect = [
        {"between": [t, t + 0.1], "action": "hold", "kind": "think", "count": 1},
        {"between": [t + 1.2, t + 1.4], "action": "release", "count": 1},
        {"between": [t, t + 5.5], "action": "steer", "count": 1, "at": round(t + 5.0, 1)},
    ]
    assert_script(script(turns, expect, hold_results=hold_results, duration=t + 6))


def test_classifier_upgrades_an_ambiguous_fragment_to_show() -> None:
    turns, end = lively(0.0, 4)
    turns.append({"seat": 1, "text": "and then my phone has the", "start": end + 0.7, "end": end + 2.0})
    t = round(end + 2.0, 1)
    hold_results = {"4": {"hold": True, "kind": "show", "expected_seconds": 30, "confidence": 0.8}}
    # The fragment goes to the classifier once 1 s of silence followed it (HOLD_CHECK_SILENCE_S), and the
    # answer (1 s in the simulator) still lands before the 5 s steer threshold.
    expect = [
        {"between": [t + 1.9, t + 2.2], "action": "hold", "kind": "show", "count": 1},
        {"between": [t, t + 59.9], "action": "none"},
    ]
    assert_script(script(turns, expect, hold_results=hold_results, duration=t + 59.9))


def test_classifier_result_ignored_after_new_speech() -> None:
    turns, end = lively(0.0, 4)
    turns.append({"seat": 1, "text": "and then my phone has the", "start": end + 0.7, "end": end + 2.0})
    t = round(end + 2.0, 1)
    turns.append({"seat": 2, "text": "Wait, your phone has what exactly, tell me everything?", "start": t + 1.0,
                  "end": t + 3.0})
    hold_results = {"4": {"hold": True, "kind": "show", "expected_seconds": 30, "confidence": 0.9, "delay_s": 2.0}}
    expect = [{"between": [0.0, t + 4.0], "action": "hold", "count": 0}]
    assert_script(script(turns, expect, hold_results=hold_results, duration=t + 4.0))


def test_manual_hold_survives_speech_and_resume_clears_it() -> None:
    turns, end = lively(0.0, 4)
    turns.append({"seat": 1, "text": "Okay give me a moment to find the photo.", "start": end + 3.0, "end": end + 4.5})
    t = round(end + 4.5, 1)
    resume_at = round(t + 20.0, 1)
    controls = [{"t": round(end + 0.5, 1), "action": "hold"}, {"t": resume_at, "action": "resume"}]
    expect = [
        {"between": [end, end + 1.0], "action": "hold", "kind": "show", "count": 1},
        {"between": [end, resume_at + 4.9], "action": "none"},
        {"between": [resume_at, resume_at + 0.1], "action": "release", "count": 1},
        {"between": [resume_at, resume_at + 5.5], "action": "steer", "count": 1, "at": round(resume_at + 5.0, 1)},
    ]
    assert_script(script(turns, expect, controls=controls, duration=resume_at + 5.5))


def test_pause_listening_freezes_timer_and_blocks_crumbs() -> None:
    turns, end = lively(0.0, 4)
    resume_at = round(end + 30.0, 1)
    controls = [{"t": round(end + 1.0, 1), "action": "pause_listening"},
                {"t": resume_at, "action": "resume_listening"}]
    expect = [
        {"between": [end, resume_at + 4.9], "action": "none"},
        {"between": [resume_at, resume_at + 5.5], "action": "steer", "count": 1, "at": round(resume_at + 5.0, 1)},
    ]
    assert_script(script(turns, expect, controls=controls, duration=resume_at + 5.5))

    e, _ = calibrated()
    e.on_control("pause_listening", 12.0)
    assert e.snapshot(12.0)["listening_paused"]
    frozen = e.snapshot(12.0)["silence_s"]
    assert e.snapshot(40.0)["silence_s"] == frozen and e.snapshot(40.0)["next"] == "none"
    assert of_type(ticks(e, 12.1, 40.9), "deliver") == []
    e.on_control("resume_listening", 41.0)
    assert e.snapshot(41.0)["silence_s"] == 0.0


def test_safety_lexicon_pauses_automatic_crumbs_but_not_manual() -> None:
    turns, end = lively(0.0, 4)
    turns.append({"seat": 2, "text": "Honestly I think I'm having a panic attack right now.", "start": end + 0.7,
                  "end": end + 3.0})
    t = round(end + 3.0, 1)
    expect = [
        {"between": [t, t + 0.5], "action": "safety_pause", "count": 1},
        {"between": [t, t + 20.0], "action": "none"},
    ]
    result = assert_script(script(turns, expect, duration=t + 20.0))
    assert result.final_snapshot["auto_paused"] and result.final_snapshot["next"] == "none"
    manual = run_script(script(turns, [], controls=[{"t": t + 10, "action": "spark"}], duration=t + 11))
    assert [a for a in manual.actions if a["action"] == "manual"]


# --- engine direct: calibration, self-hearing, barge-in, reconnect, round timer ---------------------


def test_calibration_prompts_and_mapping() -> None:
    e = Engine("social", NAMES, Params())
    fx = e.start_calibration(0.0)
    assert of_type(fx, "calibration")[0]["step"] == "seat1"
    say = of_type(fx, "say")[0]
    assert say == {"type": "say", "id": "sys_cal1",
                   "text": "Before we start: Seat 1, say hi and tell Dev where you're from."}
    e.on_tts_state("sys_cal1", True, 0.0)
    e.on_tts_state("sys_cal1", False, 3.0)
    fx = speak(e, 7, "B", "Hi, I'm Maya and I'm from Austin.", 3.5, 5.0)
    assert e.label_map == {"B": 1}
    assert of_type(fx, "say")[0] == {"type": "say", "id": "sys_cal2", "text": "Seat 2, your turn."}
    assert of_type(fx, "calibration")[0]["step"] == "seat2"
    e.on_tts_state("sys_cal2", True, 5.0)
    e.on_tts_state("sys_cal2", False, 6.0)
    fx = speak(e, 8, "A", "And I'm Dev, from Houston.", 6.5, 8.0)
    assert e.label_map == {"B": 1, "A": 2} and e.phase == "live"
    done = of_type(fx, "calibration")[-1]
    assert done == {"type": "calibration", "step": "done", "label_map": {"B": 1, "A": 2}}
    assert of_type(fx, "live") and of_type(fx, "deliver")[0]["kind"] == "opener"
    assert [t.seat for t in e.turns] == [1, 2]
    assert e.snapshot(8.0)["pending_delivery"]


def test_calibration_fallback_after_15_s_maps_by_order_of_appearance() -> None:
    e = Engine("social", NAMES, Params())
    speak(e, 1, "A", "So are we supposed to talk now?", 0.0, 1.5)
    speak(e, 2, "B", "I think so, yeah.", 2.0, 3.0)
    e.start_calibration(3.5)
    assert ticks(e, 3.6, 18.4) == []  # nobody answers the prompts
    fx = ticks(e, 18.5, 18.5)
    assert e.label_map == {"A": 1, "B": 2} and e.phase == "live"
    assert any("fallback" in d["reason"] for d in decisions(fx, "calibration"))
    assert of_type(fx, "deliver")[0]["kind"] == "opener"
    assert [t.seat for t in e.turns] == [1, 2]  # back-filled


def test_calibration_fallback_with_one_voice_waits_for_the_second() -> None:
    e = Engine("social", NAMES, Params())
    e.start_calibration(0.0)
    speak(e, 1, "A", "Hello there.", 0.5, 1.0)
    assert e.label_map == {"A": 1}
    ticks(e, 1.1, 16.0)
    assert e.phase == "calibrating"
    fx = speak(e, 2, "C", "Hi, I'm Dev.", 20.0, 21.0)
    assert e.label_map == {"A": 1, "C": 2} and e.phase == "live"
    assert of_type(fx, "deliver")


def test_turns_before_mapping_have_no_seat_then_backfill() -> None:
    e = Engine("social", NAMES, Params())
    e.start_calibration(0.0)
    speak(e, 1, "A", "Hi, I'm from Austin.", 3.0, 4.0, complete=None)
    assert e.turns[0].seat == 1
    fx = speak(e, 2, "B", "And I'm from Houston.", 5.0, 6.0)
    assert e.turns[1].seat == 2
    fx = e.on_muse_event({"type": "speechComplete", "turnId": 1, "transcript": "Hi, I'm from Austin."}, 6.5)
    assert of_type(fx, "turn")[0]["turn"]["seat"] == 1


def test_self_hearing_drops_bot_overlap() -> None:
    e, _ = calibrated()
    e.on_tts_state("c_1", True, 20.0)
    fx = speak(e, 10, "C", "So what is something you both love?", 20.1, 22.8, complete=0.2)
    e.on_tts_state("c_1", False, 23.0)
    turn = of_type(fx, "turn")[0]["turn"]
    assert turn["dropped_as_bot"] is True
    assert decisions(fx, "dropped_bot_turn")[0]["inputs"]["overlap_ratio"] == 1.0
    assert of_type(fx, "prefetch") == [] and e._live_turns == []
    # TTS tail: a turn mostly inside the 300 ms tail after a sys_ prompt is also dropped
    e.on_tts_state("sys_x", True, 30.0)
    e.on_tts_state("sys_x", False, 31.0)
    fx = speak(e, 11, "C", "Mm.", 31.0, 31.25)
    assert of_type(fx, "turn")[0]["turn"]["dropped_as_bot"] is True
    # under 50% overlap: a human who started talking right as the crumb ended
    e.on_tts_state("c_2", True, 40.0)
    e.on_tts_state("c_2", False, 43.0)
    fx = speak(e, 12, "A", "Oh I love that question, okay so for me it has to be hiking.", 42.9, 46.0)
    assert of_type(fx, "turn")[0]["turn"]["dropped_as_bot"] is False


def test_dropped_bot_turn_does_not_reset_crumbs_this_silence_or_timer() -> None:
    e, _ = calibrated()
    speak(e, 9, "A", "That opener was fun, we really do both love ramen.", 12.0, 14.0)
    assert e.snapshot(14.0)["crumbs_this_silence"] == 0
    e.crumb_dispatched(crumb("c_1"), 20.0, 300.0, True)
    e.on_tts_state("c_1", True, 20.0)
    speak(e, 10, None, "So what is something you both love?", 20.1, 22.8)
    e.on_tts_state("c_1", False, 23.0)
    snap = e.snapshot(25.0)
    assert snap["crumbs_this_silence"] == 1 and snap["silence_s"] == pytest.approx(2.0)


def test_barge_in_vad_grace_and_single_emit() -> None:
    e, _ = calibrated()
    e.crumb_dispatched(crumb("c_1"), 20.0, 300.0, True)
    e.on_tts_state("c_1", True, 20.0)
    assert e.on_vad(True, 0.2, 20.2) == []  # chime grace: first 300 ms ignored
    assert e.on_vad(False, 0.0, 20.25) == []
    fx = e.on_vad(True, 0.2, 20.4)
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_1"}]
    assert of_type(e.on_vad(True, 0.2, 20.5), "barge_in") == []
    assert of_type(e.on_muse_event({"type": "speechStart", "turnId": 50}, 20.5), "barge_in") == []
    assert e.metrics()["interrupted"] == 1 and e.metrics()["answered"] == 1


def test_barge_in_by_speech_start_and_never_for_system_lines() -> None:
    e, _ = calibrated()
    e.on_tts_state("sys_line", True, 20.0)
    assert of_type(e.on_vad(True, 0.2, 21.0), "barge_in") == []
    assert of_type(e.on_muse_event({"type": "speechStart", "turnId": 60}, 21.0), "barge_in") == []
    e.on_tts_state("sys_line", False, 22.0)
    e.on_muse_event({"type": "speechEnd", "turnId": 60}, 22.5)
    e.on_vad(False, 0.0, 22.5)
    e.crumb_dispatched(crumb("c_2"), 30.0, 300.0, True)
    e.on_tts_state("c_2", True, 30.0)
    assert of_type(e.on_vad(True, 0.2, 30.1), "barge_in") == []  # chime grace: VAD alone does not barge yet
    fx = e.on_muse_event({"type": "speechStart", "turnId": 61}, 30.4)  # Muse speechStart + local VAD
    assert of_type(fx, "barge_in") == []  # waits for the first words: they might be the crumb's own echo
    fx = e.on_muse_event({"type": "transcript", "transcript": "Oh wait actually", "final": False}, 30.6)
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_2"}]
    assert decisions(fx, "barge_in")[0]["inputs"]["trigger"] == "speechStart"


def test_crumb_overtaken_by_speech_is_cancelled_at_playback_start() -> None:
    e, _ = calibrated()
    speak(e, 20, "A", "We should totally go back there.", 12.0, 14.0)
    fx = ticks(e, 14.1, 32.0)  # warm-up ends 29.5, by then the pause is past the exceptional threshold
    assert [f["kind"] for f in of_type(fx, "deliver")] == ["switch"]
    e.on_vad(True, 0.05, 32.1)  # starts talking at the table while generating
    e.on_muse_event({"type": "speechStart", "turnId": 21}, 32.1)
    e.crumb_dispatched(crumb("c_5", "switch"), 32.5, 900.0, False)
    fx = e.on_tts_state("c_5", True, 32.6)
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_5"}]
    assert "already talking" in decisions(fx, "barge_in")[0]["reason"]
    m = e.metrics()
    assert m["interrupted"] == 0 and m["false_interruptions"] == 1
    assert e.snapshot(32.6)["crumbs_this_silence"] == 0


def test_reconnect_resyncs_voices_without_a_new_opener() -> None:
    e, _ = calibrated()
    fx = e.on_muse_reconnect(30.0)
    assert e.label_map == {} and e.phase == "live"
    assert any("Re-syncing voices" in d["reason"] for d in decisions(fx, "calibration"))
    assert of_type(fx, "say")[0]["id"].startswith("sys_")
    assert e.snapshot(30.0)["next"] == "none"
    assert of_type(ticks(e, 30.1, 45.0), "deliver") == []  # no automatic crumbs while unmapped
    # new realtime session: turnIds restart at 1 without clobbering the old turns
    speak(e, 1, "B", "Hi again, Maya here.", 46.0, 47.0)
    fx = speak(e, 2, "A", "And Dev here.", 48.0, 49.0)
    assert e.label_map == {"B": 1, "A": 2}
    assert of_type(fx, "deliver") == [] and of_type(fx, "live") == []
    assert of_type(fx, "calibration")[-1]["step"] == "done"
    assert len(e.turns) == 4


def test_round_timer_speed_dating() -> None:
    e, _ = calibrated("speed_dating")
    e.on_control("pause_listening", 9.6)  # keep automatic crumbs out of the way
    live_at = 6.5
    snap = e.snapshot(live_at + 100)
    assert snap["round_remaining_s"] == pytest.approx(260.0)
    fx = ticks(e, live_at + 299.0, live_at + 299.9)
    assert of_type(fx, "round_warning") == []
    fx = ticks(e, live_at + 300.0, live_at + 310.0)
    assert len(of_type(fx, "round_warning")) == 1
    fx = ticks(e, live_at + 359.0, live_at + 361.0)
    assert len(of_type(fx, "round_end")) == 1
    assert Engine("social", NAMES, Params()).snapshot(0.0)["round_remaining_s"] is None


def test_stuck_turn_is_force_closed() -> None:
    e, _ = calibrated()
    e.on_muse_event({"type": "speechStart", "turnId": 70}, 20.0)
    e.on_muse_event({"type": "transcript", "transcript": "this never ends", "final": False}, 21.0)
    assert e.snapshot(49.0)["turn_active"]
    ticks(e, 49.9, 50.0)
    assert not e.snapshot(50.0)["turn_active"]
    fx = ticks(e, 50.1, 55.1)
    committed = of_type(fx, "turn")
    assert committed and committed[0]["turn"]["text"] == "this never ends"


def test_out_of_order_speech_complete_and_partial_attribution() -> None:
    e, _ = calibrated()
    e.on_muse_event({"type": "speechStart", "turnId": 80}, 20.0)
    fx = e.on_muse_event({"type": "transcript", "transcript": "first speaker", "final": False}, 20.5)
    assert of_type(fx, "partial") == [{"type": "partial", "text": "first speaker", "label": None}]
    e.on_muse_event({"type": "speaker", "label": "A"}, 21.0)
    e.on_muse_event({"type": "speechEnd", "turnId": 80}, 21.0)
    e.on_muse_event({"type": "speechStart", "turnId": 81}, 21.5)
    fx = e.on_muse_event({"type": "transcript", "transcript": "second speaker", "final": False}, 22.0)
    assert of_type(fx, "partial")[0]["text"] == "second speaker"
    e.on_muse_event({"type": "speaker", "label": "B"}, 22.4)
    e.on_muse_event({"type": "speechEnd", "turnId": 81}, 22.5)
    e.on_muse_event({"type": "speechComplete", "turnId": 81, "transcript": "Second speaker, final."}, 22.8)
    e.on_muse_event({"type": "speechComplete", "turnId": 80, "transcript": "First speaker, final."}, 23.0)
    by_text = {t.text: t for t in e.turns}
    assert by_text["First speaker, final."].seat == 1
    assert by_text["Second speaker, final."].seat == 2
    assert by_text["Second speaker, final."].gap_before == pytest.approx(0.5)


def test_speech_complete_before_speech_end_commits_at_end() -> None:
    e, _ = calibrated()
    e.on_muse_event({"type": "speechStart", "turnId": 90}, 20.0)
    assert e.on_muse_event({"type": "speechComplete", "turnId": 90, "transcript": "Early final."}, 21.0) == []
    fx = e.on_muse_event({"type": "speechEnd", "turnId": 90}, 21.2)
    assert of_type(fx, "turn")[0]["turn"]["text"] == "Early final."


def test_energy_tags_prefetch_and_staleness_e() -> None:
    e, _ = calibrated()
    all_fx: list[dict] = []
    t = 10.0
    for i in range(4):
        all_fx += speak(e, 100 + i, "AB"[i % 2], LINES[i], t, t + 2.0)
        t += 2.7
    assert len(of_type(all_fx, "prefetch")) == 4
    tag = of_type(all_fx, "tag_energy")
    assert len(tag) == 1 and tag[0]["context"].count("\n") == 3 and tag[0]["context"].startswith("Maya: ")
    fx = e.apply_energy_tag(EnergyTag(topic="climbing trips", energy="low", winding_down=True), t)
    assert of_type(fx, "prefetch") and e.current_topic == "climbing trips"
    snap = e.snapshot(t)
    assert snap["parts"]["E"] == 1.0 and snap["energy"] == "low" and snap["topic"] == "climbing trips"
    assert e.apply_energy_tag(EnergyTag(topic="climbing trips", energy="high", winding_down=False), t) == []


def test_recent_transcript_only_mapped_human_turns() -> None:
    e, _ = calibrated()
    speak(e, 100, "A", "I love ramen.", 10.0, 11.0)
    speak(e, 101, "C", "Unknown voice here.", 12.0, 13.0)
    e.on_tts_state("c_9", True, 14.0)
    speak(e, 102, "B", "Bot echo.", 14.1, 15.0)
    e.on_tts_state("c_9", False, 15.0)
    text = e.recent_transcript(16.0)
    assert "Maya: I love ramen." in text and "Unknown voice" not in text and "Bot echo" not in text
    assert "Maya: Hi, I'm from Austin." in text and "Dev: Hey, I'm from Houston." in text
    assert "Maya: I love ramen." not in e.recent_transcript(500.0, seconds=60)


def test_depth_max_ramp() -> None:
    e, _ = calibrated("social")
    live = 6.5
    assert e.depth_max(live) == 1
    assert e.depth_max(live + 5 * 60) == 2
    assert e.depth_max(live + 60 * 60) == 4
    sd, _ = calibrated("speed_dating")
    assert sd.depth_max(live + 3 * 60) == 2 and sd.depth_max(live + 60 * 60) == 3
    nw, _ = calibrated("networking")
    assert nw.depth_max(live + 59 * 60) == 3


def test_metrics_success_and_latency() -> None:
    e, _ = calibrated()
    e.crumb_dispatched(crumb("c_1"), 20.0, 400.0, True)
    e.on_tts_state("c_1", True, 20.0)
    e.on_tts_state("c_1", False, 23.0)
    t = 24.0
    for i in range(3):
        speak(e, 200 + i, "AB"[i % 2], LINES[i], t, t + 2.0)
        t += 2.5
    e.crumb_dispatched(crumb("c_2"), 40.0, 2000.0, False)
    m = e.metrics()
    assert m["crumbs"] == 3 and m["answered"] == 2  # opener + c_1 answered; c_2 still pending
    assert m["success_rate"] == 1.0 and m["false_interruptions"] == 0
    assert m["latency_ms_p50"] == 400.0 and m["latency_ms_p95"] == 2000.0
    e.tick(101.0)
    assert e.metrics()["success_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_crumb_failed_clears_pending_and_rate() -> None:
    e, _ = calibrated()
    fx = ticks(e, 10.0, 20.0)  # warm-up after the opener (ended 9.5) blocks until 29.5
    assert of_type(fx, "deliver") == []
    fx = ticks(e, 20.1, 29.5)
    assert of_type(fx, "deliver")[0]["kind"] == "switch"
    assert e.snapshot(29.6)["pending_delivery"] and e.snapshot(29.6)["crumbs_last_10min"] == 1
    e.crumb_failed(30.0)
    snap = e.snapshot(30.0)
    assert not snap["pending_delivery"] and snap["crumbs_last_10min"] == 0


# --- contract checks ---------------------------------------------------------------------------


def _ts_interface_keys(name: str) -> set[str]:
    src = TYPES_TS.read_text()
    body = re.search(r"export interface " + name + r" \{(.*?)\n\}", src, re.S)
    assert body, name
    return set(re.findall(r"^\s+(\w+)\??:", body.group(1), re.M))


def test_snapshot_matches_engine_snapshot_type() -> None:
    e, _ = calibrated()
    snap = e.snapshot(12.0)
    assert set(snap) == _ts_interface_keys("EngineSnapshot") - {"cached"}
    assert set(snap["metrics"]) == _ts_interface_keys("EngineMetrics")
    assert set(snap["parts"]) == {"L", "R", "G", "Q", "E"}
    assert snap["label_map"] == {"A": 1, "B": 2}
    json.dumps(snap)


def test_decisions_carry_no_transcript_text_and_no_em_dash() -> None:
    from sim.replay import SCRIPTS_DIR, load_script

    for path in sorted(SCRIPTS_DIR.glob("*.json")):
        s = load_script(path)
        result = run_script(s)
        blob = json.dumps(result.decisions)
        assert "\u2014" not in blob
        for t in s["turns"]:
            assert t["text"] not in blob, f"{path.name}: transcript text leaked into a decision"
        for d in result.decisions:
            assert d["reason"] and isinstance(d.get("inputs"), dict)
            assert all(not isinstance(v, str) or len(v) < 40 for v in d["inputs"].values())


def test_engine_effect_types_are_in_the_contract() -> None:
    contract = {"partial", "turn", "calibration", "say", "deliver", "barge_in", "skip", "classify_hold",
                "tag_energy", "prefetch", "decision", "round_warning", "round_end", "live"}
    seen: set[str] = set()
    e, fx = calibrated("speed_dating")
    seen |= {f["type"] for f in fx}
    seen |= {f["type"] for f in ticks(e, 9.6, 400.0)}
    assert seen <= contract
