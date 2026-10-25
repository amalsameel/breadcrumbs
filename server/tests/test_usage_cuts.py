"""Usage cuts (BUILD_LOG "usage:implement"): every billed call that a crumb, hold or tag cannot use is skipped,
and crumbs still arrive on time. Hermetic: fake synthesizers and mock text, no network."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import ai.crumbs as crumbs_mod
import ai.tts as tts_mod
import engine.runner as runner_mod
from ai.crumbs import CrumbService
from ai.facts import Fact
from ai.gemini import Gemini
from config import Params
from tests.test_ai_mock import NAMES, TRANSCRIPT, _ctx, _service
from tests.test_policy import LINES, calibrated, of_type, speak, ticks


class _Voice:
    """A fake `crumbs.synthesize`: records (fast,) per call; natural takes can be held back by a gate."""

    def __init__(self, natural_gate: asyncio.Event | None = None, fast_delay_s: float = 0.0) -> None:
        self.calls: list[bool] = []
        self.natural_gate = natural_gate
        self.fast_delay_s = fast_delay_s

    async def __call__(self, text, gemini=None, *, fast=True, budget_s=None):  # noqa: ANN001
        self.calls.append(fast)
        if not fast and self.natural_gate is not None:
            await self.natural_gate.wait()
        if fast and self.fast_delay_s:
            await asyncio.sleep(self.fast_delay_s)
        return b"FAST" if fast else b"NATURAL"


def _count_generations(svc: CrumbService) -> list[str]:
    made: list[str] = []
    original = svc._prefetch_one

    async def counted(key, convo):  # noqa: ANN001
        made.append(key)
        return await original(key, convo)

    svc._prefetch_one = counted  # type: ignore[method-assign]
    return made


# --- 1. lazy voicing: one crumb, once, at the warming band -------------------------------------------


async def test_prefetch_makes_text_only_and_voice_voices_the_next_crumb_once(monkeypatch):
    voice = _Voice()
    monkeypatch.setattr(crumbs_mod, "synthesize", voice)
    svc = _service(Gemini(mock=True))
    ctx = _ctx(TRANSCRIPT, "running")
    await svc.prefetch(ctx, now=0.0)
    assert voice.calls == [] and svc.cached_summary(0.0)["steer"] and svc.cached_summary(0.0)["switch"]
    await svc.voice("steer", ctx, now=1.0, lead_s=2.0)  # 2 s ahead: the natural model
    await svc.voice("steer", ctx, now=1.5, lead_s=1.5)  # the same line again: nothing
    assert voice.calls == [False]
    hit = await svc.get("steer", ctx, now=3.0)
    assert hit.cache_hit and hit.audio_wav == b"NATURAL" and voice.calls == [False]
    # The switch was never voiced; a Drop a crumb voices it on the spot with the fast model.
    manual = await svc.get("manual", ctx, now=4.0)
    assert manual.cache_hit and manual.audio_wav == b"FAST" and voice.calls == [False, True]


async def test_voice_with_a_short_lead_uses_the_fast_model_and_delivery_waits_for_it(monkeypatch):
    voice = _Voice(fast_delay_s=0.05)
    monkeypatch.setattr(crumbs_mod, "synthesize", voice)
    svc = _service(Gemini(mock=True))
    ctx = _ctx(TRANSCRIPT, "running")
    await svc.prefetch(ctx, now=0.0)
    voicing = asyncio.ensure_future(svc.voice("steer", ctx, now=1.0, lead_s=crumbs_mod.NATURAL_VOICE_MIN_LEAD_S - 0.5))
    await asyncio.sleep(0.01)
    hit = await svc.get("steer", ctx, now=1.2)  # fires while the fast take is in flight: joins it
    await voicing
    assert hit.audio_wav == b"FAST" and voice.calls == [True]


async def test_late_natural_voice_is_dropped_for_the_fast_model(monkeypatch):
    gate = asyncio.Event()  # the natural take never finishes on its own
    voice = _Voice(natural_gate=gate)
    monkeypatch.setattr(crumbs_mod, "synthesize", voice)
    svc = _service(Gemini(mock=True))
    ctx = _ctx(TRANSCRIPT, "running")
    await svc.prefetch(ctx, now=0.0)
    voicing = asyncio.ensure_future(svc.voice("steer", ctx, now=1.0, lead_s=2.0))
    await asyncio.sleep(0.01)
    text = svc._cache["steer"].crumb.text
    assert text in svc._voice_tasks and voice.calls == [False]
    hit = await svc.get("steer", ctx, now=3.0)  # the crumb fires before the natural take is back
    await asyncio.wait_for(voicing, 1.0)
    assert hit.cache_hit and hit.audio_wav == b"FAST" and voice.calls == [False, True]
    assert (text, False) not in svc._audio and text not in svc._voice_tasks  # the late take was cancelled


async def test_a_natural_take_that_was_late_once_is_not_chosen_again(monkeypatch):
    # End to end on the real cancel path, no hand-written samples: the natural take never lands within
    # its lead, the crumb fires at the end of that lead (Flash on the spot), and the next voicing with the
    # same lead picks Flash at once instead of paying for a second late natural take.
    lead = 0.25
    monkeypatch.setattr(crumbs_mod, "NATURAL_VOICE_MIN_LEAD_S", 0.2)
    gate = asyncio.Event()  # the natural model is slower than the lead here
    voice = _Voice(natural_gate=gate)
    monkeypatch.setattr(crumbs_mod, "synthesize", voice)
    svc = _service(Gemini(mock=True))
    ctx = _ctx(TRANSCRIPT, "running")
    await svc.prefetch(ctx, now=0.0)
    assert svc._natural_lead_s() <= lead  # no history: the lead is enough for the natural model
    voicing = asyncio.ensure_future(svc.voice("steer", ctx, now=1.0, lead_s=lead))
    await asyncio.sleep(lead)  # the crumb is due: the natural take is still out
    first = await svc.get("steer", ctx, now=1.0 + lead)
    await asyncio.wait_for(voicing, 1.0)
    assert first.audio_wav == b"FAST" and voice.calls == [False, True]
    svc.mark_delivered(first.crumb)
    assert svc._natural_lead_s() > lead  # the late take counts as at least lead + margin slow
    await svc.prefetch(ctx, now=10.0)
    await svc.voice("steer", ctx, now=11.0, lead_s=lead)
    assert voice.calls == [False, True, True]  # the next crumb goes straight to the fast model
    gate.set()


async def test_voice_starts_the_missing_text_and_a_miss_joins_a_running_prefetch(monkeypatch):
    voice = _Voice()
    monkeypatch.setattr(crumbs_mod, "synthesize", voice)
    svc = _service(Gemini(mock=True))
    made = _count_generations(svc)
    ctx = _ctx(TRANSCRIPT, "running")
    await svc.voice("switch", ctx, now=0.0, lead_s=3.5)  # the slot was empty: generated, then voiced once
    assert made == ["switch"] and voice.calls == [False]
    # A miss while that slot is being generated joins the generation instead of paying for a second one.
    gate = asyncio.Event()
    original = svc._build

    async def slow_build(plan, convo):  # noqa: ANN001
        await gate.wait()
        return await original(plan, convo)

    monkeypatch.setattr(svc, "_build", slow_build)
    prefetch = asyncio.ensure_future(svc.prefetch(ctx, now=1.0))
    await asyncio.sleep(0.01)
    assert "steer" in svc._gen_tasks
    get = asyncio.ensure_future(svc.get("steer", ctx, now=1.1))
    await asyncio.sleep(0.01)
    gate.set()
    d = await get
    await prefetch
    assert made == ["switch", "steer"] and d.crumb.kind == "steer" and not d.cache_hit


# --- 2. no regeneration of a valid crumb --------------------------------------------------------------


async def test_prefetch_does_not_regenerate_a_valid_crumb_or_run_a_slot_twice():
    svc = _service(Gemini(mock=True))
    made = _count_generations(svc)
    ctx = _ctx(TRANSCRIPT, "running")
    await asyncio.gather(svc.prefetch(ctx, now=0.0), svc.prefetch(ctx, now=0.0))  # overlapping runs
    assert sorted(made) == ["steer", "switch"]
    await svc.prefetch(ctx, now=10.0)
    await svc.prefetch(_ctx(TRANSCRIPT, "running shoes"), now=20.0)  # a reworded topic label
    await svc.prefetch(_ctx(TRANSCRIPT, "ramen"), now=30.0)  # a new subject: the switch is still a switch
    assert sorted(made) == ["steer", "switch"] and svc.slots_to_refresh(ctx, 40.0) == []
    one_more = TRANSCRIPT + "\nMaya: Podcasts, mostly."
    await svc.prefetch(_ctx(one_more, "running"), now=41.0)  # one new line: the steer is still fresh
    assert len(made) == 2
    await svc.prefetch(ctx, now=crumbs_mod.config.CACHE_MAX_AGE_S + 1)  # too old: both rebuilt
    assert sorted(made) == ["steer", "steer", "switch", "switch"]


async def test_steer_false_fills_an_empty_steer_but_keeps_a_stale_one():
    svc = _service(Gemini(mock=True))
    made = _count_generations(svc)
    await svc.prefetch(_ctx(TRANSCRIPT, "running"), now=0.0, steer=False)
    assert sorted(made) == ["steer", "switch"]  # F14: a ready steer text, even while no steer could fire
    moved_on = TRANSCRIPT + "\nMaya: Podcasts.\nDev: Which ones?\nMaya: True crime, mostly."
    await svc.prefetch(_ctx(moved_on, "running"), now=5.0, steer=False)
    assert len(made) == 2 and svc.cached_summary(5.0)["steer"]  # stale, but no steer could use a new one
    await svc.prefetch(_ctx(moved_on, "running"), now=6.0, steer=True)
    assert made[-1] == "steer" and len(made) == 3


async def test_facts_are_searched_once_per_topic_and_never_on_the_live_path(monkeypatch):
    searches: list[str | None] = []

    async def fake_get_fact(topic, location, gemini=None):  # noqa: ANN001
        searches.append(topic)
        return Fact(text="Austin has a huge bat colony.", url="https://example.org/bats", title="Bats")

    monkeypatch.setattr(crumbs_mod, "get_fact", fake_get_fact)
    svc = _service(Gemini(mock=True))
    assert await svc._fact("bats", live=True) is None and searches == []  # a miss never searches
    first = await svc._fact("bats")
    again = await svc._fact("Bats")  # same stems
    assert first is again and searches == ["bats"]
    assert await svc._fact("bats", live=True) is first  # a fact already found is used live
    delivered = svc._canned("switch", _ctx()).model_copy(update={"id": "c_fact", "citation_url": first.url})
    svc.mark_delivered(delivered)  # told once: the next crumb on the topic looks for a new fact
    await svc._fact("bats")
    assert searches == ["bats", "bats"]


async def test_a_live_generation_is_skipped_when_recent_ones_were_slower_than_its_budget():
    class _SlowGemini(Gemini):
        def __init__(self) -> None:
            super().__init__(mock=False, api_key="x", meta_api_key="")
            self.calls = 0

        async def json(self, **kwargs):  # type: ignore[override]
            self.calls += 1
            raise AssertionError("no live call expected")

        async def tts(self, text: str):  # type: ignore[override]
            return None

    fake = _SlowGemini()
    svc = _service(fake)
    svc._draft_ms.extend([3700.0, 3600.0, 3900.0])  # Claude is taking ~3.7 s per crumb
    d = await svc.get("switch", _ctx(TRANSCRIPT, "running"), now=0.0)
    assert fake.calls == 0 and d.crumb.text and not d.cache_hit  # straight to the overlap/canned fallback


# --- 3. static system line cache ---------------------------------------------------------------------------


def _runner(params: Params | None = None) -> runner_mod.SessionRunner:
    sent: list[dict] = []
    session = SimpleNamespace(mode="social", names=dict(NAMES), params=params or Params(), profiles={},
                              broadcast=lambda event, roles=None: sent.append(event) or 1)
    runner = runner_mod.SessionRunner(session, overlaps=None, on_round_end=None,  # type: ignore[arg-type]
                                      crumbs=_service(Gemini(mock=True)))
    runner.sent = sent  # type: ignore[attr-defined]
    return runner


async def test_system_lines_are_voiced_once_per_process_and_named_ones_once_per_session(monkeypatch):
    voiced: list[str] = []

    async def fake_synth(text, gemini=None, *, fast=True, elevenlabs=None, budget_s=None):  # noqa: ANN001
        voiced.append(text)
        return b"WAV:" + text.encode()

    monkeypatch.setattr(tts_mod, "synthesize", fake_synth)
    tts_mod.SYSTEM_LINES.clear()
    named = "Before we start: Seat 1, say hi and tell Dev where you're from."
    static = "Seat 2, your turn."
    first, second = _runner(), _runner()
    for runner in (first, first, second):
        await runner._say("sys_cal1", named)
        await runner._say("sys_cal2", static)
    assert voiced == [named, static, named]  # the static line once per process, the named one per session
    assert all(e["audio_b64"] for e in first.sent + second.sent if e["type"] == "system_say")
    assert first._personal(named) and not first._personal(static) and not first._personal("Developers welcome.")
    await first.stop()
    assert len(first._lines) == 0 and len(tts_mod.SYSTEM_LINES) == 1  # session audio goes with the session
    tts_mod.SYSTEM_LINES.clear()


async def test_failed_lines_are_not_cached(monkeypatch):
    calls: list[str] = []

    async def failing(text, gemini=None, *, fast=True, elevenlabs=None, budget_s=None):  # noqa: ANN001
        calls.append(text)
        return None

    monkeypatch.setattr(tts_mod, "synthesize", failing)
    cache = tts_mod.AudioCache(2)
    assert await tts_mod.synthesize_cached("Seat 2, your turn.", cache) is None
    assert await tts_mod.synthesize_cached("Seat 2, your turn.", cache) is None
    assert len(calls) == 2 and len(cache) == 0


async def test_tts_keeps_no_reserve_for_an_unavailable_gemini():
    budgets: list[float] = []

    class _Eleven:
        available = True

        async def synthesize(self, text, *, fast, budget_s=None):  # noqa: ANN001
            budgets.append(budget_s)
            return b"WAV"

    paused = Gemini(mock=False, api_key="", meta_api_key="")  # no Gemini key (or paused after a 402)
    assert not paused.gemini_available
    assert await tts_mod.synthesize("Hi Maya.", paused, fast=False, elevenlabs=_Eleven(), budget_s=10.0) == b"WAV"
    assert budgets[0] > 9.0  # the whole budget, not 70% of it


# --- 4. engine: what is worth preparing, and classifier / tagger gating -------------------------------------


def _lively(e, t: float, n: int, first_id: int = 100) -> float:  # noqa: ANN001
    for i in range(n):
        speak(e, first_id + i, "AB"[i % 2], LINES[i % len(LINES)], t, t + 2.0)
        t += 2.7
    return t


def test_steer_wanted_only_when_a_steer_could_fire_in_the_coming_pause():
    e, _ = calibrated()  # opener ended at 9.5: warm-up to 29.5, regular cooldown to 39.5 (and 2 turns)
    speak(e, 50, "A", LINES[0], 12.0, 14.0)
    assert not e.steer_wanted(14.0)  # still in warm-up and cooldown 5 s from now
    t = _lively(e, 40.0, 3, first_id=60)
    end = t - 0.7
    assert e.steer_wanted(end)
    nxt = e.next_auto_crumb(end)
    assert nxt is not None and nxt[0] == "steer" and abs(nxt[1] - e.params.T_LONG_S) < 0.05
    e.on_vad(True, 0.05, end + 0.3)  # someone at the table already talks again: the pause has not started
    e.on_muse_event({"type": "speechStart", "turnId": 90}, end + 0.3)
    assert not e.steer_wanted(end + 0.4)


def test_warming_band_asks_to_voice_exactly_the_crumb_the_pause_gets():
    e, _ = calibrated()
    end = _lively(e, 40.0, 4) - 0.7
    fx = ticks(e, end + 0.1, end + 4.9)
    warm = [f for f in of_type(fx, "prefetch") if f.get("voice")]
    assert len(warm) == 1 and warm[0]["voice"] == "steer"
    assert abs(warm[0]["due_in_s"] - (e.params.T_LONG_S - e.params.T_NORMAL_S)) < 0.15
    # Right after the opener nothing can fire in this pause: no voicing request at all.
    e2, _ = calibrated()
    speak(e2, 50, "A", LINES[0], 12.0, 14.0)
    assert not [f for f in of_type(ticks(e2, 14.1, 18.9), "prefetch") if f.get("voice")]


def test_ambiguous_fragment_is_classified_only_after_real_silence():
    e, _ = calibrated()
    t = _lively(e, 40.0, 2)
    fx = speak(e, 70, "A", "and then my phone has the", t, t + 1.5)
    assert not of_type(fx, "classify_hold")  # not at the commit
    assert not of_type(ticks(e, t + 1.6, t + 1.5 + e.params.HOLD_CHECK_SILENCE_S - 0.2), "classify_hold")
    late = ticks(e, t + 1.5 + e.params.HOLD_CHECK_SILENCE_S - 0.1, t + 1.5 + e.params.HOLD_CHECK_SILENCE_S + 0.2)
    assert len(of_type(late, "classify_hold")) == 1
    assert not of_type(ticks(e, t + 4.0, t + 6.0), "classify_hold")  # asked once


def test_no_classifier_call_when_speech_resumes_or_for_a_backchannel():
    e, _ = calibrated()
    t = _lively(e, 40.0, 2)
    speak(e, 70, "A", "and then my phone has the", t, t + 1.5)
    speak(e, 71, "B", "Wait, your phone has what exactly, tell me everything?", t + 2.0, t + 4.0)
    assert not of_type(ticks(e, t + 2.1, t + 3.9), "classify_hold")
    fx = speak(e, 72, "A", "yeah", t + 8.0, t + 8.4)
    fx += ticks(e, t + 8.5, t + 12.0)
    assert not of_type(fx, "classify_hold")


def test_lexical_hold_hit_is_still_classified_at_once():
    e, _ = calibrated()
    t = _lively(e, 40.0, 2)
    fx = speak(e, 70, "A", "Hold on, let me find it.", t, t + 1.5)
    assert len(of_type(fx, "classify_hold")) == 1


def test_energy_tagger_skips_turns_with_no_new_words_and_a_capped_session():
    e, _ = calibrated()
    t = 40.0
    fx: list[dict] = []
    for i in range(4):
        fx += speak(e, 100 + i, "AB"[i % 2], LINES[i], t, t + 2.0)
        t += 2.7
    assert len(of_type(fx, "tag_energy")) == 1
    fx = []
    for i in range(4):  # four turns Muse finalised with no words: nothing new to tag
        fx += speak(e, 200 + i, "AB"[i % 2], "", t, t + 0.5)
        t += 1.2
    assert not of_type(fx, "tag_energy")
    for k in range(e.params.MAX_CRUMBS_PER_10_MIN):  # the 10-minute automatic cap is reached
        e._ledger.note_auto_decision(t - 1.0 - k)
    assert e.auto_blocked_for(t) > 500
    fx = []
    for i in range(4):
        fx += speak(e, 300 + i, "AB"[i % 2], LINES[i], t, t + 2.0)
        t += 2.7
    assert not of_type(fx, "tag_energy")


def test_safety_pause_blocks_automatic_work_for_good():
    e, _ = calibrated()
    speak(e, 50, "A", "I want to die.", 40.0, 42.0)
    assert e.auto_blocked_for(43.0) == float("inf")
    assert not e.steer_wanted(43.0) and e.next_auto_crumb(43.0) is None


# --- 5. the harness: the cut holds and no crumb is late ------------------------------------------------------


def test_usage_harness_shows_the_cut_with_crumbs_on_time():
    from sim import usage

    r = usage.measure()
    e, c, lat = r["elevenlabs_total"], r["claude_total"], r["latency"]
    assert e["credits"] < 2_000 and e["sessions_per_free_month"] >= 5  # baseline 11,058.5 credits, 0.9 sessions
    assert c["calls"] < 80 and c["abandoned"] == 0  # baseline 125 calls, 5 abandoned
    assert lat["hits_over_target"] == 0 and (lat["miss_max_ms"] or 0) <= 700  # baseline: 4 misses at 2,450 ms
    by_purpose = r["elevenlabs"]
    assert "crumb audio (prefetch) [eleven_v4]" not in by_purpose  # nothing is voiced speculatively any more
