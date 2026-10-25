"""The runner holds a generated crumb while someone may be talking (engine.dispatch_blocked), up to
DISPATCH_WAIT_S: a background turn lets it go after the settle, a seat turn cancels it, a timeout drops it
and puts its text back in the cache. Mock AI, a clock that runs at real speed from a fake start."""
from __future__ import annotations

import asyncio
import time

import pytest

import engine.runner as runner_mod
from ai.gemini import Gemini
from tests.test_ai_mock import _ctx, _service
from tests.test_bystander import lively
from tests.test_noise_echo import seated, vad_frames
from tests.test_policy import decisions, of_type
from tests.test_usage_cuts import _runner


class _Clock:
    """Fake engine time that moves with the wall clock (the runner sleeps for real)."""

    def __init__(self, at: float) -> None:
        self.base, self.t0 = at, time.monotonic()

    def __call__(self) -> float:
        return self.base + (time.monotonic() - self.t0)


def _setup():
    e = seated()
    end = lively(e, 40.0)
    vad_frames(e, end + 0.1, end + 2.9, active=False, level_db=-60.0)
    runner = _runner()
    runner.engine = e
    runner.crumbs = _service(Gemini(mock=True))
    clock = _Clock(end + 3.0)
    runner.clock = clock
    fx = e.tick(clock())  # the 3 s steer is decided
    assert [f["kind"] for f in of_type(fx, "deliver")] == ["steer"]
    return runner, e, clock


def _crumbs_sent(runner) -> list[dict]:  # noqa: ANN001
    return [ev for ev in runner.sent if ev["type"] == "crumb"]


def _open_bystander(e, clock, mid: int = 90) -> None:  # noqa: ANN001
    e.on_muse_event({"type": "speechStart", "turnId": mid}, clock())


def _close(e, clock, mid: int, text: str, label: str) -> list[dict]:  # noqa: ANN001
    e.on_muse_event({"type": "transcript", "transcript": text, "final": False}, clock())
    e.on_muse_event({"type": "speaker", "label": label}, clock())
    return e.on_muse_event({"type": "speechEnd", "turnId": mid}, clock())


async def test_a_held_crumb_goes_out_once_a_background_turn_closes_and_settles() -> None:
    runner, e, clock = _setup()
    _open_bystander(e, clock)
    task = asyncio.create_task(runner._deliver("steer"))
    await asyncio.sleep(0.3)
    assert _crumbs_sent(runner) == [] and e.dispatch_blocked(clock())
    closed_at = clock()
    fx = _close(e, clock, 90, "yeah", "C")
    assert decisions(fx, "background_turn")
    await asyncio.wait_for(task, 3.0)
    sent = _crumbs_sent(runner)
    assert len(sent) == 1 and sent[0]["crumb"]["kind"] == "steer"
    assert clock() - closed_at >= e.params.BACKGROUND_SETTLE_S - 0.05
    await runner.stop()


async def test_a_crumb_held_too_long_is_dropped_and_its_text_goes_back_to_the_cache(monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "DISPATCH_WAIT_S", 0.5)
    runner, e, clock = _setup()
    svc = runner.crumbs
    await svc.prefetch(runner._convo(clock()), clock(), slots=("steer",))
    steer_text = svc.cached_summary(clock())["steer"]
    assert steer_text
    _open_bystander(e, clock)
    await asyncio.wait_for(runner._deliver("steer"), 3.0)
    assert _crumbs_sent(runner) == []
    blocked = [ev for ev in runner.sent if ev["type"] == "decision" and ev["decision"]["action"] == "dispatch_blocked"]
    assert len(blocked) == 1 and blocked[0]["decision"]["inputs"]["waited_s"] >= 0.45
    assert svc.cached_summary(clock())["steer"] == steer_text  # restored, not thrown away
    assert e.snapshot(clock())["crumbs_last_10min"] == 0  # the automatic decision left the rate cap
    assert e._pending is None
    await runner.stop()


async def test_a_held_crumb_is_dropped_when_the_turn_closes_as_a_seat() -> None:
    runner, e, clock = _setup()
    _open_bystander(e, clock)
    task = asyncio.create_task(runner._deliver("steer"))
    await asyncio.sleep(0.3)
    _close(e, clock, 90, "Oh wait, which gym was that again?", "B")
    await asyncio.wait_for(task, 3.0)
    assert _crumbs_sent(runner) == [] and e._pending is None
    await runner.stop()


async def test_dev_turns_are_marked_and_count_as_seats_without_vad(monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "DEV_COMPLETE_DELAY_S", 0.0)
    runner = _runner()
    fed: list[dict] = []
    original = runner._feed

    def feed(evt: dict) -> None:
        fed.append(evt)
        original(evt)

    runner._feed = feed  # type: ignore[method-assign]
    await runner._play_dev_turn(1, "Hi there, I'm from Austin.", 0.6, runner_mod.DEV_TURN_ID_BASE + 1)
    kinds = {evt["type"]: evt for evt in fed}
    assert kinds["speechStart"]["dev"] and kinds["speechEnd"]["dev"] and kinds["speechComplete"]["dev"]
    rec = runner.engine._tracker.by_muse(runner_mod.DEV_TURN_ID_BASE + 1)
    assert rec is not None and rec.dev and rec.fg == "seat" and rec.fg_reason == "dev"
    assert not rec.turn.background
    await runner.stop()


def test_restore_needs_a_cache_hit() -> None:
    svc = _service(Gemini(mock=True))
    delivery = runner_mod.Delivery(crumb=svc._canned("steer", _ctx()), audio_wav=None, cache_hit=False, gen_ms=0.0)
    svc.restore(delivery)
    assert svc._cache == {}


@pytest.mark.parametrize("wait", [runner_mod.DISPATCH_WAIT_S])
def test_dispatch_constants(wait: float) -> None:
    assert wait == 2.0 and runner_mod.DISPATCH_POLL_S == 0.1
