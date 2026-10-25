"""Staleness components on fixed inputs (PRD 8.5)."""
import pytest

from config import Params
from engine import staleness as st
from engine.state import count_words, is_backchannel, is_question
from models import Turn


def turn(i: int, words: int, *, gap: float | None = 1.0, question: bool = False, backchannel: bool = False,
         start: float | None = None) -> Turn:
    s = float(i * 5) if start is None else start
    return Turn(turn_id=i, seat=1 + i % 2, words=words, is_question=question, is_backchannel=backchannel,
                started_at=s, ended_at=s + 2, gap_before=gap)


def lively_baseline(n: int = 10, words: int = 12, gap: float = 0.8) -> list[Turn]:
    return [turn(i, words, gap=gap, question=i % 3 == 0) for i in range(n)]


P = Params()


def test_weights_and_threshold_defaults() -> None:
    assert P.STALENESS_WEIGHTS == {"L": 0.35, "R": 0.25, "G": 0.15, "Q": 0.10, "E": 0.15}
    assert sum(P.STALENESS_WEIGHTS.values()) == pytest.approx(1.0)
    assert P.STALE_SWITCH == 0.60


def test_baseline_by_turns() -> None:
    turns = lively_baseline(12)
    b = st.select_baseline(turns, live_started_at=0.0, now=60.0, params=P)
    assert b.ready and len(b.turns) == 10 and b.turns[-1].turn_id == 9


def test_baseline_by_time_when_fewer_turns() -> None:
    turns = [turn(i, 10, start=i * 40.0) for i in range(6)]  # starts 0, 40, ..., 200
    early = st.select_baseline(turns[:4], live_started_at=0.0, now=150.0, params=P)
    assert not early.ready
    late = st.select_baseline(turns, live_started_at=0.0, now=181.0, params=P)
    assert late.ready and [t.turn_id for t in late.turns] == [0, 1, 2, 3, 4]  # started before 180 s


def test_no_baseline_without_turns() -> None:
    b = st.select_baseline([], live_started_at=0.0, now=500.0, params=P)
    assert not b.ready and b.gap_ref == P.DEFAULT_GAP_S


def test_l_and_g_zero_before_baseline() -> None:
    turns = [turn(i, 1, gap=5.0) for i in range(4)]
    s = st.compute(turns, live_started_at=0.0, now=30.0, energy=None, params=P)
    assert s.parts["L"] == 0.0 and s.parts["G"] == 0.0 and not s.baseline_ready


def test_reply_length_decay() -> None:
    base = st.select_baseline(lively_baseline(), 0.0, 60.0, P)
    assert st.reply_length_decay([turn(i, 3) for i in range(6)], base) == pytest.approx(0.75)
    assert st.reply_length_decay([turn(i, 12) for i in range(6)], base) == 0.0
    assert st.reply_length_decay([turn(i, 30) for i in range(6)], base) == 0.0  # clamped at 0
    assert st.reply_length_decay([turn(i, 0) for i in range(6)], base) == 1.0


def test_short_reply_rate_divides_by_n() -> None:
    recent = [turn(0, 2), turn(1, 3), turn(2, 4), turn(3, 9, backchannel=True), turn(4, 10), turn(5, 12)]
    assert st.short_reply_rate(recent, P) == pytest.approx(3 / 6)
    assert st.short_reply_rate(recent[:2], P) == pytest.approx(2 / 6)


def test_gap_growth_and_clamp() -> None:
    base = st.select_baseline(lively_baseline(gap=0.8), 0.0, 60.0, P)
    assert base.gap_ref == pytest.approx(0.8)
    assert st.gap_growth([turn(i, 5, gap=1.2) for i in range(6)], base) == pytest.approx(0.25)  # (1.5 - 1) / 2
    assert st.gap_growth([turn(i, 5, gap=10.0) for i in range(6)], base) == 1.0  # clamp 2, / 2
    assert st.gap_growth([turn(i, 5, gap=0.2) for i in range(6)], base) == 0.0
    assert st.gap_growth([turn(i, 5, gap=None) for i in range(6)], base) == 0.0


def test_gap_reference_clamped_to_gap_clamp() -> None:
    fast = st.select_baseline(lively_baseline(gap=0.1), 0.0, 60.0, P)
    slow = st.select_baseline(lively_baseline(gap=4.0), 0.0, 60.0, P)
    assert fast.gap_ref == 0.4 and slow.gap_ref == 2.5


def test_question_deficit() -> None:
    assert st.question_deficit([turn(i, 5) for i in range(6)]) == 1.0
    assert st.question_deficit([turn(0, 5, question=True)] + [turn(i, 5) for i in range(1, 6)]) == 0.5
    assert st.question_deficit([turn(i, 5, question=True) for i in range(3)]) == 0.0
    assert st.question_deficit([]) == 0.0


@pytest.mark.parametrize("energy, value", [("high", 0.0), ("medium", 0.5), ("low", 1.0), (None, 0.0)])
def test_energy_component(energy: str | None, value: float) -> None:
    assert st.energy_component(energy) == value


def test_full_score_dead_conversation() -> None:
    turns = lively_baseline(gap=0.8) + [turn(10 + i, 1, gap=2.5, backchannel=True) for i in range(6)]
    s = st.compute(turns, live_started_at=0.0, now=120.0, energy="low", params=P)
    # L = 1 - 1/12, R = 1, G = clamp(2.5/0.8 - 1, 0, 2)/2 = 1, Q = 1, E = 1
    expected = 0.35 * (1 - 1 / 12) + 0.25 + 0.15 + 0.10 + 0.15
    assert s.score == pytest.approx(expected) and s.stale
    assert s.parts == pytest.approx({"L": 1 - 1 / 12, "R": 1.0, "G": 1.0, "Q": 1.0, "E": 1.0})


def test_full_score_lively_conversation() -> None:
    turns = lively_baseline(16)
    s = st.compute(turns, live_started_at=0.0, now=120.0, energy="high", params=P)
    assert s.score == pytest.approx(0.0) and not s.stale


def test_weights_are_live_tunable() -> None:
    p = Params()
    p.update({"STALENESS_WEIGHTS": {"E": 1.0, "L": 0, "R": 0, "G": 0, "Q": 0}, "STALE_SWITCH": 0.4})
    s = st.compute(lively_baseline(), live_started_at=0.0, now=60.0, energy="medium", params=p)
    assert s.score == pytest.approx(0.5) and s.stale


@pytest.mark.parametrize(
    "text, words, question, backchannel",
    [
        ("Yeah.", 1, False, True),
        ("for sure", 2, False, True),
        ("Hahaha", 1, False, True),
        ("mmm", 1, False, True),
        ("Oh wow, totally!", 3, False, True),
        ("What do you do?", 4, True, False),
        ("How was the drive up there", 6, True, False),
        ("I'm from Austin.", 3, False, False),
        ("You went alone?", 3, True, False),
        ("Yeah totally, we went every summer when I was a kid.", 11, False, False),
    ],
)
def test_turn_text_features(text: str, words: int, question: bool, backchannel: bool) -> None:
    assert count_words(text) == words
    assert is_question(text) is question
    assert is_backchannel(text) is backchannel
