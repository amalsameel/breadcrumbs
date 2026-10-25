"""Regressions for the server review fixes: seat ownership, AI fallbacks on timeout, stuck bot speech,
cache refill after a dispatch, and Muse ownership when a second Table's audio socket comes and goes."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from ai import gemini as gemini_mod
from engine import runner as runner_mod
from session_store import SessionStore
from tests.test_api import (  # noqa: F401 - `client` and `mock_mode` are fixtures used by name
    NAMES,
    SILENCE_FRAME,
    client,
    consent,
    create,
    go_live,
    mock_mode,
    of_type,
    poll_trail,
    setup_ready,
    summary,
    wait_for,
)


def other_phone(client: TestClient) -> TestClient:
    """A second browser for the same app: its own cookie jar, so it holds no seat cookies."""
    return TestClient(client.app)


def ready_both(client: TestClient, sid: str) -> None:
    """Consent, interview, profile and ready for both seats (before any audio socket: it needs consent)."""
    setup_ready(client, sid)
    for seat in (1, 2):
        assert client.post(f"/api/sessions/{sid}/seats/{seat}/ready").status_code == 200


# --- seat ownership (PRD 5.6, F2, F4, F17) --------------------------------------------------------


def test_other_phone_cannot_act_for_a_consented_seat(client: TestClient) -> None:
    sid = create(client)
    setup_ready(client, sid)
    seat2 = f"/api/sessions/{sid}/seats/2"
    r = client.patch(f"{seat2}/profile", json={"removed_item_ids": [], "off_limits": ["my ex"]})
    assert r.status_code == 200

    intruder = other_phone(client)
    assert intruder.post(f"{seat2}/consent", json={"typed_name": "Not Dev", "agreed": True}).status_code == 403
    assert intruder.get(f"{seat2}/profile").status_code == 403  # off-limits never reach another phone
    assert intruder.patch(f"{seat2}/profile", json={"removed_item_ids": [], "off_limits": []}).status_code == 403
    assert intruder.post(f"{seat2}/profile").status_code == 403
    assert intruder.post(f"{seat2}/ready").status_code == 403
    assert summary(client, sid)["seats"]["2"]["name"] == NAMES[2]
    assert client.get(f"{seat2}/profile").json()["off_limits"] == ["my ex"]  # the owner still can
    consent(client, sid, 2)  # re-consent from the owning phone is fine


def test_a_decline_cannot_be_overridden_by_the_other_phone(client: TestClient) -> None:
    sid = create(client)
    phone1, phone2 = client, other_phone(client)
    consent(phone1, sid, 1)
    consent(phone2, sid, 2)
    phone1.post(f"/api/sessions/{sid}/end")
    assert phone2.post(f"/api/sessions/{sid}/seats/2/trail-optin", json={"share": False}).status_code == 200
    forged = phone1.post(f"/api/sessions/{sid}/seats/2/trail-optin", json={"share": True, "contact": "x"})
    assert forged.status_code == 403
    assert phone1.post(f"/api/sessions/{sid}/seats/1/trail-optin", json={"share": True, "contact": "@maya"}).status_code == 200
    assert phone1.get(f"/api/sessions/{sid}/seats/2/trail").status_code == 403  # nor fetch (and wipe) it
    t2 = poll_trail(phone2, sid, 2)
    assert t2 == {"matched": False, "partner_name": None, "partner_contact": None, "trail": []}


# --- AI fallbacks when the model hangs (PRD 9.7, F4, F11, 11.3) -----------------------------------


class _HangingInteractions:
    calls = 0

    async def create(self, **_: object) -> object:
        _HangingInteractions.calls += 1
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


def hang_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live (non-mock) Gemini whose every call hangs, with short caller timeouts."""
    fake = SimpleNamespace(aio=SimpleNamespace(interactions=_HangingInteractions()))
    slow = gemini_mod.Gemini(mock=False, api_key="test", meta_api_key="", client=fake)
    monkeypatch.setattr(gemini_mod, "_singleton", slow)
    for name in ("PROFILE_TIMEOUT_S", "OVERLAP_TIMEOUT_S", "TRAIL_TIMEOUT_S"):
        monkeypatch.setattr(main, name, 0.3)


def test_profile_falls_back_to_heuristic_on_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = create(client, quick=True)
    consent(client, sid, 1)
    from tests.test_api import answer

    assert answer(client, sid, 1, "q1").status_code == 200
    hang_gemini(monkeypatch)
    r = client.post(f"/api/sessions/{sid}/seats/1/profile")
    assert r.status_code == 200, r.text  # was a 504
    assert r.json()["items"]


def test_overlaps_and_trail_fall_back_to_heuristic_on_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    store: SessionStore = client.app.state.store
    sid = create(client)
    setup_ready(client, sid)  # profiles built in mock mode
    hang_gemini(monkeypatch)
    for seat in (1, 2):
        assert client.post(f"/api/sessions/{sid}/seats/{seat}/ready").status_code == 200
    session = store.peek(sid)
    assert session is not None and session.overlaps is not None
    assert session.overlaps.overlaps, "the opener should still be overlap-based (F11)"
    client.post(f"/api/sessions/{sid}/end")
    client.post(f"/api/sessions/{sid}/seats/1/trail-optin", json={"share": True, "contact": "@maya"})
    client.post(f"/api/sessions/{sid}/seats/2/trail-optin", json={"share": True, "contact": "@dev"})
    t1 = poll_trail(client, sid, 1)
    assert t1["matched"] is True and t1["trail"], "both opted in, so the heuristic trail must be there"
    assert _HangingInteractions.calls > 0


# --- bot speech that the Table never finished (PRD 8.3, F8, F9) -----------------------------------


def test_table_reload_mid_crumb_does_not_leave_bot_speaking(client: TestClient) -> None:
    sid = create(client)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        ready_both(client, sid)
        with client.websocket_connect(f"/ws/audio/{sid}") as audio:
            crumb_id = go_live(client, sid, table, audio)["crumb"]["id"]
            table.send_json({"type": "tts_state", "crumb_id": crumb_id, "playing": True})
            wait_for(table, lambda m: m["type"] == "engine" and m["engine"]["tts_playing"] is True)
    # The page reloaded mid-crumb: `playing:false` was never sent.
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        snap = wait_for(table, of_type("engine"))["engine"]
        assert snap["tts_playing"] is False
        table.send_json({"type": "tts_state", "crumb_id": crumb_id, "playing": False})  # a late replay is harmless
        wait_for(table, lambda m: m["type"] == "engine" and m["engine"]["tts_playing"] is False)


def test_open_bot_speech_expires(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "MAX_BOT_SPEECH_S", 0.5, raising=False)
    sid = create(client)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        ready_both(client, sid)
        with client.websocket_connect(f"/ws/audio/{sid}") as audio:
            crumb_id = go_live(client, sid, table, audio)["crumb"]["id"]
            table.send_json({"type": "tts_state", "crumb_id": crumb_id, "playing": True})
            wait_for(table, lambda m: m["type"] == "engine" and m["engine"]["tts_playing"] is True)
            wait_for(table, lambda m: m["type"] == "engine" and m["engine"]["tts_playing"] is False, timeout=3)


# --- cache refill after a dispatch (F14, F8) ------------------------------------------------------


def test_used_cache_slot_is_refilled_right_away(client: TestClient) -> None:
    def both_cached(m: dict) -> bool:
        return m["type"] == "engine" and all(m["engine"]["cached"].values())

    sid = create(client)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        ready_both(client, sid)
        with client.websocket_connect(f"/ws/audio/{sid}") as audio:
            go_live(client, sid, table, audio)
            wait_for(table, both_cached)
            table.send_json({"type": "control", "action": "spark"})
            evt = wait_for(table, of_type("crumb"))
            assert evt["cache_hit"] is True
            dispatched = time.monotonic()
            wait_for(table, both_cached, timeout=3)  # was empty until the 30 s refresh or the next turn
            assert time.monotonic() - dispatched < 3


# --- a second Table's audio socket takes Muse over, then leaves (PRD 11.1, F5) --------------------


def test_original_table_reclaims_muse_after_second_table_leaves(client: TestClient) -> None:
    sid = create(client)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        ready_both(client, sid)
        with client.websocket_connect(f"/ws/audio/{sid}") as audio_a:
            go_live(client, sid, table, audio_a)
            with client.websocket_connect(f"/ws/audio/{sid}") as audio_b:
                audio_b.send_bytes(SILENCE_FRAME)  # Table B takes Muse over
                wait_for(table, lambda m: m["type"] == "muse_status" and m["status"] == "connecting")
            wait_for(table, lambda m: m["type"] == "muse_status" and m["status"] == "closed")
            # Table A is still streaming: its next frame must open a fresh Muse session for it.
            audio_a.send_bytes(SILENCE_FRAME)
            wait_for(table, lambda m: m["type"] == "muse_status" and m["status"] == "connecting", timeout=3)
            wait_for(table, lambda m: m["type"] == "system_say" and m["id"] == "sys_resync1")  # voices re-mapped


# --- a crumb cancelled while it was generated never reaches the Table (PRD 8.1) -------------------


class _GatedCrumbs:
    """A CrumbService stand-in whose `get` waits until the test opens the gate."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.delivered: list[str] = []

    async def get(self, kind, convo, now):  # noqa: ANN001
        from tests.test_policy import crumb
        await self.gate.wait()
        return runner_mod.Delivery(crumb=crumb("c_gen", kind), audio_wav=None, cache_hit=False, gen_ms=5.0)

    def mark_delivered(self, crumb) -> None:  # noqa: ANN001
        self.delivered.append(crumb.id)

    def cached_summary(self, now: float) -> dict:
        return {}


def _gated_runner() -> tuple[runner_mod.SessionRunner, _GatedCrumbs, list[dict], list[float]]:
    from config import Params
    from tests.test_policy import NAMES, calibrated, of_type, speak, ticks
    sent: list[dict] = []
    clock = [32.0]
    session = SimpleNamespace(mode="social", names=NAMES, params=Params(), profiles={},
                              broadcast=lambda event, roles=None: sent.append(event) or 1)
    crumbs = _GatedCrumbs()
    runner = runner_mod.SessionRunner(session, overlaps=None, on_round_end=None,  # type: ignore[arg-type]
                                      clock=lambda: clock[0], crumbs=crumbs)  # type: ignore[arg-type]
    e, _ = calibrated()
    speak(e, 20, "A", "We should totally go back there.", 12.0, 14.0)
    assert [f["kind"] for f in of_type(ticks(e, 14.1, 32.0), "deliver")] == ["switch"]
    runner.engine = e
    return runner, crumbs, sent, clock


async def test_crumb_cancelled_by_speech_while_generating_is_not_sent() -> None:
    from tests.test_policy import speak
    runner, crumbs, sent, clock = _gated_runner()
    task = asyncio.create_task(runner._deliver("switch"))
    await asyncio.sleep(0)
    speak(runner.engine, 21, "B", "Totally.", 32.1, 32.4)  # speechStart/speechEnd during generation
    assert runner.engine.pending_cancelled
    clock[0] = 33.0
    crumbs.gate.set()
    await task
    assert [m for m in sent if m["type"] == "crumb"] == []
    assert crumbs.delivered == []
    snap = runner.engine.snapshot(33.0)
    assert snap["crumbs_last_10min"] == 0
    assert snap["pending_delivery"] is False


async def test_crumb_not_cancelled_is_still_sent() -> None:
    runner, crumbs, sent, clock = _gated_runner()
    task = asyncio.create_task(runner._deliver("switch"))
    await asyncio.sleep(0)
    clock[0] = 33.0
    crumbs.gate.set()
    await task
    assert [m["crumb"]["id"] for m in sent if m["type"] == "crumb"] == ["c_gen"]
    assert crumbs.delivered == ["c_gen"]
    assert runner.engine.snapshot(33.0)["crumbs_last_10min"] == 1
