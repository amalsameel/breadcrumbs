"""The "Just talk" setup (setup="quick"): no phones, consent on the Table, live at once, voices mapped by
order of appearance, a light icebreaker, the live memory and the Table's own trail.

Engine tests run on a fake clock; the API tests run the whole flow over REST + WebSockets in mock mode.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import config
import main
from ai import gemini as gemini_mod
from ai.classifiers import EnergyMemoryDraft, tag_energy_memory
from ai.crumbs import ConvoCtx, CrumbService
from ai.gemini import Gemini
from ai.memory import Memory, MemoryDelta, mock_memory, prompt_view
from ai.trail import generate_trail
from config import Params
from engine.core import ICEBREAKER_REASON, Engine
from main import create_app
from models import OverlapSet, Profile, ProfileItem
from session_store import SessionStore
from tests.test_api import SILENCE_FRAME, expect_close, of_type, summary, wait_for
from tests.test_noise_echo import vad_frames
from tests.test_policy import decisions, speak
from tests.test_policy import of_type as fx_of_type

ANON = {1: "Person 1", 2: "Person 2"}
LINES = [
    "So I've been training for my first half marathon, the race is in November.",
    "That's awesome, what got you into running in the first place?",
    "Honestly a friend dragged me to a five k and I was hooked after that.",
    "Mostly the gym downtown, but I did a trip to Enchanted Rock last spring.",
]


def quick_engine(params: Params | None = None, names: dict[int, str] | None = None) -> tuple[Engine, list[dict]]:
    e = Engine("social", names or ANON, params or Params(), setup="quick")
    return e, e.start_calibration(0.0)


def near_turn(e: Engine, mid: int, label: str, text: str, start: float, end: float, level_db: float = -30.0) -> list[dict]:
    """A Muse turn the Table VAD hears as someone at the table, then a short quiet."""
    fx = vad_frames(e, start - 0.2, end - 0.1, level_db=level_db)
    fx += speak(e, mid, label, text, start, end, complete=0.1)
    fx += vad_frames(e, end, end + 0.3, active=False, level_db=-60.0)
    return fx


# --- engine: going live, mapping, icebreaker ------------------------------------------------------------


def test_quick_goes_live_at_once_with_no_prompts_and_no_opener() -> None:
    e, fx = quick_engine()
    assert e.phase == "live"
    assert fx_of_type(fx, "live") and not fx_of_type(fx, "say") and not fx_of_type(fx, "calibration")
    assert not fx_of_type(fx, "deliver")  # no overlap opener
    assert decisions(fx, "calibration")[0]["inputs"]["step"] == "auto"


def test_icebreaker_fires_once_after_t_long_of_silence() -> None:
    e, _ = quick_engine()
    fx = vad_frames(e, 0.1, 2.8, active=False, level_db=-60.0)
    assert not fx_of_type(fx, "deliver")
    fx = vad_frames(e, 2.9, 3.2, active=False, level_db=-60.0)
    delivers = fx_of_type(fx, "deliver")
    assert [d["kind"] for d in delivers] == ["opener"] and delivers[0]["reason"] == ICEBREAKER_REASON
    from tests.test_policy import crumb
    e.crumb_dispatched(crumb("c_1", "opener"), 3.2, 50.0, False)
    e.on_tts_state("c_1", True, 3.3)
    e.on_tts_state("c_1", False, 6.0)
    later = vad_frames(e, 6.1, 12.0, active=False, level_db=-60.0)
    assert not [d for d in fx_of_type(later, "deliver") if d["kind"] == "opener"]  # only once


def test_no_icebreaker_once_someone_at_the_table_spoke() -> None:
    e, _ = quick_engine()
    near_turn(e, 1, "A", "Hi, so how do you know the host?", 0.8, 2.0)
    fx = vad_frames(e, 2.4, 8.0, active=False, level_db=-60.0)
    assert not [d for d in fx_of_type(fx, "deliver") if d["kind"] == "opener"]
    assert e._icebreaker_due is False


def test_an_icebreaker_cancelled_by_noise_comes_back_but_never_after_a_seat_turn() -> None:
    e, _ = quick_engine()
    fx = vad_frames(e, 0.1, 3.1, active=False, level_db=-60.0)
    assert fx_of_type(fx, "deliver")
    fx = e.on_vad(True, 0.05, 3.2, level_db=-30.0)  # a near onset while it is generated: cancelled, not queued
    assert e.pending_cancelled and e._queued is None
    e.crumb_cancelled(3.3)
    assert e._icebreaker_due  # nobody spoke a turn: armed again
    near_turn(e, 1, "A", "Okay hi, I'm here for the robotics thing.", 3.4, 5.0)
    assert not e._icebreaker_due


def test_labels_map_to_person_1_and_2_by_order_of_first_appearance() -> None:
    e, _ = quick_engine()
    fx = near_turn(e, 1, "B", "Hi, I came with my roommate tonight.", 0.5, 2.0)
    assert e.label_map == {"B": 1}
    assert not fx_of_type(fx, "say") and not fx_of_type(fx, "calibration")
    near_turn(e, 2, "A", "Oh nice, I came on my own actually.", 2.5, 4.0)
    assert e.label_map == {"B": 1, "A": 2}
    assert [t.seat for t in e.turns] == [1, 2]
    transcript = e.recent_transcript(5.0)
    assert transcript.startswith("Person 1: Hi") and "Person 2: Oh nice" in transcript


def test_a_far_voice_never_takes_a_free_seat() -> None:
    e, _ = quick_engine()
    near_turn(e, 1, "A", "Hi, I came with my roommate tonight.", 0.5, 2.0)
    # A voice behind the table: Muse hears it (label C), the Table mic barely does.
    fx = e.on_muse_event({"type": "speechStart", "turnId": 2}, 2.5)
    fx += vad_frames(e, 2.5, 4.4, active=False, level_db=-60.0)
    fx += speak(e, 2, "C", "did you see the game last night though", 2.5, 4.5, complete=0.1)
    assert e.label_map == {"A": 1}
    assert decisions(fx, "background_turn") and e.turns[-1].background
    near_turn(e, 3, "B", "Same, my roommate is over there somewhere.", 5.0, 6.5)
    assert e.label_map == {"A": 1, "B": 2}


def test_dev_turns_take_the_seat_they_were_typed_as() -> None:
    e, _ = quick_engine()
    e.on_muse_event({"type": "speechStart", "turnId": 1, "dev": True, "seat": 2}, 0.5)
    e.on_muse_event({"type": "speaker", "label": "A"}, 1.0)
    e.on_muse_event({"type": "speechEnd", "turnId": 1, "dev": True}, 1.1)
    assert e.label_map == {"A": 2}


def test_crumbs_do_not_wait_for_both_voices_and_reconnect_needs_no_prompts() -> None:
    p = Params(WARMUP_S=0)
    e, _ = quick_engine(p)
    near_turn(e, 1, "A", "So I just moved here from Chicago for a new job.", 0.5, 2.5)
    near_turn(e, 2, "A", "Honestly it has been a lot, but the tacos help.", 3.0, 5.0)
    fx = vad_frames(e, 5.4, 9.0, active=False, level_db=-60.0)
    assert any(d["kind"] in ("steer", "switch") for d in fx_of_type(fx, "deliver"))  # one voice mapped is enough
    fx = e.on_muse_reconnect(20.0)
    assert not fx_of_type(fx, "say") and e.label_map == {} and e.phase == "live"


# --- memory ----------------------------------------------------------------------------------------------


def test_memory_merge_filters_dedupes_and_caps() -> None:
    m = Memory()
    assert m.merge(MemoryDelta(person1=["training for a half marathon in November", "hurt their knee last year"],
                               person2=["teaches high school chemistry"], threads=["what got them into running"]))
    d = m.to_dict()
    assert d["1"] == ["training for a half marathon in November"]  # an injury is a sensitive category
    assert d["2"] == ["teaches high school chemistry"] and d["threads"] == ["what got them into running"]
    assert not m.merge(MemoryDelta(person1=["training for the half marathon in November"]))  # same thing
    assert not m.merge(MemoryDelta(person2=["loves her work at the hospital"]), blocked=["work"])
    hobbies = ["sailing boats", "pottery classes", "chess openings", "vinyl records", "trail running", "bread baking",
               "salsa dancing", "film photography", "bird watching", "board games", "street tacos", "jazz piano"]
    for hobby in hobbies:
        m.merge(MemoryDelta(person2=[hobby]))
    assert len(m.items("2")) == 8  # oldest dropped first
    m.clear()
    assert m.empty


def test_mock_memory_learns_from_attributed_lines_only() -> None:
    context = "\n".join(f"{['Maya', 'Dev'][i % 2]}: {line}" for i, line in enumerate(LINES)) + "\nSomeone: I love sailing boats"
    delta = mock_memory(context, {1: "Maya", 2: "Dev"})
    assert any("half marathon" in i for i in delta.person1)
    assert any("Enchanted Rock" in i for i in delta.person2)
    assert delta.threads == ["What got you into running in the first place?"]
    assert not any("sailing" in i for lst in delta.lists().values() for i in lst)


async def test_memory_rides_on_the_energy_tag_call() -> None:
    calls: list[str] = []

    class OneCall(Gemini):
        async def json(self, *, schema, **kw):  # type: ignore[override]
            calls.append(schema.__name__)
            return EnergyMemoryDraft(topic="running", energy="high", winding_down=False,
                                     person1=["training for a half marathon"], threads=["the Austin half route"])

    tag, delta = await tag_energy_memory("Maya: hi", OneCall(mock=False, api_key="", meta_api_key=""),
                                         names={1: "Maya", 2: "Dev"})
    assert calls == ["EnergyMemoryDraft"]  # one call for both
    assert tag.topic == "running" and delta.person1 == ["training for a half marathon"]
    mock_tag, mock_delta = await tag_energy_memory("Maya: " + LINES[0], Gemini(mock=True), names={1: "Maya", 2: "Dev"})
    assert mock_tag.topic and mock_delta.person1


# --- crumbs with no profiles and no names -----------------------------------------------------------------


def quick_service(names: dict[int, str] = ANON, anonymous: frozenset[int] = frozenset({1, 2})) -> CrumbService:
    return CrumbService(mode="social", names=names, profiles={}, overlaps=OverlapSet(overlaps=[]),
                        location="Austin, Texas", gemini=Gemini(mock=True), setup="quick", anonymous=anonymous)


MEMORY = {"1": ["training for a half marathon in November"], "2": ["did a trip to Enchanted Rock"],
          "shared": [], "threads": ["What got you into running in the first place?"]}


def ctx(transcript: str = "", memory: dict | None = None) -> ConvoCtx:
    return ConvoCtx(recent_transcript=transcript, current_topic=None, depth_max=1, minutes_live=1.0, memory=memory)


async def test_quick_switch_picks_up_a_thread_from_memory_then_alternates() -> None:
    svc = quick_service()
    first = await svc.get("switch", ctx(memory=MEMORY), 0.0)
    assert first.crumb.source == "conversation" and "running" in first.crumb.text
    assert "Person" not in first.crumb.text  # labels are never spoken
    svc.mark_delivered(first.crumb)
    assert "what got you into running in the first place?" in svc.used_memory
    second = await svc.get("switch", ctx(memory=MEMORY), 1.0)
    assert second.crumb.source == "outside"  # alternates with an outside question
    svc.mark_delivered(second.crumb)
    payload = svc.crumb_payload("switch", ctx(memory=MEMORY))
    assert payload["setup"] == "just talk" and payload["names_unknown"] == ["1", "2"]
    assert payload["requested_memory"] == "training for a half marathon in November"  # thread used: next item
    assert payload["memory"]["open_threads"] == []


async def test_anonymous_validation_needs_no_name_and_rejects_placeholders() -> None:
    svc = quick_service()
    steer = await svc.get("steer", ctx("Person 1: I just got back from a climbing trip in Colorado."), 0.0)
    assert steer.crumb.source == "conversation" and "Person" not in steer.crumb.text
    from tests.test_policy import crumb
    bad = crumb("c_x", "steer").model_copy(update={"text": "Person 1, tell us more about Colorado?"})
    ok, _, errors = svc._validate(bad)
    assert not ok and "placeholder name" in errors
    canned = svc._canned("switch", ctx())
    assert "{" not in canned.text and "Person" not in canned.text


async def test_icebreaker_is_a_fixed_light_question() -> None:
    svc = quick_service()
    d = await svc.opener(ctx())
    assert d.crumb.kind == "opener" and d.crumb.topic == "icebreaker" and "Breadcrumbs" in d.crumb.text
    assert "Person" not in d.crumb.text and d.cache_hit is False


async def test_named_people_are_still_named_in_memory_crumbs() -> None:
    svc = quick_service({1: "Maya", 2: "Dev"}, frozenset())
    d = await svc.get("switch", ctx(memory={**MEMORY, "threads": []}), 0.0)
    assert d.crumb.source == "conversation" and ("Maya" in d.crumb.text or "Dev" in d.crumb.text)


async def test_profiles_crumb_payload_gains_memory_only_when_there_is_some() -> None:
    item = ProfileItem(id="s1_i1", kind="interest", text="rock climbing", hook="Where do you climb?", depth=1)
    svc = CrumbService(mode="social", names={1: "Maya", 2: "Dev"},
                       profiles={1: Profile(seat=1, display_name="Maya", items=[item])},
                       overlaps=OverlapSet(overlaps=[]), location="Austin", gemini=Gemini(mock=True))
    assert "memory" not in svc.crumb_payload("steer", ctx())
    assert svc.crumb_payload("steer", ctx(memory=MEMORY))["memory"]["Maya"] == MEMORY["1"]
    assert prompt_view({"1": [], "2": [], "shared": [], "threads": []}, {1: "a", 2: "b"}) is None


async def test_table_trail_uses_memory_and_never_writes_labels() -> None:
    trail = await generate_trail("social", ANON, "Person 1: I'm going to run the Austin half in November.", {},
                                 gemini=Gemini(mock=True), memory=MEMORY, anonymous={1, 2}, table=True)
    texts = [i.text for i in trail.items]
    assert texts and not any("Person" in t for t in texts)
    assert sum(1 for i in trail.items if i.for_seat == 1) <= 3 and sum(1 for i in trail.items if i.for_seat == 2) <= 3
    assert any("running" in t for t in texts)


async def test_a_safety_pause_keeps_memory_out_of_the_table_trail() -> None:
    # PRD 15.3: memory is learned from the transcript, so after a distress pause none of it becomes a thread.
    trail = await generate_trail("social", ANON, "Person 1: I had a panic attack before work today.", {}, gemini=Gemini(mock=True),
                                 memory=MEMORY, anonymous={1, 2}, table=True, safety_paused=True)
    assert trail.items == []


# --- API: the whole "Just talk" flow -----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MUSE_MOCK", True)
    monkeypatch.setattr(config, "GEMINI_MOCK", True)
    monkeypatch.setattr(config, "DEV_TOOLS", True)
    monkeypatch.setattr(gemini_mod, "_singleton", gemini_mod.Gemini(mock=True))


@pytest.fixture
def client():
    with TestClient(create_app(SessionStore())) as c:
        yield c


FAST = {"T_NORMAL_S": 0.3, "T_LONG_S": 0.7, "T_EXC_S": 1.2, "WARMUP_S": 0, "MIN_GAP_BETWEEN_CRUMBS_S": 0,
        "MIN_HUMAN_TURNS_BETWEEN": 0, "ENERGY_EVERY_N_TURNS": 2}


def test_create_defaults_to_just_talk(client: TestClient) -> None:
    r = client.post("/api/sessions", json={}).json()
    assert r["setup"] == "quick" and r["join_urls"] == {}
    s = summary(client, r["session_id"])
    assert (s["setup"], s["mode"], s["phase"]) == ("quick", "social", "lobby")
    legacy = client.post("/api/sessions", json={"mode": "social", "quick": True}).json()
    assert legacy["setup"] == "quick" and summary(client, legacy["session_id"])["quick"] is False


def test_start_is_quick_only_and_only_once(client: TestClient) -> None:
    profiles_sid = client.post("/api/sessions", json={"setup": "profiles"}).json()["session_id"]
    assert client.post(f"/api/sessions/{profiles_sid}/start", json={}).status_code == 409
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    assert client.post(f"/api/sessions/{sid}/start", json={"names": {"1": "  Maya  ", "2": ""}}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/start", json={}).status_code == 409
    s = summary(client, sid)
    assert s["phase"] == "calibrating"
    assert [s["seats"][k]["name"] for k in ("1", "2")] == ["Maya", "Person 2"]
    assert all(s["seats"][k]["consented"] and s["seats"][k]["ready"] for k in ("1", "2"))
    session = client.app.state.store.peek(sid)
    assert {session.seat(k).consent.source for k in (1, 2)} == {"table"}
    assert session.anonymous == frozenset({2}) and session.seat(1).consent.at > 0


def test_phone_links_of_a_quick_table_say_no_phones(client: TestClient) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    r = client.post(f"/api/sessions/{sid}/seats/1/consent", json={"typed_name": "Maya", "agreed": True})
    assert r.status_code == 409 and "doesn't need phones" in r.json()["detail"]
    assert client.get(f"/api/sessions/{sid}/seats/1/profile").status_code == 409
    assert client.post(f"/api/sessions/{sid}/seats/1/ready").status_code == 409
    with client.websocket_connect(f"/ws/events/{sid}?role=seat1") as phone:
        assert phone.receive_json()["session"]["setup"] == "quick"
    assert summary(client, sid)["seats"]["1"]["joined"] is False
    assert client.get(f"/api/sessions/{sid}/trail").status_code == 409  # not ended yet


def test_just_talk_full_flow(client: TestClient) -> None:
    store: SessionStore = client.app.state.store
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    assert client.patch(f"/api/sessions/{sid}/params", json=FAST).status_code == 200
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        assert table.receive_json()["session"]["setup"] == "quick"
        expect_close(client, f"/ws/audio/{sid}", 4403)  # no consent yet
        assert client.post(f"/api/sessions/{sid}/start", json={}).status_code == 200
        wait_for(table, lambda m: m["type"] == "session" and m["session"]["phase"] == "calibrating")
        with client.websocket_connect(f"/ws/audio/{sid}") as audio:
            audio.send_bytes(SILENCE_FRAME)
            seen: list[str] = []
            live = wait_for(table, lambda m: seen.append(m["type"]) or (m["type"] == "session" and m["session"]["phase"] == "live"))
            assert live and "system_say" not in seen and "calibration" not in seen
            ice = wait_for(table, of_type("crumb"), timeout=5)
            assert ice["crumb"]["kind"] == "opener" and ice["crumb"]["topic"] == "icebreaker"
            for playing in (True, False):
                table.send_json({"type": "tts_state", "crumb_id": ice["crumb"]["id"], "playing": playing})
            for i, line in enumerate(LINES):
                table.send_json({"type": "dev_turn", "seat": 2 - i % 2, "text": line, "duration_s": 0.3})
                time.sleep(0.6)
            memory = wait_for(table, of_type("memory"), timeout=5)["memory"]
            assert set(memory) == {"1", "2", "shared", "threads"} and any(memory.values())
            snap = wait_for(table, of_type("engine"))["engine"]
            assert snap["label_map"] == {"A": 2, "B": 1}  # dev turns take the seat they were typed as
            assert snap["memory"] == memory or sum(map(len, snap["memory"].values())) >= sum(map(len, memory.values()))
            auto = wait_for(table, lambda m: m["type"] == "crumb" and m["crumb"]["kind"] in ("steer", "switch"), timeout=8)
            assert "Person" not in auto["crumb"]["text"]
        assert client.post(f"/api/sessions/{sid}/end").status_code == 200
        wait_for(table, lambda m: m["type"] == "session" and m["session"]["phase"] == "ended")
        trail = wait_for(table, of_type("table_trail"), timeout=10)["trail"]
        assert trail["items"] and not any("Person" in i["text"] for i in trail["items"])
        assert client.get(f"/api/sessions/{sid}/trail").json() == {"trail": trail}
        session = store.peek(sid)
        assert session is not None and session.memory is None and session.transcript == ""
        with client.websocket_connect(f"/ws/events/{sid}?role=table") as again:  # a reload shows it again
            assert wait_for(again, of_type("table_trail"))["trail"] == trail
        assert client.delete(f"/api/sessions/{sid}").status_code == 200  # the Done button
        wait_for(table, of_type("wiped"))
        with pytest.raises(WebSocketDisconnect):
            table.receive_json()
    assert store.peek(sid) is None


def test_a_safety_paused_quick_session_ends_with_an_empty_trail(client: TestClient) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.patch(f"/api/sessions/{sid}/params", json=FAST)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        client.post(f"/api/sessions/{sid}/start", json={})
        for i, line in enumerate(LINES):
            table.send_json({"type": "dev_turn", "seat": 1 + i % 2, "text": line, "duration_s": 0.2})
            time.sleep(0.4)

        def played(m: dict) -> bool:  # report every crumb as played, so the next turn is not taken for bot speech
            if m["type"] == "crumb":
                for playing in (True, False):
                    table.send_json({"type": "tts_state", "crumb_id": m["crumb"]["id"], "playing": playing})
            return False

        assert any(wait_for(table, lambda m: played(m) or m["type"] == "memory", timeout=5)["memory"].values())
        time.sleep(0.3)
        table.send_json({"type": "dev_turn", "seat": 1, "text": "Honestly I had a panic attack before work today.", "duration_s": 0.2})
        wait_for(table, lambda m: played(m) or (m["type"] == "decision" and m["decision"]["action"] == "safety_pause"),
                 timeout=5)
        client.post(f"/api/sessions/{sid}/end")
        assert wait_for(table, of_type("table_trail"), timeout=10)["trail"]["items"] == []


def test_a_quick_session_is_wiped_a_while_after_it_ended(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "QUICK_WIPE_AFTER_END_S", 0.3)
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/start", json={"names": {"1": "Maya", "2": "Dev"}})
    assert client.post(f"/api/sessions/{sid}/end").status_code == 200
    deadline = time.monotonic() + 5
    while client.get(f"/api/sessions/{sid}").status_code == 200:
        assert time.monotonic() < deadline, "not wiped"
        time.sleep(0.1)
    assert client.app.state.store.peek(sid) is None


def test_runner_never_logs_memory_text(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.patch(f"/api/sessions/{sid}/params", json=FAST)
    with client.websocket_connect(f"/ws/events/{sid}?role=table") as table:
        client.post(f"/api/sessions/{sid}/start", json={})
        for i, line in enumerate(LINES):
            table.send_json({"type": "dev_turn", "seat": 1 + i % 2, "text": line, "duration_s": 0.2})
            time.sleep(0.4)
        wait_for(table, of_type("memory"), timeout=5)
        client.post(f"/api/sessions/{sid}/end")
        wait_for(table, of_type("table_trail"), timeout=10)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "marathon" not in text and "Enchanted" not in text and "memory updated" in text
