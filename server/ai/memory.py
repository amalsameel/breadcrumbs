"""Live memory: what Breadcrumbs learns about the two people from the conversation itself.

`{"1": [...], "2": [...], "shared": [...], "threads": [...]}`: short items (interests, facts, plans and
stories each person mentioned, things they have in common, open threads worth picking up). In the "Just
talk" setup it stands in for the profiles and overlaps; with profiles it adds to them.

No extra model call: the items come back with the periodic energy tag (ai.classifiers.tag_energy_memory),
and `mock_memory` is the deterministic offline path. Every item passes the same safety and off-limits
filters as crumbs and the trail. Memory lives in the session's runner only, is wiped with the session,
and is never logged (counts only).
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from ai import heuristics as h
from ai.profiles import mock_profile_items
from ai.safety import clean_text, find_keyword, is_sensitive, is_unsafe, truncate_words
from engine.hold import is_distress  # the PRD 15.3 lexicon (pure, no engine state)

MEMORY_KEYS: tuple[str, ...] = ("1", "2", "shared", "threads")
MAX_ITEMS = {"1": 8, "2": 8, "shared": 6, "threads": 6}  # oldest dropped first
MAX_NEW_PER_LIST = 3  # per update: the model is asked for at most this many new items per list
ITEM_MAX_WORDS, ITEM_MAX_CHARS = 12, 90
ITEM_MIN_WORDS = 2
DUPLICATE_OVERLAP = 0.6  # share of word stems (of the shorter item) that makes two items the same thing
MOCK_ITEM_MIN_WORDS = 3
MOCK_SHARED_MIN_CHARS = 4
_MOCK_KINDS = frozenset({"interest", "story", "goal", "fun_fact", "value", "preference"})
_MOCK_JUNK = re.compile(r"^(they would|they could|they think|they mean|totally|honestly|really)\b|\bsounds?\b",
                        re.IGNORECASE)
_TEMPLATE_STEMS = frozenset({"both", "talk", "talked", "mention"})  # words of the mock "shared" phrasing
_QUESTION_LEAD = re.compile(r"^(?:so|and|but|okay|ok|wait|oh|hey|well|right)\b[,\s]*", re.IGNORECASE)
_SHARED_STOP = frozenset(
    "about after again also always because being could doing every first going gonna great have having here last "
    "into just know like little maybe much need never only other over pretty really right said should "
    "since some something still sure than that thats their them then there these they thing things "
    "think this those though time today together totally very want wanna well were what when where "
    "which while with would yeah your yours".split()
)


class MemoryDelta(BaseModel):
    """New items from one update (the model's part of EnergyMemoryDraft, or mock_memory)."""

    person1: list[str] = []
    person2: list[str] = []
    shared: list[str] = []
    threads: list[str] = []

    def lists(self) -> dict[str, list[str]]:
        return {"1": self.person1, "2": self.person2, "shared": self.shared, "threads": self.threads}


class Memory:
    """The per-session memory. `merge` cleans, filters and de-duplicates; it returns True on any change."""

    def __init__(self) -> None:
        self._items: dict[str, list[str]] = {k: [] for k in MEMORY_KEYS}

    def to_dict(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self._items.items()}

    def items(self, key: str) -> list[str]:
        return list(self._items.get(key, []))

    @property
    def empty(self) -> bool:
        return not any(self._items.values())

    def count(self) -> int:
        return sum(len(v) for v in self._items.values())

    def clear(self) -> None:
        for items in self._items.values():
            items.clear()

    def merge(self, delta: MemoryDelta | None, *, blocked: list[str] | tuple[str, ...] = ()) -> bool:
        if delta is None:
            return False
        changed = False
        for key, new in delta.lists().items():
            kept = self._items[key]
            for raw in new[:MAX_NEW_PER_LIST]:
                item = clean_item(raw, blocked)
                if item is None or any(same_item(item, old) for key2 in MEMORY_KEYS for old in self._items[key2]):
                    continue
                kept.append(item)
                changed = True
            del kept[: max(0, len(kept) - MAX_ITEMS[key])]
        return changed

    def drop(self, item: str) -> bool:
        """Forget one item (e.g. a thread a crumb already picked up)."""
        for items in self._items.values():
            if item in items:
                items.remove(item)
                return True
        return False


def clean_item(raw: object, blocked: list[str] | tuple[str, ...] = ()) -> str | None:
    """One short phrase, or None when it is empty, too short, or fails a safety or off-limits filter."""
    text = truncate_words(clean_text(str(raw or "")), ITEM_MAX_WORDS, ITEM_MAX_CHARS).strip(" .,;:")
    if len(text.split()) < ITEM_MIN_WORDS:
        return None
    if is_unsafe(text) or is_distress(text) or is_sensitive(text) or find_keyword(text, list(blocked)):
        return None
    return text


def same_item(a: str, b: str) -> bool:
    if a.lower() == b.lower():
        return True
    sa, sb = h.stems(a) - _TEMPLATE_STEMS, h.stems(b) - _TEMPLATE_STEMS
    if min(len(sa), len(sb)) < 2:
        return bool(sa) and sa == sb
    return len(sa & sb) / min(len(sa), len(sb)) >= DUPLICATE_OVERLAP


PROMPT_ITEMS = 3  # newest items per list in a prompt (every crumb call carries them: input tokens)


def recent(memory: dict[str, list[str]] | None, per_list: int = PROMPT_ITEMS) -> dict[str, list[str]]:
    """The newest `per_list` items of each list (what a prompt carries; the rest stays server-side)."""
    return {k: list((memory or {}).get(k, [])[-per_list:]) for k in MEMORY_KEYS}


def prompt_view(memory: dict[str, list[str]] | None, names: dict[int, str],
                per_list: int = PROMPT_ITEMS) -> dict | None:
    """The memory as the crumb prompt sees it (None when empty): the newest items, keyed by name."""
    if not memory or not any(memory.get(k) for k in MEMORY_KEYS):
        return None
    view = recent(memory, per_list)
    n1, n2 = names.get(1) or "Person 1", names.get(2) or "Person 2"
    return {n1: view["1"], n2: view["2"], "both": view["shared"], "open_threads": view["threads"]}


# --- Mock extraction (deterministic, offline) ------------------------------------------------------


def mock_memory(context: str, names: dict[int, str]) -> MemoryDelta:
    """Rule-based memory from "Name: text" lines: self-statements per person, words both used, and the
    latest questions as open threads. Unattributed lines ("Someone: ...") are ignored."""
    by_seat: dict[int, list[str]] = {1: [], 2: []}
    questions: list[str] = []
    for speaker, text in h.transcript_lines(context):
        seat = _seat_of(speaker, names)
        if seat is None:
            continue
        by_seat[seat].append(text)
        if "?" in text:
            q = _question(text)
            if q:
                questions.append(q)
    out: dict[str, list[str]] = {}
    for seat in (1, 2):
        items: list[str] = []
        for draft in mock_profile_items("social", by_seat[seat]):
            if draft.kind in _MOCK_KINDS and len(draft.text.split()) >= MOCK_ITEM_MIN_WORDS and not _MOCK_JUNK.search(draft.text):
                items.append(draft.text)
        out[str(seat)] = items[-MAX_NEW_PER_LIST:]
    words1 = _topic_words(" ".join(by_seat[1]))
    words2 = _topic_words(" ".join(by_seat[2]))
    shared = [f"both talked about {words1[k]}" for k in words1 if k in words2][:MAX_NEW_PER_LIST]
    return MemoryDelta(person1=out["1"], person2=out["2"], shared=shared, threads=questions[-2:])


def _seat_of(speaker: str, names: dict[int, str]) -> int | None:
    spoken = speaker.strip().lower()
    for seat, name in names.items():
        name = (name or "").strip().lower()
        if name and (spoken == name or spoken == name.split()[0]):
            return int(seat)
    return None


def _question(text: str) -> str | None:
    """The last question of a line, trimmed: "So what got you into running?" -> "What got you into running?"."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip().endswith("?")]
    if not parts:
        return None
    clauses = [c.strip() for c in re.split(r"[,;]", parts[-1].rstrip("?")) if len(c.split()) >= 3]
    if not clauses:
        return None
    q = _QUESTION_LEAD.sub("", clauses[-1]).strip()
    q = h.short(q, 10)
    if len(q.split()) < 3:
        return None
    return q[:1].upper() + q[1:] + "?"


def _topic_words(text: str) -> dict[str, str]:
    """Content-word stems (first surface form) worth calling a shared topic."""
    out: dict[str, str] = {}
    for word in h.content_words(text):
        w = word.lower().strip("'")
        if len(w) < MOCK_SHARED_MIN_CHARS or w in _SHARED_STOP or w in h.STOPWORDS:
            continue
        out.setdefault(h.stem(w), w)
    return out


__all__ = ["MEMORY_KEYS", "Memory", "MemoryDelta", "clean_item", "mock_memory", "prompt_view", "recent", "same_item"]
