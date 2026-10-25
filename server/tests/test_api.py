"""Integration tests: REST + WebSockets + runner, end to end in mock mode (docs/CONTRACTS.md section 7)."""
from __future__ import annotations

import io
import math
import struct
import time
import wave
from collections import Counter
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import config
import main
from ai import gemini as gemini_mod
from audio import convert
from main import create_app
from session_store import SessionStore

SILENCE_FRAME = bytes(3840)  # 80 ms of PCM16 mono at 24 kHz
NAMES = {1: "Maya", 2: "Dev"}


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force mock services even if a developer's .env has real keys."""
    monkeypatch.setattr(config, "MUSE_MOCK", True)
    monkeypatch.setattr(config, "GEMINI_MOCK", True)
    monkeypatch.setattr(config, "DEV_TOOLS", True)
    monkeypatch.setattr(gemini_mod, "_singleton", gemini_mod.Gemini(mock=True))


def make_client(store: SessionStore | None = None) -> TestClient:
    return TestClient(create_app(store if store is not None else SessionStore()))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client() as c:
        yield c


def tiny_wav(seconds: float = 0.4) -> bytes:
    """A 24 kHz mono 16-bit sine (already in Muse's format, so no ffmpeg is needed)."""
    rate = 24_000
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(rate * seconds))
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return out.getvalue()


def wait_for(ws, predicate: Callable[[dict], bool], timeout: float = 10.0) -> dict:
    """Read Table events until one matches. Engine snapshots arrive at 4 Hz, so reads never stall long."""
    deadline = time.monotonic() + timeout
    seen: Counter[str] = Counter()
    while time.monotonic() < deadline:
        msg = ws.receive_json()
        seen[str(msg.get("type"))] += 1
        if predicate(msg):
            return msg
    raise AssertionError(f"timed out waiting for an event; saw {dict(seen)}")


def of_type(kind: str, **fields) -> Callable[[dict], bool]:
    return lambda m: m.get("type") == kind and all(m.get(k) == v for k, v in fields.items())


def create(client: TestClient, mode: str = "social", quick: bool = False) -> str:
    r = client.post("/api/sessions", json={"mode": mode, "quick": quick})
    assert r.status_code == 200
    return r.json()["session_id"]


def consent(client: TestClient, sid: str, seat: int) -> None:
    r = client.post(f"/api/sessions/{sid}/seats/{seat}/consent", json={"typed_name": NAMES[seat], "agreed": True})
    assert r.status_code == 200, r.text


def answer(client: TestClient, sid: str, seat: int, qid: str, data: bytes | None = None, name: str = "answer.wav"):
    return client.post(
        f"/api/sessions/{sid}/seats/{seat}/answers",
        data={"question_id": qid},
        files={"audio": (name, data if data is not None else tiny_wav(), "audio/wav")},
    )


def summary(client: TestClient, sid: str) -> dict:
    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    return r.json()


def setup_ready(client: TestClient, sid: str, quick: bool = False) -> dict[int, dict]:
    """Consent, interview, profile and ready for both seats. Returns the built profiles."""
    profiles: dict[int, dict] = {}
    qids = ["q1"] if quick else ["q1", "q2", "q3"]
    for seat in (1, 2):
        consent(client, sid, seat)
        for qid in qids:
            r = answer(client, sid, seat, qid)
            assert r.status_code == 200, r.text
            assert r.json()["transcript"]
        r = client.post(f"/api/sessions/{sid}/seats/{seat}/profile")
        assert r.status_code == 200, r.text
        profiles[seat] = r.json()
    return profiles


def poll_trail(client: TestClient, sid: str, seat: int, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/sessions/{sid}/seats/{seat}/trail")
        if r.status_code == 200:
            return r.json()
        assert r.status_code == 409, r.text
        time.sleep(0.05)
    raise AssertionError("trail never became ready")


def expect_close(client: TestClient, path: str, code: int) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path) as ws:
            ws.receive_json()
    assert exc.value.code == code


# --- meta ----------------------------------------------------------------------------------------


def test_health_config_and_interview(client: TestClient) -> None:
    assert client.get("/api/health").json()["ok"] is True
    cfg = client.get("/api/config").json()
    assert cfg["mock"] == {"muse": True, "gemini": True}
    assert cfg["ai_provider"] == "mock"
    assert set(cfg) == {"mock", "ai_provider", "dev_tools", "public_base_url", "ffmpeg"}
    full = client.get("/api/interview", params={"mode": "networking", "quick": "false"}).json()["questions"]
    assert [q["id"] for q in full] == ["q1", "q2", "q3"]
    assert full[0]["text"] == "What are you working on that you're excited about?"
    quick = client.get("/api/interview", params={"mode": "speed_dating", "quick": "true"}).json()["questions"]
    assert quick == [{"id": "q1", "text": "What does a perfect Saturday look like for you?"}]


def test_create_session_join_urls(client: TestClient) -> None:
    r = client.post("/api/sessions", json={"mode": "networking", "quick": True}).json()
    sid = r["session_id"]
    assert r["join_urls"] == {"1": f"{config.PUBLIC_BASE_URL}/join/{sid}/1", "2": f"{config.PUBLIC_BASE_URL}/join/{sid}/2"}
    s = summary(client, sid)
    assert (s["phase"], s["mode"], s["quick"]) == ("lobby", "networking", True)
    assert s["seats"]["1"] == {
        "joined": False, "consented": False, "ready": False, "name": None,
        "has_profile": False, "answered": 0, "opted_in": None,
    }
    assert 0 < s["expires_in_s"] <= config.SESSION_TTL_S


# --- the full happy path ---------------------------------------------------------------------------


def test_full_flow_mock_mode(client: TestClient) -> None:
    store: SessionStore = client.app.state.store
    sid = create(client)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        first = table.receive_json()
        assert first["type"] == "session" and first["session"]["phase"] == "lobby"
        assert table.receive_json()["type"] == "params"

        expect_close(client, f"/ws/audio/{sid}", 4403)  # nobody has consented yet
        with client.websocket_connect(f"/ws/events/{sid}?role=seat1") as phone:
            assert phone.receive_json()["session"]["seats"]["1"]["joined"] is True

        profiles = setup_ready(client, sid)
        assert summary(client, sid)["seats"]["1"]["answered"] == 3

        # Seat 1 removes an item and marks "work" off-limits.
        removed_id = profiles[1]["items"][0]["id"]
        r = client.patch(f"/api/sessions/{sid}/seats/1/profile", json={"removed_item_ids": [removed_id, "bogus"], "off_limits": ["work", " work "]})
        assert r.status_code == 200
        assert r.json()["removed_item_ids"] == [removed_id]
        assert r.json()["off_limits"] == ["work"]
        assert client.get(f"/api/sessions/{sid}/seats/1/profile").json()["removed_item_ids"] == [removed_id]

        assert client.post(f"/api/sessions/{sid}/seats/1/ready").status_code == 200
        assert summary(client, sid)["phase"] == "profiles"
        assert client.post(f"/api/sessions/{sid}/seats/2/ready").status_code == 200
        assert summary(client, sid)["phase"] == "calibrating"
        wait_for(table, lambda m: m["type"] == "session" and m["session"]["phase"] == "calibrating")
        session = store.peek(sid)
        assert session is not None and session.runner is not None
        assert removed_id in session.runner.crumbs.profiles[1].removed_item_ids

        # Locked once calibrating.
        assert client.patch(f"/api/sessions/{sid}/seats/2/profile", json={"removed_item_ids": []}).status_code == 409

        with client.websocket_connect(f"/ws/audio/{sid}") as audio:
            audio.send_bytes(SILENCE_FRAME)  # the first frame opens Muse (mock) and starts calibration
            assert wait_for(table, of_type("muse_status"))["status"] == "connecting"
            wait_for(table, of_type("calibration", step="seat1"))
            wait_for(table, lambda m: m["type"] == "system_say" and m["id"] == "sys_cal1")

            table.send_json({"type": "dev_turn", "seat": 1, "text": "Hi, I'm Maya and I'm from Austin.", "duration_s": 0.3})
            wait_for(table, of_type("calibration", step="seat2"))
            audio.send_bytes(SILENCE_FRAME)
            table.send_json({"type": "dev_turn", "seat": 2, "text": "Hey, I'm Dev, I grew up in Houston.", "duration_s": 0.3})
            done = wait_for(table, of_type("calibration", step="done"))
            assert done["label_map"] == {"A": 1, "B": 2}
            wait_for(table, lambda m: m["type"] == "session" and m["session"]["phase"] == "live")

            crumb_evt = wait_for(table, of_type("crumb"))
            crumb = crumb_evt["crumb"]
            assert crumb["kind"] == "opener"
            assert crumb["id"].startswith("c_")
            assert removed_id not in crumb["source_item_ids"]
            assert crumb_evt["audio_b64"] == ""  # mock TTS: the Table uses speechSynthesis
            assert crumb_evt["latency_ms"] >= 0 and crumb_evt["cache_hit"] is False
            assert "\u2014" not in crumb["text"]  # no em dashes reach speech

            snap = wait_for(table, of_type("engine"))["engine"]
            assert snap["phase"] == "live" and snap["label_map"] == {"A": 1, "B": 2}
            assert set(snap["cached"]) == {"steer", "switch"}

            # Live tuning reaches the engine.
            assert client.patch(f"/api/sessions/{sid}/params", json={"T_LONG_S": 5.5}).json()["params"]["T_LONG_S"] == 5.5
            wait_for(table, lambda m: m["type"] == "engine" and m["engine"]["t_long"] == 5.5)

        metrics = client.get(f"/api/sessions/{sid}/metrics").json()
        assert metrics["metrics"]["crumbs"] == 1 and metrics["turns"] >= 2

        assert client.post(f"/api/sessions/{sid}/end").status_code == 200
        wait_for(table, lambda m: m["type"] == "session" and m["session"]["phase"] == "ended")
        expect_close(client, f"/ws/audio/{sid}", 4403)  # nothing is captured after End

        # Trail: both share.
        assert client.get(f"/api/sessions/{sid}/seats/1/trail").status_code == 409
        client.post(f"/api/sessions/{sid}/seats/1/trail-optin", json={"share": True, "contact": "@maya.runs"})
        s = summary(client, sid)
        assert s["seats"]["1"]["opted_in"] is True and s["seats"]["2"]["opted_in"] is None
        client.post(f"/api/sessions/{sid}/seats/2/trail-optin", json={"share": True, "contact": "dev@example.com"})
        t1 = poll_trail(client, sid, 1)
        assert t1["matched"] is True
        assert (t1["partner_name"], t1["partner_contact"]) == ("Dev", "dev@example.com")
        assert all(item["for_seat"] == 1 for item in t1["trail"]) and len(t1["trail"]) <= 3
        assert summary(client, sid)["phase"] == "ended"  # not wiped until both phones fetched

        t2 = client.get(f"/api/sessions/{sid}/seats/2/trail").json()
        assert t2["partner_contact"] == "@maya.runs"
        wait_for(table, of_type("wiped"))
        with pytest.raises(WebSocketDisconnect) as closed:
            table.receive_json()
        assert closed.value.code == 4404

    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert store.peek(sid) is None


def go_live(client: TestClient, sid: str, table, audio) -> dict:
    """From calibrating: open Muse with a frame, map both voices with dev turns, return the opener event."""
    audio.send_bytes(SILENCE_FRAME)
    wait_for(table, lambda m: m["type"] == "system_say" and m["id"] == "sys_cal1")
    table.send_json({"type": "dev_turn", "seat": 1, "text": "Hi there, I'm Maya.", "duration_s": 0.2})
    wait_for(table, lambda m: m["type"] == "system_say" and m["id"] == "sys_cal2")
    table.send_json({"type": "dev_turn", "seat": 2, "text": "Hey, I'm Dev, nice to meet you.", "duration_s": 0.2})
    wait_for(table, of_type("calibration", step="done"))
    return wait_for(table, of_type("crumb"))


def test_speed_dating_round_timer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import engine.core as core

    monkeypatch.setattr(core, "ROUND_TIMER_S", {**core.ROUND_TIMER_S, "speed_dating": 2.5})
    monkeypatch.setattr(core, "ROUND_WARNING_S", 1.5)
    # The warning keeps a gap after the previous crumb; in a 2.5 s round that gap would swallow it.
    monkeypatch.setattr(core, "ROUND_WARNING_GAP_S", 0.0, raising=False)
    sid = create(client, mode="speed_dating", quick=True)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        setup_ready(client, sid, quick=True)
        for seat in (1, 2):
            client.post(f"/api/sessions/{sid}/seats/{seat}/ready")
        with client.websocket_connect(f"/ws/audio/{sid}") as audio:
            opener = go_live(client, sid, table, audio)["crumb"]
            assert opener["kind"] == "opener"
            for playing in (True, False):  # the Table plays the opener, as a real one would
                table.send_json({"type": "tts_state", "crumb_id": opener["id"], "playing": playing})
            snap = wait_for(table, of_type("engine"))["engine"]
            assert snap["round_remaining_s"] is not None and snap["round_remaining_s"] <= 2.5
            warning = wait_for(table, of_type("crumb"), timeout=5)  # the 1-minute-left crumb (F21)
            assert warning["crumb"]["kind"] != "opener"
            wait_for(table, lambda m: m["type"] == "session" and m["session"]["phase"] == "ended", timeout=5)
    assert summary(client, sid)["phase"] == "ended"


def test_audio_reconnect_resyncs_voices(client: TestClient) -> None:
    sid = create(client)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        setup_ready(client, sid)
        for seat in (1, 2):
            client.post(f"/api/sessions/{sid}/seats/{seat}/ready")
        with client.websocket_connect(f"/ws/audio/{sid}") as audio:
            go_live(client, sid, table, audio)
        wait_for(table, lambda m: m["type"] == "muse_status" and m["status"] == "closed")
        with client.websocket_connect(f"/ws/audio/{sid}") as audio:
            audio.send_bytes(SILENCE_FRAME)  # a new Muse session: labels reset, so re-map the voices
            wait_for(table, lambda m: m["type"] == "system_say" and m["id"] == "sys_resync1")
            table.send_json({"type": "dev_turn", "seat": 1, "text": "Okay, Maya again.", "duration_s": 0.2})
            wait_for(table, lambda m: m["type"] == "system_say" and m["id"] == "sys_resync2")
            table.send_json({"type": "dev_turn", "seat": 2, "text": "And this is Dev.", "duration_s": 0.2})
            done = wait_for(table, of_type("calibration", step="done"))
            assert sorted(done["label_map"].values()) == [1, 2]
    assert summary(client, sid)["phase"] == "live"  # a re-sync never sends the session back to calibrating


def test_trail_one_no_reveals_nothing(client: TestClient) -> None:
    sid = create(client)
    consent(client, sid, 1)
    consent(client, sid, 2)
    assert client.post(f"/api/sessions/{sid}/seats/1/trail-optin", json={"share": True, "contact": "x"}).status_code == 409
    client.post(f"/api/sessions/{sid}/end")
    client.post(f"/api/sessions/{sid}/seats/1/trail-optin", json={"share": True, "contact": "@maya"})
    client.post(f"/api/sessions/{sid}/seats/2/trail-optin", json={"share": False, "contact": "@dev"})
    seats = summary(client, sid)["seats"]
    assert seats["1"]["opted_in"] is True and seats["2"]["opted_in"] is True  # answered-only, never the choice
    for seat in (1, 2):
        t = poll_trail(client, sid, seat)
        assert t == {"matched": False, "partner_name": None, "partner_contact": None, "trail": []}
    assert client.get(f"/api/sessions/{sid}").status_code == 404  # wiped after both fetched


# --- consent gating and uploads ---------------------------------------------------------------


def test_consent_gating(client: TestClient) -> None:
    sid = create(client)
    assert answer(client, sid, 1, "q1").status_code == 403
    assert client.post(f"/api/sessions/{sid}/seats/1/profile").status_code == 403
    assert client.post(f"/api/sessions/{sid}/seats/1/ready").status_code == 403
    r = client.post(f"/api/sessions/{sid}/seats/1/consent", json={"typed_name": "Maya", "agreed": False})
    assert r.status_code == 400
    assert client.post(f"/api/sessions/{sid}/seats/1/consent", json={"typed_name": "", "agreed": True}).status_code == 422
    assert client.post(f"/api/sessions/{sid}/seats/3/consent", json={"typed_name": "X", "agreed": True}).status_code == 404

    consent(client, sid, 1)
    s = summary(client, sid)
    assert s["phase"] == "profiles" and s["seats"]["1"]["consented"] and s["seats"]["1"]["name"] == "Maya"
    expect_close(client, f"/ws/audio/{sid}", 4403)  # both seats must consent
    assert answer(client, sid, 1, "q1").status_code == 200
    assert answer(client, sid, 2, "q1").status_code == 403

    consent(client, sid, 2)
    with client.websocket_connect(f"/ws/audio/{sid}") as audio:
        audio.send_bytes(SILENCE_FRAME)  # accepted; frames are dropped until both seats are ready


def test_answers_rules(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = create(client, mode="speed_dating", quick=True)
    consent(client, sid, 1)
    first = answer(client, sid, 1, "q1").json()["transcript"]
    assert answer(client, sid, 1, "q1").json()["transcript"] == first  # deterministic mock key
    assert summary(client, sid)["seats"]["1"]["answered"] == 1  # a re-record replaces the answer
    assert answer(client, sid, 1, "q2").status_code == 422  # quick profile: q1 only
    assert answer(client, sid, 1, "q1", data=b"").status_code == 422
    assert client.post(f"/api/sessions/{sid}/seats/2/profile").status_code == 403
    consent(client, sid, 2)
    assert client.post(f"/api/sessions/{sid}/seats/2/profile").status_code == 409  # no answers yet

    # No ffmpeg: mock mode still works for a phone container; a real key gets a clear 503.
    monkeypatch.setattr(convert, "ffmpeg_available", lambda: False)
    monkeypatch.setattr(main, "ffmpeg_available", lambda: False)
    webm = b"\x1aE\xdf\xa3" + bytes(200)
    assert answer(client, sid, 2, "q1", data=webm, name="answer.webm").status_code == 200
    monkeypatch.setattr(config, "MUSE_MOCK", False)
    r = answer(client, sid, 2, "q1", data=webm, name="answer.webm")
    assert r.status_code == 503 and "brew install ffmpeg" in r.json()["detail"]


def test_dev_turn_requires_live_session(client: TestClient) -> None:
    sid = create(client)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        table.receive_json()
        table.receive_json()
        table.send_json({"type": "dev_turn", "seat": 1, "text": "hello there"})
        assert table.receive_json()["type"] == "error"


# --- wipe and TTL -----------------------------------------------------------------------------------


def test_delete_wipes_everything(client: TestClient) -> None:
    store: SessionStore = client.app.state.store
    sid = create(client)
    consent(client, sid, 1)
    answer(client, sid, 1, "q1")
    session = store.peek(sid)
    assert session is not None
    with client.websocket_connect(f"/ws/events/{sid}?role=seat1") as phone:
        phone.receive_json()
        assert client.delete(f"/api/sessions/{sid}").json() == {"ok": True}
        assert phone.receive_json()["type"] == "wiped"
        with pytest.raises(WebSocketDisconnect) as closed:
            phone.receive_json()
        assert closed.value.code == 4404
    assert session.seat(1).answers == {} and session.seat(1).consent is None and session.runner is None
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.delete(f"/api/sessions/{sid}").status_code == 404
    expect_close(client, f"/ws/events/{sid}?role=table", 4404)
    expect_close(client, f"/ws/audio/{sid}", 4404)


def test_ttl_sweeper_wipes_expired_sessions() -> None:
    store = SessionStore(ttl_s=0.3, sweep_interval_s=0.05)
    with make_client(store) as client:
        sid = create(client)
        assert client.get(f"/api/sessions/{sid}").status_code == 200
        deadline = time.monotonic() + 3.0
        while sid in store and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sid not in store
        assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_expired_session_is_wiped_on_access() -> None:
    store = SessionStore(ttl_s=0.2, sweep_interval_s=3600)
    with make_client(store) as client:
        sid = create(client)
        time.sleep(0.3)
        assert sid in store  # the sweeper hasn't run
        assert client.get(f"/api/sessions/{sid}").status_code == 404
        assert sid not in store


# --- static web app ------------------------------------------------------------------------------


def test_spa_fallback(client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "WEB_DIST", tmp_path / "missing")
    assert client.get("/").status_code == 404  # not built: a clear 404, never a crash

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Breadcrumbs</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(main, "WEB_DIST", dist)
    for path in ("/", "/table/abc", "/join/abc/1"):
        r = client.get(path)
        assert r.status_code == 200 and "<title>Breadcrumbs</title>" in r.text
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert "<title>" in client.get("/../../etc/passwd").text  # traversal falls back to index.html
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
