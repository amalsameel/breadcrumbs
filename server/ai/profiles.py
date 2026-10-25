"""Profile extraction (PRD 9.2), allowed items (F4, principle 3) and the overlap finder (9.3, F11)."""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

import config
from ai import heuristics as h
from ai.gemini import Gemini, GeminiError, get_gemini
from ai.prompts import overlap_system, profile_system
from ai.safety import (
    clean_text,
    expand_off_limits,
    find_keyword,
    is_sensitive,
    is_unsafe,
    truncate_words,
    validate_crumb_text,
)
from models import Mode, Overlap, OverlapSet, Profile, ProfileItem

log = logging.getLogger("breadcrumbs.ai.profiles")

ItemKind = Literal["interest", "story", "goal", "fun_fact", "value", "preference", "offer", "seeking"]

MAX_ITEMS = 10
MAX_OVERLAPS = 6
ITEM_MAX_WORDS, ITEM_MAX_CHARS = 12, 100
HOOK_MAX_WORDS, HOOK_MAX_CHARS = 18, 140
VIBE_MAX_CHARS = 80
SUMMARY_MAX_CHARS = 140
_KIND_RANK = {"direct": 0, "adjacent": 1, "contrast": 2}
# Words too generic to count as a "direct" overlap on their own.
_GENERIC_STEMS = {h.stem(w) for w in (
    "first", "new", "good", "best", "big", "little", "long", "great", "fun", "hour", "hours", "talk",
    "could", "story", "friends", "friend", "tell", "help", "hoping", "meet", "looking", "trying",
    "wants", "place", "places", "home", "together", "next", "last", "around", "everyone", "whole",
    "perfect", "happy", "really", "unreasonably", "surprised", "people", "makes", "saturday", "weekend",
)}


# --- Draft schemas (what the model writes; ids and seat are server-owned) ----------------------


class ItemDraft(BaseModel):
    kind: ItemKind
    text: str
    hook: str
    depth: int


class ProfileDraft(BaseModel):
    items: list[ItemDraft]
    vibe: str = ""


class OverlapDraft(BaseModel):
    kind: Literal["direct", "adjacent", "contrast"]
    item_ids_seat1: list[str]
    item_ids_seat2: list[str]
    summary: str
    opener: str


class OverlapDraftSet(BaseModel):
    overlaps: list[OverlapDraft]


# --- Profile extraction ---------------------------------------------------------------------


async def extract_profile(
    seat: int, mode: Mode, display_name: str, answers: list[str], gemini: Gemini | None = None
) -> Profile:
    """Turn 1 to 3 transcribed answers into a reviewed-ready Profile (ids s{seat}_i{n})."""
    g = gemini or get_gemini()
    drafts: list[ItemDraft] = []
    vibe = ""
    if not g.mock:
        try:
            draft = await g.json(
                model=config.GEMINI_MODEL,
                system=profile_system(mode),
                payload={"mode": mode, "display_name": display_name, "answers": answers},
                schema=ProfileDraft,
                max_tokens=900,
            )
            drafts, vibe = draft.items, draft.vibe
        except GeminiError as exc:
            log.warning("profile extraction failed (%s); using heuristic extraction", type(exc).__name__)
    if not drafts:
        drafts = mock_profile_items(mode, answers)
    return Profile(
        seat=seat,  # type: ignore[arg-type]
        display_name=display_name,
        items=finalize_items(seat, drafts),
        vibe=truncate_words(clean_text(vibe), 12, VIBE_MAX_CHARS),
    )


def finalize_items(seat: int, drafts: list[ItemDraft]) -> list[ProfileItem]:
    """Clean, truncate, drop sensitive/unsafe/duplicate items and assign ids."""
    items: list[ProfileItem] = []
    seen: set[str] = set()
    for draft in drafts:
        text = truncate_words(clean_text(draft.text).rstrip("."), ITEM_MAX_WORDS, ITEM_MAX_CHARS)
        hook = truncate_words(clean_text(draft.hook), HOOK_MAX_WORDS, HOOK_MAX_CHARS)
        if not text or text.lower() in seen:
            continue
        if is_sensitive(f"{text} {hook}") or is_unsafe(f"{text} {hook}"):
            continue
        if hook and not hook.endswith("?"):
            hook = hook.rstrip(".!") + "?"
        seen.add(text.lower())
        items.append(ProfileItem(
            id=f"s{seat}_i{len(items) + 1}",
            kind=draft.kind,
            text=text,
            hook=hook or h.make_hook(draft.kind, h.topic_of(text)),
            depth=min(4, max(1, int(draft.depth))),
        ))
        if len(items) >= MAX_ITEMS:
            break
    return items


def mock_profile_items(mode: Mode, answers: list[str]) -> list[ItemDraft]:
    """Heuristic extraction: clauses -> third-person phrases with kind guesses and templated hooks."""
    defaults = h.QUESTION_KINDS[mode]
    drafts: list[ItemDraft] = []
    for index, answer in enumerate(answers):
        default_kind = defaults[min(index, len(defaults) - 1)]
        for clause in h.split_clauses(answer):
            rewrite = h.rewrite_clause(clause)
            kind = rewrite.kind or h.guess_kind(clause, default_kind)
            if mode != "networking" and kind in ("offer", "seeking"):
                kind = "goal" if kind == "seeking" else "interest"
            drafts.append(ItemDraft(
                kind=kind,  # type: ignore[arg-type]
                text=rewrite.text,
                hook=h.make_hook(kind, rewrite.core, rewrite.hook_style, HOOK_MAX_WORDS),
                depth=h.KIND_DEPTH.get(kind, 1),
            ))
    return drafts


# --- Allowed items ----------------------------------------------------------------------------


def allowed_items(profile: Profile) -> list[ProfileItem]:
    """Items minus removed ids and anything matching the profile's off-limits terms."""
    removed = set(profile.removed_item_ids)
    keywords = expand_off_limits(profile.off_limits)
    return [
        item for item in profile.items
        if item.id not in removed and find_keyword(f"{item.text} {item.hook}", keywords) is None
    ]


# --- Overlap finder -----------------------------------------------------------------------------


async def find_overlaps(p1: Profile, p2: Profile, mode: Mode, gemini: Gemini | None = None) -> OverlapSet:
    """Up to 6 validated overlaps, best first. Falls back to the closest adjacent/contrast pair (F11)."""
    g = gemini or get_gemini()
    a1, a2 = allowed_items(p1), allowed_items(p2)
    if not a1 or not a2:
        return OverlapSet(overlaps=[])
    names = {1: p1.display_name, 2: p2.display_name}
    profiles = {1: p1, 2: p2}
    overlaps: list[Overlap] = []
    if not g.mock:
        try:
            drafts = await g.json(
                model=config.GEMINI_MODEL,
                system=overlap_system(mode),
                payload=_overlap_payload(mode, names, a1, a2),
                schema=OverlapDraftSet,
                max_tokens=900,
            )
            overlaps = _validate_overlaps(drafts.overlaps, a1, a2, names, profiles)
        except GeminiError as exc:
            log.warning("overlap finder failed (%s); using heuristic overlaps", type(exc).__name__)
    if not overlaps:
        overlaps = mock_overlaps(a1, a2, names, profiles)
    overlaps.sort(key=lambda o: _KIND_RANK[o.kind])
    return OverlapSet(overlaps=overlaps[:MAX_OVERLAPS])


def _overlap_payload(mode: Mode, names: dict[int, str], a1: list[ProfileItem], a2: list[ProfileItem]) -> dict:
    def items(xs: list[ProfileItem]) -> list[dict]:
        return [{"id": x.id, "kind": x.kind, "text": x.text} for x in xs]

    return {"mode": mode, "names": {"1": names[1], "2": names[2]}, "profiles": {"1": items(a1), "2": items(a2)}}


def _validate_overlaps(
    drafts: list[OverlapDraft],
    a1: list[ProfileItem],
    a2: list[ProfileItem],
    names: dict[int, str],
    profiles: dict[int, Profile],
) -> list[Overlap]:
    ids1, ids2 = {x.id for x in a1}, {x.id for x in a2}
    by_id = {x.id: x for x in [*a1, *a2]}
    out: list[Overlap] = []
    for draft in drafts:
        s1 = [i for i in draft.item_ids_seat1 if i in ids1]
        s2 = [i for i in draft.item_ids_seat2 if i in ids2]
        # Any unknown/removed id means the model looked at something it should not reference.
        if not s1 or not s2 or len(s1) != len(draft.item_ids_seat1) or len(s2) != len(draft.item_ids_seat2):
            continue
        ok, opener, _ = validate_crumb_text(draft.opener, names=names, profiles=profiles)
        if not ok:
            opener = _template_opener(draft.kind, by_id[s1[0]], by_id[s2[0]], names, profiles, None)
            if opener is None:
                continue
        out.append(Overlap(
            kind=draft.kind,
            item_ids_seat1=s1,
            item_ids_seat2=s2,
            summary=truncate_words(clean_text(draft.summary), 24, SUMMARY_MAX_CHARS),
            opener=opener,
        ))
    return out


def mock_overlaps(
    a1: list[ProfileItem], a2: list[ProfileItem], names: dict[int, str], profiles: dict[int, Profile]
) -> list[Overlap]:
    """Keyword + concept-family matching: direct (shared word), adjacent (same family), contrast."""
    candidates: list[tuple[int, int, Overlap]] = []
    for i, x in enumerate(a1):
        for j, y in enumerate(a2):
            overlap = _pair_overlap(x, y, names, profiles)
            if overlap is not None:
                candidates.append((i, j, overlap))
    candidates.sort(key=lambda c: (_KIND_RANK[c[2].kind], c[0], c[1]))
    used1: set[int] = set()
    used2: set[int] = set()
    out: list[Overlap] = []
    for i, j, overlap in candidates:
        if i in used1 or j in used2:
            continue
        used1.add(i)
        used2.add(j)
        out.append(overlap)
    if not out:
        fallback = _closest_pair(a1, a2, names, profiles)
        if fallback is not None:
            out.append(fallback)
    return out[:MAX_OVERLAPS]


def _pair_overlap(
    x: ProfileItem, y: ProfileItem, names: dict[int, str], profiles: dict[int, Profile]
) -> Overlap | None:
    tx, ty = f"{x.text}", f"{y.text}"
    shared = sorted(h.stems(tx) & h.stems(ty) - _GENERIC_STEMS - {h.stem(n) for n in names.values()})
    if shared:
        word = _surface_word(tx, shared[0])
        summary = f"Both mentioned {word}"
        return _build(x, y, "direct", summary, names, profiles, word)
    common = sorted(h.families_of(tx) & h.families_of(ty))
    if common:
        label = h.family_label(common[0])
        return _build(x, y, "adjacent", f"Different takes on {label}", names, profiles, label)
    for index, pair in enumerate(h.CONTRAST_PAIRS):
        sx, sy = h.contrast_side(tx, index), h.contrast_side(ty, index)
        if sx and sy and sx != sy:
            label_x = pair[3] if sx == "a" else pair[4]
            label_y = pair[3] if sy == "a" else pair[4]
            summary = f"{names[1]} sounds like {label_x}, {names[2]} like {label_y}"
            return _build(x, y, "contrast", summary, names, profiles, None)
    return None


def _surface_word(text: str, stem_value: str) -> str:
    for word in h.content_words(text):
        if h.stem(word) == stem_value:
            return word
    return stem_value


def _build(
    x: ProfileItem, y: ProfileItem, kind: str, summary: str,
    names: dict[int, str], profiles: dict[int, Profile], label: str | None,
) -> Overlap | None:
    opener = _template_opener(kind, x, y, names, profiles, label)
    if opener is None:
        return None
    return Overlap(kind=kind, item_ids_seat1=[x.id], item_ids_seat2=[y.id],  # type: ignore[arg-type]
                   summary=truncate_words(clean_text(summary), 24, SUMMARY_MAX_CHARS), opener=opener)


def _template_opener(
    kind: str, x: ProfileItem, y: ProfileItem, names: dict[int, str], profiles: dict[int, Profile],
    label: str | None,
) -> str | None:
    """A validated opener addressed by name, shrinking the item phrases until it fits 28 words."""
    n1, n2 = names[1], names[2]
    attempts: list[str] = []
    for max_words in (8, 6):
        t1, t2 = h.short(h.topic_of(x.text), max_words), h.short(h.topic_of(y.text), max_words)
        if kind == "direct" and label:
            attempts.append(f"{n1} mentioned {t1} and {n2} mentioned {t2}. You both brought up {label}. "
                            f"{n2}, what's your version?")
        elif kind == "adjacent" and label:
            attempts.append(f"{n1} mentioned {t1} and {n2} mentioned {t2}, both about {label}. "
                            f"{n2}, how did you get into yours?")
        elif kind == "contrast":
            attempts.append(f"{n1} mentioned {t1}, {n2} mentioned {t2}. Total opposites. {n2}, make the case for your side.")
        else:
            attempts.append(f"{n1} mentioned {t1} and {n2} mentioned {t2}. {n2}, what should {n1} know about yours?")
    if label and kind == "direct":
        attempts.append(f"{n1} and {n2}, you both brought up {label}. {n2}, what's your version of it? {n1}, you're next.")
    elif label:
        attempts.append(f"{n1} and {n2}, you both have a {label} side. {n2}, how did you get into yours?")
    attempts.append(f"{n1} and {n2}, you each brought something fun to the table. {n2}, you start.")
    for text in attempts:
        ok, cleaned, _ = validate_crumb_text(text, names=names, profiles=profiles)
        if ok:
            return cleaned
    return None


def _closest_pair(
    a1: list[ProfileItem], a2: list[ProfileItem], names: dict[int, str], profiles: dict[int, Profile]
) -> Overlap | None:
    """F11 fallback when nothing matches: the two lightest items as an adjacent pair."""
    x = min(a1, key=lambda i: i.depth)
    y = min(a2, key=lambda i: i.depth)
    summary = f"{names[1]} and {names[2]} bring different interests to swap"
    return _build(x, y, "adjacent", summary, names, profiles, None)
