"""AI services in mock mode: profiles, overlaps, crumbs (rotation, depth, cache, fallbacks), classifiers, trail."""
from __future__ import annotations

import pytest

import config
from ai import crumbs as crumbs_mod
from ai.classifiers import classify_hold, tag_energy
from ai.crumbs import ConvoCtx, CrumbDraft, CrumbService, validate_crumb
from ai.facts import Fact, get_fact
from ai.gemini import Gemini, GeminiError
from ai.profiles import (
    ItemDraft,
    OverlapDraft,
    OverlapDraftSet,
    extract_profile,
    finalize_items,
    find_overlaps,
)
from ai.safety import EM_DASH, word_count
from ai.trail import generate_trail
from ai.tts import synthesize
from models import Crumb, Overlap, OverlapSet, Profile, ProfileItem

LOCATION = "Austin, Texas (HackTX at UT Austin)"
NAMES = {1: "Maya", 2: "Dev"}
MAYA_ANSWERS = [
    "I could talk for an hour about running. I'm training for my first half marathon and I love baking sourdough.",
    "My friends always make me tell the story about the time I got lost in Tokyo with no phone.",
    "I'm looking forward to my sister's wedding in December.",
]
DEV_ANSWERS = [
    "Rock climbing, honestly. I go bouldering three times a week.",
    "The story of how I accidentally joined a mariachi band in Mexico.",
    "I'm hoping to visit Japan next spring and I want to learn to cook ramen.",
]
TRANSCRIPT = "Maya: I just did a ten mile long run on Sunday.\nDev: Wait, ten miles? How do you not get bored?"


@pytest.fixture
def g() -> Gemini:
    return Gemini(mock=True)


def _item(id_: str, text: str, depth: int = 1, kind: str = "interest") -> ProfileItem:
    return ProfileItem(id=id_, kind=kind, text=text, hook=f"What do you love about {text}?", depth=depth)


def _profiles() -> dict[int, Profile]:
    return {
        1: Profile(seat=1, display_name="Maya", items=[
            _item("s1_i1", "running"), _item("s1_i2", "baking sourdough", 2), _item("s1_i3", "learning Japanese", 3),
        ]),
        2: Profile(seat=2, display_name="Dev", items=[
            _item("s2_i1", "rock climbing"), _item("s2_i2", "cooking ramen", 2), _item("s2_i3", "building robots", 3),
        ]),
    }


def _one_overlap() -> OverlapSet:
    return OverlapSet(overlaps=[Overlap(
        kind="adjacent", item_ids_seat1=["s1_i1"], item_ids_seat2=["s2_i1"], summary="Different takes on staying active",
        opener="Maya runs and Dev climbs. Dev, what would it take to get Maya on a wall?",
    )])


def _service(g: Gemini, *, mode: str = "social", overlaps: OverlapSet | None = None,
             profiles: dict[int, Profile] | None = None) -> CrumbService:
    return CrumbService(mode=mode, names=NAMES, profiles=profiles or _profiles(),
                        overlaps=overlaps if overlaps is not None else _one_overlap(), location=LOCATION, gemini=g)


def _ctx(transcript: str = "", topic: str | None = None, depth: int = 2, instruction: str | None = None) -> ConvoCtx:
    return ConvoCtx(recent_transcript=transcript, current_topic=topic, depth_max=depth, minutes_live=3.0,
                    instruction=instruction)


def _assert_valid(crumb: Crumb, svc: CrumbService) -> None:
    ok, _, errors = validate_crumb(crumb, names=svc.names, profiles=svc.profiles)
    assert ok, errors
    assert word_count(crumb.text) <= config.CRUMB_MAX_WORDS
    assert EM_DASH not in crumb.text
    assert crumb.format in config.ALLOWED_FORMATS[svc.mode]


# --- Profiles ------------------------------------------------------------------------------------


async def test_extract_profile_mock(g: Gemini):
    p = await extract_profile(1, "social", "Maya", MAYA_ANSWERS, gemini=g)
    assert p.seat == 1 and p.display_name == "Maya"
    assert 4 <= len(p.items) <= 10
    assert [i.id for i in p.items] == [f"s1_i{n}" for n in range(1, len(p.items) + 1)]
    texts = [i.text for i in p.items]
    assert "training for their first half marathon" in texts
    assert "story: got lost in Tokyo with no phone" in texts
    kinds = {i.text: i.kind for i in p.items}
    assert kinds["story: got lost in Tokyo with no phone"] == "story"
    assert kinds["training for their first half marathon"] == "goal"
    for item in p.items:
        assert item.hook.endswith("?") and len(item.hook.split()) <= 18
        assert len(item.text.split()) <= 12 and EM_DASH not in item.text + item.hook
        assert 1 <= item.depth <= 4


async def test_extract_profile_drops_sensitive_and_networking_kinds(g: Gemini):
    answers = [
        "I'm building a hardware startup. I'm in therapy for anxiety.",
        "I'm trying to learn how to raise a seed round. My salary is too low.",
        "I can help with embedded firmware and I'm hoping to meet product designers.",
    ]
    p = await extract_profile(2, "networking", "Dev", answers, gemini=g)
    texts = " | ".join(i.text for i in p.items)
    assert "therapy" not in texts and "salary" not in texts
    kinds = {i.kind for i in p.items}
    assert {"offer", "seeking"} <= kinds
    assert all(i.id.startswith("s2_i") for i in p.items)


def test_finalize_items_truncates_instead_of_failing():
    long_text = " ".join(["word"] * 40) + f" {EM_DASH} end"
    items = finalize_items(1, [ItemDraft(kind="interest", text=long_text, hook=" ".join(["why"] * 40), depth=9)])
    assert len(items) == 1
    assert len(items[0].text) <= 100 and len(items[0].text.split()) <= 12
    assert len(items[0].hook) <= 140 and items[0].depth == 4
    assert EM_DASH not in items[0].text


# --- Overlaps ---------------------------------------------------------------------------------------


async def test_find_overlaps_mock_kinds_and_openers(g: Gemini):
    p1 = Profile(seat=1, display_name="Maya", items=[
        _item("s1_i1", "loves running"), _item("s1_i2", "baking sourdough"), _item("s1_i3", "up at sunrise every morning"),
    ])
    p2 = Profile(seat=2, display_name="Dev", items=[
        _item("s2_i1", "running a weekly trail group"), _item("s2_i2", "cooking ramen"), _item("s2_i3", "a total night owl"),
    ])
    result = await find_overlaps(p1, p2, "social", gemini=g)
    kinds = [o.kind for o in result.overlaps]
    assert kinds == ["direct", "adjacent", "contrast"]  # best first
    direct = result.overlaps[0]
    assert direct.item_ids_seat1 == ["s1_i1"] and direct.item_ids_seat2 == ["s2_i1"]
    assert result.overlaps[1].item_ids_seat1 == ["s1_i2"] and result.overlaps[1].item_ids_seat2 == ["s2_i2"]
    for o in result.overlaps:
        assert word_count(o.opener) <= 28 and EM_DASH not in o.opener
        assert "Maya" in o.opener or "Dev" in o.opener


async def test_find_overlaps_respects_removed_and_off_limits(g: Gemini):
    p1 = Profile(seat=1, display_name="Maya", items=[_item("s1_i1", "loves running"), _item("s1_i2", "baking bread")],
                 removed_item_ids=["s1_i1"])
    p2 = Profile(seat=2, display_name="Dev", items=[_item("s2_i1", "running marathons"), _item("s2_i2", "baking pies")])
    result = await find_overlaps(p1, p2, "social", gemini=g)
    ids = {i for o in result.overlaps for i in o.item_ids_seat1}
    assert "s1_i1" not in ids and ids == {"s1_i2"}


async def test_find_overlaps_falls_back_to_closest_pair(g: Gemini):
    p1 = Profile(seat=1, display_name="Maya", items=[_item("s1_i1", "collects stamps", 2), _item("s1_i2", "knows sign language")])
    p2 = Profile(seat=2, display_name="Dev", items=[_item("s2_i1", "restores old radios")])
    result = await find_overlaps(p1, p2, "social", gemini=g)
    assert len(result.overlaps) == 1
    o = result.overlaps[0]
    assert o.kind in ("adjacent", "contrast") and o.item_ids_seat1 == ["s1_i2"] and o.item_ids_seat2 == ["s2_i1"]


async def test_find_overlaps_empty_profile_returns_empty(g: Gemini):
    p1 = Profile(seat=1, display_name="Maya", items=[])
    result = await find_overlaps(p1, _profiles()[2], "social", gemini=g)
    assert result.overlaps == []


class _OverlapGemini(Gemini):
    def __init__(self, drafts: OverlapDraftSet) -> None:
        super().__init__(mock=False, api_key="x", meta_api_key="")
        self.drafts = drafts

    async def json(self, **kwargs):  # type: ignore[override]
        return self.drafts


async def test_find_overlaps_real_path_drops_unknown_ids_and_bad_openers():
    p1, p2 = _profiles()[1], _profiles()[2]
    p1.removed_item_ids = ["s1_i3"]
    drafts = OverlapDraftSet(overlaps=[
        OverlapDraft(kind="contrast", item_ids_seat1=["s1_i2"], item_ids_seat2=["s2_i2"], summary="Bread vs ramen",
                     opener=f"Maya bakes {EM_DASH} Dev cooks ramen. Dev, which is harder?"),
        OverlapDraft(kind="direct", item_ids_seat1=["s1_i3"], item_ids_seat2=["s2_i3"], summary="removed",
                     opener="Maya, tell Dev about it."),
        OverlapDraft(kind="adjacent", item_ids_seat1=["s1_i9"], item_ids_seat2=["s2_i1"], summary="unknown",
                     opener="Maya, tell Dev about it."),
        OverlapDraft(kind="adjacent", item_ids_seat1=["s1_i1"], item_ids_seat2=["s2_i1"], summary="Active",
                     opener="No names here at all, so this opener is invalid and gets replaced."),
    ])
    result = await find_overlaps(p1, p2, "social", gemini=_OverlapGemini(drafts))
    assert [(o.kind, o.item_ids_seat1) for o in result.overlaps] == [("adjacent", ["s1_i1"]), ("contrast", ["s1_i2"])]
    assert all(("Maya" in o.opener or "Dev" in o.opener) and EM_DASH not in o.opener for o in result.overlaps)


# --- Crumbs: opener, sources, rotation, depth ----------------------------------------------------------


async def test_opener_uses_best_overlap_and_marks_it_used(g: Gemini):
    svc = _service(g)
    d = await svc.opener(_ctx())
    assert d.crumb.kind == "opener" and d.crumb.source == "overlap"
    assert d.crumb.text == _one_overlap().overlaps[0].opener
    assert d.crumb.id == "c_1" and d.audio_wav is None and not d.cache_hit
    assert svc.used_overlaps == {0} and {"s1_i1", "s2_i1"} <= svc.used_item_ids
    assert svc.featured_count == {1: 1, 2: 1}
    svc.mark_delivered(d.crumb)  # idempotent
    assert svc.featured_count == {1: 1, 2: 1} and len(svc.history) == 1
    _assert_valid(d.crumb, svc)


async def test_opener_without_overlaps_still_returns_valid_crumb(g: Gemini):
    svc = _service(g, overlaps=OverlapSet(overlaps=[]))
    d = await svc.opener(_ctx())
    assert d.crumb.kind == "opener"
    _assert_valid(d.crumb, svc)


async def test_steer_prefers_conversation_and_quotes_the_latest_line(g: Gemini):
    svc = _service(g)
    d = await svc.get("steer", _ctx(TRANSCRIPT, "long run training"), now=10.0)
    c = d.crumb
    assert c.kind == "steer" and c.source == "conversation" and c.format == "callback"
    assert c.addressed_to == "seat1"  # Dev spoke last, so Maya gets the follow-up
    assert '"How do you not get bored"' in c.text and c.text.startswith("Maya,")
    assert c.topic == "long run training"
    _assert_valid(c, svc)


async def test_steer_without_transcript_uses_outside_question_not_fact(g: Gemini):
    svc = _service(g)
    plan_source = svc.next_source("steer", _ctx("", "coffee shops"))
    assert plan_source == "outside"
    d = await svc.get("steer", _ctx("", "coffee shops"), now=1.0)
    assert d.crumb.source == "outside" and d.crumb.format == "question"
    assert d.crumb.citation_url is None and "coffee shops" in d.crumb.text
    _assert_valid(d.crumb, svc)


async def test_steer_without_fact_prefers_a_related_profile_item(g: Gemini):
    svc = _service(g)
    d = await svc.get("steer", _ctx("", "running"), now=1.0)
    assert d.crumb.source == "profile_seat1" and d.crumb.source_item_ids == ["s1_i1"]
    assert d.crumb.topic == "running"  # a steer stays on the current topic
    _assert_valid(d.crumb, svc)


async def test_steer_without_transcript_or_topic_uses_a_profile_item(g: Gemini):
    svc = _service(g)
    assert svc.next_source("steer", _ctx()) in ("profile_seat1", "profile_seat2")


async def test_switch_rotation_overlap_then_alternating_profile_and_outside(g: Gemini):
    svc = _service(g)
    ctx = _ctx(TRANSCRIPT, "running", depth=2)
    sources = []
    for step in range(6):
        d = await svc.get("switch", ctx, now=float(step))
        _assert_valid(d.crumb, svc)
        svc.mark_delivered(d.crumb)
        sources.append(d.crumb.source)
    assert sources[0] == "overlap"
    assert sources[1].startswith("profile") and sources[2] == "outside"
    assert sources[3].startswith("profile") and sources[4] == "outside"
    assert sources[1] != sources[3]  # the other person gets featured next
    assert len({c.topic for c in svc.history}) == len(svc.history)  # never repeat a topic
    item_ids = [i for c in svc.history for i in c.source_item_ids]
    assert len(item_ids) == len(set(item_ids))  # never reuse an item


async def test_featured_count_balance(g: Gemini):
    svc = _service(g, overlaps=OverlapSet(overlaps=[]))
    svc.featured_count = {1: 3, 2: 0}
    assert svc.next_source("switch", _ctx()) == "profile_seat2"
    svc.featured_count = {1: 0, 2: 2}
    assert svc.next_source("switch", _ctx()) == "profile_seat1"
    # On a tie, the seat featured last waits its turn.
    svc.featured_count = {1: 1, 2: 1}
    svc._last_profile_seat = 1
    assert svc.next_source("switch", _ctx()) == "profile_seat2"


async def test_depth_is_respected(g: Gemini):
    svc = _service(g, overlaps=OverlapSet(overlaps=[]))
    ctx = _ctx(depth=1)
    for step in range(6):
        d = await svc.get("switch", ctx, now=float(step))
        svc.mark_delivered(d.crumb)
        assert d.crumb.depth <= 1
    used = svc.used_item_ids
    assert used == {"s1_i1", "s2_i1"}  # depth-2 and depth-3 items wait for the ramp
    deeper = await svc.get("switch", _ctx(depth=3), now=10.0)
    assert deeper.crumb.source.startswith("profile") and deeper.crumb.depth == 3


async def test_deep_overlap_waits_for_depth(g: Gemini):
    deep = OverlapSet(overlaps=[Overlap(kind="direct", item_ids_seat1=["s1_i3"], item_ids_seat2=["s2_i3"],
                                        summary="Both building things", opener="Maya and Dev, you both build. Dev, go.")])
    svc = _service(g, overlaps=deep)
    assert svc.next_source("switch", _ctx(depth=1)) != "overlap"
    assert svc.next_source("switch", _ctx(depth=3)) == "overlap"


@pytest.mark.parametrize("mode", ["social", "networking", "speed_dating"])
async def test_formats_follow_mode(g: Gemini, mode: str):
    svc = _service(g, mode=mode)
    for kind in ("switch", "wildcard", "manual", "steer", "switch", "wildcard"):
        d = await svc.get(kind, _ctx(TRANSCRIPT if kind == "steer" else "", None), now=0.0)
        assert d.crumb.format in config.ALLOWED_FORMATS[mode]
        assert d.crumb.kind == kind
        svc.mark_delivered(d.crumb)


async def test_networking_overlap_uses_collab(g: Gemini):
    svc = _service(g, mode="networking")
    d = await svc.get("switch", _ctx(), now=0.0)
    assert d.crumb.source == "overlap" and d.crumb.format == "collab"
    _assert_valid(d.crumb, svc)


async def test_payload_matches_prd_9_4_and_hides_removed_items(g: Gemini):
    profiles = _profiles()
    profiles[1].removed_item_ids = ["s1_i2"]
    profiles[2].off_limits = ["robots"]
    svc = _service(g, profiles=profiles)
    svc.used_topics.append("where you're from")
    payload = svc.crumb_payload("switch", _ctx(TRANSCRIPT, "running"))
    assert set(payload) == {
        "mode", "kind", "requested_source", "current_depth_max", "names", "profiles", "overlaps_unused",
        "recent_transcript", "current_topic", "used_topics", "used_item_ids", "featured_count", "off_limits",
        "allowed_formats", "requested_item_ids",
    }
    assert payload["requested_item_ids"] == ["s1_i1", "s2_i1"]  # the planned overlap's items
    assert payload["names"] == {"1": "Maya", "2": "Dev"}
    assert payload["requested_source"] == "overlap" and payload["kind"] == "switch"
    assert "s1_i2" not in payload["profiles"]["1"] and "sourdough" not in payload["profiles"]["1"]
    assert "robots" not in payload["profiles"]["2"]
    assert payload["off_limits"] == {"1": [], "2": ["robots"]}
    assert payload["featured_count"] == {"1": 0, "2": 0}
    assert payload["allowed_formats"] == config.ALLOWED_FORMATS["social"]
    with_instruction = svc.crumb_payload("switch", _ctx(instruction="one minute left in the round"))
    assert with_instruction["instruction"] == "one minute left in the round"


# --- Cache ------------------------------------------------------------------------------------------------


async def test_prefetch_then_cache_hit(g: Gemini):
    svc = _service(g)
    ctx = _ctx(TRANSCRIPT, "running")
    await svc.prefetch(ctx, now=100.0)
    summary = svc.cached_summary(100.0)
    assert summary["steer"] and summary["switch"]
    d = await svc.get("steer", ctx, now=110.0)
    assert d.cache_hit and d.crumb.text == summary["steer"] and d.gen_ms < 50
    manual = await svc.get("manual", ctx, now=111.0)
    assert manual.cache_hit and manual.crumb.kind == "manual" and manual.crumb.text == summary["switch"]
    assert svc.cached_summary(111.0) == {"steer": None, "switch": None}  # consumed


async def test_cache_invalid_when_old(g: Gemini):
    svc = _service(g)
    ctx = _ctx(TRANSCRIPT, "running")
    await svc.prefetch(ctx, now=0.0)
    assert svc.cached_summary(config.CACHE_MAX_AGE_S + 1) == {"steer": None, "switch": None}
    d = await svc.get("steer", ctx, now=config.CACHE_MAX_AGE_S + 1)
    assert not d.cache_hit


async def test_cache_invalid_when_topic_changes(g: Gemini):
    """A steer built on the topic label is invalid after a real change of subject (no shared word stem), not
    after a reworded label. A steer built on the latest lines follows the lines instead, and a switch (a
    change of subject itself) survives a topic change that is not its own subject."""
    svc = _service(g)
    await svc.prefetch(_ctx("", "running"), now=0.0)  # no transcript: a steer built on the topic label
    assert svc._cache["steer"].crumb.source != "conversation"
    assert (await svc.get("steer", _ctx("", "long runs"), now=5.0)).cache_hit  # same stem: still valid
    await svc.prefetch(_ctx("", "running"), now=6.0)
    assert not (await svc.get("steer", _ctx("", "ramen"), now=7.0)).cache_hit
    assert (await svc.get("switch", _ctx("", "ramen"), now=8.0)).cache_hit
    await svc.prefetch(_ctx(TRANSCRIPT, "running"), now=9.0)
    assert svc._cache["steer"].crumb.source == "conversation"
    assert (await svc.get("steer", _ctx(TRANSCRIPT, "ramen"), now=10.0)).cache_hit


async def test_cache_invalid_when_its_item_is_used(g: Gemini):
    svc = _service(g)
    ctx = _ctx(TRANSCRIPT, "running")
    await svc.prefetch(ctx, now=0.0)
    cached_switch = svc._cache["switch"].crumb
    assert cached_switch.source_item_ids  # the overlap crumb
    other = cached_switch.model_copy(update={"id": "c_99", "kind": "manual"})
    svc.mark_delivered(other)
    assert svc.cached_summary(1.0)["switch"] is None
    d = await svc.get("switch", ctx, now=1.0)
    assert not d.cache_hit and d.crumb.source != "overlap"


async def test_invalidate_and_refill(g: Gemini):
    svc = _service(g)
    ctx = _ctx(TRANSCRIPT, "running")
    await svc.prefetch(ctx, now=0.0)
    svc.invalidate("speakers changed")
    assert svc.cached_summary(0.0) == {"steer": None, "switch": None}
    await svc.prefetch(ctx, now=1.0)
    assert svc.cached_summary(1.0)["steer"] is not None


async def test_instruction_uses_cache_with_spoken_lead_in(g: Gemini):
    svc = _service(g)
    await svc.prefetch(_ctx(TRANSCRIPT, "running"), now=0.0)
    cached = svc._cache["switch"].crumb.text
    d = await svc.get("switch", _ctx(TRANSCRIPT, "running", instruction="one minute left"), now=1.0)
    assert d.cache_hit and d.crumb.text == f"One minute left. {cached}"


# --- Fallback chain ----------------------------------------------------------------------------------------


def _bad_crumb(svc: CrumbService, kind: str = "switch") -> Crumb:
    return Crumb(id="c_bad", kind=kind, format="question", text="no names and " + "far too many words " * 10,
                 addressed_to="both", source="outside", topic="x", depth=1, why="bad")


async def test_fallback_to_overlap_opener_after_two_failures(g: Gemini, monkeypatch):
    svc = _service(g)
    calls = []

    async def bad_draft(plan, convo, attempt):
        calls.append(attempt)
        return _bad_crumb(svc)

    monkeypatch.setattr(svc, "_draft", bad_draft)
    d = await svc.get("steer", _ctx(TRANSCRIPT, "running"), now=0.0)
    assert calls == [0, 1]  # regenerate exactly once
    assert d.crumb.source == "overlap" and d.crumb.text == _one_overlap().overlaps[0].opener
    assert d.crumb.why.startswith("Fallback")


async def test_fallback_to_canned_when_no_overlap_left(g: Gemini, monkeypatch):
    svc = _service(g, overlaps=OverlapSet(overlaps=[]), mode="networking")

    async def bad_draft(plan, convo, attempt):
        return _bad_crumb(svc)

    monkeypatch.setattr(svc, "_draft", bad_draft)
    first = await svc.get("switch", _ctx(), now=0.0)
    assert first.crumb.text == "Dev, what's a problem you'd love help with right now? Maya, any ideas?"
    svc.mark_delivered(first.crumb)
    second = await svc.get("switch", _ctx(), now=1.0)
    assert second.crumb.text != first.crumb.text and "{" not in second.crumb.text
    _assert_valid(second.crumb, svc)


async def test_off_limits_quote_triggers_regeneration_without_quote(g: Gemini):
    profiles = _profiles()
    profiles[2].off_limits = ["work"]
    svc = _service(g, profiles=profiles)
    transcript = "Maya: So what do you do?\nDev: Honestly my job at the bank is pretty boring."
    d = await svc.get("steer", _ctx(transcript, "the bank"), now=0.0)
    assert "job" not in d.crumb.text.lower() and d.crumb.source == "conversation"
    assert d.crumb.format == "question"  # the quoting callback was rejected, the plain follow-up passed
    _assert_valid(d.crumb, svc)
    # An off-limits current topic is never steered into: the crumb falls back off-topic.
    away = await svc.get("steer", _ctx(transcript, "jobs"), now=1.0)
    assert away.crumb.source != "conversation" and "job" not in away.crumb.text.lower()


class _FakeGemini(Gemini):
    """Non-mock Gemini with scripted json/grounded results (no network)."""

    def __init__(self, drafts: list[CrumbDraft | Exception], grounded: tuple[str, str | None, str | None]) -> None:
        super().__init__(mock=False, api_key="x", meta_api_key="")
        self.drafts = list(drafts)
        self.grounded_result = grounded
        self.payloads: list[dict] = []

    async def json(self, *, payload, **kwargs):  # type: ignore[override]
        self.payloads.append(payload)
        result = self.drafts.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def grounded(self, prompt: str):  # type: ignore[override]
        return self.grounded_result

    async def tts(self, text: str):  # type: ignore[override]
        return None


def _fact_draft(fmt: str = "fun_fact") -> CrumbDraft:
    return CrumbDraft(format=fmt, text="Austin has the largest urban bat colony in North America. Dev, ever seen it? Maya, you too?",
                      addressed_to="both", source_item_ids=[], topic="austin bats", depth=1, why="A local fact.")


async def test_outside_fun_fact_is_grounded_and_cited():
    fake = _FakeGemini([_fact_draft()], ("Austin has a huge bat colony.", "https://example.org/bats", "Bats of Austin"))
    svc = _service(fake, overlaps=OverlapSet(overlaps=[]))
    svc._last_alt = "profile"  # next switch source is outside
    await svc.voice("switch", _ctx(), now=0.0, lead_s=5.0)  # generates the switch; the search runs off the live path
    d = await svc.get("switch", _ctx(), now=1.0)
    assert d.cache_hit and d.crumb.source == "outside" and d.crumb.format == "fun_fact"
    assert d.crumb.citation_url == "https://example.org/bats" and d.crumb.citation_title == "Bats of Austin"
    assert fake.payloads[0]["verified_fact"] == {"text": "Austin has a huge bat colony.", "url": "https://example.org/bats"}


async def test_ungrounded_fact_is_never_used_as_a_fact():
    question = CrumbDraft(format="question", text="Maya, what's the strangest wildlife you've seen in a city? Dev, you next.",
                          addressed_to="both", topic="city wildlife", depth=1, why="No fact, so a question.")
    fake = _FakeGemini([_fact_draft(), question], ("Some claim.", None, None))
    svc = _service(fake, overlaps=OverlapSet(overlaps=[]))
    svc._last_alt = "profile"
    d = await svc.get("switch", _ctx(), now=0.0)
    assert "verified_fact" not in fake.payloads[0]
    assert d.crumb.format == "question" and d.crumb.citation_url is None  # the fun_fact draft was rejected
    assert len(fake.payloads) == 2


async def test_real_path_generation_errors_fall_back():
    fake = _FakeGemini([GeminiError("boom"), GeminiError("boom")], ("", None, None))
    svc = _service(fake)
    d = await svc.get("steer", _ctx(TRANSCRIPT, "running"), now=0.0)
    assert d.crumb.source == "overlap" and not d.cache_hit


async def test_get_fact_and_tts_mock_return_none(g: Gemini):
    assert await get_fact("bats", LOCATION, gemini=g) is None
    assert await synthesize("Maya, hi!", gemini=g) is None


async def test_get_fact_requires_citation():
    fake = _FakeGemini([], ("A fact.", None, None))
    assert await get_fact("bats", LOCATION, gemini=fake) is None
    fake.grounded_result = (f"Bats eat bugs {EM_DASH} lots of them. Second sentence.", "https://x.org", None)
    fact = await get_fact("bats", LOCATION, gemini=fake)
    assert isinstance(fact, Fact) and fact.text == "Bats eat bugs, lots of them." and fact.title == "https://x.org"


async def test_miss_budget_falls_back(g: Gemini, monkeypatch):
    svc = _service(g)
    monkeypatch.setattr(crumbs_mod, "MISS_BUDGET_S", 0.05)

    async def slow_draft(plan, convo, attempt):
        import asyncio
        await asyncio.sleep(1)
        raise AssertionError("should have timed out")

    monkeypatch.setattr(svc, "_draft", slow_draft)
    d = await svc.get("switch", _ctx(), now=0.0)
    _assert_valid(d.crumb, svc)


# --- Classifiers ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("Wait, let me show you.", "show"),
        ("hold on, where is it", "show"),
        ("Hmm, good question.", "think"),
        ("Let me pull it up real quick.", "show"),
        ("sorry I have to take this.", "external"),
    ],
)
async def test_classify_hold_mock_positives(g: Gemini, line: str, kind: str):
    d = await classify_hold(f"Dev: so what happened?\nMaya: {line}", gemini=g)
    assert d.hold and d.kind == kind and 0 < d.expected_seconds <= config.HOLD_MAX_S[kind]


@pytest.mark.parametrize(
    "line",
    ["Wait, really? That's insane, how did you even get in?", "I can't wait for the trip.", "That was a great trip."],
)
async def test_classify_hold_mock_negatives(g: Gemini, line: str):
    d = await classify_hold(f"Maya: {line}", gemini=g)
    assert not d.hold and d.kind == "none"


async def test_tag_energy_mock(g: Gemini):
    tag = await tag_energy(TRANSCRIPT, gemini=g)
    assert 1 <= len(tag.topic.split()) <= 5
    assert "mile" in tag.topic
    assert tag.energy in ("high", "medium", "low")
    low = await tag_energy("Maya: yeah\nDev: cool\nMaya: totally\nDev: nice", gemini=g)
    assert low.energy == "low" and low.winding_down


# --- Trail ------------------------------------------------------------------------------------------------------


async def test_trail_mock(g: Gemini):
    p1 = await extract_profile(1, "social", "Maya", MAYA_ANSWERS, gemini=g)
    p2 = await extract_profile(2, "social", "Dev", DEV_ANSWERS, gemini=g)
    p2.removed_item_ids = ["s2_i1"]
    p1.off_limits = ["family"]
    transcript = TRANSCRIPT + "\nDev: I'm going to try a climbing gym next week."
    trail = await generate_trail("social", NAMES, transcript, {1: p1, 2: p2}, gemini=g)
    for seat in (1, 2):
        mine = [t for t in trail.items if t.for_seat == seat]
        assert 1 <= len(mine) <= 3
        other = NAMES[3 - seat]
        assert all(t.text.startswith(f"Ask {other}") for t in mine)
    texts = " ".join(t.text for t in trail.items)
    assert "Ask Dev how trying a climbing gym next week went." in texts
    assert "sister" not in texts  # off-limits family item
    assert "rock climbing" not in texts.lower().replace("climbing gym", "")  # removed item
    assert EM_DASH not in texts


async def test_trail_empty_inputs(g: Gemini):
    trail = await generate_trail("social", NAMES, "", {1: Profile(seat=1, display_name="Maya", items=[]),
                                                         2: Profile(seat=2, display_name="Dev", items=[])}, gemini=g)
    assert trail.items == []


# --- Full mock session per mode ------------------------------------------------------------------------------

MODE_ANSWERS = {
    "social": (MAYA_ANSWERS, DEV_ANSWERS),
    "networking": (
        ["I'm working on a robotics startup that sorts recycling.", "I'm trying to learn how to raise a seed round.",
         "I can help with embedded firmware and I'm hoping to meet product designers."],
        ["I'm building a design tool for teachers.", "I'm trying to figure out pricing.",
         "I can help with user research and I'm looking for a technical cofounder."],
    ),
    "speed_dating": (
        ["A perfect Saturday is a long run, tacos, and a movie.", "Fresh tortillas make me unreasonably happy.",
         "People are surprised I can juggle."],
        ["My perfect Saturday is a farmers market and a nap.", "A really good cup of coffee makes me happy.",
         "I used to compete in chess tournaments."],
    ),
}


@pytest.mark.parametrize("mode", ["social", "networking", "speed_dating"])
async def test_full_mock_session(g: Gemini, mode: str):
    a1, a2 = MODE_ANSWERS[mode]
    p1 = await extract_profile(1, mode, "Maya", a1, gemini=g)
    p2 = await extract_profile(2, mode, "Dev", a2, gemini=g)
    overlaps = await find_overlaps(p1, p2, mode, gemini=g)
    assert overlaps.overlaps
    svc = CrumbService(mode=mode, names=NAMES, profiles={1: p1, 2: p2}, overlaps=overlaps, location=LOCATION, gemini=g)
    opener = await svc.opener(_ctx(depth=1))
    _assert_valid(opener.crumb, svc)
    kinds = ["steer", "switch", "manual", "steer", "switch", "wildcard", "switch", "switch", "steer", "switch"]
    for step, kind in enumerate(kinds):
        minutes = step * 1.5
        depth = min(config.MAX_DEPTH[mode], 1 + int(minutes // config.RAMP_MINUTES[mode]))
        ctx = ConvoCtx(TRANSCRIPT, "long run training", depth, minutes)
        await svc.prefetch(ctx, now=step * 20.0)
        d = await svc.get(kind, ctx, now=step * 20.0 + 1)
        _assert_valid(d.crumb, svc)
        assert d.crumb.depth <= depth and d.crumb.kind == kind
        svc.mark_delivered(d.crumb)
    ids = [c.id for c in svc.history]
    assert len(ids) == len(set(ids)) and all(i.startswith("c_") for i in ids)
    switch_topics = [c.topic for c in svc.history if c.kind != "steer"]
    assert len(switch_topics) == len(set(switch_topics))
    trail = await generate_trail(mode, NAMES, TRANSCRIPT, {1: p1, 2: p2}, gemini=g)
    assert all(sum(t.for_seat == s for t in trail.items) <= 3 for s in (1, 2))
