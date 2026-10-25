"""Usage cut follow-ups (BUILD_LOG "usage:fix"): the savings never make a crumb later, a hold check that
is asked later keeps its full window, and a delivered steer follows the turn that opened its pause.
Hermetic: fake drafts and synthesizers, no network."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import ai.crumbs as crumbs_mod
from ai.gemini import Gemini
from config import Params
from models import HoldDecision
from tests.test_ai_mock import TRANSCRIPT, _ctx, _service
from tests.test_policy import calibrated, decisions, of_type, speak
from tests.test_usage_cuts import _lively, _runner, _Voice


# --- 1. a miss that joins a running refill stays inside one budget (F8) --------------------------------


def _real_gateway(svc, draft_s: float, samples: list[float]):  # noqa: ANN001
    """A non-mock gateway whose drafts take `draft_s` (the mock text otherwise), with prior timings."""
    real_mock = svc._mock_crumb

    async def slow_draft(plan, convo, attempt):  # noqa: ANN001
        await asyncio.sleep(draft_s)
        svc._draft_ms.append(draft_s * 1000.0)
        return real_mock(plan, convo, attempt)

    async def no_fact(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    svc._draft = slow_draft  # type: ignore[method-assign]
    svc._fact = no_fact  # type: ignore[method-assign]
    svc.gemini = SimpleNamespace(mock=False)
    svc._draft_ms.extend(samples)


async def _miss_during_refill(monkeypatch, kind: str, slot: str, draft_s: float, samples: list[float],
                              refill_hangs: bool = True) -> tuple[float, object]:
    monkeypatch.setattr(crumbs_mod, "MISS_BUDGET_S", 0.4)
    monkeypatch.setattr(crumbs_mod, "MISS_JOIN_S", 0.1)
    monkeypatch.setattr(crumbs_mod, "MISS_JOIN_MARGIN_S", 0.04)
    monkeypatch.setattr(crumbs_mod, "MISS_LIVE_MIN_S", 0.06)
    monkeypatch.setattr(crumbs_mod, "synthesize", _Voice())
    svc = _service(Gemini(mock=True))
    _real_gateway(svc, draft_s, samples)
    ctx = _ctx(TRANSCRIPT, "running")
    if refill_hangs:  # the slot's refill is stuck (a grounded search, or a slow model): it never lands
        gate = asyncio.Event()
        real_build = svc._build

        async def stuck_build(plan, convo):  # noqa: ANN001
            if not plan.live:
                await gate.wait()
            return await real_build(plan, convo)

        svc._build = stuck_build  # type: ignore[method-assign]
    svc._start_generation(slot, ctx, 0.0)
    await asyncio.sleep(0.01)
    started = time.perf_counter()
    d = await svc.get(kind, ctx, now=0.1)
    d.drafts = len(svc._draft_ms) - len(samples)  # type: ignore[attr-defined]
    return time.perf_counter() - started, d


async def test_drop_during_a_refill_early_in_a_session_takes_one_budget_not_two(monkeypatch):
    # No timings yet and the model is slower than the budget: the join and the live call share the budget,
    # so the fallback goes out after one budget (it took two before: join, then a full live call).
    took, d = await _miss_during_refill(monkeypatch, "manual", "switch", 5.0, [])
    assert took < 0.4 + 0.1 and not d.cache_hit and d.crumb.text


async def test_steer_miss_during_its_own_refill_takes_one_budget(monkeypatch):
    took, d = await _miss_during_refill(monkeypatch, "steer", "steer", 5.0, [])
    assert took < 0.4 + 0.1 and d.crumb.text


async def test_a_short_join_leaves_room_for_a_live_call_that_fits(monkeypatch):
    # The model is fast (0.15 s) but the refill is stuck: wait MISS_JOIN_S at most, then the live call
    # lands inside the same budget with a generated crumb.
    took, d = await _miss_during_refill(monkeypatch, "manual", "switch", 0.15, [150.0, 150.0, 150.0])
    assert d.drafts >= 1 and 0.1 + 0.15 - 0.03 < took < 0.4  # joined 0.1 s, then a live draft that fit


async def test_a_join_gets_the_whole_budget_when_the_live_call_would_be_skipped(monkeypatch):
    # Recent generations took longer than the budget: no live call can make it, so waiting for the running
    # refill is the only way to a real crumb. It lands at 0.2 s and is used.
    took, d = await _miss_during_refill(monkeypatch, "manual", "switch", 0.2, [3700.0, 3700.0, 3700.0],
                                        refill_hangs=False)
    assert 0.15 < took < 0.4 and d.crumb.source != "canned"


def test_join_budget_rules():
    svc = _service(Gemini(mock=True))
    assert svc._join_budget_s() == crumbs_mod.MISS_JOIN_S  # mock / no timings: a short join
    svc.gemini = SimpleNamespace(mock=False)
    svc._draft_ms.extend([3700.0, 3700.0])
    assert svc._join_budget_s() == crumbs_mod.MISS_BUDGET_S  # live would be skipped: the whole budget
    svc._draft_ms.clear()
    svc._draft_ms.extend([1900.0, 1900.0])
    assert svc._join_budget_s() == 0.0  # the live call needs all of it: no join at all
    svc._draft_ms.clear()
    svc._draft_ms.extend([1000.0, 1000.0])
    assert svc._join_budget_s() == crumbs_mod.MISS_JOIN_S


# --- 2. a deferred hold check keeps the classifier's window ----------------------------------------------


def _fragment_pause(params: Params | None = None):
    e, _ = calibrated(params=params)
    t = _lively(e, 40.0, 3, first_id=60)
    end = t + 1.5
    speak(e, 70, "A", "and then my phone has the", t, end)
    return e, end


def _run(e, end: float, until: float, answer_at: float | None = None, hold: bool = True,  # noqa: ANN001
         fail_at: float | None = None):
    asked = steer_at = None
    voiced: list[tuple[float, dict]] = []
    holds: list[dict] = []
    k = round(end * 10) + 1
    while k / 10 <= until:
        now = k / 10
        out = e.tick(now)
        ch = of_type(out, "classify_hold")
        if ch:
            asked = (now, ch[0]["turn_id"])
        if asked and answer_at is not None and abs(now - (end + answer_at)) < 0.05:
            d = HoldDecision(hold=hold, kind="show" if hold else "none", expected_seconds=10, confidence=0.9)
            out += e.apply_hold_decision(asked[1], d, now)
        if asked and fail_at is not None and abs(now - (end + fail_at)) < 0.05:
            e.hold_check_failed(asked[1])
        holds += decisions(out, "hold")
        voiced += [(now, f) for f in of_type(out, "prefetch") if f.get("voice")]
        if steer_at is None and decisions(out, "steer"):
            steer_at = round(now - end, 1)
        k += 1
    return asked, steer_at, voiced, holds


def test_deferred_check_is_asked_at_the_silence_mark_and_a_slow_answer_still_holds():
    e, end = _fragment_pause()
    # Answer at 5.5 s of silence: after the 5 s steer threshold, yet the pause is held (no steer).
    asked, steer_at, voiced, holds = _run(e, end, end + 9.0, answer_at=5.5)
    assert asked is not None and abs(asked[0] - end - e.params.HOLD_CHECK_SILENCE_S) < 0.15
    assert steer_at is None and len(holds) == 1 and "(show)" in holds[0]["reason"]
    assert not voiced  # the steer audio was not paid for before the answer


def test_a_no_hold_answer_releases_the_steer_at_once():
    e, end = _fragment_pause()
    _, steer_at, voiced, _ = _run(e, end, end + 9.0, answer_at=5.4, hold=False)
    assert 5.4 <= steer_at <= 5.5  # waited for the answer, then fired on the next tick
    e2, end2 = _fragment_pause()
    _, steer_at2, voiced2, _ = _run(e2, end2, end2 + 9.0, answer_at=3.5, hold=False)
    assert steer_at2 == e2.params.T_LONG_S  # a quick "no" leaves the steer on time
    assert len(voiced2) == 1 and voiced2[0][0] - end2 >= 3.5 - 0.05  # voiced once, after the answer


def test_the_wait_for_the_answer_is_capped_and_a_failed_call_releases_it():
    e, end = _fragment_pause()
    _, steer_at, _, _ = _run(e, end, end + 9.0)  # the classifier never answers
    assert steer_at == e.params.T_LONG_S + e.params.HOLD_CHECK_SILENCE_S
    e2, end2 = _fragment_pause()
    _, steer_at2, _, _ = _run(e2, end2, end2 + 9.0, fail_at=5.2)
    assert 5.2 <= steer_at2 <= 5.3


def test_speech_resuming_ends_the_wait():
    e, end = _fragment_pause()
    asked, _, _, _ = _run(e, end, end + 2.0)
    assert asked is not None and e._hold_answer_pending()
    speak(e, 71, "B", "Your phone has what, tell me everything?", end + 2.5, end + 4.0)
    assert not e._hold_answer_pending()


# --- 3. a delivered steer follows the turn that opened its pause (F14) -------------------------------------


def _count_generations(svc) -> list[str]:  # noqa: ANN001
    made: list[str] = []
    original = svc._prefetch_one

    async def counted(key, convo):  # noqa: ANN001
        made.append(key)
        return await original(key, convo)

    svc._prefetch_one = counted  # type: ignore[method-assign]
    return made


ONE_MORE = TRANSCRIPT + "\nMaya: Podcasts, mostly."
TWO_MORE = ONE_MORE + "\nDev: Which ones?"


async def test_fresh_rebuilds_a_steer_that_is_one_turn_behind():
    svc = _service(Gemini(mock=True))
    made = _count_generations(svc)
    await svc.prefetch(_ctx(TRANSCRIPT, "running"), now=0.0)
    assert sorted(made) == ["steer", "switch"]
    await svc.prefetch(_ctx(ONE_MORE, "running"), now=5.0)  # not a steer pause: one line behind is fine
    assert len(made) == 2
    await svc.prefetch(_ctx(ONE_MORE, "running"), now=6.0, fresh=True)  # a steer could fill this pause
    assert made == ["steer", "switch", "steer"] or sorted(made) == ["steer", "steer", "switch"]
    entry = svc._cache["steer"]
    assert svc._lines_since(entry, _ctx(ONE_MORE, "running")) == 0
    assert svc.slots_to_refresh(_ctx(ONE_MORE, "running"), 7.0, fresh=True) == []  # already on that turn


async def test_fresh_steer_for_a_newer_turn_follows_the_running_one_never_in_parallel():
    svc = _service(Gemini(mock=True))
    made = _count_generations(svc)
    await svc.prefetch(_ctx(TRANSCRIPT, "running"), now=0.0)
    gate = asyncio.Event()
    in_flight: list[int] = []
    real_build = svc._build

    async def gated_build(plan, convo):  # noqa: ANN001
        in_flight.append(len(svc._gen_tasks))
        await gate.wait()
        return await real_build(plan, convo)

    svc._build = gated_build  # type: ignore[method-assign]
    first = asyncio.ensure_future(svc.prefetch(_ctx(ONE_MORE, "running"), now=5.0, fresh=True))
    await asyncio.sleep(0.01)
    assert made[-1] == "steer" and "steer" in svc._gen_tasks
    # The next turn lands while that steer is still being written: queued, not started in parallel.
    assert svc.slots_to_refresh(_ctx(TWO_MORE, "running"), 6.0, fresh=True) == ["steer"]
    await svc.prefetch(_ctx(TWO_MORE, "running"), now=6.0, fresh=True)
    assert made.count("steer") == 2 and svc.slots_to_refresh(_ctx(TWO_MORE, "running"), 6.1, fresh=True) == []
    gate.set()
    await first
    for _ in range(20):
        await asyncio.sleep(0)
        if "steer" not in svc._gen_tasks and made.count("steer") == 3:
            break
    while "steer" in svc._gen_tasks:
        await svc._gen_tasks["steer"]
    assert made.count("steer") == 3 and all(n <= 1 for n in in_flight)
    assert svc._lines_since(svc._cache["steer"], _ctx(TWO_MORE, "running")) == 0


async def test_voice_waits_for_the_fresher_steer_instead_of_voicing_the_old_one(monkeypatch):
    voice = _Voice()
    monkeypatch.setattr(crumbs_mod, "synthesize", voice)
    monkeypatch.setattr(crumbs_mod, "VOICE_FRESH_RESERVE_S", 0.1)
    svc = _service(Gemini(mock=True))
    await svc.prefetch(_ctx(TRANSCRIPT, "running"), now=0.0)
    old_text = svc._cache["steer"].crumb.text
    gate = asyncio.Event()
    real_build = svc._build

    async def gated_build(plan, convo):  # noqa: ANN001
        await gate.wait()
        return await real_build(plan, convo)

    svc._build = gated_build  # type: ignore[method-assign]
    ctx = _ctx(ONE_MORE, "running")
    refresh = asyncio.ensure_future(svc.prefetch(ctx, now=5.0, fresh=True))
    await asyncio.sleep(0.01)
    voicing = asyncio.ensure_future(svc.voice("steer", ctx, now=6.0, lead_s=2.0))
    await asyncio.sleep(0.05)
    assert voice.calls == []  # not voicing the steer for the earlier turn
    gate.set()
    await refresh
    await voicing
    new = svc._cache["steer"]
    assert svc._lines_since(new, ctx) == 0 and len(voice.calls) == 1
    assert (new.crumb.text, voice.calls[0]) in svc._audio
    assert (old_text, False) not in svc._audio or old_text == new.crumb.text


def test_stale_fallback_is_capped_at_stale_turns():
    svc = _service(Gemini(mock=True))
    asyncio.run(svc.prefetch(_ctx(TRANSCRIPT, "running"), now=0.0))
    lines = TRANSCRIPT
    for i in range(crumbs_mod.STEER_STALE_TURNS):
        lines += f"\n{'Maya' if i % 2 else 'Dev'}: Line number {i} about podcasts."
    assert svc._stale_steer(1.0, _ctx(lines, "running")) is not None  # STEER_STALE_TURNS behind: still ok
    lines += "\nMaya: One more line about podcasts."
    assert svc._stale_steer(1.0, _ctx(lines, "running")) is None  # further behind: canned beats it


async def test_runner_marks_a_steer_pause_commit_as_fresh(monkeypatch):
    runner = _runner()
    runner.engine.steer_wanted = lambda now: True  # type: ignore[method-assign]
    runner._execute({"type": "prefetch", "reason": "turn committed"})
    assert runner._prefetch_fresh and runner._prefetch_due is not None
    runner2 = _runner()
    runner2.engine.steer_wanted = lambda now: False  # type: ignore[method-assign]
    runner2._execute({"type": "prefetch", "reason": "turn committed"})
    assert not runner2._prefetch_fresh  # debounced, not fresh
    await runner.stop()
    await runner2.stop()


# --- 4. nothing is prepared for a crumb the round's end rules out ----------------------------------------


def test_no_automatic_crumb_is_expected_after_the_round_ends():
    e, _ = calibrated("speed_dating")
    end = _lively(e, 40.0, 3, first_id=60) - 0.7
    assert e.steer_wanted(end) and e.round_left_s(end) > 60
    e._live_at = end + 3.0 - crumbs_mod.config.ROUND_TIMER_S["speed_dating"]  # now 3 s of the round are left
    assert abs(e.round_left_s(end) - 3.0) < 1e-6
    assert e.next_auto_crumb(end) is None and not e.steer_wanted(end)  # a 5 s pause outlasts the round


async def test_runner_prepares_no_steer_when_the_round_ends_first():
    runner = _runner()
    seen: list[tuple] = []

    def slots_to_refresh(convo, now, *, steer=True, fresh=False, slots=("steer", "switch")):  # noqa: ANN001
        seen.append(slots)
        return []

    runner.crumbs.slots_to_refresh = slots_to_refresh  # type: ignore[method-assign]
    runner.engine._phase = "live"
    runner.engine.round_left_s = lambda now: 3.0  # type: ignore[method-assign]
    runner._maybe_prefetch(100.0)
    runner.engine.round_left_s = lambda now: None  # type: ignore[method-assign]
    runner._last_prefetch_at = None
    runner._maybe_prefetch(200.0)
    assert seen == [("switch",), ("steer", "switch")]
    await runner.stop()
