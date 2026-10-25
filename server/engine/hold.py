"""Intentional-hold detection (PRD 8.6, Appendix A) and the safety lexicon (PRD 15.3).

Pure text functions plus the `Hold` record. The engine decides when to call them: a lexical hit only
becomes a hold when silence follows the turn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from config import HOLD_TAIL_WORDS, Params
from models import HoldKind

HoldSource = Literal["lexical", "llm", "manual"]

# Appendix A, verbatim.
HOLD_PATTERNS: dict[str, list[str]] = {
    "show": [
        r"\blet me (show|pull (it|this|that) up|find (it|this|that)|grab (it|my phone)|look (it|this) up)\b",
        r"\b(look|check) (at )?this( out)?\b",
        r"\b(hold on|hang on|one sec(ond)?|just a sec(ond)?|give me a (sec(ond)?|minute))\b",
        r"\bwhere (is|was) (it|that)\b",
        r"\bi('m| am) (looking|trying to find)\b",
        r"\bwait\W*$",  # "wait" as the last word of the turn
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
}
NEGATIVE_PATTERNS: list[str] = [
    r"\bwait,? (really|what|seriously|no way)\b.*\w+\s+\w+\s+\w+",  # surprise followed by more speech
    r"\bcan'?t wait\b",
]

# PRD 15.3: a small distress lexicon. A hit pauses automatic crumbs for the rest of the session.
SAFETY_PATTERNS: list[str] = [
    r"\b(kill|hurt|harm|cut) myself\b",
    r"\b(want|wanna) to die\b",
    r"\bend (my life|it all)\b",
    r"\bsuicid(e|al)\b",
    r"\bself[- ]harm\b",
    r"\bpanic attack\b",
    r"\bcall (911|an ambulance|the police)\b",
    r"\bi('m| am) not safe\b",
    r"\b(he|she|they|someone) (is |keeps )?(hurting|abusing|threatening) me\b",
]

HOLD_CONFIDENCE_MIN = 0.5  # classifier decisions below this neither create nor cancel a hold
_KIND_PRIORITY: dict[str, int] = {"think": 1, "show": 2, "external": 3}

_HOLD_RES = {kind: [re.compile(p, re.IGNORECASE) for p in pats] for kind, pats in HOLD_PATTERNS.items()}
_NEGATIVE_RES = [re.compile(p, re.IGNORECASE) for p in NEGATIVE_PATTERNS]
_SAFETY_RES = [re.compile(p, re.IGNORECASE) for p in SAFETY_PATTERNS]
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.lower().split())


def tail(text: str, n_words: int = HOLD_TAIL_WORDS) -> str:
    """The last `n_words` whitespace tokens, punctuation kept (patterns anchor on the turn's end)."""
    tokens = normalize(text).split()
    return " ".join(tokens[-n_words:]) if n_words > 0 else ""


def match_hold(text: str, n_words: int = HOLD_TAIL_WORDS) -> HoldKind | None:
    """Lexical hold kind in the tail of a turn, or None. When several kinds match, the phrase closest
    to the end of the turn wins (ties: external > show > think)."""
    t = tail(text, n_words)
    if not t or any(r.search(t) for r in _NEGATIVE_RES):
        return None
    best: tuple[int, int] | None = None
    best_kind: HoldKind | None = None
    for kind, regexes in _HOLD_RES.items():
        for regex in regexes:
            for m in regex.finditer(t):
                key = (m.end(), _KIND_PRIORITY[kind])
                if best is None or key > best:
                    best, best_kind = key, kind  # type: ignore[assignment]
    return best_kind


def is_ambiguous_fragment(text: str) -> bool:
    """PRD 8.6 step 2: ends without punctuation (trailing off counts), or under 4 words."""
    stripped = text.strip()
    if not stripped:
        return False
    trailing_off = stripped.endswith(("...", "…"))
    punctuated = stripped[-1] in ".!?" and not trailing_off
    return not punctuated or len(_WORD_RE.findall(stripped)) < 4


def is_distress(text: str) -> bool:
    t = normalize(text)
    return any(r.search(t) for r in _SAFETY_RES)


@dataclass
class Hold:
    kind: HoldKind
    started_at: float
    until: float
    source: HoldSource
    turn_id: int | None = None

    @property
    def suppresses(self) -> bool:
        """show / external suppress all automatic crumbs; think only doubles the thresholds."""
        return self.kind in ("show", "external")

    def remaining(self, now: float) -> float:
        return max(0.0, self.until - now)

    def expired(self, now: float) -> bool:
        return now >= self.until

    def to_snapshot(self, now: float) -> dict:
        return {"kind": self.kind, "remaining_s": round(self.remaining(now), 1), "source": self.source}


def max_duration(kind: HoldKind, params: Params) -> float:
    return float(params.HOLD_MAX_S.get(kind, 60))


def start_hold(
    kind: HoldKind,
    started_at: float,
    source: HoldSource,
    params: Params,
    *,
    turn_id: int | None = None,
    duration_s: float | None = None,
) -> Hold:
    duration = duration_s if duration_s is not None else max_duration(kind, params)
    return Hold(kind=kind, started_at=started_at, until=started_at + duration, source=source, turn_id=turn_id)
