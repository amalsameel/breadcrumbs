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


def test_all_prd_scripts_present() -> None:
    assert {p.stem for p in SCRIPT_PATHS} == REQUIRED
    for p in SCRIPT_PATHS:
        s = load_script(p)
        assert s["name"] == p.stem and s["expect"] and s["turns"]
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
    ],
)
def test_scripts_detect_broken_parameters(name: str, changes: dict) -> None:
    script = load_script(SCRIPTS_DIR / f"{name}.json")
    params = Params()
    params.update(changes)
    assert check_expectations(script, run_script(script, params)), f"{name} did not notice {changes}"


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
