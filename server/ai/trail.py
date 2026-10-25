"""Trail generator (PRD 9.7, F17): up to 3 threads per person to pick up next time.

`for_seat` is the reader; each thread is about the other person ("Ask Maya how ...").
Only things said in the conversation or listed in the allowed profiles are used.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel

import config
from ai import heuristics as h
from ai.gemini import Gemini, GeminiError, get_gemini
from ai.profiles import allowed_items
from ai.prompts import trail_system
from ai.safety import clean_text, find_keyword, is_unsafe, off_limits_keywords, truncate_words
from models import Mode, Profile, ProfileItem, Trail, TrailItem

log = logging.getLogger("breadcrumbs.ai.trail")

MAX_PER_SEAT = 3
TRAIL_MAX_WORDS, TRAIL_MAX_CHARS = 25, 160
_PLAN_RE = re.compile(
    r"\bi(?:'m|\s+am)\s+(?:going to|gonna|planning to|hoping to|about to|signed up to)\s+([a-z]+)\s*([^.!?]*)",
    re.IGNORECASE,
)


class TrailItemDraft(BaseModel):
    for_seat: int
    text: str


class TrailDraft(BaseModel):
    items: list[TrailItemDraft]


async def generate_trail(
    mode: Mode,
    names: dict[int, str],
    transcript: str,
    profiles: dict[int, Profile],
    gemini: Gemini | None = None,
) -> Trail:
    g = gemini or get_gemini()
    drafts: list[TrailItemDraft] = []
    if not g.mock:
        try:
            result = await g.json(
                model=config.GEMINI_MODEL,
                system=trail_system(mode),
                payload=_payload(mode, names, transcript, profiles),
                schema=TrailDraft,
                max_tokens=600,
            )
            drafts = result.items
        except GeminiError as exc:
            log.warning("trail generation failed (%s); using heuristic trail", type(exc).__name__)
    items = finalize_trail(drafts, profiles)
    if not items:
        items = finalize_trail(mock_trail(names, transcript, profiles), profiles)
    return Trail(items=items)


def _payload(mode: Mode, names: dict[int, str], transcript: str, profiles: dict[int, Profile]) -> dict:
    return {
        "mode": mode,
        "names": {str(s): n for s, n in names.items()},
        "transcript": transcript,
        "profiles": {
            str(s): [{"text": i.text, "hook": i.hook} for i in allowed_items(p)] for s, p in profiles.items()
        },
        "off_limits": {str(s): p.off_limits for s, p in profiles.items()},
    }


def finalize_trail(drafts: list[TrailItemDraft], profiles: dict[int, Profile]) -> list[TrailItem]:
    """Seat check, cleanup, safety filters and the 3-per-seat cap."""
    keywords = off_limits_keywords(profiles)
    per_seat: dict[int, int] = {1: 0, 2: 0}
    seen: set[str] = set()
    out: list[TrailItem] = []
    for draft in drafts:
        if draft.for_seat not in (1, 2) or per_seat[draft.for_seat] >= MAX_PER_SEAT:
            continue
        text = truncate_words(clean_text(draft.text), TRAIL_MAX_WORDS, TRAIL_MAX_CHARS)
        if not text or text.lower() in seen or is_unsafe(text) or find_keyword(text, keywords):
            continue
        if text[-1] not in ".?!":
            text += "."
        seen.add(text.lower())
        per_seat[draft.for_seat] += 1
        out.append(TrailItem(for_seat=draft.for_seat, text=text))  # type: ignore[arg-type]
    return out


def mock_trail(names: dict[int, str], transcript: str, profiles: dict[int, Profile]) -> list[TrailItemDraft]:
    """Threads from the other person's future plans in the transcript, then their discussed items."""
    lines = h.transcript_lines(transcript)
    said = " ".join(t for _, t in lines)
    drafts: list[TrailItemDraft] = []
    for reader, other in ((1, 2), (2, 1)):
        other_name = names.get(other, f"Seat {other}")
        threads = _plan_threads(other_name, lines)
        threads += [_item_thread(other_name, item) for item in _discussed_first(profiles.get(other), said)]
        drafts += [TrailItemDraft(for_seat=reader, text=t) for t in threads[:MAX_PER_SEAT]]
    return drafts


def _plan_threads(other_name: str, lines: list[tuple[str, str]]) -> list[str]:
    """The other person's latest stated plan, e.g. "I'm going to try a climbing gym next week"."""
    first = other_name.split()[0].lower() if other_name else ""
    out: list[str] = []
    for speaker, text in lines:
        if speaker.lower().split()[0] != first:
            continue
        match = _PLAN_RE.search(text)
        if match:
            verb, rest = match.group(1), h.short(h.to_third(match.group(2)), 8)
            out.append(f"Ask {other_name} how {h.to_gerund(verb)}{' ' + rest if rest else ''} went.")
    return out[-1:]


def _discussed_first(profile: Profile | None, said: str) -> list[ProfileItem]:
    if profile is None:
        return []
    items = allowed_items(profile)
    said_stems = h.stems(said)
    discussed = [i for i in items if h.stems(i.text) & said_stems]
    return discussed + [i for i in items if i not in discussed]


def _item_thread(other_name: str, item: ProfileItem) -> str:
    topic = h.topic_of(item.text)
    if item.kind == "goal" and item.text.lower().startswith("wants to"):
        return f"Ask {other_name} about the plan for {topic}."
    if item.kind == "goal":
        return f"Ask {other_name} how {topic} is going."
    if item.kind == "story":
        return f"Ask {other_name} for the full story: {topic}."
    return f"Ask {other_name} more about {topic}."
