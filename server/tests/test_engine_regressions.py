"""Regression tests for the engine review findings (self-hearing, hold negatives, pending crumbs, playback
gaps, round warning gates, safety pause, lost tts_state). Each test failed before its fix."""
from __future__ import annotations

import copy

import pytest

from config import Params
from engine import core as core_mod
from engine.core import Engine
from engine.hold import hold_vetoed, is_distress, match_hold
from engine.state import BOT_SPEECH_MAX_S
from sim.replay import SCRIPTS_DIR, check_expectations, load_script, run_script
from tests.test_policy import LINES, NAMES, calibrated, crumb, decisions, lively, of_type, script, speak, ticks


def _actions(result, action: str) -> list[dict]:
    return [a for a in result.actions if a["action"] == action]


# --- self-hearing: a Muse speechStart alone never barges in on the crumb (PRD 8.11, F19) ---------------


@pytest.mark.parametrize("echo_start", [59.4, 59.6, 60.2])
def test_late_echo_turn_is_dropped_not_a_barge_in(echo_start: float) -> None:
    s = copy.deepcopy(load_script(SCRIPTS_DIR / "self_hearing.json"))
    s["turns"][-1]["start"] = echo_start  # Muse reports the echo 0.5 s or more into the crumb
    result = run_script(s)
    assert not check_expectations(s, result), check_expectations(s, result)
    assert _actions(result, "crumb_interrupted") == []
    assert result.metrics["interrupted"] == 0


def test_speech_start_barges_in_only_with_local_vad() -> None:
    e, _ = calibrated()
    e.crumb_dispatched(crumb("c_1"), 20.0, 300.0, True)
    e.on_tts_state("c_1", True, 20.0)
    fx = e.on_muse_event({"type": "speechStart", "turnId": 60}, 21.0)  # no VAD: maybe our own echo
    assert of_type(fx, "barge_in") == []
    fx = e.on_vad(True, 0.2, 21.1)  # the room really has a voice in it
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_1"}]
    e.on_vad(False, 0.0, 21.2)
    e.on_tts_state("c_1", False, 21.35)
    e.crumb_dispatched(crumb("c_2"), 30.0, 300.0, True)
    e.on_tts_state("c_2", True, 30.0)
    e.on_vad(True, 0.2, 30.1)  # inside the chime grace
    fx = e.on_muse_event({"type": "speechStart", "turnId": 61}, 30.5)  # Muse + VAD agree: barge at once
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_2"}]


# --- hold negatives apply only to the matches they target (PRD 8.6, Appendix A) ----------------------


@pytest.mark.parametrize("text", [
    "Wait, really? Hold on, let me find it.",
    "Wait, what? Let me pull it up.",
    "I can't wait, hold on let me show you.",
    "Wait, seriously? Let me show you.",
])
def test_surprise_or_cant_wait_then_explicit_hold_phrase_is_a_show_hold(text: str) -> None:
    assert match_hold(text) == "show"


@pytest.mark.parametrize("text", [
    "Wait, really? That's insane, how did you even get in?",
    "Wait, what? Hold on, that is so insane how",
    "I honestly cant wait",
    "I can't wait for the trip.",
])
def test_negatives_still_veto_what_they_target(text: str) -> None:
    assert match_hold(text) is None


def test_vetoed_hold_phrase_goes_to_the_classifier() -> None:
    assert hold_vetoed("I honestly cant wait.")
    assert not hold_vetoed("That was a great time at the lake with everyone.")
    e, _ = calibrated()
    fx = speak(e, 30, "A", "Honestly I just cannot wait, I really cant wait.", 12.0, 14.0)
    assert match_hold("Honestly I just cannot wait, I really cant wait.") is None
    assert of_type(fx, "classify_hold")


def test_surprise_then_show_holds_in_the_simulator() -> None:
    s = copy.deepcopy(load_script(SCRIPTS_DIR / "show_hold_then_resume.json"))
    s["turns"][2]["text"] = "Wait, really? Hold on, let me find it."
    result = run_script(s)
    assert not check_expectations(s, result), check_expectations(s, result)


# --- a crumb still being generated is cancelled by speech (PRD 8.1) -----------------------------------


def _pending_script(talk_end: float) -> dict:
    s = copy.deepcopy(load_script(SCRIPTS_DIR / "long_pause_lively.json"))
    s["turns"] = [t for t in s["turns"] if t["end"] <= 65.5]
    s["turns"].append({"seat": 2, "text": "Hmm okay so", "start": 71.0, "end": talk_end})
    s["gen_latency_s"] = 2.5
    s["expect"] = [{"between": [65.5, 71.0], "action": "steer", "count": 1, "at": 70.5}]
    s["duration"] = 76.0
    return s


@pytest.mark.parametrize("talk_end", [71.8, 75.0])
def test_speech_during_generation_cancels_the_pending_crumb(talk_end: float) -> None:
    s = _pending_script(talk_end)
    result = run_script(s)
    assert not check_expectations(s, result)
    assert [a["t"] for a in _actions(result, "crumb_cancelled")] == [73.0]
    assert _actions(result, "crumb_interrupted") == []  # never started, so nothing to fade
    assert result.metrics["crumbs"] == 1  # only the opener reached the room
    cancel = [d for d in result.decisions if d["action"] == "barge_in"]
    assert cancel and cancel[0]["inputs"]["trigger"] == "pending" and cancel[0]["t"] == 71.0
    assert result.final_snapshot["crumbs_last_10min"] == 0  # the cancelled decision does not count


def test_cancelled_manual_crumb_is_requeued_until_silence() -> None:
    e, _ = calibrated()
    fx = e.on_control("spark", 12.0)
    assert of_type(fx, "deliver")[0]["kind"] == "manual"
    speak(e, 40, "A", "Oh wait, before that, one more thing.", 12.5, 14.0)
    assert e.pending_cancelled
    e.crumb_cancelled(14.5)
    fx = ticks(e, 14.6, 14.6)
    assert [f["kind"] for f in of_type(fx, "deliver")] == ["manual"]


def test_cancelled_crumb_sent_anyway_is_faded_at_playback_start() -> None:
    """Fallback while the runner does not veto yet: the stale crumb never plays into the fresh pause."""
    e, _ = calibrated()
    speak(e, 20, "A", "We should totally go back there.", 12.0, 14.0)
    assert [f["kind"] for f in of_type(ticks(e, 14.1, 32.0), "deliver")] == ["switch"]
    speak(e, 21, "B", "Totally.", 32.1, 32.4)  # short reply while generating, done before dispatch
    e.crumb_dispatched(crumb("c_5", "switch"), 33.0, 900.0, False)
    fx = e.on_tts_state("c_5", True, 33.1)
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_5"}]
    assert decisions(fx, "barge_in")[0]["inputs"]["trigger"] == "cancelled"
    e.on_tts_state("c_5", False, 33.35)
    assert e.snapshot(34.0)["crumbs_this_silence"] == 0
    assert e.snapshot(34.0)["crumbs_last_10min"] == 0  # the faded automatic crumb does not count toward the rate limit


# --- Skip while generating, and the dispatch-to-playback gap (PRD 6.6) ---------------------------------


def test_skip_while_generating_discards_the_pending_crumb() -> None:
    e, _ = calibrated()
    e.on_control("spark", 12.0)
    fx = e.on_control("skip", 12.5)
    assert of_type(fx, "deliver") == []  # the switch waits for the cancelled one to resolve
    assert decisions(fx, "skip")[0]["inputs"]["crumb_generating"] is True
    assert e.pending_cancelled
    e.crumb_cancelled(13.0)
    fx = ticks(e, 13.1, 13.1)
    assert [f["kind"] for f in of_type(fx, "deliver")] == ["switch"]


def test_skip_during_the_chime_interrupts_the_dispatched_crumb() -> None:
    e, _ = calibrated()
    e.on_control("spark", 12.0)
    e.crumb_dispatched(crumb("c_3", "manual"), 12.1, 100.0, True)
    fx = e.on_control("skip", 12.4)  # chime still playing, tts_state not reported yet
    assert of_type(fx, "skip") == [{"type": "skip", "crumb_id": "c_3"}]
    assert e._ledger.get("c_3").skipped
    fx = e.on_tts_state("c_3", True, 12.85)  # the Table played it anyway: fade at once
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_3"}]


def test_double_tap_during_the_chime_sends_one_crumb() -> None:
    e, _ = calibrated()
    assert len(of_type(e.on_control("spark", 12.0), "deliver")) == 1
    e.crumb_dispatched(crumb("c_3", "manual"), 12.05, 50.0, True)
    assert of_type(e.on_control("spark", 12.11), "deliver") == []
    assert of_type(ticks(e, 12.2, 12.7), "deliver") == []
    e.on_tts_state("c_3", True, 12.8)
    e.on_tts_state("c_3", False, 15.8)
    assert of_type(ticks(e, 15.9, 17.0), "deliver") == []


def test_awaiting_playback_times_out() -> None:
    e, _ = calibrated()
    e.on_control("spark", 12.0)
    e.crumb_dispatched(crumb("c_3", "manual"), 12.0, 50.0, True)  # the Table never reports playing
    assert e.snapshot(12.5)["tts_playing"]
    ticks(e, 12.1, 12.0 + core_mod.AWAIT_PLAYBACK_S + 0.1)
    assert not e.snapshot(21.0)["tts_playing"]
    assert of_type(e.on_control("skip", 21.0), "deliver")


# --- round warning: pending tracking, holds, safety pause, gap (F21, 8.7, 15.3) ------------------------


def _speed_dating_at_warning() -> tuple[Engine, float]:
    e, _ = calibrated("speed_dating")
    live_at = 6.5
    t = live_at + 290.0
    for i in range(3):
        speak(e, 300 + i, "AB"[i % 2], LINES[i], t, t + 2.0)
        t += 2.5
    return e, live_at


def test_round_warning_is_the_pending_delivery() -> None:
    e, live_at = _speed_dating_at_warning()
    fx = ticks(e, live_at + 297.5, live_at + 300.2)
    assert len(of_type(fx, "round_warning")) == 1
    assert e.snapshot(live_at + 300.3)["pending_delivery"]
    fx = ticks(e, live_at + 300.3, live_at + 308.0)  # slow generation: no auto crumb meanwhile
    assert of_type(fx, "deliver") == []
    e.crumb_dispatched(crumb("c_7", "switch"), live_at + 308.0, 7000.0, False)
    assert e._ledger.get("c_7").auto is False and e.snapshot(live_at + 308.0)["crumbs_last_10min"] == 0


def test_round_warning_waits_for_a_gap_after_the_previous_crumb() -> None:
    e, live_at = _speed_dating_at_warning()
    e.crumb_dispatched(crumb("c_6"), live_at + 297.0, 100.0, True)
    e.on_tts_state("c_6", True, live_at + 297.0)
    e.on_tts_state("c_6", False, live_at + 300.5)
    fx = ticks(e, live_at + 300.0, live_at + 305.4)
    assert of_type(fx, "round_warning") == []
    fx = ticks(e, live_at + 305.5, live_at + 306.0)
    assert len(of_type(fx, "round_warning")) == 1


def test_round_warning_deferred_during_show_hold() -> None:
    e, live_at = _speed_dating_at_warning()
    e.on_control("hold", live_at + 298.0)  # 60 s show hold
    assert of_type(ticks(e, live_at + 298.1, live_at + 357.0), "round_warning") == []
    assert len(of_type(ticks(e, live_at + 358.1, live_at + 358.5), "round_warning")) == 1


def test_no_round_warning_or_opener_after_distress() -> None:
    e, _ = calibrated("speed_dating")
    speak(e, 9, "A", "Honestly some days I wanna die.", 12.0, 14.0)
    assert e.snapshot(14.0)["auto_paused"]
    fx = ticks(e, 14.1, 6.5 + 361.0)
    assert of_type(fx, "round_warning") == [] and of_type(fx, "deliver") == []
    assert len(of_type(fx, "round_end")) == 1
    # distress during calibration: no automatic opener either
    e2 = Engine("social", NAMES, Params())
    e2.start_calibration(0.0)
    speak(e2, 1, "A", "Hi, I'm from Austin and I want to die.", 3.0, 4.0)
    fx = speak(e2, 2, "B", "Hey, I'm from Houston.", 5.5, 6.5)
    assert e2.phase == "live" and of_type(fx, "deliver") == []
    assert of_type(e2.on_control("spark", 7.0), "deliver")  # manual Drop still works


def test_round_warning_in_simulated_speed_dating_after_safety_pause() -> None:
    turns, end = lively(0.0, 4)
    turns.insert(0, {"seat": 1, "text": "Some days I wanna die, honestly.", "start": 0.0, "end": 0.0})
    turns[0]["end"] = 1.0
    for t in turns[1:]:
        t["start"] += 1.5
        t["end"] += 1.5
    s = script(turns, [{"between": [0.0, 300.0], "action": "none"}], mode="speed_dating", duration=300.0)
    result = run_script(s)
    assert not check_expectations(s, result)
    assert _actions(result, "safety_pause") and _actions(result, "round_warning") == []


def test_simulator_handles_distress_during_calibration() -> None:
    """The sim's calibration lines can carry a distress phrase: no opener, no automatic crumb at all."""
    from sim import replay

    turns, end = lively(0.0, 6)
    s = script(turns, [{"between": [0.0, end + 30.0], "action": "none"}], duration=end + 30.0)
    original = replay.CALIBRATION_LINES
    replay.CALIBRATION_LINES = {**original, "seat1": (1, "Hi, I'm from Austin and some days I want to die.")}
    try:
        result = run_script(s)
    finally:
        replay.CALIBRATION_LINES = original
    assert not check_expectations(s, result)
    assert _actions(result, "opener") == [] and len(_actions(result, "safety_pause")) == 1


# --- safety lexicon (PRD 15.3) ------------------------------------------------------------------------


@pytest.mark.parametrize("text, hit", [
    ("i wanna die", True),
    ("I just wanna die honestly", True),
    ("I don't want to be here anymore", True),
    ("I'm dying to try that taco place", False),
    ("I wanna dive there someday", False),
])
def test_distress_variants(text: str, hit: bool) -> None:
    assert is_distress(text) is hit


# --- a lost tts_state playing:false does not freeze the engine ----------------------------------------


def test_bot_speech_without_an_end_is_closed_after_the_cap() -> None:
    e, _ = calibrated()
    e.crumb_dispatched(crumb("c_1"), 20.0, 300.0, True)
    e.on_tts_state("c_1", True, 20.0)  # the Table reloads mid-crumb: playing:false never arrives
    ticks(e, 20.1, 20.0 + BOT_SPEECH_MAX_S - 0.2)
    assert e.snapshot(39.7)["tts_playing"]
    ticks(e, 20.0 + BOT_SPEECH_MAX_S - 0.1, 20.0 + BOT_SPEECH_MAX_S + 0.1)
    snap = e.snapshot(41.0)
    assert not snap["tts_playing"] and snap["silence_s"] > 0
    e.on_tts_state("c_1", False, 45.0)  # a late end (runner cap, reconnect) does not restart the timer
    assert e.snapshot(45.0)["silence_s"] == pytest.approx(45.0 - (20.0 + BOT_SPEECH_MAX_S), abs=0.11)
    assert of_type(e.on_control("spark", 45.0), "deliver")


def test_table_disconnect_closes_open_bot_speech() -> None:
    e, _ = calibrated()
    e.crumb_dispatched(crumb("c_1"), 20.0, 300.0, True)
    e.on_tts_state("c_1", True, 20.0)
    e.on_table_disconnect(21.0)
    assert not e.snapshot(21.0)["tts_playing"]
    assert of_type(e.on_control("spark", 21.5), "deliver")


# --- the 15 s calibration fallback never drops the opener into someone's turn (PRD 6.5, 8.7) -----------


def _fallback_mid_turn(text: str = "So anyway, the thing about that trip was the food.") -> Engine:
    """Labels A and B heard before the prompts, A answers seat 1, B never answers seat 2, and A is talking
    again (open turn, local VAD on) when the 15 s fallback fires."""
    e = Engine("social", NAMES, Params())
    speak(e, 1, "A", "Testing, testing.", -4.0, -3.0)
    speak(e, 2, "B", "Is this thing on?", -2.5, -1.5)
    e.start_calibration(0.0)
    e.on_tts_state("sys_cal1", True, 0.0)
    e.on_tts_state("sys_cal1", False, 2.0)
    speak(e, 3, "A", "Hi, I'm from Austin.", 2.6, 4.0)
    assert e.label_map == {"A": 1}
    e.on_tts_state("sys_cal2", True, 4.2)
    e.on_tts_state("sys_cal2", False, 5.2)
    assert of_type(ticks(e, 5.3, 8.9), "deliver") == []
    e.on_muse_event({"type": "speechStart", "turnId": 4, "audioProcessedMs": 0}, 9.0)
    e.on_vad(True, 0.2, 9.0)
    e.on_muse_event({"type": "transcript", "transcript": text, "final": False}, 12.0)
    return e


def _finish_turn(e: Engine, text: str, end: float) -> list[dict]:
    fx = e.on_muse_event({"type": "speaker", "label": "A"}, end - 0.05)
    fx += e.on_muse_event({"type": "speechEnd", "turnId": 4}, end)
    fx += e.on_vad(False, 0.0, end)
    return fx + e.on_muse_event({"type": "speechComplete", "turnId": 4, "transcript": text}, end)


def test_calibration_fallback_mid_turn_queues_the_opener_until_silence() -> None:
    e = _fallback_mid_turn()
    fx = ticks(e, 9.1, 16.9)
    assert of_type(fx, "live") and e.phase == "live"
    assert of_type(fx, "deliver") == []  # A is still talking: the opener waits
    fx = _finish_turn(e, "So anyway, the thing about that trip was the food.", 17.0) + ticks(e, 17.1, 17.5)
    assert [f["kind"] for f in of_type(fx, "deliver")] == ["opener"]


def test_spark_while_the_opener_is_queued_keeps_the_opener() -> None:
    e = _fallback_mid_turn()
    ticks(e, 9.1, 15.5)
    assert of_type(e.on_control("spark", 15.6), "deliver") == []
    fx = _finish_turn(e, "So anyway, the thing about that trip was the food.", 17.0) + ticks(e, 17.1, 17.5)
    assert [f["kind"] for f in of_type(fx, "deliver")] == ["opener"]


def test_distress_in_the_turn_the_opener_waited_for_drops_it() -> None:
    text = "Honestly some days I wanna die."
    e = _fallback_mid_turn(text)
    ticks(e, 9.1, 16.9)
    fx = _finish_turn(e, text, 17.0) + ticks(e, 17.1, 40.0)
    assert e.snapshot(40.0)["auto_paused"]
    assert of_type(fx, "deliver") == []
    assert [d["reason"] for d in decisions(fx, "calibration")] == ["Opening crumb skipped: automatic crumbs are paused"]


def test_distress_while_the_opener_is_generating_cancels_it() -> None:
    """Speech starts during opener generation (re-queued), and that turn turns out to be distress: the
    re-queue is withdrawn and the generated opener is vetoed (or faded if it is sent anyway)."""
    e = Engine("social", NAMES, Params())
    e.start_calibration(0.0)
    e.on_tts_state("sys_cal1", True, 0.0)
    e.on_tts_state("sys_cal1", False, 2.0)
    speak(e, 1, "A", "Hi, I'm from Austin.", 3.0, 4.0)
    e.on_tts_state("sys_cal2", True, 4.0)
    e.on_tts_state("sys_cal2", False, 5.0)
    fx = speak(e, 2, "B", "Hey, I'm from Houston.", 5.5, 6.5)
    assert [f["kind"] for f in of_type(fx, "deliver")] == ["opener"]
    speak(e, 3, "B", "Honestly some days I wanna die.", 7.0, 9.0)
    assert e.pending_cancelled
    e.crumb_dispatched(crumb("c_0", "opener"), 9.5, 2500.0, False)  # a runner that does not veto yet
    fx = e.on_tts_state("c_0", True, 9.6)
    assert decisions(fx, "barge_in")[0]["inputs"]["trigger"] == "cancelled"
    e.on_tts_state("c_0", False, 9.85)
    assert of_type(ticks(e, 9.9, 30.0), "deliver") == []
