"""Staleness score (PRD 8.5). Pure functions over committed human turns.

S = 0.35*L + 0.25*R + 0.15*G + 0.10*Q + 0.15*E   (0 = lively, 1 = dead), STALE = S >= STALE_SWITCH.
Baseline = the first BASELINE_TURNS live human turns or the first BASELINE_SECONDS, whichever comes first.
"""
from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from config import GAP_CLAMP, Params
from models import Turn

ENERGY_VALUE: dict[str, float] = {"high": 0.0, "medium": 0.5, "low": 1.0}
PART_KEYS = ("L", "R", "G", "Q", "E")


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


@dataclass(frozen=True)
class Baseline:
    turns: list[Turn]
    ready: bool
    gap_ref: float  # g: median baseline gap clamped to GAP_CLAMP (DEFAULT_GAP_S until ready)


@dataclass(frozen=True)
class Staleness:
    score: float = 0.0
    stale: bool = False
    parts: dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in PART_KEYS})
    baseline_ready: bool = False
    gap_ref: float = 1.2


def select_baseline(turns: Sequence[Turn], live_started_at: float, now: float, params: Params) -> Baseline:
    cutoff = live_started_at + params.BASELINE_SECONDS
    early = [t for t in turns if t.started_at < cutoff][: params.BASELINE_TURNS]
    ready = len(early) >= params.BASELINE_TURNS or (now >= cutoff and len(early) > 0)
    return Baseline(turns=early, ready=ready, gap_ref=gap_reference(early, ready, params))


def gap_reference(baseline: Sequence[Turn], ready: bool, params: Params) -> float:
    if not ready:
        return params.DEFAULT_GAP_S
    g = median([t.gap_before for t in baseline if t.gap_before is not None])
    return clamp(g if g is not None else params.DEFAULT_GAP_S, GAP_CLAMP[0], GAP_CLAMP[1])


def reply_length_decay(recent: Sequence[Turn], baseline: Baseline) -> float:
    """L = clamp(1 - median_words(last N) / median_words(baseline), 0, 1); 0 before the baseline."""
    if not baseline.ready or not recent:
        return 0.0
    base = median([t.words for t in baseline.turns])
    if not base:
        return 0.0
    return clamp(1.0 - median([t.words for t in recent]) / base, 0.0, 1.0)


def is_short(turn: Turn, params: Params) -> bool:
    return turn.words <= params.SHORT_TURN_WORDS or turn.is_backchannel


def short_reply_rate(recent: Sequence[Turn], params: Params) -> float:
    """R = count(short turns in last N) / N."""
    n = max(1, params.N_RECENT_TURNS)
    return sum(1 for t in recent if is_short(t, params)) / n


def gap_growth(recent: Sequence[Turn], baseline: Baseline) -> float:
    """G = clamp(median_gap(last N) / g - 1, 0, 2) / 2; 0 before the baseline."""
    if not baseline.ready:
        return 0.0
    gap = median([t.gap_before for t in recent if t.gap_before is not None])
    if gap is None or baseline.gap_ref <= 0:
        return 0.0
    return clamp(gap / baseline.gap_ref - 1.0, 0.0, 2.0) / 2.0


def question_deficit(recent: Sequence[Turn]) -> float:
    """Q = 1 - min(questions_in_last_N / 2, 1); 0 with no turns yet."""
    if not recent:
        return 0.0
    return 1.0 - min(sum(1 for t in recent if t.is_question) / 2.0, 1.0)


def energy_component(energy: str | None) -> float:
    """E from the latest EnergyTag: high 0, medium 0.5, low 1; 0 before any tag."""
    return ENERGY_VALUE.get(energy, 0.0) if energy else 0.0


def compute(
    turns: Sequence[Turn],
    *,
    live_started_at: float,
    now: float,
    energy: str | None,
    params: Params,
) -> Staleness:
    """`turns` = committed human (non-bot) turns since live start, in start order."""
    baseline = select_baseline(turns, live_started_at, now, params)
    recent = list(turns[-params.N_RECENT_TURNS:]) if params.N_RECENT_TURNS > 0 else []
    parts = {
        "L": reply_length_decay(recent, baseline),
        "R": short_reply_rate(recent, params),
        "G": gap_growth(recent, baseline),
        "Q": question_deficit(recent),
        "E": energy_component(energy),
    }
    weights = params.STALENESS_WEIGHTS
    score = clamp(sum(weights.get(k, 0.0) * v for k, v in parts.items()), 0.0, 1.0)
    return Staleness(
        score=score,
        stale=score >= params.STALE_SWITCH,
        parts=parts,
        baseline_ready=baseline.ready,
        gap_ref=baseline.gap_ref,
    )
