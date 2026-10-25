"""Crumb generation, validation, source rotation and the prefetch cache (PRD 8.9, 8.10, 9.4, 9.5).

One CrumbService per session. It owns the crumb history, which items/topics/overlaps were used,
who has been featured, and the ready_steer / ready_switch cache (text + pre-synthesized audio).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

import config
from ai import heuristics as h
from ai.facts import Fact, get_fact
from ai.gemini import Gemini, GeminiError, get_gemini
from ai.profiles import allowed_items
from ai.prompts import canned_crumbs, crumb_system
from ai.safety import (
    UNSAFE_KEYWORDS,
    clean_text,
    find_keyword,
    first_sentence,
    off_limits_keywords,
    short_place,
    truncate_words,
    validate_crumb_text,
)
from ai.tts import synthesize
from models import Crumb, CrumbFormat, CrumbKind, CrumbSource, Mode, OverlapSet, Profile, ProfileItem

log = logging.getLogger("breadcrumbs.ai.crumbs")

MISS_BUDGET_S = 6.0  # text generation on a cache miss (live path)
PREFETCH_BUDGET_S = 25.0
MISS_TTS_BUDGET_S = 4.0  # past this the Table speaks with speechSynthesis
PREFETCH_TTS_BUDGET_S = 12.0
OPENER_TTS_BUDGET_S = 5.0
FACT_BUDGET_S = 8.0
TOPIC_MAX_CHARS = 80
WHY_MAX_CHARS = 200

# Mock outside questions per mode: (topic key, template). {n1}/{n2} are names, {place} the city.
OUTSIDE_QUESTIONS: dict[str, list[tuple[str, str]]] = {
    "social": [
        ("local favorites", "{n1}, what's one spot in {place} you'd take {n2} to this week? {n2}, would you go?"),
        ("hidden talents", "{n2}, what's a skill you have that would surprise {n1}? {n1}, guess first."),
        ("small joys", "{n1} and {n2}, what's the best small thing that happened to you this week? {n1} first."),
        ("dream trip", "{n2}, if you could leave for any trip tomorrow, where would you go? {n1}, would you come?"),
    ],
    "networking": [
        ("event goals", "{n1}, what's one thing you want to walk away from tonight with? {n2}, could you help?"),
        ("best advice", "{n2}, what's the best advice you got when starting out? {n1}, would you agree?"),
        ("learning", "{n1}, what's the most useful thing you learned this year? {n2}, does it apply to your world?"),
        ("tools", "{n2}, what's a tool or habit that changed how you get things done? {n1}, what's yours?"),
    ],
    "speed_dating": [
        ("perfect afternoon", "{n1}, plan a perfect afternoon in {place} for you and {n2}. {n2}, rate it out of ten."),
        ("comfort food", "{n2}, what's your go-to comfort food? {n1}, would you share it or order your own?"),
        ("travel style", "{n1}, window seat or aisle seat, and why? {n2}, you're up next."),
        ("spontaneous", "{n2}, what's the most spontaneous thing you've done on a weekend? {n1}, top it."),
    ],
}
WOULD_YOU_RATHER: list[tuple[str, str]] = [
    ("time travel", "Would you rather visit the past or the future for a day? {n1} first, then {n2}."),
    ("superpowers", "Would you rather be able to fly or be invisible for a day? {n2} first, then {n1}."),
    ("rainy sunday", "Would you rather spend a rainy Sunday at a museum or a cozy cafe? {n1} first."),
]


# --- Public dataclasses (CONTRACTS section 2) ----------------------------------------------------


@dataclass
class ConvoCtx:
    recent_transcript: str
    current_topic: str | None
    depth_max: int
    minutes_live: float
    instruction: str | None = None  # e.g. "one minute left in the round"


@dataclass
class Delivery:
    crumb: Crumb
    audio_wav: bytes | None
    cache_hit: bool
    gen_ms: float


class CrumbDraft(BaseModel):
    """What the model writes; id, kind, source and citations are server-owned."""

    format: CrumbFormat
    text: str
    addressed_to: Literal["both", "seat1", "seat2"]
    source_item_ids: list[str] = []
    topic: str
    depth: int
    why: str


# --- Validation -------------------------------------------------------------------------------


def validate_crumb(
    crumb: Crumb, *, names: dict[int, str], profiles: dict[int, Profile], max_words: int = 28
) -> tuple[bool, Crumb, list[str]]:
    """Word cap, a participant name, no removed ids, no off-limits or unsafe terms, em dashes -> commas.

    Returns (ok, cleaned_crumb, errors). Unknown item ids are stripped from the cleaned crumb.
    """
    _, text, errors = validate_crumb_text(crumb.text, names=names, profiles=profiles, max_words=max_words)
    removed = {i for p in profiles.values() for i in p.removed_item_ids}
    known = {i.id for p in profiles.values() for i in p.items}
    bad = [i for i in crumb.source_item_ids if i in removed]
    if bad:
        errors.append(f"removed item ids: {', '.join(bad)}")
    topic = truncate_words(clean_text(crumb.topic), 12, TOPIC_MAX_CHARS)
    topic_hit = find_keyword(topic, off_limits_keywords(profiles)) or find_keyword(topic, UNSAFE_KEYWORDS)
    if topic_hit:
        errors.append(f"topic uses a blocked term: {topic_hit}")
    cleaned = crumb.model_copy(update={
        "text": text,
        "topic": topic or "open question",
        "why": first_sentence(crumb.why, WHY_MAX_CHARS) or "Chosen to get the conversation going again.",
        "source_item_ids": [i for i in crumb.source_item_ids if i in known and i not in removed],
        "citation_title": clean_text(crumb.citation_title) if crumb.citation_title else None,
    })
    return not errors, cleaned, errors


# --- Internal plan and cache types ----------------------------------------------------------------


@dataclass
class _Plan:
    kind: CrumbKind
    source: CrumbSource
    formats: list[CrumbFormat]
    items: list[ProfileItem] = field(default_factory=list)
    overlap_index: int | None = None
    seat: int | None = None
    topic_hint: str | None = None
    fact: Fact | None = None


@dataclass
class _CacheEntry:
    crumb: Crumb
    audio: bytes | None
    created_at: float
    topic: str | None
    gen_ms: float


# --- Service ----------------------------------------------------------------------------------------


class CrumbService:
    def __init__(
        self,
        *,
        mode: Mode,
        names: dict[int, str],
        profiles: dict[int, Profile],
        overlaps: OverlapSet,
        location: str,
        gemini: Gemini | None = None,
    ) -> None:
        self.mode = mode
        self.names = names
        self.profiles = profiles
        self.overlaps = overlaps
        self.location = location
        self.gemini = gemini or get_gemini()
        self.used_topics: list[str] = []
        self.used_item_ids: set[str] = set()
        self.featured_count: dict[int, int] = {1: 0, 2: 0}
        self.history: list[Crumb] = []
        self.used_overlaps: set[int] = set()
        self._last_alt: Literal["profile", "outside"] = "outside"  # so the first non-overlap switch is a profile
        self._last_profile_seat: int | None = None
        self._delivered: set[str] = set()
        self._counter = 0
        self._cache: dict[str, _CacheEntry] = {}
        self._epoch = 0
        self._prefetch_lock = asyncio.Lock()

    # -- public API -----------------------------------------------------------------------------

    async def opener(self, convo: ConvoCtx) -> Delivery:
        """Best unused overlap opener (F11), validated, marked used immediately."""
        started = time.perf_counter()
        crumb = self._overlap_crumb_validated("opener", convo)
        if crumb is None:
            crumb = await self._build_within(self._plan("switch", convo, as_kind="opener"), convo, MISS_BUDGET_S)
        self.mark_delivered(crumb)
        audio = await self._synth(crumb.text, OPENER_TTS_BUDGET_S)
        return Delivery(crumb=crumb, audio_wav=audio, cache_hit=False, gen_ms=_ms_since(started))

    async def get(self, kind: CrumbKind, convo: ConvoCtx, now: float) -> Delivery:
        """A crumb for `kind`: the cached one when still valid (instant), otherwise generated now."""
        started = time.perf_counter()
        if kind == "opener":
            return await self.opener(convo)
        key = _cache_key(kind)
        entry = self._cache.pop(key, None) if key else None
        if entry is not None and convo.instruction is None and self._entry_valid(entry, now, convo.current_topic):
            crumb = entry.crumb.model_copy(update={"kind": kind})
            log.info("crumb cache hit (%s)", key)
            return Delivery(crumb=crumb, audio_wav=entry.audio, cache_hit=True, gen_ms=_ms_since(started))
        crumb = await self._build_within(self._plan(kind, convo), convo, MISS_BUDGET_S)
        audio = await self._synth(crumb.text, MISS_TTS_BUDGET_S)
        return Delivery(crumb=crumb, audio_wav=audio, cache_hit=False, gen_ms=_ms_since(started))

    async def prefetch(self, convo: ConvoCtx, now: float) -> None:
        """Keep ready_steer and ready_switch (text + audio) fresh. Concurrent calls are skipped."""
        if self._prefetch_lock.locked():
            return
        async with self._prefetch_lock:
            epoch = self._epoch
            missing = [k for k in ("steer", "switch") if not self._key_valid(k, now, convo.current_topic)]
            for key in missing:
                self._cache.pop(key, None)
            results = await asyncio.gather(*(self._prefetch_one(k, convo) for k in missing), return_exceptions=True)
            for key, result in zip(missing, results):
                if isinstance(result, BaseException):
                    log.warning("prefetch %s failed (%s)", key, type(result).__name__)
                    continue
                if self._epoch != epoch:
                    continue  # invalidated while generating
                crumb, audio, gen_ms = result
                self._cache[key] = _CacheEntry(crumb, audio, now, convo.current_topic, gen_ms)

    def invalidate(self, reason: str) -> None:
        self._cache.clear()
        self._epoch += 1
        log.info("crumb cache invalidated (%s)", reason)

    def cached_summary(self, now: float) -> dict:
        def text(key: str) -> str | None:
            entry = self._cache.get(key)
            return entry.crumb.text if entry and self._entry_valid(entry, now, entry.topic) else None

        return {"steer": text("steer"), "switch": text("switch")}

    def mark_delivered(self, crumb: Crumb) -> None:
        """Record a crumb that reached the Table (idempotent per crumb id)."""
        if crumb.id in self._delivered:
            return
        self._delivered.add(crumb.id)
        self.history.append(crumb)
        topic = crumb.topic.strip().lower()
        if topic and topic not in self.used_topics:
            self.used_topics.append(topic)
        ids = set(crumb.source_item_ids)
        self.used_item_ids |= ids
        for index, overlap in enumerate(self.overlaps.overlaps):
            if ids & set(overlap.item_ids_seat1 + overlap.item_ids_seat2):
                self.used_overlaps.add(index)
        if crumb.source == "overlap":
            self.featured_count[1] += 1
            self.featured_count[2] += 1
        elif crumb.source in ("profile_seat1", "profile_seat2"):
            seat = int(crumb.source[-1])
            self.featured_count[seat] += 1
            self._last_profile_seat = seat
        if crumb.kind in ("switch", "manual"):  # wildcards sit outside the rotation
            if crumb.source.startswith("profile"):
                self._last_alt = "profile"
            elif crumb.source == "outside":
                self._last_alt = "outside"

    def crumb_payload(self, kind: CrumbKind, convo: ConvoCtx) -> dict:
        """The PRD 9.4 prompt payload for the next `kind` crumb (exposed for tests and X-ray)."""
        return self._payload(self._plan(kind, convo), convo)

    def next_source(self, kind: CrumbKind, convo: ConvoCtx) -> CrumbSource:
        return self._plan(kind, convo).source

    # -- planning (PRD 8.9) ---------------------------------------------------------------------

    def _plan(self, kind: CrumbKind, convo: ConvoCtx, *, as_kind: CrumbKind | None = None) -> _Plan:
        """Steer or switch source selection; `as_kind` relabels the crumb (e.g. an opener built as a switch)."""
        if kind == "steer":
            plan = self._steer_plan(convo)
        else:
            plan = self._switch_plan(convo, wildcard=(kind == "wildcard"))
        plan.kind = as_kind or kind
        return plan

    def _steer_plan(self, convo: ConvoCtx) -> _Plan:
        """Conversation first, then a related outside fact, then a related profile item (same topic)."""
        if self._latest_line(convo) is not None:
            return _Plan("steer", "conversation", self._formats(["callback", "question"]),
                         topic_hint=convo.current_topic)
        if convo.current_topic:
            return _Plan("steer", "outside", self._formats(["fun_fact", "question"]), topic_hint=convo.current_topic)
        for seat in self._seat_order():
            items = self._fresh_items(seat, convo.depth_max)
            if items:
                return _Plan("steer", _profile_source(seat), self._formats(["question"]), items=items[:1], seat=seat)
        return _Plan("steer", "outside", self._formats(["question"]))

    def _retarget_to_related_item(self, plan: _Plan, convo: ConvoCtx) -> None:
        """Steer without a verified fact: use a profile item related to the current topic, if any."""
        topic_stems = h.stems(plan.topic_hint or "")
        if not topic_stems:
            return
        for seat in self._seat_order():
            related = [i for i in self._allowed(seat) if i.depth <= convo.depth_max and h.stems(i.text) & topic_stems]
            if related:
                plan.source, plan.items, plan.seat = _profile_source(seat), related[:1], seat
                plan.formats = self._formats(["question"])
                return

    def _switch_plan(self, convo: ConvoCtx, *, wildcard: bool) -> _Plan:
        """Overlap while unused overlaps remain, then alternate less-featured profile and outside.

        A wildcard (F22) is always an outside interjection, a would-you-rather where the mode allows it.
        """
        if wildcard:
            return _Plan("switch", "outside", self._formats(["would_you_rather", "question"]))
        unused = self._unused_overlaps(convo.depth_max)
        if unused:
            fmt: list[CrumbFormat] = ["collab", "question"] if self.mode == "networking" else ["question"]
            return _Plan("switch", "overlap", self._formats(fmt), overlap_index=unused[0])
        outside_formats = self._formats(["fun_fact", "question"])
        order = ("profile", "outside") if self._last_alt == "outside" else ("outside", "profile")
        for alt in order:
            if alt == "outside":
                return _Plan("switch", "outside", outside_formats)
            for seat in self._seat_order():
                items = self._fresh_items(seat, convo.depth_max)
                if items:
                    return _Plan("switch", _profile_source(seat), self._formats(["question"]), items=items[:1], seat=seat)
        return _Plan("switch", "outside", outside_formats)

    def _formats(self, preferred: list[str]) -> list[CrumbFormat]:
        allowed = config.ALLOWED_FORMATS[self.mode]
        out = [f for f in preferred if f in allowed]
        return out or ["question"]  # type: ignore[return-value]

    def _seat_order(self) -> list[int]:
        """Less-featured seat first; on a tie, the seat not featured last."""
        c1, c2 = self.featured_count[1], self.featured_count[2]
        if c1 != c2:
            return [1, 2] if c1 < c2 else [2, 1]
        return [2, 1] if self._last_profile_seat == 1 else [1, 2]

    def _allowed(self, seat: int) -> list[ProfileItem]:
        profile = self.profiles.get(seat)
        return allowed_items(profile) if profile else []

    def _allowed_ids(self) -> set[str]:
        return {i.id for seat in (1, 2) for i in self._allowed(seat)}

    def _fresh_items(self, seat: int, depth_max: int) -> list[ProfileItem]:
        """Unused allowed items at or below the depth cap, deepest first (the ladder climbs gradually)."""
        items = [i for i in self._allowed(seat) if i.id not in self.used_item_ids and i.depth <= depth_max]
        return sorted(items, key=lambda i: -i.depth)

    def _unused_overlaps(self, depth_max: int | None = None) -> list[int]:
        allowed = self._allowed_ids()
        depth_of = {i.id: i.depth for seat in (1, 2) for i in self._allowed(seat)}
        out: list[int] = []
        for index, ov in enumerate(self.overlaps.overlaps):
            ids = ov.item_ids_seat1 + ov.item_ids_seat2
            if index in self.used_overlaps or not ids or not set(ids) <= allowed or set(ids) & self.used_item_ids:
                continue
            if depth_max is not None and max(depth_of[i] for i in ids) > depth_max:
                continue
            out.append(index)
        return out

    # -- payload (PRD 9.4) ---------------------------------------------------------------------------

    def _payload(self, plan: _Plan, convo: ConvoCtx) -> dict:
        payload: dict = {
            "mode": self.mode,
            "kind": plan.kind,
            "requested_source": plan.source,
            "current_depth_max": convo.depth_max,
            "names": {"1": self.names.get(1, ""), "2": self.names.get(2, "")},
            "profiles": {"1": self._profile_text(1), "2": self._profile_text(2)},
            "overlaps_unused": [self._overlap_text(i) for i in self._unused_overlaps()],
            "recent_transcript": convo.recent_transcript,
            "current_topic": convo.current_topic,
            "used_topics": list(self.used_topics),
            "used_item_ids": sorted(self.used_item_ids),
            "featured_count": {"1": self.featured_count[1], "2": self.featured_count[2]},
            "off_limits": {str(s): list(self.profiles[s].off_limits) if s in self.profiles else [] for s in (1, 2)},
            "allowed_formats": list(config.ALLOWED_FORMATS[self.mode]),
        }
        if plan.fact is not None:
            payload["verified_fact"] = {"text": plan.fact.text, "url": plan.fact.url}
        if convo.instruction:
            payload["instruction"] = convo.instruction
        return payload

    def _profile_text(self, seat: int) -> str:
        return "\n".join(
            f"[{i.id}] ({i.kind}, depth {i.depth}) {i.text}. Hook: {i.hook}" for i in self._allowed(seat)
        )

    def _overlap_text(self, index: int) -> str:
        ov = self.overlaps.overlaps[index]
        ids = ", ".join(ov.item_ids_seat1 + ov.item_ids_seat2)
        return f"[{ids}] {ov.kind}: {ov.summary}. Opener idea: {ov.opener}"

    # -- generation and the fallback chain (PRD 9.4) ---------------------------------------------

    async def _prefetch_one(self, key: str, convo: ConvoCtx) -> tuple[Crumb, bytes | None, float]:
        started = time.perf_counter()
        crumb = await self._build_within(self._plan(key, convo), convo, PREFETCH_BUDGET_S)  # type: ignore[arg-type]
        audio = await self._synth(crumb.text, PREFETCH_TTS_BUDGET_S)
        return crumb, audio, _ms_since(started)

    async def _build_within(self, plan: _Plan, convo: ConvoCtx, budget_s: float) -> Crumb:
        try:
            return await asyncio.wait_for(self._build(plan, convo), timeout=budget_s)
        except asyncio.TimeoutError:
            log.warning("crumb generation over budget (%.1f s); using fallback", budget_s)
            return self._fallback(plan.kind, convo)

    async def _build(self, plan: _Plan, convo: ConvoCtx) -> Crumb:
        """Generate, validate, regenerate once, then overlap opener, then canned (Appendix B)."""
        if plan.source == "outside" and "fun_fact" in plan.formats:
            plan.fact = await self._fact(plan.topic_hint)
            if plan.fact is None:
                plan.formats = [f for f in plan.formats if f != "fun_fact"] or ["question"]
                if plan.kind == "steer":
                    self._retarget_to_related_item(plan, convo)
        for attempt in range(2):
            try:
                crumb = await self._draft(plan, convo, attempt)
            except GeminiError as exc:
                log.warning("crumb generation failed (%s), attempt %d", type(exc).__name__, attempt + 1)
                continue
            ok, cleaned, errors = self._check(crumb, plan, convo)
            if ok:
                return cleaned
            # Log only the error categories: matched terms can come from profiles or the transcript.
            log.info("crumb rejected on attempt %d: %s", attempt + 1, "; ".join(e.split(":")[0] for e in errors))
        return self._fallback(plan.kind, convo)

    async def _fact(self, topic: str | None) -> Fact | None:
        try:
            return await asyncio.wait_for(get_fact(topic, self.location, gemini=self.gemini), FACT_BUDGET_S)
        except Exception as exc:  # noqa: BLE001 - no fact simply means "ask a question instead"
            log.warning("fact lookup failed (%s)", type(exc).__name__)
            return None

    async def _draft(self, plan: _Plan, convo: ConvoCtx, attempt: int) -> Crumb:
        if self.gemini.mock:
            return self._mock_crumb(plan, convo, attempt)
        draft = await self.gemini.json(
            model=config.GEMINI_MODEL,
            system=crumb_system(self.mode),
            payload=self._payload(plan, convo),
            schema=CrumbDraft,
            max_tokens=400,
        )
        return self._from_draft(draft, plan, convo)

    def _from_draft(self, draft: CrumbDraft, plan: _Plan, convo: ConvoCtx) -> Crumb:
        ids = list(draft.source_item_ids)
        if plan.source == "overlap" and plan.overlap_index is not None and not ids:
            ov = self.overlaps.overlaps[plan.overlap_index]
            ids = ov.item_ids_seat1 + ov.item_ids_seat2
        fmt = draft.format if draft.format in config.ALLOWED_FORMATS[self.mode] else "question"
        return self._crumb(
            plan, fmt, draft.text, draft.addressed_to, ids, draft.topic or plan.topic_hint or "open question",
            draft.depth, draft.why, convo,
        )

    def _crumb(
        self, plan: _Plan, fmt: str, text: str, addressed_to: str, ids: list[str], topic: str, depth: int,
        why: str, convo: ConvoCtx,
    ) -> Crumb:
        self._counter += 1
        fact = plan.fact if plan.source == "outside" else None
        return Crumb(
            id=f"c_{self._counter}",
            kind=plan.kind,
            format=fmt,  # type: ignore[arg-type]
            text=text,
            addressed_to=addressed_to,  # type: ignore[arg-type]
            source=plan.source,
            source_item_ids=ids,
            topic=topic,
            depth=max(1, min(int(depth or 1), max(1, convo.depth_max), 4)),
            why=why,
            citation_url=fact.url if fact else None,
            citation_title=fact.title if fact else None,
        )

    def _check(self, crumb: Crumb, plan: _Plan, convo: ConvoCtx) -> tuple[bool, Crumb, list[str]]:
        ok, cleaned, errors = validate_crumb(crumb, names=self.names, profiles=self.profiles,
                                             max_words=config.CRUMB_MAX_WORDS)
        allowed = self._allowed_ids()
        if any(i not in allowed for i in cleaned.source_item_ids):
            errors.append("item not allowed")
        if plan.kind != "steer":
            if set(cleaned.source_item_ids) & self.used_item_ids:
                errors.append("reuses a used item")
            if cleaned.topic.strip().lower() in self.used_topics:
                errors.append("reuses a used topic")
        if cleaned.format == "fun_fact" and (plan.source != "outside" or plan.fact is None):
            errors.append("fun fact without a verified source")
        if cleaned.format == "fun_fact" and "?" not in cleaned.text:
            errors.append("fun fact must end with a question")
        if cleaned.format not in config.ALLOWED_FORMATS[self.mode]:
            errors.append("format not allowed in this mode")
        return not errors, cleaned, errors

    def _fallback(self, kind: CrumbKind, convo: ConvoCtx) -> Crumb:
        crumb = self._overlap_crumb_validated(kind, convo, fallback=True)
        return crumb if crumb is not None else self._canned(kind, convo)

    def _overlap_crumb_validated(self, kind: CrumbKind, convo: ConvoCtx, fallback: bool = False) -> Crumb | None:
        # The opener may use any overlap (F11); later fallbacks still respect the depth cap.
        for index in self._unused_overlaps(convo.depth_max if fallback else None):
            ov = self.overlaps.overlaps[index]
            why = ("Fallback: generation failed validation twice, so the best unused overlap opener was used."
                   if fallback else f"Opener: the strongest {ov.kind} overlap between the two profiles.")
            plan = _Plan(kind, "overlap", ["question"], overlap_index=index)
            crumb = self._crumb(plan, "question", ov.opener, "both", ov.item_ids_seat1 + ov.item_ids_seat2,
                                _short_topic(ov.summary), self._overlap_depth(index), why, convo)
            ok, cleaned, _ = validate_crumb(crumb, names=self.names, profiles=self.profiles)
            if ok:
                return cleaned
        return None

    def _canned(self, kind: CrumbKind, convo: ConvoCtx) -> Crumb:
        """Appendix B, names filled; the first one not already delivered that passes validation."""
        spoken = {c.text for c in self.history}
        n1, n2 = self.names.get(1, "Seat 1"), self.names.get(2, "Seat 2")
        why = "Fallback: generation failed validation twice, so a safe canned crumb was used."
        candidates: list[Crumb] = []
        for index, (fmt, template) in enumerate(canned_crumbs(self.mode)):
            plan = _Plan(kind, "outside", ["question"])
            crumb = self._crumb(plan, fmt, template.format(name1=n1, name2=n2), "both", [],
                                f"canned {index + 1}", 1, why, convo)
            ok, cleaned, _ = validate_crumb(crumb, names=self.names, profiles=self.profiles)
            if ok and cleaned.text not in spoken:
                return cleaned
            candidates.append(cleaned)
        return candidates[-1]

    def _overlap_depth(self, index: int) -> int:
        ov = self.overlaps.overlaps[index]
        depth_of = {i.id: i.depth for p in self.profiles.values() for i in p.items}
        depths = [depth_of.get(i, 1) for i in ov.item_ids_seat1 + ov.item_ids_seat2]
        return max(depths or [1])

    # -- mock generation (deterministic templates that pass validate_crumb) -------------------------

    def _mock_crumb(self, plan: _Plan, convo: ConvoCtx, attempt: int) -> Crumb:
        if plan.source == "conversation":
            return self._mock_callback(plan, convo, attempt)
        if plan.source == "overlap" and plan.overlap_index is not None:
            return self._mock_overlap(plan, convo, attempt)
        if plan.source in ("profile_seat1", "profile_seat2") and plan.items:
            return self._mock_profile(plan, convo, attempt)
        return self._mock_outside(plan, convo, attempt)

    def _mock_callback(self, plan: _Plan, convo: ConvoCtx, attempt: int) -> Crumb:
        latest = self._latest_line(convo)
        if latest is None:
            return self._mock_outside(plan, convo, attempt)
        seat, line = latest
        speaker, other = self.names[seat], self.names[3 - seat]
        snippet = h.quote_snippet(line, 6)
        topic = convo.current_topic or h.short(" ".join(h.content_words(snippet)), 4) or "the last thing said"
        if attempt == 0 and snippet and "callback" in plan.formats:
            text = f'{other}, {speaker} just said "{snippet}". What do you want to ask about that?'
            fmt, why = "callback", f"Steer: building on {speaker}'s last words to keep the current topic going."
        else:
            text = f"{other}, what would you ask {speaker} about what they just said? Go for it."
            fmt, why = "question", f"Steer: handing {other} the follow-up on {speaker}'s last point."
        return self._crumb(plan, fmt, text, _seat_label(3 - seat), [], topic, 2, why, convo)

    def _mock_overlap(self, plan: _Plan, convo: ConvoCtx, attempt: int) -> Crumb:
        ov = self.overlaps.overlaps[plan.overlap_index or 0]
        n1, n2 = self.names[1], self.names[2]
        ids = ov.item_ids_seat1 + ov.item_ids_seat2
        fmt: str = plan.formats[0]
        if fmt == "collab" and attempt == 0:
            text = f"{n1} and {n2}, {_lower_first(_short_topic(ov.summary))}. What could you two build or trade around that? {n2} first."
        else:
            fmt, text = "question", ov.opener
        why = f"Switch: an unused {ov.kind} overlap, so both people get featured."
        return self._crumb(plan, fmt, text, "both", ids, _short_topic(ov.summary),
                           self._overlap_depth(plan.overlap_index or 0), why, convo)

    def _mock_profile(self, plan: _Plan, convo: ConvoCtx, attempt: int) -> Crumb:
        item = plan.items[0]
        owner_seat = plan.seat or int(plan.source[-1])
        owner, other = self.names[owner_seat], self.names[3 - owner_seat]
        hook = item.hook or f"What should {other} know about {h.topic_of(item.text)}?"
        if attempt == 0:
            text = f"{other}, here's one for {owner}: {hook}"
        else:
            text = f"{owner}, {_lower_first(hook)} {other}, you get the follow-up."
        if plan.kind == "steer":
            why = f"Steer: no transcript or verified fact, so a related item from {owner}'s profile."
        else:
            why = f"Switch: featuring {owner}, who has been featured less, with an item from their profile."
        topic = plan.topic_hint if plan.kind == "steer" and plan.topic_hint else h.short(h.topic_of(item.text), 6)
        return self._crumb(plan, "question", text, _seat_label(owner_seat), [item.id], topic, item.depth, why, convo)

    def _mock_outside(self, plan: _Plan, convo: ConvoCtx, attempt: int) -> Crumb:
        n1, n2 = self.names[1], self.names[2]
        if plan.fact is not None and "fun_fact" in plan.formats and attempt == 0:
            text = f"{plan.fact.text} {n1}, did you know that? {n2}, what would you add?"
            why = "Outside: a grounded fun fact with a source, ending in a question."
            return self._crumb(plan, "fun_fact", text, "both", [], plan.topic_hint or "outside: fun fact", 1, why, convo)
        if plan.kind == "steer" and plan.topic_hint:
            topic = h.short(plan.topic_hint, 5)
            text = f"{n1} and {n2}, staying on {topic}: what's the most surprising thing either of you knows about it? {n2} first."
            why = "Steer: no verified fact was available, so an open question on the current topic."
            return self._crumb(plan, "question", text, "both", [], plan.topic_hint, 1, why, convo)
        if "would_you_rather" in plan.formats:
            options = WOULD_YOU_RATHER
            fmt = "would_you_rather"
        else:
            options = OUTSIDE_QUESTIONS[self.mode]
            fmt = "question"
        fresh = [o for o in options if f"outside: {o[0]}" not in self.used_topics] or options
        key, template = fresh[attempt % len(fresh)]
        text = template.format(n1=n1, n2=n2, place=short_place(self.location))
        why = "Switch: a fresh outside topic; with no verified fact, it is a question, not a claim."
        return self._crumb(plan, fmt, text, "both", [], f"outside: {key}", 1, why, convo)

    # -- helpers ---------------------------------------------------------------------------------------

    def _latest_line(self, convo: ConvoCtx) -> tuple[int, str] | None:
        """(seat, text) of the latest transcript line spoken by a known participant."""
        for speaker, text in reversed(h.transcript_lines(convo.recent_transcript)):
            seat = self._seat_of(speaker)
            if seat is not None:
                return seat, text
        return None

    def _seat_of(self, speaker: str) -> int | None:
        spoken = speaker.strip().lower()
        for seat, name in self.names.items():
            name = (name or "").strip().lower()
            if name and (spoken == name or spoken == name.split()[0]):
                return seat
        return None

    def _entry_valid(self, entry: _CacheEntry, now: float, topic: str | None) -> bool:
        if now - entry.created_at > config.CACHE_MAX_AGE_S:
            return False
        if (entry.topic or None) != (topic or None):
            return False
        if set(entry.crumb.source_item_ids) & self.used_item_ids:
            return False
        if entry.crumb.kind != "steer" and entry.crumb.topic.strip().lower() in self.used_topics:
            return False
        return True

    def _key_valid(self, key: str, now: float, topic: str | None) -> bool:
        entry = self._cache.get(key)
        return entry is not None and self._entry_valid(entry, now, topic)

    async def _synth(self, text: str, budget_s: float) -> bytes | None:
        try:
            return await asyncio.wait_for(synthesize(text, self.gemini), timeout=budget_s)
        except Exception as exc:  # noqa: BLE001 - the Table falls back to speechSynthesis
            log.warning("tts skipped (%s)", type(exc).__name__)
            return None


def _cache_key(kind: CrumbKind) -> str | None:
    """Cache slot for `kind`; wildcards are generated fresh (they are not switches in the rotation)."""
    if kind == "wildcard":
        return None
    return "steer" if kind == "steer" else "switch"


def _profile_source(seat: int) -> CrumbSource:
    return "profile_seat1" if seat == 1 else "profile_seat2"


def _seat_label(seat: int) -> Literal["seat1", "seat2"]:
    return "seat1" if seat == 1 else "seat2"


def _short_topic(summary: str) -> str:
    return h.short(clean_text(summary), 8) or "shared ground"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


__all__ = ["ConvoCtx", "Delivery", "CrumbService", "CrumbDraft", "validate_crumb"]
