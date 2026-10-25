"""Foreground (who is at the table): every Muse turn is judged at speechEnd from its label, words and the
Table VAD over it (classify_turn). Only a seat turn restarts the pause or counts as conversation; a pending
turn only blocks crumbs while open. Pure engine tests on a fake clock; no network, no AI."""
from __future__ import annotations

import json

import pytest

from config import Params
from engine.core import Engine
from engine.state import SeatLevel, TurnRecord, VadLog, VadStats, classify_turn
from models import Turn
from tests.test_noise_echo import line_crumb, seated, vad_frames
from tests.test_policy import NAMES, calibrated, decisions, of_type, speak

P = Params()


def stats(*, frames: int = 20, expected: float = 20.0, active_frac: float = 1.0, level_db: float | None = -30.0) -> VadStats:
    return VadStats(frames=frames, expected=expected, active_frac=active_frac, level_db=level_db,
                    last_active_t=None, voice_end_t=None)


def rec(text: str, label: str | None = "A", *, dev: bool = False) -> TurnRecord:
    r = TurnRecord(turn=Turn(turn_id=1, label=label), epoch=0, muse_id=1, dev=dev)
    r.partial = text
    return r


def known(db: float = -30.0) -> SeatLevel:
    s = SeatLevel()
    s.observe(db)
    s.observe(db)
    return s


NO_VAD = stats(frames=0, active_frac=0.0, level_db=None)


# --- classify_turn ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("r, st, mapped, fg, reason", [
    # mapped (seat) labels
    (rec("I mostly climb at the gym"), stats(), True, "seat", "seat label"),  # near
    (rec("I mostly climb at the gym"), stats(level_db=-48.0), True, "background", "far voice on a seat label"),
    (rec("I mostly climb at the gym"), stats(active_frac=0.1), True, "background", "far voice on a seat label"),
    (rec("I mostly climb at the gym"), NO_VAD, True, "seat", "seat label"),  # no evidence: trust the label
    (rec("I mostly climb at the gym"), stats(level_db=-40.0), True, "seat", "seat label"),  # in between
    # unmapped labels
    (rec("I mostly climb at the gym", "C"), stats(), False, "seat", "near voice, unmapped label"),
    (rec("I mostly climb at the gym", "C"), stats(level_db=-48.0), False, "background", "unmapped label"),
    (rec("yeah right", "C"), stats(), False, "seat", "near voice, unmapped label"),  # near: a drifted seat, however short
    (rec("I mostly climb at the gym", "C"), NO_VAD, False, "background", "unmapped label"),
    (rec("I mostly climb at the gym", None), NO_VAD, False, "seat", "no VAD evidence"),  # an old Table
    (rec("yeah", None), NO_VAD, False, "background", "unmapped label"),
    # words
    (rec("", "A"), stats(), True, "background", "empty"),
    (rec("uh", "A"), stats(level_db=-42.0), True, "background", "filler"),  # a filler not near
    (rec("uh", "A"), stats(), True, "seat", "seat label"),  # the same filler, near: a seat backchannel
    (rec("uh", "A"), NO_VAD, True, "seat", "seat label"),
    # dev
    (rec("", None, dev=True), NO_VAD, False, "seat", "dev"),
])
def test_classify_turn_matrix(r: TurnRecord, st: VadStats, mapped: bool, fg: str, reason: str) -> None:
    assert classify_turn(r, st, known(), mapped, True, P) == (fg, reason)


def test_classify_turn_is_ungated_during_calibration_or_with_gating_off() -> None:
    far = stats(level_db=-55.0)
    assert classify_turn(rec("hi there", "C"), far, known(), False, False, P) == ("seat", "ungated")
    off = Params(BYSTANDER_GATING=False)
    assert classify_turn(rec("", "C"), far, known(), False, True, off) == ("seat", "ungated")


def test_unknown_seat_level_judges_on_coverage_only() -> None:
    unknown = SeatLevel()
    assert classify_turn(rec("I mostly climb at the gym", "C"), stats(level_db=-60.0), unknown, False, True, P)[0] == "seat"
    assert classify_turn(rec("I mostly climb at the gym"), stats(active_frac=0.1), unknown, True, True, P)[0] == "background"


def test_vadlog_stats_coverage_level_and_voice_end() -> None:
    log = VadLog()
    for k in range(10):
        log.add(k / 10, True, -30.0 if k < 8 else -20.0)
    log.add(1.0, False, -60.0, 500)  # the VAD falls after its 500 ms release
    log.add(1.1, False, -60.0, 600)
    st = log.stats(0.0, 1.1)
    assert st.frames == 12 and st.evidence
    assert st.active_frac == pytest.approx(10 / 12)
    assert st.level_db == -30.0  # 80th percentile of the active frames
    assert st.last_active_t == pytest.approx(0.9) and st.voice_end_t == pytest.approx(0.5)
    assert not log.stats(5.0, 7.0).evidence  # no frames there


# --- background turns change nothing ----------------------------------------------------------------------


def bystander(e: Engine, mid: int, text: str, start: float, end: float, label: str = "C") -> list[dict]:
    """A Muse turn the Table VAD does not hear (frames keep arriving, all inactive)."""
    fx = e.on_muse_event({"type": "speechStart", "turnId": mid}, start)
    fx += vad_frames(e, start, end - 0.1, active=False, level_db=-60.0)
    fx += e.on_muse_event({"type": "transcript", "transcript": text, "final": False}, end - 0.1)
    fx += e.on_muse_event({"type": "speaker", "label": label}, end - 0.05)
    fx += e.on_muse_event({"type": "speechEnd", "turnId": mid}, end)
    fx += e.on_muse_event({"type": "speechComplete", "turnId": mid, "transcript": text}, end + 0.1)
    return fx


def lively(e: Engine, t: float) -> float:
    """Two seat turns with VAD (gives the 2 human turns the regular cooldown wants)."""
    for i, label in enumerate("ABA"):
        vad_frames(e, t, t + 1.9, level_db=-30.0)
        speak(e, 40 + i, label, "So we drove out to the trailhead before sunrise that day.", t, t + 2.0, complete=0.1)
        vad_frames(e, t + 2.0, t + 2.6, active=False, level_db=-60.0)
        t += 2.7
    return t - 0.7


def test_a_background_close_leaves_the_conversation_untouched() -> None:
    e = seated()
    end = lively(e, 40.0)
    e.on_control("hold", end + 0.1)  # a manual hold must survive it too
    pending_before = e._pending
    crumbs_before = e._crumbs_this_silence
    ledger_before = [r.human_turns_after for r in e._ledger.records]
    live_before = len(e._live_turns)
    anchor_before = e._timer.anchor
    transcript_before = e.recent_transcript(end + 5)
    fx = bystander(e, 70, "did you see the game last night", end + 0.5, end + 2.5)
    assert decisions(fx, "background_turn") and e.turns[-1].background
    assert e._pending is pending_before and e._crumbs_this_silence == crumbs_before
    assert [r.human_turns_after for r in e._ledger.records] == ledger_before
    assert len(e._live_turns) == live_before and e._timer.anchor == anchor_before
    assert e.recent_transcript(end + 5) == transcript_before
    assert "game last night" not in e._context(8)
    assert [f for f in fx if f["type"] == "prefetch"] == [] and of_type(fx, "tag_energy") == []
    assert e.snapshot(end + 2.6)["hold"] is not None  # the hold is kept


def test_a_background_turn_does_not_cancel_a_crumb_being_prepared() -> None:
    e = seated()
    end = lively(e, 40.0)
    fx = vad_frames(e, end + 0.1, end + 3.0, active=False, level_db=-60.0)
    assert [f["kind"] for f in of_type(fx, "deliver")] == ["steer"]  # 3.0 s after the seat turn
    fx = bystander(e, 71, "no way that is so funny", end + 3.1, end + 4.0)
    assert not e.pending_cancelled and decisions(fx, "barge_in") == []


def test_an_open_pending_turn_blocks_firing_while_the_pause_keeps_growing() -> None:
    e = seated()
    end = lively(e, 40.0)
    e.on_muse_event({"type": "speechStart", "turnId": 72}, end + 0.5)
    fx = vad_frames(e, end + 0.5, end + 2.4, active=False, level_db=-60.0)  # open under OPEN_TURN_MIN_S
    fx += vad_frames(e, end + 2.5, end + 2.9, active=False, level_db=-60.0)
    snap = e.snapshot(end + 2.4)
    assert snap["silence_s"] == pytest.approx(2.4, abs=0.11) and snap["defer"] == "turn"
    e.on_muse_event({"type": "transcript", "transcript": "yeah", "final": False}, end + 2.9)
    e.on_muse_event({"type": "speaker", "label": "C"}, end + 2.95)
    fx += e.on_muse_event({"type": "speechEnd", "turnId": 72}, end + 3.0)  # past T_LONG_S, still open until now
    assert of_type(fx, "deliver") == []
    fx = vad_frames(e, end + 3.1, end + 3.6, active=False, level_db=-60.0)
    delivered = [d for d in decisions(fx) if d["action"] == "steer"]
    assert len(delivered) == 1
    assert delivered[0]["inputs"]["silence_s"] == pytest.approx(3.4, abs=0.11)  # 0.4 s settle after the close


def test_an_open_turn_the_mic_does_not_hear_is_released_and_a_near_onset_blocks_again() -> None:
    e = seated()
    end = lively(e, 40.0)
    e.on_muse_event({"type": "speechStart", "turnId": 73}, end + 0.2)
    vad_frames(e, end + 0.2, end + 2.6, active=False, level_db=-60.0)
    assert e.snapshot(end + 2.6)["defer"] == "turn"  # not released before OPEN_TURN_MIN_S
    vad_frames(e, end + 2.7, end + 2.7, active=False, level_db=-60.0)
    assert e.snapshot(end + 2.7)["defer"] is None and e.snapshot(end + 2.7)["turn_active"] is False
    e.on_vad(True, 0.05, end + 2.8, level_db=-30.0)  # someone at the table starts talking
    assert e.dispatch_blocked(end + 2.8) and e.snapshot(end + 2.8)["defer"] == "vad"


def test_a_speech_complete_upgrades_an_empty_background_turn_and_resets_to_its_voice_end() -> None:
    e = seated()
    end = lively(e, 40.0)
    vad_frames(e, end + 1.0, end + 2.0, level_db=-30.0)
    e.on_muse_event({"type": "speechStart", "turnId": 74}, end + 1.2)
    e.on_vad(False, 0.002, end + 2.1, level_db=-60.0, quiet_ms=100)
    e.on_muse_event({"type": "speaker", "label": "B"}, end + 2.3)
    fx = e.on_muse_event({"type": "speechEnd", "turnId": 74}, end + 2.4)  # no words yet: "empty"
    assert [d["inputs"]["cause"] for d in decisions(fx, "background_turn")] == ["empty"]
    assert e._timer.anchor == pytest.approx(end + 0.0, abs=0.05)
    fx = e.on_muse_event({"type": "speechComplete", "turnId": 74, "transcript": "Wait, which trail was it?"}, end + 2.6)
    assert not e.turns[-1].background
    assert e._timer.anchor == pytest.approx(end + 2.0, abs=0.05)  # VAD fell at 2.1 with quiet_ms 100
    assert [f for f in fx if f["type"] == "prefetch"]  # a normal commit


def test_the_noise_latch_survives_a_background_turn_and_clears_on_a_seat_turn() -> None:
    e = seated()
    end = lively(e, 40.0)
    vad_frames(e, end + 0.1, end + 3.0, level_db=-42.0)  # a fan: the room is marked noisy
    assert e._noise_latched
    e.on_muse_event({"type": "speechStart", "turnId": 75}, end + 3.1)
    vad_frames(e, end + 3.1, end + 3.9, level_db=-42.0)
    e.on_muse_event({"type": "transcript", "transcript": "uh", "final": False}, end + 3.9)
    e.on_muse_event({"type": "speaker", "label": "C"}, end + 3.95)
    e.on_muse_event({"type": "speechEnd", "turnId": 75}, end + 4.0)
    assert e.turns[-1].background is False and e._noise_latched  # not committed yet, but judged background
    e.on_vad(True, 0.05, end + 4.1, level_db=-30.0)
    speak(e, 76, "A", "Anyway, back to the climbing trip.", end + 4.1, end + 5.5)
    assert not e._noise_latched


def test_calibration_skips_a_far_label_and_maps_the_near_one() -> None:
    e = Engine("social", NAMES, Params())
    e.start_calibration(0.0)
    e.on_tts_state("sys_cal1", True, 0.0)
    e.on_tts_state("sys_cal1", False, 2.0)
    vad_frames(e, 2.8, 3.9, level_db=-30.0)
    speak(e, 1, "A", "Hi, I'm from Austin.", 3.0, 4.0)
    e.on_tts_state("sys_cal2", True, 4.4)
    e.on_tts_state("sys_cal2", False, 5.0)
    vad_frames(e, 5.0, 6.4, active=False, level_db=-60.0)
    speak(e, 2, "C", "and then the waiter came back", 5.2, 6.5)  # a bystander the Table mic does not hear
    assert e.label_map == {"A": 1}
    vad_frames(e, 6.6, 7.9, level_db=-30.0)
    speak(e, 3, "B", "Hey, I'm from Houston.", 6.7, 8.0)
    assert e.label_map == {"A": 1, "B": 2} and e.snapshot(8.0)["seat_level_db"] == pytest.approx(-30.0)


# --- the pause starts at the end of voice --------------------------------------------------------------------


def test_the_pause_is_backdated_to_the_end_of_voice() -> None:
    e = seated()
    t = 40.0
    vad_frames(e, t - 2.0, t - 0.1, level_db=-30.0)
    e.on_muse_event({"type": "speechStart", "turnId": 80}, t - 1.8)
    e.on_vad(False, 0.002, t, level_db=-60.0, quiet_ms=500)  # voice ended at t - 0.5 (500 ms release)
    vad_frames(e, t + 0.1, t + 0.6, active=False, level_db=-60.0)
    e.on_muse_event({"type": "transcript", "transcript": "So what do you do for fun", "final": False}, t + 0.6)
    e.on_muse_event({"type": "speaker", "label": "A"}, t + 0.65)
    e.on_muse_event({"type": "speechEnd", "turnId": 80}, t + 0.7)
    assert e._timer.anchor == pytest.approx(t - 0.5, abs=0.05)
    assert e.metrics()["endpoint_lag_ms_p95"] == pytest.approx(1200, abs=50)  # the calibration turns had none


def test_backdating_is_capped() -> None:
    e = seated()
    t = 40.0
    vad_frames(e, t - 2.0, t - 0.1, level_db=-30.0)
    e.on_muse_event({"type": "speechStart", "turnId": 81}, t - 1.8)
    e.on_vad(False, 0.002, t, level_db=-60.0, quiet_ms=0)
    vad_frames(e, t + 0.1, t + 2.9, active=False, level_db=-60.0)
    e.on_muse_event({"type": "transcript", "transcript": "So what do you do for fun", "final": False}, t + 2.9)
    e.on_muse_event({"type": "speaker", "label": "A"}, t + 2.95)
    e.on_muse_event({"type": "speechEnd", "turnId": 81}, t + 3.0)  # 3 s late
    assert e._timer.anchor == pytest.approx(t + 3.0 - e.params.ENDPOINT_BACKDATE_MAX_S, abs=0.01)


def test_level_falls_back_to_the_rms_in_db() -> None:
    e, _ = calibrated()
    e.on_vad(True, 0.1, 20.0)
    assert e._vadlog.window(20.0, 20.0)[0].level_db == pytest.approx(-20.0)
    e.on_vad(True, 0.0, 20.1)
    assert e._vadlog.window(20.1, 20.1)[0].level_db == pytest.approx(-120.0)


def test_background_turn_decisions_carry_numbers_only() -> None:
    e = seated()
    end = lively(e, 40.0)
    secret = "my landlord never fixed the heater"
    fx = bystander(e, 82, secret, end + 0.5, end + 2.5)
    d = decisions(fx, "background_turn")[0]
    assert secret not in json.dumps(d)
    strings = {k: v for k, v in d["inputs"].items() if isinstance(v, str)}
    assert set(strings) == {"cause"} and strings["cause"] in {
        "empty", "filler", "far voice on a seat label", "unmapped label"}
    assert set(d["inputs"]) == {"turn_id", "duration_s", "words", "active_frac", "level_db", "seat_db",
                                "label_mapped", "cause"}


def test_a_pending_speech_start_cancels_nothing_until_it_closes_as_a_seat() -> None:
    e = seated()
    end = lively(e, 40.0)
    fx = vad_frames(e, end + 0.1, end + 3.0, active=False, level_db=-60.0)
    assert of_type(fx, "deliver")
    e.on_muse_event({"type": "speechStart", "turnId": 83}, end + 3.1)  # no near VAD: pending
    assert not e.pending_cancelled and e.dispatch_blocked(end + 3.1)
    e.on_muse_event({"type": "transcript", "transcript": "Oh wait, I wanted to ask you something", "final": False}, end + 4.0)
    e.on_muse_event({"type": "speaker", "label": "B"}, end + 4.05)
    e.on_muse_event({"type": "speechEnd", "turnId": 83}, end + 4.1)  # a seat (no VAD evidence against it)
    assert e.pending_cancelled  # it started after the steer was decided


def test_dispatch_timed_out_requeues_a_manual_crumb_and_frees_the_rate_cap() -> None:
    e = seated()
    end = lively(e, 40.0)
    fx = vad_frames(e, end + 0.1, end + 3.0, active=False, level_db=-60.0)
    assert of_type(fx, "deliver") and e.snapshot(end + 3.0)["crumbs_last_10min"] == 1
    fx = e.dispatch_timed_out(end + 5.0, waited_s=2.0)
    assert decisions(fx, "dispatch_blocked")[0]["inputs"]["waited_s"] == 2.0
    assert e._pending is None and e.snapshot(end + 5.0)["crumbs_last_10min"] == 0
    e.on_control("spark", end + 6.0)
    assert e._pending is not None and e._pending.kind == "manual"
    e.dispatch_timed_out(end + 8.0, waited_s=2.0)
    assert e._queued is not None and e._queued[0] == "manual"  # re-queued for the next quiet moment


def test_snapshot_reports_the_foreground_fields() -> None:
    e = seated()
    snap = e.snapshot(10.0)
    assert snap["seat_level_db"] == pytest.approx(-30.0) and snap["background_turns"] == 0
    assert snap["defer"] is None and snap["foreground_open"] is False
    crumb = line_crumb("c_9")
    assert crumb.id == "c_9"


def test_a_dev_turn_maps_in_calibration_even_with_a_silent_table_vad() -> None:
    e = Engine("social", NAMES, Params())
    e.start_calibration(0.0)
    e.on_tts_state("sys_cal1", True, 0.0)
    e.on_tts_state("sys_cal1", False, 2.0)
    vad_frames(e, 2.0, 4.0, active=False, level_db=-60.0)  # the Table mic is on, nobody is talking
    for evt in ({"type": "speechStart", "turnId": 1, "dev": True},
                {"type": "transcript", "transcript": "Hi, I'm from Austin.", "final": False},
                {"type": "speaker", "label": "A"}, {"type": "speechEnd", "turnId": 1, "dev": True}):
        e.on_muse_event(evt, 4.0)
    assert e.label_map == {"A": 1}
