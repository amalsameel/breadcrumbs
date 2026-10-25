"""Fast classifiers on Flash-Lite (PRD 9.6): intentional-hold decisions and the energy/topic tagger.

Both fall back to deterministic lexical heuristics in mock mode or when the model call fails, so
the engine always gets an answer quickly.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

import config
from ai import heuristics as h
from ai.gemini import Gemini, GeminiError, get_gemini
from ai.prompts import ENERGY_SYSTEM, HOLD_SYSTEM
from ai.safety import clean_text, truncate_words
from models import EnergyTag, HoldDecision

log = logging.getLogger("breadcrumbs.ai.classifiers")

HOLD_MAX_TOKENS = 160
ENERGY_MAX_TOKENS = 160

# Appendix A lexicon, mirrored for the mock classifier (the engine's own copy lives in engine/hold.py).
_HOLD_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    kind: [re.compile(p, re.IGNORECASE) for p in patterns]
    for kind, patterns in {
        "show": [
            r"\blet me (show|pull (it|this|that) up|find (it|this|that)|grab (it|my phone)|look (it|this) up)\b",
            r"\b(look|check) (at )?this( out)?\b",
            r"\b(hold on|hang on|one sec(ond)?|just a sec(ond)?|give me a (sec(ond)?|minute))\b",
            r"\bwhere (is|was) (it|that)\b",
            r"\bi('m| am) (looking|trying to find)\b",
            r"\bwait\W*$",
        ],
        "think": [
            r"\blet me think\b",
            r"\b(hmm+|umm+)\W*$",
            r"\b(good|great|tough|hard) question\b",
            r"\bhow do i (put|say) (this|it)\b",
            r"\bthat'?s a (tough|hard|good) one\b",
        ],
        "external": [
            r"\b(sorry,? )?i (have|need|gotta|got) to take this\b",
            r"\bexcuse me\b",
            r"\b(be right back|brb)\b",
        ],
    }.items()
}
_HOLD_NEGATIVES = [
    re.compile(r"\bwait,? (really|what|seriously|no way)\b.*\w+\s+\w+\s+\w+", re.IGNORECASE),
    re.compile(r"\bcan'?t wait\b", re.IGNORECASE),
]
_MOCK_EXPECTED_S = {"show": 20, "think": 8, "external": 45}

_TOPIC_STOP = frozenset(
    "wait really bored sure totally cool nice seriously crazy insane awesome amazing funny wow haha lol "
    "did does didn't don't can't isn't wasn't how yes yep nope literally gonna wanna".split()
)
_CLOSERS = re.compile(r"\b(anyway|so yeah|cool|nice|yeah|for sure|totally|mm+|true|fair|okay|ok)\b", re.IGNORECASE)
_LAUGHTER = re.compile(r"\b(haha+|lol|ha ha|hilarious|no way|wow)\b|!", re.IGNORECASE)


# --- Hold classifier ---------------------------------------------------------------------------


async def classify_hold(context: str, gemini: Gemini | None = None) -> HoldDecision:
    g = gemini or get_gemini()
    if not g.mock:
        try:
            decision = await g.json(
                model=config.GEMINI_LITE_MODEL,
                system=HOLD_SYSTEM,
                payload=context,
                schema=HoldDecision,
                max_tokens=HOLD_MAX_TOKENS,
            )
            return normalize_hold(decision)
        except GeminiError as exc:
            log.warning("hold classifier failed (%s); using lexicon", type(exc).__name__)
    return mock_hold(context)


def normalize_hold(d: HoldDecision) -> HoldDecision:
    if not d.hold or d.kind == "none":
        return HoldDecision(hold=False, kind="none", expected_seconds=0, confidence=_clamp01(d.confidence))
    cap = int(config.HOLD_MAX_S.get(d.kind, 60))
    return HoldDecision(
        hold=True,
        kind=d.kind,
        expected_seconds=max(1, min(cap, int(d.expected_seconds or 0) or _MOCK_EXPECTED_S[d.kind])),
        confidence=_clamp01(d.confidence),
    )


def mock_hold(context: str) -> HoldDecision:
    """Appendix A lexicon over the last 8 words of the latest turn."""
    lines = h.transcript_lines(context)
    latest = lines[-1][1] if lines else context.strip().splitlines()[-1] if context.strip() else ""
    tail = " ".join(latest.split()[-config.HOLD_TAIL_WORDS:])
    if not tail or any(p.search(latest) for p in _HOLD_NEGATIVES):
        return HoldDecision(hold=False, kind="none", expected_seconds=0, confidence=0.6)
    for kind, patterns in _HOLD_PATTERNS.items():
        if any(p.search(tail) for p in patterns):
            return HoldDecision(hold=True, kind=kind, expected_seconds=_MOCK_EXPECTED_S[kind], confidence=0.75)
    return HoldDecision(hold=False, kind="none", expected_seconds=0, confidence=0.6)


# --- Energy and topic tagger ------------------------------------------------------------------


async def tag_energy(context: str, gemini: Gemini | None = None) -> EnergyTag:
    g = gemini or get_gemini()
    if not g.mock:
        try:
            tag = await g.json(
                model=config.GEMINI_LITE_MODEL,
                system=ENERGY_SYSTEM,
                payload=context,
                schema=EnergyTag,
                max_tokens=ENERGY_MAX_TOKENS,
            )
            topic = _clean_topic(tag.topic) or mock_energy(context).topic
            return EnergyTag(topic=topic, energy=tag.energy, winding_down=tag.winding_down)
        except GeminiError as exc:
            log.warning("energy tagger failed (%s); using heuristic", type(exc).__name__)
    return mock_energy(context)


def mock_energy(context: str) -> EnergyTag:
    lines = h.transcript_lines(context)[-6:]
    if not lines:
        return EnergyTag(topic="getting started", energy="medium", winding_down=False)
    return EnergyTag(topic=_mock_topic(lines), energy=_mock_level(lines), winding_down=_mock_winding_down(lines))


def _mock_topic(lines: list[tuple[str, str]]) -> str:
    """Most repeated content words of the last few turns, in the order they were first said."""
    speakers = {h.stem(s) for s, _ in lines}
    counts: Counter[str] = Counter()
    surface: dict[str, str] = {}
    first_seen: dict[str, int] = {}
    position = 0
    for _, text in lines:
        for word in h.content_words(text):
            key = h.stem(word)
            position += 1
            if key in speakers or word in _TOPIC_STOP:
                continue
            counts[key] += 1
            surface.setdefault(key, word)
            first_seen.setdefault(key, position)
    if not counts:
        return "small talk"
    ranked = sorted(counts, key=lambda k: (-counts[k], first_seen[k]))[:3]
    return " ".join(surface[k] for k in sorted(ranked, key=lambda k: first_seen[k]))


def _mock_level(lines: list[tuple[str, str]]) -> str:
    recent = lines[-4:]
    avg_words = sum(len(t.split()) for _, t in recent) / len(recent)
    excited = sum(1 for _, t in recent if _LAUGHTER.search(t))
    if avg_words >= 12 or excited >= 2:
        return "high"
    if avg_words <= 4:
        return "low"
    return "medium"


def _mock_winding_down(lines: list[tuple[str, str]]) -> bool:
    last_two = lines[-2:]
    return len(last_two) == 2 and all(len(t.split()) <= 4 and (_CLOSERS.search(t) or len(t.split()) <= 2)
                                      for _, t in last_two)


def _clean_topic(topic: str) -> str:
    cleaned = re.sub(r"[^\w\s'\-]", " ", clean_text(topic or "")).strip()
    return truncate_words(cleaned, 5, 60)


def _clamp01(x: float) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0
