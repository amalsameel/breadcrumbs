"""Round 2: self-hearing by text (Muse transcribing the bot's own voice) and background noise that keeps
the local VAD on (VAD_UNCONFIRMED_S). Pure engine tests on a fake clock; no network, no AI."""
from __future__ import annotations

import pytest

from config import Params
from engine.core import NOISE_LATCH_CLEAR_S, Engine
from engine.state import echo_overlap, echo_tail_words
from models import Crumb
from tests.test_policy import NAMES, calibrated, decisions, of_type, speak, ticks

LINE = "Maya, what made you sign up for your first half marathon?"


def line_crumb(cid: str, text: str = LINE, kind: str = "steer") -> Crumb:
    return Crumb(id=cid, kind=kind, format="question", text=text, addressed_to="both", source="conversation",
                 topic="t", depth=1, why="test")


def playing(e: Engine, cid: str, at: float, text: str = LINE) -> None:
    e.crumb_dispatched(line_crumb(cid, text), at, 300.0, True)
    e.on_tts_state(cid, True, at)


def vad_frames(e: Engine, t0: float, t1: float, active: bool = True) -> list[dict]:
    """Local VAD at 10 Hz plus engine ticks, like the Table and the runner."""
    fx: list[dict] = []
    for k in range(round(t0 * 10), round(t1 * 10) + 1):
        fx += e.on_vad(active, 0.05 if active else 0.002, k / 10)
        fx += e.tick(k / 10)
    return fx


# --- echo_overlap --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, low", [
    (LINE, 1.0),
    ("what made you sign up for your first", 1.0),
    ("Maya what made you sign up for you're first half marathon", 0.8),  # ASR slip on one word
    ("made you sign", 1.0),
])
def test_echo_overlap_is_high_for_the_line_itself(text: str, low: float) -> None:
    assert echo_overlap(text, LINE) >= low - 1e-9


@pytest.mark.parametrize("text, line", [
    ("Hi, I'm from Austin.", "Before we start: Seat 1, say hi and tell Dev where you're from."),
    ("Honestly my roommate dared me to do it", LINE),
    ("Hey, I'm from Houston.", "Seat 2, your turn."),
])
def test_echo_overlap_is_low_for_replies_that_share_words(text: str, line: str) -> None:
    assert echo_overlap(text, line) < 0.5


def test_echo_overlap_needs_two_words() -> None:
    assert echo_overlap("marathon", LINE) == 0.0
    assert echo_overlap("", LINE) == 0.0


# --- echo-aware barge-in (PRD 8.11, F12, F19) ----------------------------------------------------------


def test_muse_echo_of_the_crumb_never_barges_in_even_with_vad() -> None:
    e, _ = calibrated()
    speak(e, 20, "A", "We should go back there sometime.", 12.0, 14.0)
    playing(e, "c_1", 20.0)
    e.on_vad(True, 0.2, 20.1)  # chime grace
    fx = e.on_muse_event({"type": "speechStart", "turnId": 30}, 20.5)
    fx += e.on_muse_event({"type": "transcript", "transcript": "Maya what made", "final": False}, 21.0)
    assert of_type(fx, "barge_in") == []
    assert of_type(fx, "partial") == []  # our own words are not shown as a caption
    assert e.snapshot(21.0)["crumbs_this_silence"] == 1
    e.on_vad(False, 0.0, 21.2)
    e.on_tts_state("c_1", False, 23.0)
    fx = e.on_muse_event({"type": "speechEnd", "turnId": 30}, 23.6)
    fx += e.on_muse_event({"type": "speechComplete", "turnId": 30, "transcript": LINE}, 23.9)
    dropped = decisions(fx, "dropped_bot_turn")
    assert len(dropped) == 1 and dropped[0]["inputs"]["trigger"] == "echo_text"
    assert e.turns[-1].dropped_as_bot
    assert e.snapshot(23.9)["crumbs_this_silence"] == 1  # echo never counts as a reply
    assert e.metrics()["interrupted"] == 0


def test_late_echo_after_the_line_ended_is_still_dropped() -> None:
    """Muse reports the echo late: most of the turn is after the audio ended (time overlap < 50%)."""
    e, _ = calibrated()
    playing(e, "c_1", 20.0)
    e.on_tts_state("c_1", False, 23.0)
    e.on_muse_event({"type": "speechStart", "turnId": 31}, 22.4)
    e.on_muse_event({"type": "transcript", "transcript": "sign up for your first half marathon", "final": False}, 24.0)
    fx = e.on_muse_event({"type": "speechEnd", "turnId": 31}, 25.5)
    assert decisions(fx, "dropped_bot_turn") and e.turns[-1].dropped_as_bot


def test_real_words_during_the_crumb_still_barge_in() -> None:
    e, _ = calibrated()
    playing(e, "c_1", 20.0)
    e.on_muse_event({"type": "speechStart", "turnId": 32}, 20.8)
    fx = e.on_vad(True, 0.2, 20.8)  # the Table forwards VAD only when it is louder than the echo
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_1"}]
    fx = e.on_muse_event({"type": "transcript", "transcript": "Oh wait, I have to tell you", "final": False}, 21.3)
    fx += e.on_muse_event({"type": "speechEnd", "turnId": 32}, 22.5)
    assert decisions(fx, "dropped_bot_turn") == []
    assert not e.turns[-1].dropped_as_bot


def test_muse_words_that_are_not_the_line_barge_in_without_waiting_for_speech_end() -> None:
    e, _ = calibrated()
    playing(e, "c_1", 20.0)
    e.on_vad(True, 0.2, 20.1)  # onset inside the chime grace: no barge-in from VAD alone
    fx = e.on_muse_event({"type": "speechStart", "turnId": 33}, 20.6)
    assert of_type(fx, "barge_in") == []
    fx = e.on_muse_event({"type": "transcript", "transcript": "Hang on, actually", "final": False}, 20.9)
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_1"}]
    assert decisions(fx, "barge_in")[0]["inputs"]["trigger"] == "speechStart"


def test_reply_right_after_the_line_counts_as_human() -> None:
    e, _ = calibrated()
    playing(e, "c_1", 20.0)
    e.on_tts_state("c_1", False, 23.0)
    e.on_muse_event({"type": "speechStart", "turnId": 34}, 23.6)  # inside the 1 s echo window
    fx = e.on_muse_event({"type": "transcript", "transcript": "Honestly my roommate dared me", "final": False}, 24.1)
    assert of_type(fx, "partial")
    assert e.snapshot(24.1)["crumbs_this_silence"] == 0  # a reply: the unanswered count resets
    speak_end = e.on_muse_event({"type": "speechEnd", "turnId": 34}, 26.0)
    assert decisions(speak_end, "dropped_bot_turn") == []


def test_reply_that_starts_with_the_question_words_is_still_human() -> None:
    e, _ = calibrated()
    playing(e, "c_1", 20.0)
    e.on_tts_state("c_1", False, 23.0)
    e.on_muse_event({"type": "speechStart", "turnId": 35}, 23.5)
    e.on_muse_event({"type": "transcript", "transcript": "what made me", "final": False}, 23.9)
    text = "What made me do it? Honestly my roommate dared me and I could not say no to him."
    e.on_muse_event({"type": "transcript", "transcript": text, "final": False}, 26.0)
    fx = e.on_muse_event({"type": "speechEnd", "turnId": 35}, 26.1)
    assert decisions(fx, "dropped_bot_turn") == []  # re-checked on the whole turn
    assert e.snapshot(26.1)["crumbs_this_silence"] == 0


def test_calibration_prompt_echo_is_not_mapped_as_a_seat() -> None:
    e = Engine("social", NAMES, Params())
    e.start_calibration(0.0)
    e.on_tts_state("sys_cal1", True, 0.0)
    e.on_muse_event({"type": "speechStart", "turnId": 1}, 0.4)
    e.on_muse_event({"type": "transcript", "transcript": "before we start seat one say hi", "final": False}, 1.0)
    e.on_muse_event({"type": "speaker", "label": "A"}, 1.4)
    e.on_tts_state("sys_cal1", False, 3.0)
    fx = e.on_muse_event({"type": "speechEnd", "turnId": 1}, 3.9)  # ran past the line: time overlap < 77%
    assert decisions(fx, "dropped_bot_turn")
    assert e.label_map == {}
    speak(e, 2, "B", "Hi, I'm from Austin.", 4.5, 6.0)  # the real seat 1
    assert e.label_map == {"B": 1}


# --- background noise (VAD_UNCONFIRMED_S) ---------------------------------------------------------------


def test_unconfirmed_vad_stops_holding_the_pause_timer() -> None:
    e, _ = calibrated()
    speak(e, 20, "A", "That trip sounded incredible honestly.", 40.0, 42.0)
    window = e.params.VAD_UNCONFIRMED_S
    vad_frames(e, 41.0, 42.0 + window - 0.2)
    snap = e.snapshot(42.0 + window - 0.2)
    assert snap["silence_s"] == 0.0 and snap["vad_active"] and not snap["noise_suspected"]
    vad_frames(e, 42.0 + window - 0.1, 46.0)
    snap = e.snapshot(46.0)
    assert snap["vad_active"] and snap["noise_suspected"]
    assert snap["silence_s"] == pytest.approx(46.0 - (42.0 + window - 0.1), abs=0.11)
    assert not snap["turn_active"]


def test_steady_noise_never_freezes_crumbs() -> None:
    e, _ = calibrated()
    speak(e, 20, "A", "That trip sounded incredible honestly.", 40.0, 42.0)
    fx = vad_frames(e, 30.0, 60.0)  # the fan was on before the turn and stays on
    delivered = of_type(fx, "deliver")
    assert delivered, "noise must not hold the pause timer forever"
    first = next(d["decision"]["at_s"] for d in fx if d["type"] == "decision" and d["decision"]["action"] in ("steer", "switch"))
    assert first >= 42.0 + e.params.VAD_UNCONFIRMED_S - 9.5  # at_s counts from live start (9.5)


def test_new_onsets_in_a_noisy_room_wait_for_muse() -> None:
    e, _ = calibrated()
    speak(e, 20, "A", "That trip sounded incredible honestly.", 40.0, 42.0)
    vad_frames(e, 42.0, 46.0)  # noise outlives the window: the room is marked noisy
    assert e.snapshot(46.0)["noise_suspected"]
    vad_frames(e, 46.1, 46.5, active=False)
    vad_frames(e, 46.6, 47.0)  # a fresh flicker of the same noise
    snap = e.snapshot(47.0)
    assert snap["noise_suspected"] and snap["silence_s"] > 2.0  # the flicker did not restart the timer
    # Muse hears a person: VAD counts again.
    e.on_muse_event({"type": "speechStart", "turnId": 21}, 47.1)
    vad_frames(e, 47.1, 47.5)
    snap = e.snapshot(47.5)
    assert not snap["noise_suspected"] and snap["silence_s"] == 0.0


def test_noise_latch_clears_after_a_quiet_spell() -> None:
    e, _ = calibrated()
    speak(e, 20, "A", "That trip sounded incredible honestly.", 40.0, 42.0)
    vad_frames(e, 42.0, 46.0)
    vad_frames(e, 46.1, 46.1 + NOISE_LATCH_CLEAR_S, active=False)
    t = round(46.2 + NOISE_LATCH_CLEAR_S, 1)
    vad_frames(e, t, t + 0.5)
    snap = e.snapshot(t + 0.5)
    assert not snap["noise_suspected"] and snap["silence_s"] == 0.0  # a fresh onset counts again


def test_noise_does_not_cancel_a_crumb_or_barge_in() -> None:
    e, _ = calibrated()
    speak(e, 20, "A", "That trip sounded incredible honestly.", 40.0, 42.0)
    fx = vad_frames(e, 30.0, 60.0)
    assert of_type(fx, "deliver") and not decisions(fx, "barge_in")
    assert e.pending_cancelled is False


def test_vad_with_muse_confirmation_keeps_counting() -> None:
    """A long monologue: Muse keeps confirming, so VAD keeps holding the timer past the window."""
    e, _ = calibrated()
    e.on_muse_event({"type": "speechStart", "turnId": 22}, 40.0)
    vad_frames(e, 40.0, 46.0)
    snap = e.snapshot(46.0)
    assert snap["turn_active"] and not snap["noise_suspected"] and snap["silence_s"] == 0.0


# --- review fixes: an echo start merged with a reply, and bursty noise ---------------------------------

REPLY = "honestly my roommate dared me and I could not say no to him so I signed up the next morning and trained for months"


def test_echo_tail_words_counts_the_reply_after_the_line() -> None:
    assert echo_tail_words(LINE, [LINE]) == 0
    assert echo_tail_words("Maya what made you sign up for you're first half marathon", [LINE]) == 0  # one slip
    assert echo_tail_words(LINE + " honestly my roommate", [LINE]) == 3
    assert echo_tail_words("what made you sign up honestly my", [LINE]) == 2
    assert echo_tail_words("Honestly my roommate dared me", []) == 5


@pytest.mark.parametrize("use_vad", [True, False])
def test_reply_merged_into_an_echo_turn_is_a_person_and_no_crumb_fires_over_it(use_vad: bool) -> None:
    """Muse keeps one turn open while the crumb's echo runs straight into the answer: the partials are the
    line followed by the reply. Once the reply words show up the turn is a person's: captions, a held pause
    timer and a reset unanswered count, and no crumb is decided while they talk."""
    e, _ = calibrated()
    speak(e, 20, "A", "We should go back there sometime.", 12.0, 14.0)
    speak(e, 21, "B", "Yeah that would be amazing honestly.", 14.5, 16.0)
    playing(e, "c_1", 20.0)
    fx = e.on_muse_event({"type": "speechStart", "turnId": 30}, 20.6)
    fx += e.on_muse_event({"type": "transcript", "transcript": "Maya what made", "final": False}, 21.0)
    assert of_type(fx, "partial") == []  # still our own line
    fx += e.on_tts_state("c_1", False, 23.0)
    words = REPLY.split()
    max_silence = 0.0
    captions = 0
    for k in range(230, 341):
        t = k / 10
        if use_vad and t >= 23.8:
            fx += e.on_vad(True, 0.05, t)  # the Table passes the stretch on 800 ms after the line
        if k % 5 == 0 and t >= 23.5:
            n = min(len(words), int((t - 23.3) * 2.6))
            step = e.on_muse_event({"type": "transcript", "transcript": LINE + " " + " ".join(words[:n]), "final": False}, t)
            captions += len(of_type(step, "partial"))
            fx += step
        fx += e.tick(t)
        max_silence = max(max_silence, e.snapshot(t)["silence_s"])
    crumbs = [d for d in decisions(fx) if d["action"] in ("steer", "switch", "spark", "wildcard")]
    assert crumbs == [] and of_type(fx, "deliver") == []
    assert max_silence < 2.0
    assert captions >= 15
    snap = e.snapshot(34.0)
    assert snap["turn_active"] and not snap["noise_suspected"] and snap["crumbs_this_silence"] == 0
    assert of_type(fx, "barge_in") == []  # the line had ended: nothing to fade
    fx = e.on_muse_event({"type": "speechEnd", "turnId": 30}, 34.1)
    assert decisions(fx, "dropped_bot_turn") == [] and not e.turns[-1].dropped_as_bot


def test_short_reply_merged_into_an_echo_turn_is_not_dropped_by_time_overlap() -> None:
    """A brief answer: most of the Muse turn is the echo, so time overlap alone would drop it as bot."""
    e, _ = calibrated()
    playing(e, "c_1", 20.0)
    e.on_muse_event({"type": "speechStart", "turnId": 31}, 20.6)
    e.on_muse_event({"type": "transcript", "transcript": "Maya what made you", "final": False}, 21.2)
    e.on_tts_state("c_1", False, 23.0)
    fx = e.on_muse_event({"type": "transcript", "transcript": LINE + " my roommate dared me", "final": False}, 24.2)
    assert of_type(fx, "partial") and e.snapshot(24.2)["crumbs_this_silence"] == 0
    fx = e.on_muse_event({"type": "speechEnd", "turnId": 31}, 24.6)
    assert decisions(fx, "dropped_bot_turn") == [] and not e.turns[-1].dropped_as_bot


def test_echo_with_an_asr_slip_at_its_end_stays_echo() -> None:
    e, _ = calibrated()
    playing(e, "c_1", 20.0)
    unanswered = e.snapshot(20.0)["crumbs_this_silence"]
    e.on_muse_event({"type": "speechStart", "turnId": 32}, 20.6)
    e.on_muse_event({"type": "transcript", "transcript": "Maya what made you", "final": False}, 21.2)
    fx = e.on_muse_event({"type": "transcript", "transcript": "Maya what made you sign up for your first half martin", "final": False}, 22.6)
    assert of_type(fx, "partial") == [] and e.snapshot(22.6)["crumbs_this_silence"] == unanswered
    e.on_tts_state("c_1", False, 23.0)
    fx = e.on_muse_event({"type": "speechEnd", "turnId": 32}, 23.4)
    assert decisions(fx, "dropped_bot_turn") and e.turns[-1].dropped_as_bot


def test_reply_words_during_the_line_with_vad_barge_in() -> None:
    """The person cuts in while the line still plays, inside the echo turn, and the Table forwards VAD."""
    e, _ = calibrated()
    playing(e, "c_1", 20.0)
    assert of_type(e.on_vad(True, 0.2, 20.1), "barge_in") == []  # onset inside the chime grace
    e.on_muse_event({"type": "speechStart", "turnId": 33}, 20.6)
    fx = e.on_muse_event({"type": "transcript", "transcript": "Maya what made", "final": False}, 21.0)
    assert of_type(fx, "barge_in") == []  # the line's own words
    fx = e.on_muse_event({"type": "transcript", "transcript": "Maya what made hang on wait a second", "final": False}, 21.6)
    assert of_type(fx, "barge_in") == [{"type": "barge_in", "crumb_id": "c_1"}]
    assert of_type(fx, "partial")


def _first_crumb_under_noise(on_s: float, off_s: float) -> tuple[float | None, list[dict], Engine]:
    e, _ = calibrated()
    speak(e, 20, "A", "That trip sounded incredible honestly.", 40.0, 42.0)
    fx: list[dict] = []
    period = on_s + off_s
    for k in range(420, 701):
        t = k / 10
        active = off_s == 0 or (t - 42.0 + 1e-9) % period < on_s
        fx += e.on_vad(active, 0.05 if active else 0.002, t)
        fx += e.tick(t)
    first = next((d["at_s"] for d in decisions(fx) if d["action"] in ("steer", "switch")), None)
    return first, fx, e


@pytest.mark.parametrize("on_s, off_s", [(2.0, 0.6), (1.5, 0.6), (2.4, 0.6), (1.0, 1.0)])
def test_bursty_noise_never_freezes_crumbs(on_s: float, off_s: float) -> None:
    """Noise in bursts shorter than VAD_UNCONFIRMED_S (nearby talk, dishes, a TV) shares one confirmation
    window: each burst does not get a fresh one, so the room is marked noisy and a crumb fires about when
    it would under steady noise."""
    steady, _, _ = _first_crumb_under_noise(28.0, 0.0)
    first, fx, e = _first_crumb_under_noise(on_s, off_s)
    assert steady is not None
    assert first is not None and of_type(fx, "deliver"), "bursts of noise must not hold the pause timer forever"
    assert first <= steady + 0.5
    assert not decisions(fx, "barge_in")


def test_speech_after_noise_bursts_counts_once_muse_hears_it() -> None:
    e, _ = calibrated()
    speak(e, 20, "A", "That trip sounded incredible honestly.", 40.0, 42.0)
    for t0 in (42.0, 44.6, 47.2):  # bursts of 2 s
        vad_frames(e, t0, t0 + 1.9)
        vad_frames(e, t0 + 2.0, t0 + 2.5, active=False)
    assert e.snapshot(49.7)["silence_s"] > 2.0
    e.on_muse_event({"type": "speechStart", "turnId": 21}, 49.9)
    vad_frames(e, 49.9, 50.5)
    snap = e.snapshot(50.5)
    assert snap["silence_s"] == 0.0 and not snap["noise_suspected"]
    e.on_muse_event({"type": "speechEnd", "turnId": 21}, 50.6)
    vad_frames(e, 50.6, 50.9, active=False)
    vad_frames(e, 51.0, 51.8)  # their next phrase, Muse not caught up yet: confirmed recently, so it counts
    assert e.snapshot(51.8)["silence_s"] == 0.0
