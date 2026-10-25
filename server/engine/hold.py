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
    r"\b(want to|wanna) die\b",
    r"\bdon'?t want to (live|be alive|be here) anymore\b",
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
_SURPRISE_RE, _CANT_WAIT_RE = _NEGATIVE_RES
SURPRISE_TERMINAL_SLACK_WORDS = 2  # after a surprise, a hold phrase must end the turn (at most 2 more words)
SURPRISE_LOOKBACK_WORDS = 3  # "wait, really?" may start just before the 8-word tail
_SAFETY_RES = [re.compile(p, re.IGNORECASE) for p in SAFETY_PATTERNS]
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.lower().split())


def tail(text: str, n_words: int = HOLD_TAIL_WORDS) -> str:
    """The last `n_words` whitespace tokens, punctuation kept (patterns anchor on the turn's end)."""
    tokens = normalize(text).split()
    return " ".join(tokens[-n_words:]) if n_words > 0 else ""


def _words_after(t: str, pos: int) -> int:
    return len(_WORD_RE.findall(t[pos:]))


def _vetoed(t: str, start: int, end: int, surprise_end: int | None) -> bool:
    """Appendix A negatives, each applied only to the matches it is about.

    - "can't wait" vetoes a match that overlaps it (the trailing-"wait" pattern), not the whole tail:
      "I can't wait, hold on, let me show you." is still a show hold.
    - "wait, really/what/... + more speech" means the speaker kept going. A hold phrase only survives if it
      comes after the surprise and ends the turn ("Wait, really? Let me pull it up."), so "Wait, really?
      Hold on, that's insane, how did you..." stays a non-hold.
    """
    for m in _CANT_WAIT_RE.finditer(t):
        if start < m.end() and m.start() < end:
            return True
    if surprise_end is not None:
        if start < surprise_end or _words_after(t, end) > SURPRISE_TERMINAL_SLACK_WORDS:
            return True
    return False


def _surprise_end(text: str, t: str, n_words: int) -> int | None:
    """End of "wait, really" (in tail coordinates, may be <= 0) when a surprise followed by more speech is
    in or just before the tail: the 8-word window can cut off the "wait" of "Wait, what? Hold on, ...". """
    wide = tail(text, n_words + SURPRISE_LOOKBACK_WORDS)
    m = _SURPRISE_RE.search(wide)
    return None if m is None else m.end(1) - (len(wide) - len(t))


def _lexical_hits(text: str, n_words: int) -> list[tuple[int, int, HoldKind, bool]]:
    """Every positive match in the tail: (start, end, kind, vetoed)."""
    t = tail(text, n_words)
    surprise_end = _surprise_end(text, t, n_words)
    hits: list[tuple[int, int, HoldKind, bool]] = []
    for kind, regexes in _HOLD_RES.items():
        for regex in regexes:
            for m in regex.finditer(t):
                vetoed = _vetoed(t, m.start(), m.end(), surprise_end)
                hits.append((m.start(), m.end(), kind, vetoed))  # type: ignore[arg-type]
    return hits


def match_hold(text: str, n_words: int = HOLD_TAIL_WORDS) -> HoldKind | None:
    """Lexical hold kind in the tail of a turn, or None. When several kinds match, the phrase closest
    to the end of the turn wins (ties: external > show > think)."""
    if not tail(text, n_words):
        return None
    best: tuple[int, int] | None = None
    best_kind: HoldKind | None = None
    for _, end, kind, vetoed in _lexical_hits(text, n_words):
        if vetoed:
            continue
        key = (end, _KIND_PRIORITY[kind])
        if best is None or key > best:
            best, best_kind = key, kind
    return best_kind


def hold_vetoed(text: str, n_words: int = HOLD_TAIL_WORDS) -> bool:
    """A hold phrase is in the tail but a negative pattern overrode it: worth a classifier check (PRD 8.6
    step 2), since the lexicon alone cannot tell surprise from an intentional pause every time."""
    if not tail(text, n_words) or match_hold(text, n_words) is not None:
        return False
    return any(v for *_, v in _lexical_hits(text, n_words))


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
