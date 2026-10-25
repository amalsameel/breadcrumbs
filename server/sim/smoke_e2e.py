"""End-to-end smoke test against a running server (dev tool, mock mode friendly).

Drives the whole product over real HTTP + WebSockets, the way two phones and the Table would:
create -> consent x2 -> answers -> profiles -> ready x2 -> Table events + audio sockets (80 ms silence
frames in real time, VAD at 10 Hz) -> calibration via dev turns -> opener -> lively turns -> silence ->
steer about 3 s after the last turn -> Drop a crumb -> End -> opt-in x2 -> trail -> wiped.
The script plays the Table's part for TTS: it answers every crumb and system line with tts_state
start/end. It never prints transcript text, only event kinds and timings.

`--setup quick` drives the "Just talk" setup instead: create (no join links) -> phone links refused ->
"We're both in" (POST /start) on the Table -> audio -> live with no prompts -> the icebreaker about 3 s into
the first silence -> lively turns (the live memory arrives) -> steer about 3 s after the last turn -> Drop a
crumb -> End -> the Table's own trail -> Done (DELETE) -> wiped. `--setup both` runs both, profiles first.

Usage (server running with DEV_TOOLS=true):
    cd server && .venv/bin/python -m sim.smoke_e2e [--base http://localhost:8000] [--setup profiles|quick|both]
Exit code 0 when every check passes.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import math
import struct
import sys
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

FRAME_BYTES = 3840  # 80 ms of PCM16 mono at 24 kHz
FRAME_S = 0.08
VAD_EVERY_S = 0.1
CRUMB_PLAY_S = 2.0
SYSTEM_PLAY_S = 0.8
POST_TTS_GAP_S = 0.6  # wait past the 300 ms tail before speaking after a system line
GAP_BETWEEN_TURNS_S = 1.2
LIVELY_MIN_S = 33.0  # the regular cooldown is 30 s since the last crumb ended
NAMES = {1: "Maya", 2: "Dev"}

CALIBRATION_LINES = {
    1: "Hi Dev, I'm Maya and I'm from Austin, born and raised.",
    2: "Hey Maya, I'm Dev, I grew up in Houston but live here now.",
}
LIVELY_LINES = [
    (1, "So I've been training for my first half marathon, the race is in November."),
    (2, "That's awesome, what got you into running in the first place?"),
    (1, "Honestly a friend dragged me to a five k and I was hooked after that."),
    (2, "I used to run a lot too before I hurt my knee, now I mostly climb."),
    (1, "Climbing sounds so fun, do you go to a gym or climb outdoors?"),
    (2, "Mostly the gym downtown, but I did a trip to Enchanted Rock last spring."),
    (1, "I've always wanted to go there, is it good for total beginners?"),
    (2, "Yeah there are easy routes, and the view from the top is unreal."),
    (1, "Okay that's going on my list right after the race is done."),
    (2, "You should come with our group sometime, we go every other weekend."),
    (1, "I would love that, do you all get food afterwards or just climb?"),
    (2, "Always tacos afterwards, that's honestly half the reason I go."),
]


def tiny_wav(seconds: float = 0.4) -> bytes:
    rate = 24_000
    pcm = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(rate * seconds)))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class SmokeFailure(AssertionError):
    pass


@dataclass
class Timeline:
    t0: float = field(default_factory=time.monotonic)
    rows: list[tuple[float, str]] = field(default_factory=list)
    checks: list[tuple[bool, str]] = field(default_factory=list)

    def now(self) -> float:
        return time.monotonic() - self.t0

    def mark(self, label: str) -> float:
        t = self.now()
        self.rows.append((t, label))
        print(f"  {t:7.2f}s  {label}", flush=True)
        return t

    def check(self, ok: bool, label: str) -> None:
        self.checks.append((ok, label))
        print(f"  {'PASS' if ok else 'FAIL'}  {label}", flush=True)
        if not ok:
            raise SmokeFailure(label)


class TableClient:
    """Plays the Table: records events, answers crumbs/system lines with tts_state like CrumbPlayer."""

    def __init__(self, ws: ClientConnection, tl: Timeline) -> None:
        self.ws = ws
        self.tl = tl
        self.events: list[tuple[float, dict]] = []
        self.play_ended: dict[str, float] = {}
        self.closed_code: int | None = None
        self._changed = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    async def send(self, event: dict) -> None:
        await self.ws.send(json.dumps(event))

    async def reader(self) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                evt = json.loads(raw)
                self.events.append((self.tl.now(), evt))
                kind = evt.get("type")
                if kind == "crumb":
                    self._play(evt["crumb"]["id"], CRUMB_PLAY_S)
                elif kind == "system_say":
                    self._play(evt["id"], SYSTEM_PLAY_S)
                self._changed.set()
        except ConnectionClosed as exc:
            self.closed_code = exc.rcvd.code if exc.rcvd else None
        finally:
            self._changed.set()

    def _play(self, speech_id: str, seconds: float) -> None:
        task = asyncio.create_task(self._playback(speech_id, seconds))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _playback(self, speech_id: str, seconds: float) -> None:
        await self.send({"type": "tts_state", "crumb_id": speech_id, "playing": True})
        await asyncio.sleep(seconds)
        await self.send({"type": "tts_state", "crumb_id": speech_id, "playing": False})
        self.play_ended[speech_id] = self.tl.now()
        self._changed.set()

    async def wait(self, pred: Callable[[dict], bool], timeout: float, label: str, since: int = 0) -> tuple[int, float, dict]:
        deadline = time.monotonic() + timeout
        cursor = since
        while True:
            while cursor < len(self.events):
                t, evt = self.events[cursor]
                cursor += 1
                if pred(evt):
                    return cursor, t, evt
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.closed_code is not None:
                raise SmokeFailure(f"timed out waiting for {label}")
            self._changed.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._changed.wait(), timeout=remaining)

    async def wait_played(self, speech_id: str, timeout: float = 15.0) -> float:
        deadline = time.monotonic() + timeout
        while speech_id not in self.play_ended:
            if time.monotonic() > deadline:
                raise SmokeFailure(f"playback of {speech_id} never finished")
            self._changed.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._changed.wait(), timeout=0.5)
        return self.play_ended[speech_id]


async def stream_audio(ws: ClientConnection, table: TableClient, stop: asyncio.Event) -> None:
    """80 ms silence frames in real time on /ws/audio and VAD frames at 10 Hz on the events socket."""
    frame = bytes(FRAME_BYTES)
    start = time.monotonic()
    sent = 0
    next_vad = start
    while not stop.is_set():
        now = time.monotonic()
        if now >= start + sent * FRAME_S:
            await ws.send(frame)
            sent += 1
        if now >= next_vad:
            await table.send({"type": "vad", "active": False, "rms": 0.002})
            next_vad += VAD_EVERY_S
        await asyncio.sleep(0.01)


def crumb_of(kind: str | tuple[str, ...]) -> Callable[[dict], bool]:
    kinds = (kind,) if isinstance(kind, str) else kind
    return lambda e: e.get("type") == "crumb" and e["crumb"]["kind"] in kinds


async def dev_turn(table: TableClient, seat: int, text: str) -> float:
    """Inject a turn; returns its speaking duration (the runner paces it at 0.3 s per word)."""
    duration = max(0.6, 0.3 * len(text.split()))
    await table.send({"type": "dev_turn", "seat": seat, "text": text, "duration_s": duration})
    return duration


async def run(base: str, setup: str = "profiles") -> None:
    tl = Timeline()
    async with httpx.AsyncClient(base_url=base, timeout=30) as http:
        print("Static web app")
        for path in ("/", "/table/x", "/join/x/1"):
            r = await http.get(path)
            tl.check(r.status_code == 200 and '<div id="root">' in r.text, f"GET {path} serves the built index.html")
        cfg = (await http.get("/api/config")).json()
        tl.check(cfg["dev_tools"] is True, "DEV_TOOLS is on (needed for dev turns)")
        print(f"  mock={cfg['mock']} ffmpeg={cfg['ffmpeg']}")
        if setup in ("profiles", "both"):
            await run_profiles(base, http, tl)
        if setup in ("quick", "both"):
            await run_quick(base, http, tl)
    print_summary(tl)


async def run_profiles(base: str, http: httpx.AsyncClient, tl: Timeline) -> None:
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    print("With profiles. Phones: consent, interview, profile, ready")
    sid = (await http.post("/api/sessions", json={"setup": "profiles", "mode": "social", "quick": True})).json()["session_id"]
    tl.mark("session created")
    for seat in (1, 2):
        r = await http.post(f"/api/sessions/{sid}/seats/{seat}/consent", json={"typed_name": NAMES[seat], "agreed": True})
        tl.check(r.status_code == 200, f"seat {seat} consent")
        r = await http.post(
            f"/api/sessions/{sid}/seats/{seat}/answers",
            data={"question_id": "q1"},
            files={"audio": ("answer.wav", tiny_wav(), "audio/wav")},
        )
        tl.check(r.status_code == 200 and bool(r.json().get("transcript")), f"seat {seat} answer transcribed")
        r = await http.post(f"/api/sessions/{sid}/seats/{seat}/profile")
        tl.check(r.status_code == 200 and len(r.json()["items"]) > 0, f"seat {seat} profile built ({len(r.json()['items'])} items)")

    async with connect(f"{ws_base}/ws/events/{sid}?role=table") as events_ws:
        table = TableClient(events_ws, tl)
        reader = asyncio.create_task(table.reader())
        await table.wait(lambda e: e.get("type") == "session", 5, "session on connect")

        for seat in (1, 2):
            r = await http.post(f"/api/sessions/{sid}/seats/{seat}/ready")
            tl.check(r.status_code == 200, f"seat {seat} ready")
        await table.wait(lambda e: e.get("type") == "session" and e["session"]["phase"] == "calibrating", 10, "calibrating")
        tl.mark("phase calibrating")

        stop_audio = asyncio.Event()
        async with connect(f"{ws_base}/ws/audio/{sid}") as audio_ws:
            streamer = asyncio.create_task(stream_audio(audio_ws, table, stop_audio))
            try:
                await drive_live(table, tl, http, sid)
            finally:
                stop_audio.set()
                await streamer

        print("End and trail")
        r = await http.post(f"/api/sessions/{sid}/end")
        tl.check(r.status_code == 200, "POST end")
        await table.wait(lambda e: e.get("type") == "session" and e["session"]["phase"] == "ended", 5, "ended")
        tl.mark("phase ended")
        for seat, contact in ((1, "@maya.runs"), (2, "dev@example.com")):
            r = await http.post(f"/api/sessions/{sid}/seats/{seat}/trail-optin", json={"share": True, "contact": contact})
            tl.check(r.status_code == 200, f"seat {seat} opted in")
        trails = {}
        for seat in (1, 2):
            deadline = time.monotonic() + 15
            while True:
                r = await http.get(f"/api/sessions/{sid}/seats/{seat}/trail")
                if r.status_code == 200:
                    trails[seat] = r.json()
                    break
                if time.monotonic() > deadline:
                    raise SmokeFailure(f"trail for seat {seat} never became ready ({r.status_code})")
                await asyncio.sleep(0.2)
            t = trails[seat]
            tl.mark(f"seat {seat} trail fetched (matched={t['matched']}, items={len(t['trail'])})")
        tl.check(trails[1]["partner_contact"] == "dev@example.com" and trails[2]["partner_contact"] == "@maya.runs", "contacts swapped on a mutual yes")
        tl.check(all(i["for_seat"] == s for s in (1, 2) for i in trails[s]["trail"]), "each phone sees only its own trail")
        await table.wait(lambda e: e.get("type") == "wiped", 5, "wiped")
        tl.mark("session wiped")
        await asyncio.wait_for(reader, timeout=5)
        tl.check(table.closed_code == 4404, f"Table socket closed with 4404 (got {table.closed_code})")
    r = await http.get(f"/api/sessions/{sid}")
    tl.check(r.status_code == 404, "GET session is 404 after the wipe")


async def run_quick(base: str, http: httpx.AsyncClient, tl: Timeline) -> None:
    """"Just talk": no phones, consent on the Table, live at once, the icebreaker, memory, the Table trail."""
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    print("Just talk: create, phone links refused, We're both in")
    created = (await http.post("/api/sessions", json={})).json()
    sid = created["session_id"]
    tl.check(created.get("setup") == "quick" and created.get("join_urls") == {}, "create defaults to Just talk (no join links)")
    tl.mark("session created")
    r = await http.post(f"/api/sessions/{sid}/seats/1/consent", json={"typed_name": "Maya", "agreed": True})
    tl.check(r.status_code == 409, "a phone link is refused (this table doesn't need phones)")
    async with connect(f"{ws_base}/ws/events/{sid}?role=table") as events_ws:
        table = TableClient(events_ws, tl)
        reader = asyncio.create_task(table.reader())
        _, _, first = await table.wait(lambda e: e.get("type") == "session", 5, "session on connect")
        tl.check(first["session"]["setup"] == "quick", "the Table sees setup quick")
        r = await http.post(f"/api/sessions/{sid}/start", json={"names": {"1": "", "2": ""}})
        tl.check(r.status_code == 200, "POST start (both consented on the Table, no names)")
        r = await http.post(f"/api/sessions/{sid}/start", json={})
        tl.check(r.status_code == 409, "a second start is refused")
        summary = (await http.get(f"/api/sessions/{sid}")).json()
        tl.check([summary["seats"][k]["name"] for k in ("1", "2")] == ["Person 1", "Person 2"], "blank names are Person 1 / Person 2")

        stop_audio = asyncio.Event()
        async with connect(f"{ws_base}/ws/audio/{sid}") as audio_ws:
            streamer = asyncio.create_task(stream_audio(audio_ws, table, stop_audio))
            try:
                await drive_quick(table, tl, http, sid)
            finally:
                stop_audio.set()
                await streamer

        print("End, the Table's trail, Done")
        r = await http.post(f"/api/sessions/{sid}/end")
        tl.check(r.status_code == 200, "POST end")
        _, _, trail_evt = await table.wait(lambda e: e.get("type") == "table_trail", 15, "table_trail")
        items = trail_evt["trail"]["items"]
        tl.mark(f"table trail ({len(items)} threads)")
        tl.check(bool(items) and not any("Person" in i["text"] for i in items), "the trail has threads and never writes the labels")
        r = await http.get(f"/api/sessions/{sid}/trail")
        tl.check(r.status_code == 200 and r.json()["trail"] == trail_evt["trail"], "GET trail returns the same trail")
        r = await http.delete(f"/api/sessions/{sid}")
        tl.check(r.status_code == 200, "Done (DELETE) wipes the session")
        await table.wait(lambda e: e.get("type") == "wiped", 5, "wiped")
        await asyncio.wait_for(reader, timeout=5)
        tl.check(table.closed_code == 4404, f"Table socket closed with 4404 (got {table.closed_code})")
    r = await http.get(f"/api/sessions/{sid}")
    tl.check(r.status_code == 404, "GET session is 404 after the wipe")


async def drive_quick(table: TableClient, tl: Timeline, http: httpx.AsyncClient, sid: str) -> None:
    print("Live at once, then the icebreaker")
    idx, t_live, _ = await table.wait(lambda e: e.get("type") == "session" and e["session"]["phase"] == "live", 10, "live")
    tl.mark("phase live")
    idx, t_ice, ice = await table.wait(crumb_of("opener"), 10, "icebreaker", since=idx)
    early = [e.get("type") for _, e in table.events[:idx] if e.get("type") in ("system_say", "calibration")]
    tl.check(not early, "no say-hi prompts and no calibration events")
    tl.mark(f"icebreaker {ice['crumb']['id']} {t_ice - t_live:.2f} s after going live ({ice['latency_ms']:.0f} ms)")
    tl.check(2.6 <= t_ice - t_live <= 4.0, f"the icebreaker arrives about 3 s into the first silence ({t_ice - t_live:.2f} s)")
    ice_end = await table.wait_played(ice["crumb"]["id"])

    print("Lively conversation (memory is learned from it)")
    await asyncio.sleep(1.0)
    last_end = tl.now()
    i = 0
    while i < len(LIVELY_LINES) and (tl.now() - ice_end < LIVELY_MIN_S or i < 4):
        seat, text = LIVELY_LINES[i]
        duration = await dev_turn(table, seat, text)
        await asyncio.sleep(duration)
        last_end = tl.now()
        i += 1
        await asyncio.sleep(GAP_BETWEEN_TURNS_S if i < len(LIVELY_LINES) else 0)
    tl.mark(f"{i} lively turns done; the last one ended at {last_end:.2f}s")
    crumbs = [e for _, e in table.events[idx:] if e.get("type") == "crumb"]
    tl.check(not crumbs, "no crumb while the conversation was lively")
    memories = [e["memory"] for _, e in table.events[idx:] if e.get("type") == "memory"]
    tl.check(bool(memories) and any(memories[-1].values()), f"memory learned live ({sum(map(len, memories[-1].values())) if memories else 0} items, no text printed)")

    print("Silence")
    idx2, t_crumb, steer = await table.wait(crumb_of(("steer", "switch")), 15, "automatic crumb", since=idx)
    delay = t_crumb - last_end
    tl.mark(f"{steer['crumb']['kind']} crumb {steer['crumb']['id']} {delay:.2f} s after the last turn (cache_hit={steer['cache_hit']})")
    tl.check(steer["crumb"]["kind"] == "steer", "a lively conversation gets a steer, not a switch")
    tl.check(2.6 <= delay <= 4.0, f"steer arrives about 3 s into the silence ({delay:.2f} s)")
    tl.check("Person" not in steer["crumb"]["text"], "the labels are never spoken")
    await table.wait_played(steer["crumb"]["id"])

    print("Drop a crumb")
    await asyncio.sleep(0.5)
    t_spark = tl.mark("control spark")
    await table.send({"type": "control", "action": "spark"})
    _, t_manual, manual = await table.wait(crumb_of("manual"), 5, "manual crumb", since=idx2)
    tl.mark(f"manual crumb {manual['crumb']['id']} (source={manual['crumb']['source']})")
    tl.check(t_manual - t_spark <= 3.0, f"manual crumb within 3 s ({t_manual - t_spark:.2f} s)")
    await table.wait_played(manual["crumb"]["id"])
    metrics = (await http.get(f"/api/sessions/{sid}/metrics")).json()["metrics"]
    tl.check(metrics["false_interruptions"] == 0, "no false interruptions")


async def drive_live(table: TableClient, tl: Timeline, http: httpx.AsyncClient, sid: str) -> None:
    print("Calibration")
    for seat, prompt_id in ((1, "sys_cal1"), (2, "sys_cal2")):
        await table.wait(lambda e, p=prompt_id: e.get("type") == "system_say" and e["id"] == p, 10, prompt_id)
        tl.mark(f"system_say {prompt_id}")
        await table.wait_played(prompt_id)
        await asyncio.sleep(POST_TTS_GAP_S)
        duration = await dev_turn(table, seat, CALIBRATION_LINES[seat])
        tl.mark(f"dev turn seat {seat} ({duration:.1f} s)")
    _, _, done = await table.wait(lambda e: e.get("type") == "calibration" and e["step"] == "done", 15, "calibration done")
    tl.check(done["label_map"] == {"A": 1, "B": 2}, f"calibration mapped voices {done['label_map']}")
    await table.wait(lambda e: e.get("type") == "session" and e["session"]["phase"] == "live", 5, "live")
    idx, t_open, opener = await table.wait(crumb_of("opener"), 10, "opener crumb")
    tl.mark(f"opener crumb {opener['crumb']['id']} (source={opener['crumb']['source']}, {opener['latency_ms']:.0f} ms)")
    opener_end = await table.wait_played(opener["crumb"]["id"])
    tl.mark("opener playback ended")

    print("Lively conversation")
    await asyncio.sleep(1.0)
    started = tl.now()
    last_end = started
    i = 0
    while i < len(LIVELY_LINES) and (tl.now() - opener_end < LIVELY_MIN_S or i < 4):
        seat, text = LIVELY_LINES[i]
        duration = await dev_turn(table, seat, text)
        await asyncio.sleep(duration)
        last_end = tl.now()
        i += 1
        await asyncio.sleep(GAP_BETWEEN_TURNS_S if i < len(LIVELY_LINES) else 0)
    tl.mark(f"{i} lively turns done; the last one ended at {last_end:.2f}s (silence starts there)")
    early = [e for _, e in table.events[idx:] if e.get("type") == "crumb"]
    tl.check(not early, "no crumb while the conversation was lively")

    print("Silence")
    idx2, t_crumb, steer = await table.wait(crumb_of(("steer", "switch")), 15, "automatic crumb", since=idx)
    delay = t_crumb - last_end
    tl.mark(f"{steer['crumb']['kind']} crumb {steer['crumb']['id']} {delay:.2f} s after the last turn ({steer['latency_ms']:.0f} ms gen, cache_hit={steer['cache_hit']})")
    tl.check(steer["crumb"]["kind"] == "steer", "a lively conversation gets a steer, not a switch")
    tl.check(2.6 <= delay <= 4.0, f"steer arrives about 3 s into the silence ({delay:.2f} s)")
    decision = next((e["decision"] for _, e in table.events[idx:idx2] if e.get("type") == "decision" and e["decision"]["action"] == "steer"), None)
    if decision:
        print(f"           why: {decision['reason']} (silence {decision['inputs'].get('silence_s')} s, staleness {decision['inputs'].get('staleness')})")
    await table.wait_played(steer["crumb"]["id"])

    print("Drop a crumb")
    await asyncio.sleep(0.5)
    t_spark = tl.mark("control spark")
    await table.send({"type": "control", "action": "spark"})
    _, t_manual, manual = await table.wait(crumb_of("manual"), 5, "manual crumb", since=idx2)
    tl.mark(f"manual crumb {manual['crumb']['id']} (source={manual['crumb']['source']})")
    tl.check(t_manual - t_spark <= 3.0, f"manual crumb within 3 s ({t_manual - t_spark:.2f} s)")
    await table.wait_played(manual["crumb"]["id"])
    metrics = (await http.get(f"/api/sessions/{sid}/metrics")).json()["metrics"]
    print(f"  metrics: crumbs={metrics['crumbs']} interrupted={metrics['interrupted']} false_interruptions={metrics['false_interruptions']} p50={metrics['latency_ms_p50']} ms")
    tl.check(metrics["false_interruptions"] == 0, "no false interruptions")


def print_summary(tl: Timeline) -> None:
    passed = sum(1 for ok, _ in tl.checks if ok)
    print(f"\nSMOKE PASS: {passed}/{len(tl.checks)} checks in {tl.now():.1f} s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--setup", choices=("profiles", "quick", "both"), default="profiles")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.base.rstrip("/"), args.setup))
    except (SmokeFailure, httpx.HTTPError, OSError) as exc:
        print(f"\nSMOKE FAIL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
