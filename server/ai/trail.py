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
from ai.memory import MEMORY_KEYS, clean_item
from ai.profiles import allowed_items
from ai.prompts import trail_system
from ai.safety import clean_text, find_keyword, is_sensitive, is_unsafe, off_limits_keywords, truncate_words
from engine.hold import is_distress  # the same PRD 15.3 lexicon that pauses crumbs (pure, no engine state)
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
    *,
    safety_paused: bool = False,
    memory: dict[str, list[str]] | None = None,
    anonymous: frozenset[int] | set[int] = frozenset(),
    table: bool = False,
) -> Trail:
    """`safety_paused`: the session hit the distress lexicon (PRD 15.3). Then nothing from the
    transcript becomes a thread, nor anything the live memory learned from it; only the allowed profile
    items are used (a "Just talk" table has none: its trail is empty).

    "Just talk" (`table=True`): the trail is shown on the Table, built from the live `memory` and the
    transcript (there are no profiles); `anonymous` seats typed no name, so their label is never written."""
    g = gemini or get_gemini()
    transcript = "" if safety_paused else safe_transcript(transcript)
    memory = {k: [] for k in MEMORY_KEYS} if safety_paused else _safe_memory(memory, profiles)
    placeholders = [names[s] for s in sorted(anonymous) if names.get(s)]
    drafts: list[TrailItemDraft] = []
    if not g.mock:
        try:
            result = await g.json(
                model=config.GEMINI_MODEL,
                system=trail_system(mode, table=table, anonymous=bool(anonymous)),
                payload=_payload(mode, names, transcript, profiles, memory),
                schema=TrailDraft,
                max_tokens=600,
            )
            drafts = result.items
        except GeminiError as exc:
            log.warning("trail generation failed (%s); using heuristic trail", type(exc).__name__)
    items = finalize_trail(drafts, profiles, placeholders=placeholders)
    if not items:
        fallback = mock_trail(names, transcript, profiles, memory=memory, anonymous=anonymous)
        items = finalize_trail(fallback, profiles, placeholders=placeholders)
    return Trail(items=items)


def _payload(mode: Mode, names: dict[int, str], transcript: str, profiles: dict[int, Profile],
             memory: dict[str, list[str]] | None = None) -> dict:
    payload = {
        "mode": mode,
        "names": {str(s): n for s, n in names.items()},
        "transcript": transcript,
        "profiles": {
            str(s): [{"text": i.text, "hook": i.hook} for i in allowed_items(p)] for s, p in profiles.items()
        },
        "off_limits": {str(s): p.off_limits for s, p in profiles.items()},
    }
    if memory and any(memory.values()):
        payload["memory"] = memory
    return payload


def _safe_memory(memory: dict[str, list[str]] | None, profiles: dict[int, Profile]) -> dict[str, list[str]]:
    """The memory again through the safety and off-limits filters (it was filtered when it was learned)."""
    keywords = off_limits_keywords(profiles)
    out: dict[str, list[str]] = {}
    for key in MEMORY_KEYS:
        items = [clean_item(i, keywords) for i in (memory or {}).get(key, [])]
        out[key] = [i for i in items if i]
    return out


def safe_transcript(transcript: str) -> str:
    """Drop every line with distress or sensitive content before anything reads it for threads.

    A statement that paused the crumbs (or a health, immigration, money... detail) must never come
    back as a cheerful "Ask Dev how ... went." on the other person's phone.
    """
    kept = [raw for raw in (transcript or "").splitlines()
            if not (is_distress(raw) or is_sensitive(raw) or is_unsafe(raw))]
    return "\n".join(kept)


def finalize_trail(drafts: list[TrailItemDraft], profiles: dict[int, Profile], *,
                   placeholders: list[str] | tuple[str, ...] = ()) -> list[TrailItem]:
    """Seat check, cleanup, safety filters (unsafe, distress, sensitive, off-limits) and the 3-per-seat cap.
    `placeholders` ("Person 1" for someone who typed no name) are never written."""
    keywords = off_limits_keywords(profiles)
    per_seat: dict[int, int] = {1: 0, 2: 0}
    seen: set[str] = set()
    out: list[TrailItem] = []
    for draft in drafts:
        if draft.for_seat not in (1, 2) or per_seat[draft.for_seat] >= MAX_PER_SEAT:
            continue
        text = truncate_words(clean_text(draft.text), TRAIL_MAX_WORDS, TRAIL_MAX_CHARS)
        if not text or text.lower() in seen or find_keyword(text, keywords):
            continue
        if is_unsafe(text) or is_distress(text) or is_sensitive(text):
            continue
        if any(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text, re.IGNORECASE) for p in placeholders):
            continue
        if text[-1] not in ".?!":
            text += "."
        seen.add(text.lower())
        per_seat[draft.for_seat] += 1
        out.append(TrailItem(for_seat=draft.for_seat, text=text))  # type: ignore[arg-type]
    return out


def mock_trail(names: dict[int, str], transcript: str, profiles: dict[int, Profile], *,
               memory: dict[str, list[str]] | None = None,
               anonymous: frozenset[int] | set[int] = frozenset()) -> list[TrailItemDraft]:
    """Threads from the other person's future plans in the transcript, then their discussed items, then
    what the live memory says about them and the open threads ("them" for someone who typed no name)."""
    lines = h.transcript_lines(transcript)
    said = " ".join(t for _, t in lines)
    memory = memory or {}
    per_reader: dict[int, list[str]] = {}
    for reader, other in ((1, 2), (2, 1)):
        speaker = names.get(other, f"Seat {other}")
        other_name = "them" if other in anonymous else speaker
        threads = _plan_threads(speaker, lines, other_name)
        threads += [_item_thread(other_name, item) for item in _discussed_first(profiles.get(other), said)]
        threads += [_memory_thread(other_name, item) for item in reversed(memory.get(str(other), []))]
        per_reader[reader] = list(dict.fromkeys(threads))[:MAX_PER_SEAT]
    for thread in reversed(memory.get("threads", [])):
        # An open thread belongs to both of them: it goes to whoever has fewer threads (no repeats).
        reader = min((1, 2), key=lambda r: len(per_reader[r]))
        if len(per_reader[reader]) < MAX_PER_SEAT:
            per_reader[reader].append(_open_thread(thread))
    return [TrailItemDraft(for_seat=r, text=t) for r in (1, 2) for t in per_reader[r]]


def _memory_thread(other_name: str, item: str) -> str:
    return f"Ask {other_name} more about this: {h.to_third(item).rstrip(' .?!')}."


def _open_thread(thread: str) -> str:
    thread = thread.rstrip(" .?!")
    return f"Pick up where you left off: {thread[:1].lower() + thread[1:]}?"


def _plan_threads(speaker: str, lines: list[tuple[str, str]], other_name: str | None = None) -> list[str]:
    """The other person's latest stated plan, e.g. "I'm going to try a climbing gym next week".
    `speaker` is their transcript label; `other_name` how the thread refers to them (default: the label)."""
    label = speaker.strip().lower()
    first = label.split()[0] if label else ""
    by_first = bool(first) and not label.split()[-1].isdigit()  # "Person 1" and "Person 2" share "person"
    other_name = other_name or speaker
    out: list[str] = []
    for said_by, text in lines:
        spoken = said_by.strip().lower()
        if spoken != label and not (by_first and spoken.split()[0] == first):
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
