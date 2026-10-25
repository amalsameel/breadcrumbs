"""Regression tests for the AI review findings (depth cap, retries, source, safety, round warning, trail,
steer freshness, miss budget, heuristic phrasing). Every test fails on the pre-fix code."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

import config
from ai import crumbs as crumbs_mod
from ai import heuristics as h
from ai.crumbs import ConvoCtx, CrumbDraft, CrumbService, validate_crumb
from ai.gemini import Gemini
from ai.profiles import allowed_items, extract_profile, finalize_items, find_overlaps, mock_profile_items
from ai.safety import expand_off_limits, find_keyword, is_sensitive, word_count
from ai.trail import generate_trail
from models import Overlap, OverlapSet, Profile, ProfileItem
from muse.transcribe import MOCK_ANSWERS

NAMES = {1: "Maya", 2: "Dev"}
LOCATION = "Austin, Texas"


class _Status(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class _ScriptedGemini(Gemini):
    """Non-mock Gemini that returns scripted crumb drafts and records every payload (no network)."""

    def __init__(self, drafts: list[CrumbDraft]) -> None:
        super().__init__(mock=False, api_key="x", meta_api_key="")
        self.drafts = list(drafts)
        self.payloads: list[dict] = []

    async def json(self, *, payload, **kwargs):  # type: ignore[override]
        self.payloads.append(payload)
        return self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]

    async def grounded(self, prompt: str):  # type: ignore[override]
        return "", None, None

    async def tts(self, text: str):  # type: ignore[override]
        return None


def _item(id_: str, text: str, depth: int = 1, kind: str = "interest") -> ProfileItem:
    return ProfileItem(id=id_, kind=kind, text=text, hook=f"What do you love about {text}?", depth=depth)


def _profiles() -> dict[int, Profile]:
    return {
        1: Profile(seat=1, display_name="Maya", items=[
            _item("s1_i1", "training for a first half marathon"),
            _item("s1_i2", "wishes they had reconciled with an old friend", 4, "value"),
        ]),
        2: Profile(seat=2, display_name="Dev", items=[_item("s2_i1", "bouldering"), _item("s2_i2", "cooking ramen")]),
    }


def _draft(ids: list[str], depth: int, text: str = "Dev, ask Maya about this one. Maya, what would you say now?") -> CrumbDraft:
    return CrumbDraft(format="question", text=text, addressed_to="both", source_item_ids=ids,
                      topic=f"topic {'-'.join(ids)} {depth}", depth=depth, why="Scripted.")


def _svc(gemini: Gemini, mode: str = "networking", overlaps: OverlapSet | None = None,
         profiles: dict[int, Profile] | None = None) -> CrumbService:
    return CrumbService(mode=mode, names=NAMES, profiles=profiles or _profiles(),
                        overlaps=overlaps or OverlapSet(overlaps=[]), location=LOCATION, gemini=gemini)


def _ctx(transcript: str = "", topic: str | None = None, depth: int = 1, instruction: str | None = None) -> ConvoCtx:
    return ConvoCtx(recent_transcript=transcript, current_topic=topic, depth_max=depth, minutes_live=0.5,
                    instruction=instruction)


# --- 1. Depth ladder on model output (PRD 8.9) ----------------------------------------------------------


async def test_over_depth_draft_is_rejected_and_never_relabeled():
    fake = _ScriptedGemini([_draft(["s1_i2"], 4)])  # the model ignores the cap every time
    svc = _svc(fake)
    d = await svc.get("switch", _ctx(depth=1), now=0.0)
    assert "s1_i2" not in d.crumb.source_item_ids
    assert d.crumb.why.startswith("Fallback")  # both attempts were rejected
    assert d.crumb.depth <= 1


async def test_payload_leaves_out_items_and_overlaps_over_the_cap():
    deep = OverlapSet(overlaps=[Overlap(kind="direct", item_ids_seat1=["s1_i2"], item_ids_seat2=["s2_i2"],
                                        summary="Both old friends", opener="Maya and Dev, old friends. Dev, go.")])
    svc = _svc(Gemini(mock=True), overlaps=deep)
    payload = svc.crumb_payload("switch", _ctx(depth=1))
    assert "s1_i2" not in payload["profiles"]["1"] and "s1_i1" in payload["profiles"]["1"]
    assert payload["overlaps_unused"] == []
    assert "s1_i2" in svc.crumb_payload("switch", _ctx(depth=4))["profiles"]["1"]


async def test_reported_depth_is_the_real_depth():
    svc = _svc(Gemini(mock=True))
    plan = svc._plan("switch", _ctx(depth=1))
    crumb = svc._crumb(plan, "question", "Maya, go.", "both", ["s1_i2"], "x", 4, "why", _ctx(depth=1))
    assert crumb.depth == 4
    ok, _, errors = svc._check(crumb, plan, _ctx(depth=1))
    assert not ok and "over the depth cap" in errors


# --- 2. Retry stacking and the Muse Spark fallback on a 429 (PRD 11.3, 11.4) -----------------------------


class Small(BaseModel):
    name: str


async def test_rate_limit_goes_straight_to_spark():
    fake = SimpleNamespace(calls=[])

    async def create(**kwargs: Any) -> Any:
        fake.calls.append(kwargs)
        raise _Status(429)

    client = SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=create)))
    g = Gemini(mock=False, api_key="k", meta_api_key="meta", client=client, backoff_s=(5.0,))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"name": "spark"}'}}]})

    g._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    started = time.monotonic()
    result = await g.json(model="m", system="s", payload="p", schema=Small)
    assert result.name == "spark" and len(fake.calls) == 1  # no Gemini retry, no backoff sleep
    assert time.monotonic() - started < 1.0
    await g.aclose()


async def test_sdk_does_not_retry_inside_our_attempts(monkeypatch):
    """Through the real google-genai client: one request per attempt (was 4, so 12 per call)."""
    sent: list[dict] = []

    async def fake_send(self, request, **kwargs):  # noqa: ANN001
        sent.append(json.loads(request.content or b"{}"))
        return httpx.Response(429, json={"error": {"code": 429, "message": "rate", "status": "RESOURCE_EXHAUSTED"}},
                              request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    g = Gemini(mock=False, api_key="fake-key", meta_api_key="", backoff_s=(0.0,))
    started = time.monotonic()
    with pytest.raises(Exception):
        await g.json(model=config.GEMINI_MODEL, system="s", payload="p", schema=Small)
    assert len(sent) == 1 + g.retries
    assert time.monotonic() - started < 2.0
    assert all(body.get("store") is False for body in sent)


# --- 5. store=False on every Interactions call (PRD 15.1) ------------------------------------------------


async def test_every_interactions_call_sets_store_false():
    calls: list[dict] = []

    async def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(output_text="hello", steps=[], output_audio=None)

    client = SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=create)))
    g = Gemini(mock=False, api_key="k", meta_api_key="", client=client)
    await g.text(model="m", input="x")
    await g.grounded("x")
    await g.tts("Maya, go.")
    assert len(calls) >= 3 and all(call["store"] is False for call in calls)


# --- 3. The crumb source matches the items used (PRD 8.9 featured_count) ---------------------------------


async def test_draft_citing_the_other_seat_is_rejected():
    fake = _ScriptedGemini([_draft(["s1_i1"], 1)])  # asked for Dev's item, wrote about Maya's
    svc = _svc(fake)
    svc.featured_count = {1: 1, 2: 0}
    assert svc.next_source("switch", _ctx()) == "profile_seat2"
    d = await svc.get("switch", _ctx(), now=0.0)
    assert not (d.crumb.source == "profile_seat2" and "s1_i1" in d.crumb.source_item_ids)
    svc.mark_delivered(d.crumb)
    assert "s1_i1" not in svc.used_item_ids
    assert fake.payloads[0]["requested_item_ids"] == ["s2_i1"]


async def test_draft_without_ids_is_credited_to_the_planned_item():
    fake = _ScriptedGemini([_draft([], 1, "Maya, ask Dev about bouldering. Dev, what got you started?")])
    svc = _svc(fake)
    svc.featured_count = {1: 1, 2: 0}
    d = await svc.get("switch", _ctx(), now=0.0)
    assert d.crumb.source == "profile_seat2" and d.crumb.source_item_ids == ["s2_i1"]


# --- 4. Injuries are sensitive (PRD 9.2, 9.9) -------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "stopped running after a knee injury", "tore my ACL last year", "used to run a lot until they hurt their knee",
    "broke my wrist skateboarding", "recovering from a concussion", "sprained my ankle on a hike",
])
def test_injuries_are_sensitive(text: str):
    assert is_sensitive(text)


@pytest.mark.parametrize("text", [
    "wants to become a doctor", "broke my phone screen", "loves the back of the bookstore", "hurt my feelings",
])
def test_injury_check_has_no_everyday_false_positives(text: str):
    assert not is_sensitive(text)


def test_demo_answers_drop_the_knee_injury():
    answers = [MOCK_ANSWERS[f"social:q{i}:seat2"] for i in (1, 2, 3)]
    items = finalize_items(2, mock_profile_items("social", answers))
    joined = " | ".join(f"{i.text} {i.hook}" for i in items).lower()
    assert "knee" not in joined and "hurt" not in joined
    assert any("climbing" in i.text for i in items)


def test_health_toggle_blocks_hurt():
    assert find_keyword("they hurt their knee", expand_off_limits(["health"]))


# --- 6. Off-limits variants (Principle 3, 8.8) ------------------------------------------------------------


@pytest.mark.parametrize("text", ["a product designer at a startup", "a backend engineer at a small startup",
                                  "founder of a booking company", "freelance developer"])
def test_work_toggle_covers_occupations(text: str):
    assert find_keyword(text, expand_off_limits(["work"]))


def test_free_text_terms_match_word_forms():
    keywords = expand_off_limits(["japan, tokyo"])
    assert find_keyword("did not speak any Japanese", keywords)
    assert find_keyword("a Tokyoite at heart", keywords)
    assert find_keyword("loves japan", keywords)
    assert not find_keyword("loves the jam session", keywords)
    assert "japan" in keywords  # literal terms are still listed as given


async def test_networking_opener_never_announces_an_off_limits_job():
    g = Gemini(mock=True)
    ps = {s: await extract_profile(s, "networking", NAMES[s], [MOCK_ANSWERS[f"networking:q{i}:seat{s}"] for i in (1, 2, 3)],
                                   gemini=g) for s in (1, 2)}
    ps[1] = ps[1].model_copy(update={"off_limits": ["work"]})
    assert all("designer" not in i.text for i in allowed_items(ps[1]))
    overlaps = await find_overlaps(ps[1], ps[2], "networking", gemini=g)
    svc = CrumbService(mode="networking", names=NAMES, profiles=ps, overlaps=overlaps, location=LOCATION, gemini=g)
    opener = await svc.opener(_ctx())
    assert "designer" not in opener.crumb.text and "engineer" not in opener.crumb.text


# --- 7. The 1-minute round warning (PRD 4.2, F21) ---------------------------------------------------------

WARNING = "one minute left in the round"


async def test_round_warning_mentions_the_time_and_keeps_the_prefetched_switch():
    svc = _svc(Gemini(mock=True), mode="speed_dating")
    ctx = _ctx(topic=None, depth=2)
    await svc.prefetch(ctx, now=0.0)
    cached = svc._cache["switch"].crumb.text
    d = await svc.get("switch", _ctx(depth=2, instruction=WARNING), now=1.0)
    assert d.crumb.text.startswith("One minute left.") and d.cache_hit
    assert d.crumb.text.endswith(cached)
    assert word_count(d.crumb.text) <= config.CRUMB_MAX_WORDS
    ok, _, errors = validate_crumb(d.crumb, names=svc.names, profiles=svc.profiles)
    assert ok, errors


async def test_round_warning_on_a_miss_and_with_a_cached_crumb_too_long_to_carry_it():
    svc = _svc(Gemini(mock=True), mode="speed_dating")
    d = await svc.get("switch", _ctx(depth=2, instruction=WARNING), now=0.0)
    assert d.crumb.text.startswith("One minute left.") and not d.cache_hit
    svc.mark_delivered(d.crumb)
    await svc.prefetch(_ctx(depth=2), now=10.0)
    long_text = "Maya, " + " ".join(["word"] * 25) + "?"
    svc._cache["switch"].crumb = svc._cache["switch"].crumb.model_copy(update={"text": long_text})
    d2 = await svc.get("switch", _ctx(depth=2, instruction=WARNING), now=11.0)
    assert d2.crumb.text.startswith("One minute left.") and word_count(d2.crumb.text) <= config.CRUMB_MAX_WORDS
    assert svc._cache["switch"].crumb.text == long_text  # kept for the next switch instead of thrown away


# --- 9. The trail never turns distress or sensitive statements into threads (PRD 15.3, 9.9) ---------------


@pytest.mark.parametrize("line", [
    "I'm going to hurt myself tonight.", "I'm planning to end it all next week.",
    "I'm about to lose my job and my visa.",
])
async def test_trail_drops_distress_and_sensitive_plans(line: str):
    profiles = {1: Profile(seat=1, display_name="Maya", items=[_item("s1_i1", "running")]),
                2: Profile(seat=2, display_name="Dev", items=[_item("s2_i1", "cooking")])}
    trail = await generate_trail("social", NAMES, f"Maya: that sounds fun.\nDev: {line}", profiles, gemini=Gemini(mock=True))
    texts = " ".join(t.text for t in trail.items).lower()
    for word in ("hurt", "end", "visa", "job", "losing"):
        assert word not in texts.split() and f"{word}ing" not in texts
    assert "Ask Dev more about cooking." in [t.text for t in trail.items]


async def test_trail_drops_sensitive_model_threads_and_respects_the_safety_pause():
    class _TrailGemini(Gemini):
        def __init__(self) -> None:
            super().__init__(mock=False, api_key="x", meta_api_key="")
            self.payload: dict | None = None

        async def json(self, *, payload, schema, **kwargs):  # type: ignore[override]
            self.payload = payload
            return schema(items=[{"for_seat": 1, "text": "Ask Dev how the visa interview went."},
                                 {"for_seat": 1, "text": "Ask Dev how the climbing trip went."}])

    fake = _TrailGemini()
    profiles = {1: Profile(seat=1, display_name="Maya", items=[]), 2: Profile(seat=2, display_name="Dev", items=[])}
    transcript = "Dev: I'm going to climb in Japan.\nDev: I want to die."
    trail = await generate_trail("social", NAMES, transcript, profiles, gemini=fake)
    assert [t.text for t in trail.items] == ["Ask Dev how the climbing trip went."]
    assert "die" not in fake.payload["transcript"] and "climb in Japan" in fake.payload["transcript"]
    paused = await generate_trail("social", NAMES, transcript, profiles, gemini=fake, safety_paused=True)
    assert fake.payload["transcript"] == "" and paused.items


# --- 10. The cached steer follows the latest turn (PRD 8.10, F14) -------------------------------------------


async def test_cached_steer_is_rebuilt_after_a_new_turn():
    svc = _svc(Gemini(mock=True), mode="social")
    t1 = "Maya: I ran ten miles on Sunday.\nDev: Ten miles, wow."
    t2 = t1 + "\nMaya: Next I want to try a trail race in the hills."
    await svc.prefetch(_ctx(t1, "running", 2), now=0.0)
    first = svc._cache["steer"].crumb.text
    assert "Ten miles, wow" in first or "wow" in first.lower()
    stale = await svc.get("steer", _ctx(t2, "running", 2), now=1.0)
    assert not stale.cache_hit and "Next I want" in stale.crumb.text
    await svc.prefetch(_ctx(t1, "running", 2), now=2.0)
    await svc.prefetch(_ctx(t2, "running", 2), now=3.0)  # a committed turn refreshes the steer
    assert "Next I want" in svc._cache["steer"].crumb.text
    hit = await svc.get("steer", _ctx(t2, "running", 2), now=4.0)
    assert hit.cache_hit


# --- 11. A cache miss reaches the Table in about 3 s (PRD 8.10, F8) ------------------------------------------


def test_miss_budgets_fit_three_seconds():
    assert crumbs_mod.MISS_BUDGET_S + crumbs_mod.MISS_TTS_BUDGET_S <= 3.0
    assert crumbs_mod.MISS_FACT_BUDGET_S < crumbs_mod.MISS_BUDGET_S


async def test_slow_generation_and_tts_still_deliver_within_three_seconds(monkeypatch):
    svc = _svc(Gemini(mock=True))

    async def slow_build(plan, convo):  # noqa: ANN001
        await asyncio.sleep(100)

    async def slow_tts(text, gemini=None):  # noqa: ANN001
        await asyncio.sleep(100)

    monkeypatch.setattr(svc, "_build", slow_build)
    monkeypatch.setattr(crumbs_mod, "synthesize", slow_tts)
    started = time.monotonic()
    d = await svc.get("switch", _ctx(), now=0.0)
    assert time.monotonic() - started < 3.3
    assert d.audio_wav is None and d.crumb.text


# --- 8. Heuristic phrasing (F11 opener, 9.4 "write for the ear") ------------------------------------------


@pytest.mark.parametrize(("item", "topic"), [
    ("used to run a lot until they hurt their knee", "running a lot until they hurt their knee"),
    ("did not speak any Japanese", "not speaking any Japanese"),
    ("can build almost anything", "being able to build almost anything"),
    ("still has the napkin", "still having the napkin"),
])
def test_topic_of_handles_helper_verbs(item: str, topic: str):
    assert h.topic_of(item) == topic


@pytest.mark.parametrize(("text", "n", "out"), [
    ("used to run a lot until they hurt their knee", 8, "used to run a lot"),
    ("a backend engineer at a small startup that builds booking software", 6, "a backend engineer"),
    ("a long run in the morning, then the farmers market", 8, "a long run in the morning"),
])
def test_short_ends_on_a_whole_phrase(text: str, n: int, out: str):
    assert h.short(text, n) == out


GARBLED = ("useding", "doing not", "being it", "stills", "both mentioned figure", "How is it is", "at a small.",
           "until they hurt.", ", then and")


@pytest.mark.parametrize("mode", ["social", "networking", "speed_dating"])
async def test_mock_demo_session_speaks_clean_lines(mode: str):
    g = Gemini(mock=True)
    ps = {s: await extract_profile(s, mode, NAMES[s], [MOCK_ANSWERS[f"{mode}:q{i}:seat{s}"] for i in (1, 2, 3)], gemini=g)
          for s in (1, 2)}
    overlaps = await find_overlaps(ps[1], ps[2], mode, gemini=g)
    svc = CrumbService(mode=mode, names=NAMES, profiles=ps, overlaps=overlaps, location=LOCATION, gemini=g)
    spoken = [(await svc.opener(_ctx())).crumb.text]
    for step in range(10):
        d = await svc.get("switch", _ctx(depth=config.MAX_DEPTH[mode]), now=step * 100.0)
        svc.mark_delivered(d.crumb)
        spoken.append(d.crumb.text)
    spoken += [o.opener for o in overlaps.overlaps]
    for line in spoken:
        assert not any(bad in line for bad in GARBLED), line


# --- 9. Heuristic phrasing, second pass (F11 opener, 9.4 "write for the ear") ----------------------------


@pytest.mark.parametrize(("kind", "core", "hook"), [
    ("story", "getting halfway up, froze, and a ten year old kid on the next route talked you through it",
     "What's the story with getting halfway up?"),
    ("preference", "when a houseplant you almost gave up on puts out a new leaf",
     "What makes it so good when a houseplant you almost gave up on puts out a new leaf?"),
    ("story", "your battery died in Shinjuku", "Your battery died in Shinjuku. What's the story there?"),
    ("fun_fact", "your Japanese mostly stops there", "Your Japanese mostly stops there. What's the story?"),
    ("goal", "reading about user interviews and trying to run your first few", "How is reading about user interviews going so far?"),
    ("preference", "a board game at the end that gets way too competitive", "What makes a board game at the end so good?"),
])
def test_hooks_ask_about_one_whole_phrase(kind: str, core: str, hook: str):
    assert h.make_hook(kind, core) == hook


@pytest.mark.parametrize(("clause", "kind", "text", "hook"), [
    ("I did not speak any Japanese", "story", "did not speak any Japanese",
     "What's the story with not speaking any Japanese?"),
    ("I can still order ramen like a local", "fun_fact", "can still order ramen like a local",
     "What's the story behind still ordering ramen like a local?"),
    ("I love that every route is basically a puzzle you solve", "interest",
     "loves that every route is basically a puzzle you solve",
     "Why do you love that every route is basically a puzzle you solve?"),
    ("I am weirdly into running playlists", "interest", "into running playlists",
     "What do you love most about running playlists?"),
    ("I am a product designer", "interest", "is a product designer",
     "What do you love most about being a product designer?"),
    ("Ideally there is a board game at the end", "preference", "a board game at the end",
     "What makes a board game at the end so good?"),
])
def test_clause_rewrites_read_aloud(clause: str, kind: str, text: str, hook: str):
    r = h.rewrite_clause(clause)
    assert r.text == text
    assert h.make_hook(r.kind or kind, r.core, r.hook_style) == hook


@pytest.mark.parametrize(("item", "topic"), [
    ("hoping to meet an engineer", "meeting an engineer"),
    ("can help with user research", "helping with user research"),
    ("looking forward to the race", "the race"),
    ("is a product designer", "being a product designer"),
])
def test_topic_of_keeps_the_action(item: str, topic: str):
    assert h.topic_of(item) == topic


def test_mock_items_end_on_a_whole_phrase():
    for mode in ("social", "networking", "speed_dating"):
        for seat in (1, 2):
            answers = [MOCK_ANSWERS[f"{mode}:q{i}:seat{seat}"] for i in (1, 2, 3)]
            for draft in mock_profile_items(mode, answers):
                words = draft.text.split()
                assert len(words) <= 12 and words[-1].lower().strip(",") not in h._DANGLING, draft.text


async def test_overlap_label_is_said_as_written_and_heard_in_both_topics():
    g = Gemini(mock=True)
    ps = {s: await extract_profile(s, "social", NAMES[s], [MOCK_ANSWERS[f"social:q{i}:seat{s}"] for i in (1, 2, 3)], gemini=g)
          for s in (1, 2)}
    ps[1] = ps[1].model_copy(update={"removed_item_ids": ["s1_i2"]})  # drop the "spring" pair: Tokyo leads
    openers = [o.opener for o in (await find_overlaps(ps[1], ps[2], "social", gemini=g)).overlaps]
    assert any("You both brought up Tokyo." in o for o in openers), openers
    ps = {s: await extract_profile(s, "social", NAMES[s], [MOCK_ANSWERS[f"social:q{i}:seat{s}"] for i in (1, 2, 3)], gemini=g)
          for s in (1, 2)}
    spring = next(o for o in (await find_overlaps(ps[1], ps[2], "social", gemini=g)).overlaps if "spring" in o.summary)
    assert spring.opener.startswith("Maya mentioned starting with couch to 5K last spring and Dev mentioned going to Japan in the spring.")


GARBLED_2 = ("stilling", "halfway up, froze", "about that every", "puts out?", "brought up tokyo", "brought up user.",
             "brought up designer", "mentioned a product designer", "mentioned an engineer", "with your battery died",
             "What makes ideally", "about being weirdly", "trying to run going", "and Dev mentioned APIs, databases")


@pytest.mark.parametrize("mode", ["social", "networking", "speed_dating"])
async def test_mock_hooks_and_openers_read_aloud(mode: str):
    g = Gemini(mock=True)
    ps = {s: await extract_profile(s, mode, NAMES[s], [MOCK_ANSWERS[f"{mode}:q{i}:seat{s}"] for i in (1, 2, 3)], gemini=g)
          for s in (1, 2)}
    overlaps = await find_overlaps(ps[1], ps[2], mode, gemini=g)
    spoken = [i.hook for p in ps.values() for i in p.items] + [o.opener for o in overlaps.overlaps]
    for line in spoken:
        assert not any(bad in line for bad in GARBLED_2 + GARBLED), line


# --- 10. Wipe leaves no profile text in process-wide regex caches (F20, 15.1) ------------------------------


def _regex_memory_mentions(secret: str) -> list[str]:
    import gc
    import re
    hits = [o.pattern for o in gc.get_objects() if isinstance(o, re.Pattern) and secret in str(o.pattern).lower()]
    for attr in ("_cache", "_cache2"):
        cache = getattr(re, attr, None)
        if isinstance(cache, dict):
            hits += [str(k) for k in cache if secret in str(k).lower()]
    return hits


def test_off_limits_terms_and_names_are_not_compiled_into_cached_regexes():
    from ai.safety import name_present, validate_crumb_text

    term, name = "zqxsecretterm", "Zqxalice Smith"
    profiles = {1: Profile(seat=1, display_name=name, items=[_item("s1_i1", "loves climbing")], off_limits=[term]),
                2: Profile(seat=2, display_name="Dev", items=[_item("s2_i1", "loves bouldering")])}
    names = {1: name, 2: "Dev"}
    ok, _, errors = validate_crumb_text("Zqxalice, the zqxsecretterms story? Dev, go.", names=names, profiles=profiles)
    assert not ok and any(term in e for e in errors)
    assert name_present("hi zqxalice smith", names) and not name_present("hi zqxalicexx", {1: name})
    assert allowed_items(profiles[1])
    assert _regex_memory_mentions(term) == [] and _regex_memory_mentions("zqxalice") == []


@pytest.mark.parametrize(("text", "keyword", "hit"), [
    ("we got laid\n  off last year", "laid off", True),
    ("my injuries healed", "injury", True),
    ("the Japanese menu", "japan*", True),
    ("a nonjob thing", "job", False),
    ("jobless", "job", False),
    ("engineer2 here", "engineer", False),
])
def test_keyword_matching_keeps_word_boundaries_and_word_forms(text: str, keyword: str, hit: bool):
    assert (find_keyword(text, [keyword]) == keyword) is hit
