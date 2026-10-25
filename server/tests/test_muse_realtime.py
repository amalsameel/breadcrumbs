"""MuseRealtimeClient against a local fake Muse WS server + MockMuseRealtime endpointing (PRD 11.1)."""
from __future__ import annotations

import asyncio
import json
import math
import struct
import time
from urllib.parse import parse_qs, urlparse

import pytest
from websockets.asyncio.server import ServerConnection, serve

import config
from muse import realtime
from muse.realtime import (
    MAX_KEYWORDS,
    QUEUE_MAXSIZE,
    MockMuseRealtime,
    _Pacer,
    MuseRealtimeClient,
    backoff_delay,
    build_handshake,
    clean_keywords,
    make_realtime_client,
    pcm16_rms,
)

FRAME = b"\x01\x00" * 1920  # one 80 ms PCM16 frame (3,840 bytes)
TIMEOUT = 5.0


class FakeMuse:
    """Scripted fake of wss://api.meta.ai/v1/asr/realtime. `behaviors[i]` drives connection i."""

    def __init__(self, behaviors: list[str]) -> None:
        self.behaviors = behaviors
        self.handshakes: list[dict] = []
        self.session_ids: list[str] = []
        self.binary: list[bytes] = []
        self.binary_at: list[float] = []  # time.monotonic() of each binary frame's arrival
        self.text: list[dict] = []
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/v1/asr/realtime?sessionId={{sid}}"

    async def handler(self, ws: ServerConnection) -> None:
        index = len(self.handshakes)
        behavior = self.behaviors[min(index, len(self.behaviors) - 1)]
        self.session_ids.append(parse_qs(urlparse(ws.request.path).query)["sessionId"][0])
        self.handshakes.append(json.loads(await ws.recv()))
        if behavior == "reject":
            await ws.send(json.dumps({"type": "error", "message": "invalid token", "sessionId": "x"}))
            await ws.close(1008, "invalid request")
            return
        await ws.send(json.dumps({"sessionId": f"srv-{index}"}))
        if behavior in ("close_1011", "close_1013", "close_1008"):
            await ws.close(int(behavior[-4:]), "scripted")
            return
        if behavior != "quiet":  # "quiet" sends no events, so no audioProcessedMs feedback either
            await ws.send(json.dumps({"type": "speechStart", "turnId": 1, "audioProcessedMs": 400}))
            await ws.send(json.dumps({"type": "audioProgress", "audioProcessedMs": 1000}))
        async for msg in ws:
            if isinstance(msg, bytes):
                self.binary.append(msg)
                self.binary_at.append(time.monotonic())
                continue
            frame = json.loads(msg)
            self.text.append(frame)
            if frame.get("type") == "endStream":
                await ws.send(json.dumps({"type": "speechComplete", "turnId": 1, "transcript": "hi", "audioProcessedMs": 1200}))
                await ws.close(1000)
                return


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.statuses: list[tuple[str, str]] = []
        self.got_event = asyncio.Event()

    async def on_event(self, evt: dict) -> None:
        self.events.append(evt)
        self.got_event.set()

    async def on_status(self, status: str, detail: str) -> None:
        self.statuses.append((status, detail))

    @property
    def status_names(self) -> list[str]:
        return [s for s, _ in self.statuses]

    async def wait_for_status(self, name: str, count: int = 1) -> None:
        async def poll() -> None:
            while self.status_names.count(name) < count:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(poll(), TIMEOUT)


async def start_fake(behaviors: list[str]):
    fake = FakeMuse(behaviors)
    server = await serve(fake.handler, "127.0.0.1", 0)
    fake.port = server.sockets[0].getsockname()[1]
    return fake, server


def make_client(fake: FakeMuse, rec: Recorder, keywords: list[str] | None = None) -> MuseRealtimeClient:
    return MuseRealtimeClient(
        rec.on_event,
        keywords if keywords is not None else ["Maya", "Dev"],
        rec.on_status,
        url=fake.url,
        api_key="test-key",
        model="muse-voice-transcribe-1.0",
        backoff=(0.01, 0.02, 0.04),
    )


# --- pure helpers -------------------------------------------------------------


def test_handshake_shape_is_exact() -> None:
    assert build_handshake("k", "muse-voice-transcribe-1.0", ["Maya", "maya ", "Dev", ""]) == {
        "authorization": {"accessToken": "Bearer k"},
        "audioEncoding": "PCM_24KHZ",
        "model": "muse-voice-transcribe-1.0",
        "mode": "DIARIZATION",
        "partialMode": "CUMULATIVE",
        "emitAudioProgress": True,
        "languageBias": ["English"],
        "keywords": ["Maya", "Dev"],
    }


def test_clean_keywords_dedupes_and_caps() -> None:
    kws = clean_keywords(["  half   marathon ", "Half Marathon", "x" * 200] + [f"k{i}" for i in range(100)])
    assert kws[0] == "half marathon"
    assert len(kws[1]) == realtime.MAX_KEYWORD_CHARS
    assert len(kws) == MAX_KEYWORDS
    assert len({k.casefold() for k in kws}) == len(kws)


def test_backoff_ladder() -> None:
    assert [backoff_delay(i) for i in range(5)] == [0.5, 1.0, 2.0, 2.0, 2.0]


def test_push_audio_drops_oldest_when_full() -> None:
    client = MuseRealtimeClient(Recorder().on_event, [], api_key="k")
    for i in range(QUEUE_MAXSIZE + 10):
        client.push_audio(i.to_bytes(2, "little") * 4)
    assert client._queue.qsize() == QUEUE_MAXSIZE
    assert client.dropped_frames == 10
    first = client._queue._q.get_nowait()
    assert first == (10).to_bytes(2, "little") * 4


async def test_close_sentinel_survives_full_queue() -> None:
    client = MuseRealtimeClient(Recorder().on_event, [], api_key="k")
    for _ in range(QUEUE_MAXSIZE):
        client.push_audio(FRAME)
    await client.close()  # not connected: returns immediately, sentinel queued
    client.push_audio(FRAME)  # ignored after close
    items = [client._queue._q.get_nowait() for _ in range(client._queue.qsize())]
    assert items[-1] is None and len(items) == QUEUE_MAXSIZE


def test_make_realtime_client_picks_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder()
    monkeypatch.setattr(config, "MUSE_MOCK", True)
    assert isinstance(make_realtime_client(rec.on_event, ["a"], rec.on_status), MockMuseRealtime)
    monkeypatch.setattr(config, "MUSE_MOCK", False)
    assert isinstance(make_realtime_client(rec.on_event, ["a"], rec.on_status), MuseRealtimeClient)


# --- real client against the fake server ----------------------------------------


async def test_session_handshake_audio_events_and_endstream() -> None:
    fake, server = await start_fake(["normal"])
    rec = Recorder()
    client = make_client(fake, rec, ["Maya", "Dev", "maya", "half marathon"])
    async with server:
        task = asyncio.create_task(client.run())
        await rec.wait_for_status("connected")
        assert client.connected and client.session_id == "srv-0"
        for _ in range(5):
            client.push_audio(FRAME)
        await asyncio.wait_for(rec.got_event.wait(), TIMEOUT)
        await client.close()
        await asyncio.wait_for(task, TIMEOUT)

    assert fake.handshakes == [build_handshake("test-key", "muse-voice-transcribe-1.0", ["Maya", "Dev", "half marathon"])]
    assert fake.session_ids[0].startswith("bc-")
    assert fake.binary == [FRAME] * 5
    assert fake.text == [{"type": "endStream"}]
    assert [e["type"] for e in rec.events] == ["speechStart", "audioProgress", "speechComplete"]
    assert client.audio_processed_ms == 1200
    assert client.last_event_at is not None
    assert rec.status_names == ["connected", "closed"]
    assert not client.connected


@pytest.mark.parametrize("code", ["close_1011", "close_1013"])
async def test_reconnects_with_new_session_and_resyncs(code: str) -> None:
    fake, server = await start_fake([code, "normal"])
    rec = Recorder()
    client = make_client(fake, rec)
    async with server:
        task = asyncio.create_task(client.run())
        await rec.wait_for_status("resyncing")
        await client.close()
        await asyncio.wait_for(task, TIMEOUT)

    assert len(fake.handshakes) == 2
    assert fake.handshakes[0] == fake.handshakes[1]
    assert fake.session_ids[0] != fake.session_ids[1]
    assert rec.status_names == ["connected", "reconnecting", "connected", "resyncing", "closed"]
    assert client.reconnects == 1


async def test_no_retry_on_1008() -> None:
    fake, server = await start_fake(["close_1008", "normal"])
    rec = Recorder()
    client = make_client(fake, rec)
    async with server:
        await asyncio.wait_for(client.run(), TIMEOUT)
    assert len(fake.handshakes) == 1
    assert rec.status_names == ["connected", "error"]
    assert "1008" in rec.statuses[-1][1]


async def test_rejected_handshake_is_fatal() -> None:
    fake, server = await start_fake(["reject", "normal"])
    rec = Recorder()
    client = make_client(fake, rec)
    async with server:
        await asyncio.wait_for(client.run(), TIMEOUT)
    assert len(fake.handshakes) == 1
    assert rec.status_names == ["error"]
    assert "invalid token" in rec.statuses[0][1]


async def test_network_error_retries_until_closed() -> None:
    fake, server = await start_fake(["normal"])
    async with server:
        pass  # server is closed again: the port now refuses connections
    rec = Recorder()
    client = make_client(fake, rec)
    task = asyncio.create_task(client.run())
    await rec.wait_for_status("reconnecting", count=3)
    await client.close()
    await asyncio.wait_for(task, TIMEOUT)
    assert "connected" not in rec.status_names
    assert rec.status_names[-1] == "closed"
    # The Table shows this detail: a connection that never opens must name its cause, not "close None".
    detail = next(d for st, d in rec.statuses if st == "reconnecting")
    assert detail.startswith("connection failed: ") and "None" not in detail


def test_wss_uses_certifi_tls_context() -> None:
    """Regression: the python.org macOS build has no system CA store, so wss to Muse failed every
    handshake with SSLCertVerificationError and calibration stalled. wss:// must carry certifi's bundle."""
    import ssl

    import certifi

    ctx = realtime.tls_context("wss://api.meta.ai/v1/asr/realtime?sessionId=x")
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 0  # loaded from certifi, not an empty system store
    assert certifi.where()
    assert realtime.tls_context("ws://127.0.0.1:9999/x") is None  # local fake servers stay plain


async def test_on_event_exception_does_not_drop_session() -> None:
    fake, server = await start_fake(["normal"])
    rec = Recorder()
    calls = 0

    async def flaky(evt: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("consumer bug")
        await rec.on_event(evt)

    client = make_client(fake, rec)
    client.on_event = flaky
    async with server:
        task = asyncio.create_task(client.run())
        await asyncio.wait_for(rec.got_event.wait(), TIMEOUT)
        await client.close()
        await asyncio.wait_for(task, TIMEOUT)
    assert len(fake.handshakes) == 1
    assert [e["type"] for e in rec.events] == ["audioProgress", "speechComplete"]


# --- mock endpointing -------------------------------------------------------------


def pcm_frames(amplitude: float, seconds: float, freq: float = 220.0, offset: int = 0) -> list[bytes]:
    """80 ms PCM16 frames of a sine (amplitude 0 = near-silence with a tiny deterministic hiss)."""
    frames = []
    n_frames = round(seconds / 0.08)
    for f in range(n_frames):
        samples = []
        for i in range(1920):
            t = (offset + f * 1920 + i) / 24_000
            value = amplitude * math.sin(2 * math.pi * freq * t) if amplitude else 0.001 * ((i * 7919) % 13 - 6) / 6
            samples.append(int(max(-1.0, min(1.0, value)) * 32767))
        frames.append(struct.pack("<1920h", *samples))
    return frames


def test_pcm16_rms() -> None:
    assert pcm16_rms(b"") == 0.0
    tone = pcm_frames(0.5, 0.08)[0]
    assert abs(pcm16_rms(tone) - 0.5 / math.sqrt(2)) < 0.01


async def run_mock(frames: list[bytes]) -> tuple[Recorder, MockMuseRealtime]:
    rec = Recorder()
    mock = MockMuseRealtime(rec.on_event, ["Maya"], rec.on_status)
    task = asyncio.create_task(mock.run())
    for frame in frames:
        mock.push_audio(frame)
        await asyncio.sleep(0)  # let the consumer keep up so the bounded queue never drops
    await mock.close()
    await asyncio.wait_for(task, TIMEOUT)
    return rec, mock


async def test_mock_one_tone_yields_one_full_turn() -> None:
    frames = pcm_frames(0, 1.0) + pcm_frames(0.3, 1.52) + pcm_frames(0, 1.6)
    rec, mock = await run_mock(frames)
    turn_events = [e for e in rec.events if e["type"] != "audioProgress"]
    types = [e["type"] for e in turn_events]

    assert types.count("speechStart") == 1
    assert types[0] == "speechStart" and types[-4:] == ["transcript", "speaker", "speechEnd", "speechComplete"]
    assert all(t == "transcript" for t in types[1:-4])  # cumulative partials while speaking
    assert len(types) > 5
    start, complete = turn_events[0], turn_events[-1]
    assert start["turnId"] == complete["turnId"] == turn_events[-2]["turnId"] == 1
    assert complete["transcript"] == "(mock speech, 1.5 s)"
    assert turn_events[-4]["final"] is True and all(not e["final"] for e in turn_events[1:-4])
    assert turn_events[-3]["label"] == "A"
    # Start ~160 ms after the tone begins at 1.0 s; end ~700 ms after it stops at 2.52 s.
    assert 1100 <= start["audioProcessedMs"] <= 1300
    assert 3150 <= complete["audioProcessedMs"] <= 3400
    progress = [e["audioProcessedMs"] for e in rec.events if e["type"] == "audioProgress"]
    assert len(progress) == 4 and progress == sorted(progress)
    assert all("audioProcessedMs" in e for e in rec.events)
    assert rec.status_names == ["connected", "closed"]
    assert mock.audio_processed_ms == progress[-1] == 4000  # tracks the last emitted event


async def test_mock_alternates_labels_and_flushes_on_close() -> None:
    frames = pcm_frames(0, 0.8) + pcm_frames(0.3, 1.0) + pcm_frames(0, 1.0) + pcm_frames(0.2, 0.8, freq=330.0)
    rec, _ = await run_mock(frames)  # second turn is still open when the stream ends
    speakers = [e["label"] for e in rec.events if e["type"] == "speaker"]
    completes = [e for e in rec.events if e["type"] == "speechComplete"]
    assert speakers == ["A", "B"]
    assert [c["turnId"] for c in completes] == [1, 2]


async def test_mock_ignores_steady_background_noise() -> None:
    frames = pcm_frames(0.004, 3.0)  # quiet room hum below the 0.01 floor threshold
    rec, _ = await run_mock(frames)
    assert not [e for e in rec.events if e["type"] == "speechStart"]


async def test_reconnect_burst_is_capped_by_drop_oldest_queue() -> None:
    fake, server = await start_fake(["close_1011", "normal"])
    rec = Recorder()
    client = make_client(fake, rec)
    client.backoff = (0.3,)
    async with server:
        task = asyncio.create_task(client.run())
        await rec.wait_for_status("reconnecting")
        for i in range(QUEUE_MAXSIZE + 20):  # 4.8 s of audio buffered while disconnected
            client.push_audio(i.to_bytes(2, "little") * 1920)
        await rec.wait_for_status("resyncing")
        await client.close()
        await asyncio.wait_for(task, TIMEOUT)
    # Only the newest 3.2 s reach Muse after the reconnect, in order (never > 5 s ahead).
    assert len(fake.binary) == QUEUE_MAXSIZE
    assert fake.binary[0] == (20).to_bytes(2, "little") * 1920
    assert fake.binary[-1] == (QUEUE_MAXSIZE + 19).to_bytes(2, "little") * 1920
    assert client.dropped_frames == 20


# --- real-time pacing (PRD 11.1: never more than 5 s ahead, or Muse closes with 1008) -----------


def max_lead_s(arrivals: list[float], frame_s: float = 0.08) -> float:
    """Worst lead over real time as Muse sees it: a leaky bucket that drains at real-time speed."""
    lead, worst, prev = 0.0, 0.0, None
    for at in arrivals:
        if prev is not None:
            lead = max(0.0, lead - (at - prev))
        lead += frame_s
        worst = max(worst, lead)
        prev = at
    return worst


def test_pacer_bounds_lead_and_cannot_bank_idle_time() -> None:
    now = [0.0]
    pacer = _Pacer(max_ahead_s=1.0, clock=lambda: now[0])
    sent_at_once = 0
    while pacer.wait_s(0.08) == 0.0:
        pacer.sent(0.08)
        sent_at_once += 1
    assert sent_at_once == 12  # 0.96 s may go out in a burst, the 13th frame has to wait
    assert pacer.wait_s(0.08) == pytest.approx(0.04)
    now[0] = 30.0  # a long stall: Muse sat idle, which does not let the next burst run further ahead
    burst = 0
    while pacer.wait_s(0.08) == 0.0:
        pacer.sent(0.08)
        burst += 1
    assert burst == 12
    pacer.note_processed(pacer.sent_s)  # Muse reports it has processed everything sent: lead is gone
    assert pacer.lead_s == 0.0
    pacer.note_processed(pacer.sent_s + 5.0)  # nonsense feedback never opens extra room
    assert pacer.lead_s == 0.0


async def test_flushed_backlog_is_paced_to_real_time() -> None:
    """A Table that stalls and then flushes 2.4 s of audio over the network (frames arriving a few at a
    time, so the drop-oldest queue never fills) must not reach Muse faster than real time allows."""
    fake, server = await start_fake(["quiet"])
    rec = Recorder()
    client = MuseRealtimeClient(
        rec.on_event, [], rec.on_status, url=fake.url, api_key="k", model="m", backoff=(0.01,), max_ahead_s=0.5
    )
    frames = 30
    async with server:
        task = asyncio.create_task(client.run())
        await rec.wait_for_status("connected")
        for _ in range(frames):
            client.push_audio(FRAME)
            await asyncio.sleep(0)  # the uplink delivers the backlog in pieces, yielding in between
        deadline = time.monotonic() + TIMEOUT
        while len(fake.binary) < frames and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        await client.close()
        await asyncio.wait_for(task, TIMEOUT)
    assert len(fake.binary) == frames and client.dropped_frames == 0
    assert max_lead_s(fake.binary_at) <= 0.5 + 0.1
    assert "error" not in rec.status_names


# --- diagnostics: numbers only ------------------------------------------------------------------------


class _Msgs:
    def __init__(self, msgs: list[str]) -> None:
        self.msgs = msgs

    def __aiter__(self):  # noqa: ANN204
        async def gen():  # noqa: ANN202
            for m in self.msgs:
                yield m
        return gen()


async def test_error_event_log_carries_the_backlog_as_numbers(caplog: pytest.LogCaptureFixture) -> None:
    async def on_event(evt: dict) -> None:
        return None

    client = MuseRealtimeClient(on_event, ["Maya"], api_key="k")
    for _ in range(QUEUE_MAXSIZE + 3):
        client.push_audio(FRAME)
    client._pacer.lead_s = 1.25
    caplog.set_level("WARNING", logger="breadcrumbs.muse")
    await client._recv_loop(_Msgs([json.dumps({"type": "error", "message": "Ingress audio slower than real-time"})]))
    line = next(r for r in caplog.records if "muse error event" in r.getMessage())
    assert line.args[1:] == (QUEUE_MAXSIZE, 3, 1.25)
    assert all(isinstance(a, (int, float)) for a in line.args[1:])
    assert len(str(line.args[0])) <= 200


def test_dropped_frames_are_logged_at_most_once_a_minute(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    async def on_event(evt: dict) -> None:
        return None

    client = MuseRealtimeClient(on_event, [], api_key="k")
    caplog.set_level("INFO", logger="breadcrumbs.muse")
    now = [1000.0]
    monkeypatch.setattr(realtime.time, "monotonic", lambda: now[0])
    client._log_drops()
    assert not [r for r in caplog.records if "dropped" in r.getMessage()]  # nothing dropped: no line
    client._queue.dropped = 5
    client._log_drops()
    client._queue.dropped = 9
    now[0] += 30.0
    client._log_drops()  # within the minute: no second line
    now[0] += 31.0
    client._log_drops()
    lines = [r for r in caplog.records if "frames dropped" in r.getMessage()]
    assert [r.args[0] for r in lines] == [5, 9]
