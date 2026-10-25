"""PRD 16.1 simulator scripts: every script's expectations hold, and the scripts actually discriminate."""
import pytest

from config import Params
from sim.replay import SCRIPTS_DIR, check_expectations, load_script, main, run_script

REQUIRED = {
    "normal_pauses", "warming_only", "long_pause_lively", "stale_decay", "exceptional_pause", "unanswered_steer",
    "show_hold_then_resume", "show_hold_timeout", "think_hold", "wait_really_false_positive", "barge_in",
    "self_hearing", "cooldown",
}
SCRIPT_PATHS = sorted(SCRIPTS_DIR.glob("*.json"))


FOREGROUND = {
    "bystander_fragments", "bystander_on_seat_label", "seated_backchannel_resets", "noise_defers_not_resets",
    "muse_late_seat_start", "split_turn_settle", "long_bystander_turn_release", "three_second_steer",
    "natural_gaps_under_3s", "unanswered_fast", "endpoint_lag_backdate", "endpoint_lag_cap", "think_hold_fast",
    "label_drift_near", "near_level_noise", "near_level_noise_throughout", "tv_speech_mid_level",
    "label_drift_short_reply",
}
# "Just talk" (setup quick): script time 0 is go-live, and the silence before the first turn is the point.
QUICK = {"quick_icebreaker", "quick_talk_right_away", "quick_bystander_first", "quick_noise_then_person",
         "quick_burst_then_person"}


def test_legacy_scripts_are_pinned_and_new_ones_run_on_the_defaults() -> None:
    for p in SCRIPT_PATHS:
        s = load_script(p)
        if p.stem in FOREGROUND | QUICK:
            assert "params" not in s, f"{p.stem} must run on the 3 s defaults"
        else:
            assert s["params"]["T_LONG_S"] == 5.0, f"{p.stem} keeps its pre-3 s timings"
    assert FOREGROUND | QUICK <= {p.stem for p in SCRIPT_PATHS}
    for p in SCRIPT_PATHS:
        assert (load_script(p).get("setup", "profiles") == "quick") == (p.stem in QUICK)


def test_all_prd_scripts_present() -> None:
    assert REQUIRED <= {p.stem for p in SCRIPT_PATHS}
    for p in SCRIPT_PATHS:
        s = load_script(p)
        assert s["name"] == p.stem and s["expect"] and s["turns"]
        if p.stem not in QUICK:
            assert s["turns"][0]["start"] == 0.0


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=lambda p: p.stem)
def test_script_expectations(path) -> None:
    script = load_script(path)
    result = run_script(script)
    failures = check_expectations(script, result)
    assert not failures, "\n".join(failures)
    assert result.metrics["false_interruptions"] == 0


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=lambda p: p.stem)
def test_scripts_are_deterministic(path) -> None:
    script = load_script(path)
    assert run_script(script).actions == run_script(script).actions


@pytest.mark.parametrize(
    "name, changes",
    [
        ("warming_only", {"T_LONG_S": 4.0}),  # a 4.5 s pause would then steer
        ("long_pause_lively", {"T_LONG_S": 6.0}),  # no steer within the 5.5 s pause
        ("stale_decay", {"STALE_SWITCH": 0.99}),  # never stale: steer instead of switch
        ("show_hold_then_resume", {"HOLD_TAIL_WORDS": 0}),  # lexicon disabled: crumbs fire during the hold
        ("think_hold", {"HOLD_TAIL_WORDS": 0}),
        ("cooldown", {"MIN_GAP_BETWEEN_CRUMBS_S": 5}),  # the second pause would get a crumb
        ("exceptional_pause", {"EXC_SWITCH_MIN_GAP_S": 30}),  # exceptional cooldown blocks too
        ("unanswered_steer", {"MAX_UNANSWERED_CRUMBS": 3}),  # a third crumb would fire
        ("show_hold_timeout", {"HOLD_MAX_S": {"show": 30}}),
        # foreground and the 3 s thresholds
        ("bystander_fragments", {"BYSTANDER_GATING": False}),  # every fragment would restart the pause
        ("bystander_on_seat_label", {"BYSTANDER_GATING": False}),
        ("long_bystander_turn_release", {"OPEN_TURN_MIN_S": 30}),  # the open turn would block until it closes
        ("split_turn_settle", {"BACKGROUND_SETTLE_S": 0}),  # a steer between the two halves
        ("endpoint_lag_backdate", {"ENDPOINT_BACKDATE_MAX_S": 0}),  # the steer 0.8 s late
        ("endpoint_lag_cap", {"ENDPOINT_BACKDATE_MAX_S": 3}),
        ("seated_backchannel_resets", {"NEAR_ACTIVE_FRAC": 0.95}),  # the seat "yeah" would be a filler
        ("label_drift_near", {"DRIFT_MIN_WORDS": 99}),  # seat 1's turns would be background
        ("unanswered_fast", {"UNANSWERED_SWITCH_S": 4.5}),
        ("three_second_steer", {"T_LONG_S": 3.5}),
        ("noise_defers_not_resets", {"VAD_DEFER_S": 20}),
        ("think_hold_fast", {"HOLD_TAIL_WORDS": 0}),
        ("natural_gaps_under_3s", {"T_LONG_S": 2.5}),
        ("background_noise", {"BYSTANDER_GATING": False, "VAD_DEFER_S": 30}),
        ("near_level_noise", {"VAD_NEAR_MAX_S": 30}),  # the floor-level noise would hold the pause
        ("near_level_noise_throughout", {"VAD_NEAR_MAX_S": 30}),
        ("tv_speech_mid_level", {"OPEN_TURN_MIN_S": 30}),  # each TV turn would block while open
        ("label_drift_short_reply", {"DRIFT_MIN_WORDS": 3}),  # the 2-word reply would be background
    ],
)
def test_scripts_detect_broken_parameters(name: str, changes: dict) -> None:
    script = load_script(SCRIPTS_DIR / f"{name}.json")
    params = Params()
    params.update(script.get("params") or {})  # pinned legacy scripts keep their documented timings
    params.update(changes)
    assert check_expectations(script, run_script(script, params)), f"{name} did not notice {changes}"


@pytest.mark.parametrize("name", ["quick_noise_then_person", "quick_burst_then_person"])
def test_noise_before_the_first_voice_does_not_swallow_person_1(name: str) -> None:
    # Before any seat turn the seated level is unknown: the latched noise floor still tells a voice from it.
    result = run_script(load_script(SCRIPTS_DIR / f"{name}.json"))
    assert [(t["seat"], t["dropped_as_bot"]) for t in result.turns] == [(1, False), (2, False)]


def test_expectation_checker_reports_failures() -> None:
    script = load_script(SCRIPTS_DIR / "long_pause_lively.json")
    result = run_script(script)
    steer_at = next(a["t"] for a in result.actions if a["action"] == "steer")
    bad = dict(script, expect=[
        {"between": [0.0, steer_at + 1], "action": "none"},
        {"between": [0.0, steer_at + 1], "action": "steer", "count": 2},
        {"between": [0.0, steer_at + 1], "action": "steer", "at": steer_at + 0.5},
    ])
    assert len(check_expectations(bad, result)) == 3


def test_cli_prints_timeline_and_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(SCRIPTS_DIR / "barge_in.json")]) == 0
    out = capsys.readouterr().out
    assert "== barge_in ==" in out and "steer" in out and "barge_in" in out and "PASS" in out
