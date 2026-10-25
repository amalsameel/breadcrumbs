"""Muse Voice Transcribe realtime client (PRD 11.1) + an offline mock with the same interface.

The runner pushes 80 ms PCM16 frames (24 kHz mono, 3,840 bytes) with `push_audio` and receives raw Muse
event dicts through `on_event`. Connection health is reported through `on_status(status, detail)` with
status in {"reconnecting", "connected", "resyncing", "closed", "error"}.

Privacy: transcript text is never logged here (PRD 17.3). Only codes, counts and timings are.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import ssl
import sys
import time
import uuid
from array import array
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import certifi
import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus, InvalidURI

import config

log = logging.getLogger("breadcrumbs.muse")


def tls_context(url: str) -> ssl.SSLContext | None:
    """certifi CA bundle for wss:// URLs.

    The python.org macOS build ships without system root certificates, so the default context
    fails every wss handshake with SSLCertVerificationError (httpx already uses certifi, which is why
    REST transcription worked while the realtime stream did not). Plain ws:// (local test servers) needs none.
    """
    return ssl.create_default_context(cafile=certifi.where()) if url.startswith("wss://") else None

OnEvent = Callable[[dict], Awaitable[None]]
OnStatus = Callable[[str, str], Awaitable[None]]

QUEUE_MAXSIZE = 40  # 40 x 80 ms = 3.2 s of backlog at most
MAX_AHEAD_S = 1.5  # the sender never runs more than this far ahead of real time (Muse closes 1008 past 5 s)
PCM_BYTES_PER_S = 24_000 * 2  # PCM16 mono at 24 kHz
HANDSHAKE_TIMEOUT_S = 10.0
OPEN_TIMEOUT_S = 30.0
CLOSE_TIMEOUT_S = 5.0
BACKOFF_S: tuple[float, ...] = (0.5, 1.0, 2.0)  # then 2 s forever
HEALTHY_SESSION_S = 10.0  # a session that lived this long resets the backoff ladder
MAX_KEYWORDS = 50
MAX_KEYWORD_CHARS = 48

CLOSE_NORMAL = 1000
CLOSE_POLICY = 1008  # invalid request or pacing violation: fatal, never retried as-is
RETRYABLE_CLOSE_CODES = frozenset({1001, 1006, 1011, 1012, 1013, 1014})

_END = None  # endStream sentinel in the audio queue


class MuseFatalError(Exception):
    """A failure that retrying as-is cannot fix (1008, auth rejected, malformed handshake)."""


class _Retryable(Exception):
    """A transient failure: reconnect with backoff."""


def clean_keywords(keywords: Sequence[str]) -> list[str]:
    """Strip, dedupe case-insensitively (first spelling wins) and cap count and length."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in keywords:
        kw = " ".join(str(raw).split())[:MAX_KEYWORD_CHARS].strip()
        key = kw.casefold()
        if not kw or key in seen:
            continue
        seen.add(key)
        out.append(kw)
        if len(out) >= MAX_KEYWORDS:
            break
    return out


def build_handshake(api_key: str, model: str, keywords: Sequence[str]) -> dict[str, Any]:
    """The first JSON frame on the socket (PRD 11.1). Auth lives here, not in an HTTP header."""
    return {
        "authorization": {"accessToken": f"Bearer {api_key}"},
        "audioEncoding": "PCM_24KHZ",
        "model": model,
        "mode": "DIARIZATION",
        "partialMode": "CUMULATIVE",
        "emitAudioProgress": True,
        "languageBias": ["English"],
        "keywords": clean_keywords(keywords),
    }


def backoff_delay(attempt: int, ladder: Sequence[float] = BACKOFF_S) -> float:
    return ladder[min(attempt, len(ladder) - 1)]


class _DropOldestQueue:
    """Bounded audio queue: when full, the oldest frame is discarded so new audio always gets in."""

    def __init__(self, maxsize: int = QUEUE_MAXSIZE) -> None:
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, item: bytes | None) -> None:
        while self._q.full():
            if self._q.get_nowait() is _END:
                # Never drop the endStream sentinel; put it back at the cost of the new frame.
                self._q.put_nowait(_END)
                return
            self.dropped += 1
        self._q.put_nowait(item)

    async def get(self) -> bytes | None:
        return await self._q.get()

    def qsize(self) -> int:
        return self._q.qsize()


class _Pacer:
    """Real-time pacing for the uplink (PRD 11.1: "send at about real-time speed").

    Muse consumes audio at (at least) real-time speed and cannot bank idle time, so the lead is a
    leaky bucket: each frame adds its duration, wall-clock time drains it, and it never goes below
    zero. Muse's own `audioProcessedMs` can only lower it (when Muse runs faster than real time). A
    Table that stalls and then flushes its backlog (Wi-Fi hiccup, throttled tab) would otherwise put
    the stream seconds ahead in one burst, and Muse closes past 5 s ahead with 1008, which is fatal.
    """

    def __init__(self, max_ahead_s: float = MAX_AHEAD_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_ahead_s = max_ahead_s
        self.clock = clock
        self.lead_s = 0.0
        self.sent_s = 0.0  # audio sent in this Muse session
        self._at: float | None = None

    def reset(self) -> None:
        self.lead_s = 0.0
        self.sent_s = 0.0
        self._at = None

    def note_processed(self, processed_s: float) -> None:
        if 0.0 < processed_s <= self.sent_s:  # Muse can't have processed audio it was never sent
            self.lead_s = min(self.lead_s, max(0.0, self.sent_s - processed_s))

    def _drain(self) -> float:
        now = self.clock()
        if self._at is not None:
            self.lead_s = max(0.0, self.lead_s - (now - self._at))
        self._at = now
        return now

    def wait_s(self, frame_s: float) -> float:
        """How long to wait before sending a frame of `frame_s` seconds (0 when it can go now)."""
        self._drain()
        return max(0.0, self.lead_s + frame_s - self.max_ahead_s)

    def sent(self, frame_s: float) -> None:
        self._drain()
        self.lead_s += frame_s
        self.sent_s += frame_s


class _ClientBase:
    """State and callback plumbing shared by the real and mock clients."""

    def __init__(self, on_event: OnEvent, keywords: list[str], on_status: OnStatus | None = None) -> None:
        self.on_event = on_event
        self.on_status = on_status
        self.keywords = clean_keywords(keywords)
        self.connected = False
        self.audio_processed_ms = 0
        self.last_event_at: float | None = None  # time.monotonic() of the last server event
        self.session_id: str | None = None
        self._queue = _DropOldestQueue()
        self._closing = False
        self._stop = asyncio.Event()

    @property
    def dropped_frames(self) -> int:
        return self._queue.dropped

    def push_audio(self, frame: bytes) -> None:
        if self._closing or not frame:
            return
        self._queue.put(bytes(frame))

    async def _status(self, status: str, detail: str = "") -> None:
        if self.on_status is None:
            return
        try:
            await self.on_status(status, detail)
        except Exception:
            log.exception("muse on_status callback failed (status=%s)", status)

    async def _emit(self, evt: dict) -> None:
        self.last_event_at = time.monotonic()
        ms = evt.get("audioProcessedMs")
        if isinstance(ms, (int, float)) and ms > self.audio_processed_ms:
            self.audio_processed_ms = int(ms)
        try:
            await self.on_event(evt)
        except Exception:
            # A consumer bug must not tear down the Muse session (that would reset diarization labels).
            log.exception("muse on_event callback failed (type=%s)", evt.get("type"))


class MuseRealtimeClient(_ClientBase):
    """Realtime DIARIZATION stream to Muse with reconnect, backoff and drop-oldest pacing."""

    def __init__(
        self,
        on_event: OnEvent,
        keywords: list[str],
        on_status: OnStatus | None = None,
        *,
        url: str = config.MUSE_REALTIME_URL,
        api_key: str | None = None,
        model: str | None = None,
        backoff: Sequence[float] = BACKOFF_S,
        handshake_timeout: float = HANDSHAKE_TIMEOUT_S,
        max_ahead_s: float = MAX_AHEAD_S,
    ) -> None:
        super().__init__(on_event, keywords, on_status)
        self.url = url  # template with a `{sid}` placeholder
        self.api_key = config.MODEL_API_KEY if api_key is None else api_key
        self.model = model or config.ASR_MODEL
        self.backoff = tuple(backoff)
        self.handshake_timeout = handshake_timeout
        self.reconnects = 0
        self._pacer = _Pacer(max_ahead_s)
        self._ever_connected = False  # a reconnect after this means diarization labels were reset
        self._ws: ClientConnection | None = None
        self._session_done = asyncio.Event()
        self._session_done.set()

    async def run(self) -> None:
        attempt = 0
        while not self._closing:
            started = time.monotonic()
            failure = ""
            try:
                code = await self._run_session(resync=self._ever_connected)
            except (MuseFatalError, InvalidURI) as exc:
                log.error("muse fatal: %s", exc)
                await self._status("error", str(exc))
                return
            except (_Retryable, OSError, asyncio.TimeoutError, InvalidHandshake, ConnectionClosed) as exc:
                code = None
                if isinstance(exc, InvalidStatus) and _is_fatal_http(exc):
                    await self._status("error", f"Muse rejected the connection (HTTP {exc.response.status_code})")
                    return
                failure = type(exc).__name__
                log.warning("muse connection failed: %s", failure)
            finally:
                self.connected = False
            if self._closing:
                break
            if code == CLOSE_POLICY:
                await self._status("error", "Muse closed the stream with 1008 (invalid request or pacing)")
                return
            if time.monotonic() - started >= HEALTHY_SESSION_S:
                attempt = 0
            delay = backoff_delay(attempt, self.backoff)
            attempt += 1
            self.reconnects += 1
            # The Table shows this detail, so a connection that never opens names its cause instead of looking like a stall.
            why = f"connection failed: {failure}" if failure else f"close {code}"
            await self._status("reconnecting", f"{why}, retry in {delay:g} s")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
        await self._status("closed", "")

    async def _run_session(self, *, resync: bool) -> int | None:
        """One connection lifetime. Returns the close code, or raises on handshake failure."""
        sid = f"bc-{uuid.uuid4()}"
        self._session_done.clear()
        try:
            url = self.url.format(sid=sid)
            tls = tls_context(url)
            async with websockets.connect(
                url,
                open_timeout=OPEN_TIMEOUT_S,
                max_size=None,
                close_timeout=CLOSE_TIMEOUT_S,
                **({"ssl": tls} if tls else {}),
            ) as ws:
                self._ws = ws
                await ws.send(json.dumps(build_handshake(self.api_key, self.model, self.keywords)))
                self.session_id = await self._await_ack(ws)
                self.connected = True
                self._ever_connected = True
                self.audio_processed_ms = 0  # audioProcessedMs restarts with each realtime session
                log.info("muse connected (session=%s, keywords=%d)", self.session_id, len(self.keywords))
                await self._status("connected", self.session_id)
                if resync:
                    await self._status("resyncing", "new Muse session, speaker labels reset")
                await self._pump(ws)
            return ws.close_code
        finally:
            self._ws = None
            self._session_done.set()

    async def _await_ack(self, ws: ClientConnection) -> str:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self.handshake_timeout)
        except asyncio.TimeoutError as exc:
            raise _Retryable("no handshake ack") from exc
        except ConnectionClosed as exc:
            raise _close_to_error(exc) from exc
        ack = _parse_json(raw)
        if ack is not None and isinstance(ack.get("sessionId"), str) and "type" not in ack:
            return ack["sessionId"]
        # Not an ack: the server usually closes right after an error frame; its close code decides.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ws.wait_closed(), timeout=2.0)
        if ws.close_code in RETRYABLE_CLOSE_CODES:
            raise _Retryable(f"handshake rejected with close {ws.close_code}")
        message = str(ack.get("message", ""))[:200] if ack else "non-JSON frame"
        raise MuseFatalError(f"Muse handshake failed: {message or 'no sessionId in ack'}")

    async def _pump(self, ws: ClientConnection) -> None:
        """Run sender and receiver; when the receiver ends (or fails) the sender is cancelled."""
        sender = asyncio.create_task(self._send_loop(ws), name="muse-sender")
        try:
            await self._recv_loop(ws)
        except ConnectionClosed:
            pass  # the close code is read by the caller
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionClosed):
                await sender

    async def _send_loop(self, ws: ClientConnection) -> None:
        self._pacer.reset()  # a new Muse session starts with nothing buffered
        try:
            while True:
                frame = await self._queue.get()
                if frame is _END:
                    await ws.send(json.dumps({"type": "endStream"}))
                    return
                frame_s = len(frame) / PCM_BYTES_PER_S
                self._pacer.note_processed(self.audio_processed_ms / 1000.0)
                wait = self._pacer.wait_s(frame_s)
                if wait > 0:
                    # Meanwhile new frames pile up in the drop-oldest queue, so the backlog stays bounded.
                    await asyncio.sleep(wait)
                await ws.send(frame)
                self._pacer.sent(frame_s)
        except ConnectionClosed:
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("muse sender failed")
            await ws.close(1011, "sender error")

    async def _recv_loop(self, ws: ClientConnection) -> None:
        async for msg in ws:
            if isinstance(msg, bytes):
                continue
            evt = _parse_json(msg)
            if evt is None:
                continue
            if evt.get("type") == "error":
                log.warning("muse error event: %s", str(evt.get("message", ""))[:200])
            await self._emit(evt)

    async def close(self) -> None:
        """Send endStream, let Muse flush its final results, then stop. No reconnects afterwards."""
        if self._closing:
            return
        self._closing = True
        self._queue.put(_END)
        self._stop.set()
        ws = self._ws
        if ws is None:
            return
        try:
            await asyncio.wait_for(self._session_done.wait(), timeout=CLOSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            await ws.close()


def _parse_json(raw: str | bytes) -> dict | None:
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _close_to_error(exc: ConnectionClosed) -> Exception:
    code = exc.rcvd.code if exc.rcvd is not None else 1006
    if code == CLOSE_POLICY:
        return MuseFatalError("Muse closed the handshake with 1008 (invalid request)")
    return _Retryable(f"closed during handshake ({code})")


def _is_fatal_http(exc: InvalidStatus) -> bool:
    status = exc.response.status_code
    return 400 <= status < 500 and status not in (408, 429)


# ---------------------------------------------------------------------------
# Mock (MUSE_MOCK): energy-based endpointing on the PCM itself
# ---------------------------------------------------------------------------

SAMPLE_RATE = 24_000
BYTES_PER_MS = PCM_BYTES_PER_S // 1000  # 48 bytes of PCM16 mono per millisecond


def pcm16_rms(frame: bytes) -> float:
    """RMS of little-endian PCM16 samples, normalized to [0, 1]."""
    samples = array("h", frame[: len(frame) // 2 * 2])
    if not samples:
        return 0.0
    if sys.byteorder == "big":
        samples.byteswap()
    return math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0


class _Endpointer:
    """Turn detector: speech starts after START_MS above threshold, ends after END_MS below it."""

    START_MS = 160
    END_MS = 700
    MIN_THRESHOLD = 0.01
    FLOOR_FACTOR = 3.0
    MAX_TURN_MS = 30_000

    def __init__(self) -> None:
        self.floor = 0.002
        self.in_speech = False
        self.above_ms = 0.0
        self.below_ms = 0.0
        self.candidate_start_ms = 0.0
        self.turn_start_ms = 0.0
        self.last_voiced_ms = 0.0

    @property
    def threshold(self) -> float:
        return max(self.MIN_THRESHOLD, self.FLOOR_FACTOR * self.floor)

    def update(self, rms: float, frame_ms: float, end_ms: float) -> str | None:
        """Feed one frame ending at `end_ms` of stream time; returns "start", "end" or None."""
        loud = rms > self.threshold
        self._adapt_floor(rms, loud)
        if loud:
            if self.above_ms == 0:
                self.candidate_start_ms = end_ms - frame_ms
            self.above_ms += frame_ms
            self.below_ms = 0.0
            self.last_voiced_ms = end_ms
        else:
            self.above_ms = 0.0
            self.below_ms += frame_ms
        if not self.in_speech and self.above_ms >= self.START_MS:
            self.in_speech = True
            self.turn_start_ms = self.candidate_start_ms
            return "start"
        if self.in_speech and (self.below_ms >= self.END_MS or end_ms - self.turn_start_ms >= self.MAX_TURN_MS):
            self.in_speech = False
            self.above_ms = 0.0
            return "end"
        return None

    def _adapt_floor(self, rms: float, loud: bool) -> None:
        # Quiet frames pull the floor quickly; loud frames nudge it up very slowly so a
        # permanent rise in room noise is eventually absorbed instead of reading as one endless turn.
        rate = 0.002 if loud else 0.1
        self.floor += rate * (rms - self.floor)
        self.floor = max(self.floor, 1e-4)


class MockMuseRealtime(_ClientBase):
    """Offline stand-in for Muse: same interface, deterministic events driven by the audio energy."""

    PARTIAL_EVERY_MS = 480
    PROGRESS_EVERY_MS = 1000
    LABELS = ("A", "B")

    def __init__(self, on_event: OnEvent, keywords: list[str], on_status: OnStatus | None = None) -> None:
        super().__init__(on_event, keywords, on_status)
        self._ep = _Endpointer()
        self._stream_ms = 0.0
        self._next_turn_id = 1
        self._turn_id: int | None = None
        self._turns = 0
        self._last_partial_ms = 0.0
        self._next_progress_ms = float(self.PROGRESS_EVERY_MS)
        self._done = asyncio.Event()

    async def run(self) -> None:
        self.session_id = f"mock-{uuid.uuid4()}"
        self.connected = True
        await self._status("connected", self.session_id)
        try:
            while True:
                frame = await self._queue.get()
                if frame is _END:
                    await self._finish_turn()
                    return
                await self._process(frame)
        finally:
            self.connected = False
            self._done.set()
            await self._status("closed", "")

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._queue.put(_END)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._done.wait(), timeout=CLOSE_TIMEOUT_S)

    async def _process(self, frame: bytes) -> None:
        frame_ms = len(frame) / BYTES_PER_MS
        self._stream_ms += frame_ms
        edge = self._ep.update(pcm16_rms(frame), frame_ms, self._stream_ms)
        if edge == "start":
            await self._start_turn()
        elif edge == "end":
            await self._finish_turn()
        elif self._turn_id is not None and self._stream_ms - self._last_partial_ms >= self.PARTIAL_EVERY_MS:
            self._last_partial_ms = self._stream_ms
            await self._event("transcript", transcript=self._mock_text(), final=False)
        if self._stream_ms >= self._next_progress_ms:
            self._next_progress_ms += self.PROGRESS_EVERY_MS
            await self._event("audioProgress")

    async def _start_turn(self) -> None:
        self._turn_id = self._next_turn_id
        self._next_turn_id += 1
        self._last_partial_ms = self._stream_ms
        await self._event("speechStart", turnId=self._turn_id)

    async def _finish_turn(self) -> None:
        if self._turn_id is None:
            return
        turn_id, text = self._turn_id, self._mock_text()
        label = self.LABELS[self._turns % len(self.LABELS)]
        self._turn_id = None
        self._turns += 1
        self._ep.in_speech = False
        await self._event("transcript", transcript=text, final=True)
        await self._event("speaker", label=label)
        await self._event("speechEnd", turnId=turn_id)
        await self._event("speechComplete", turnId=turn_id, transcript=text)

    def _mock_text(self) -> str:
        voiced_ms = max(0.0, self._ep.last_voiced_ms - self._ep.turn_start_ms)
        return f"(mock speech, {voiced_ms / 1000:.1f} s)"

    async def _event(self, type_: str, **fields: Any) -> None:
        await self._emit({"type": type_, **fields, "audioProcessedMs": int(self._stream_ms)})


def make_realtime_client(
    on_event: OnEvent, keywords: list[str], on_status: OnStatus | None = None
) -> MuseRealtimeClient | MockMuseRealtime:
    """The real client when a Meta key is configured, otherwise the offline mock."""
    if config.MUSE_MOCK:
        return MockMuseRealtime(on_event, keywords, on_status)
    return MuseRealtimeClient(on_event, keywords, on_status)
