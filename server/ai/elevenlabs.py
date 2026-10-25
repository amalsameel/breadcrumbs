"""ElevenLabs crumb voice (PRD 9.8): text to 24 kHz mono 16-bit WAV over the REST API.

Request shapes follow the ElevenLabs API reference as of 2026-10 (not guessed):
  * Text to Speech: POST /v1/text-to-speech/{voice_id}?output_format=pcm_24000, header `xi-api-key`,
    JSON {text, model_id, language_code?, voice_settings{stability, similarity_boost, style,
    use_speaker_boost, speed}, seed}. Answers raw audio bytes.
  * Text to Dialogue: POST /v1/text-to-dialogue?output_format=pcm_24000, JSON {inputs[{text, voice_id}],
    model_id, settings{stability, similarity}, seed}. Eleven v4 is served ONLY on this endpoint; a
    single input is a one-voice line.
`pcm_24000` is headerless signed 16-bit little-endian mono PCM at 24 kHz, exactly the format the rest of
the voice pipeline uses, so it is wrapped with ai.gemini.pcm_to_wav (no decoding, no resampling).

Model choice (two paths, two priorities):
  * Prefetched crumbs (ready_steer / ready_switch, PREFETCH_TTS_BUDGET_S = 12 s): the latency is hidden
    behind the conversation, so naturalness wins. Eleven v4 is the most emotive model; if this account
    or plan cannot use it, Multilingual v2 ("lifelike, consistent") is next, then Flash.
  * Live path (cache misses, Drop a crumb, the opener, calibration lines; 1 to 5 s budgets): Flash v2.5,
    ~75 ms model latency, so the line usually lands inside the 1 s miss budget with network included.
    Turbo v2.5 is deprecated in favour of Flash v2.5.

Budgets: callers pass their time budget (`budget_s`). Each model gets a share of what is left, so a slow
or stuck model never uses up the whole budget and the next model (and, in ai.tts, Gemini) still runs.
A timeout on the natural path moves straight to the next model; a retry only runs when the time left can
cover it.

Voice: "Sarah" (default library voice EXAVITQu4vr4xnSDxMaL), a young adult American woman, warm and
reassuring with a soft, unhurried delivery: a friendly host speaking at a small table rather than a
narrator. Default voices stay available through 2026-12-31 per ElevenLabs; set ELEVENLABS_VOICE_ID to
switch voices without a code change.

Never log the text or the key: only endpoint, model, status, timings, sizes and request ids.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import ssl
import sys
import time
from array import array
from dataclasses import dataclass
from typing import Literal

import certifi
import httpx

import config
from ai.gemini import TTS_SAMPLE_RATE, pcm_to_wav

log = logging.getLogger("breadcrumbs.ai.elevenlabs")

DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"  # Sarah: warm, reassuring, soft-spoken American host
NATURAL_MODEL = "eleven_v4"  # most emotive; Text to Dialogue endpoint only
NATURAL_FALLBACK_MODEL = "eleven_multilingual_v2"  # lifelike and very consistent; Text to Speech endpoint
FAST_MODEL = "eleven_flash_v2_5"  # ~75 ms model latency; Text to Speech endpoint
OUTPUT_FORMAT = "pcm_24000"  # raw PCM16 mono at TTS_SAMPLE_RATE
LANGUAGE_CODE = "en"  # pinned on Flash so a short line full of names is never read with a foreign accent
# A fixed seed makes repeated generations of the same line sound the same (consistent voice across a session).
SEED = 20261024

# Stability 0.5 is the documented default and the "natural" middle: lower adds emotional range but drifts
# between lines, higher goes flat. Style exaggeration stays off: it adds latency and makes short lines
# theatrical. Speaker boost sharpens likeness to the voice; it costs a little latency, so Flash skips it.
NATURAL_VOICE_SETTINGS = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True, "speed": 1.0}
FAST_VOICE_SETTINGS = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": False, "speed": 1.0}
DIALOGUE_SETTINGS = {"stability": 0.5, "similarity": 0.75}

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
PROVIDER_DOWN_STATUS = {401, 402}  # bad key or quota exhausted: not fixable mid-session
# 403 can be model specific (a plan without a newer model): it sets that model aside, and pauses the whole
# provider only when even the basic Flash model is refused.
PROVIDER_PAUSE_S = 600.0
ROUTE_FAILS_BEFORE_PAUSE = 2  # consecutive 4xx answers before a model is set aside (one bad line is not enough)
ROUTE_PAUSE_S = 600.0
RETRY_BACKOFF_S = 0.25
RATE_LIMIT_BACKOFF_S = 0.6
FAST_READ_TIMEOUT_S = 4.0
NATURAL_READ_TIMEOUT_S = 12.0
CONNECT_TIMEOUT_S = 3.0
# Budget split across the models of one path: a model that is not the last gets this share of the time
# left (the first natural model, v4, gets half; Multilingual v2 then gets 60% of what remains; the last
# model gets everything left). An attempt needs at least MIN_ATTEMPT_S to be worth sending.
ROUTE_BUDGET_SHARE = (0.5, 0.6)
MIN_ATTEMPT_S = 0.3
MIN_AUDIO_BYTES = 2_400  # 50 ms of PCM16 at 24 kHz; anything shorter is not a spoken line

# Edge trimming: TTS output usually starts and ends with dead air. Trimming it makes the Table's
# "started speaking" moment match the first audible word and ends the bot-speech interval promptly.
SILENCE_THRESHOLD = 180  # |sample| on a 32767 scale, about -45 dBFS
LEAD_KEEP_MS = 30
TAIL_KEEP_MS = 120

_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")
_ERROR_CODE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_REQUEST_ID_HEADERS = ("request-id", "x-request-id", "xi-request-id")

Endpoint = Literal["speech", "dialogue"]


class ElevenLabsError(RuntimeError):
    """One request failed in a way the caller should not retry (a status, a bad payload or the network)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Route:
    model: str
    endpoint: Endpoint


def _route_for(model: str) -> Route:
    return Route(model, "dialogue" if model.startswith("eleven_v4") else "speech")


def trim_silence(pcm: bytes, sample_rate: int = TTS_SAMPLE_RATE) -> bytes:
    """Drop leading and trailing near-silence from PCM16 mono, keeping a short natural margin."""
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    first = next((i for i in range(n) if abs(samples[i]) > SILENCE_THRESHOLD), None)
    if first is None:
        return pcm  # no audible sample at all: leave it for the caller's size check to judge
    last = next(i for i in range(n - 1, -1, -1) if abs(samples[i]) > SILENCE_THRESHOLD)
    start = max(0, first - sample_rate * LEAD_KEEP_MS // 1000)
    end = min(n, last + 1 + sample_rate * TAIL_KEEP_MS // 1000)
    return pcm[start * 2:end * 2]


def _tls_context() -> ssl.SSLContext:
    # The python.org macOS build has no system root store; certifi's bundle keeps TLS verification on.
    return ssl.create_default_context(cafile=certifi.where())


def _error_code(response: httpx.Response) -> str:
    """ElevenLabs' short error code (e.g. quota_exceeded) when it is a plain identifier, else ''."""
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001 - error bodies are best effort
        return ""
    code = detail.get("status") if isinstance(detail, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE_RE.match(code) else ""


def _request_id(response: httpx.Response) -> str:
    for name in _REQUEST_ID_HEADERS:
        value = response.headers.get(name)
        if value:
            return value[:80]
    return "-"


class ElevenLabs:
    """Async ElevenLabs TTS client. One per process (see get_elevenlabs); safe to share across sessions."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        voice_id: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = (config.ELEVENLABS_API_KEY if api_key is None else api_key).strip()
        voice = (config.ELEVENLABS_VOICE_ID if voice_id is None else voice_id).strip()
        if voice and not _VOICE_ID_RE.match(voice):
            log.warning("ELEVENLABS_VOICE_ID is not a voice id; using the default voice")
            voice = ""
        self.voice_id = voice or DEFAULT_VOICE_ID
        override = (config.ELEVENLABS_MODEL if model is None else model).strip()
        natural = [_route_for(override)] if override else [Route(NATURAL_MODEL, "dialogue")]
        for fallback in (Route(NATURAL_FALLBACK_MODEL, "speech"), Route(FAST_MODEL, "speech")):
            if fallback not in natural:
                natural.append(fallback)
        self.natural_routes: tuple[Route, ...] = tuple(natural)
        self.fast_routes: tuple[Route, ...] = (Route(FAST_MODEL, "speech"),)
        self.base_url = (base_url or config.ELEVENLABS_API_BASE).rstrip("/")
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._paused_until = 0.0
        self._route_fails: dict[Route, int] = {}
        self._route_paused_until: dict[Route, float] = {}

    @property
    def available(self) -> bool:
        """A key is configured and the provider is not paused after an auth or quota failure."""
        return bool(self._api_key) and time.monotonic() >= self._paused_until

    def routes(self, fast: bool) -> list[Route]:
        """Models to try in order for this path, skipping any set aside after repeated rejections."""
        now = time.monotonic()
        chain = self.fast_routes if fast else self.natural_routes
        usable = [r for r in chain if now >= self._route_paused_until.get(r, 0.0)]
        return usable or [chain[-1]]  # never leave a path empty: the last resort is always tried

    async def synthesize(self, text: str, *, fast: bool, budget_s: float | None = None) -> bytes | None:
        """WAV bytes (24 kHz mono 16-bit) for `text`, or None when no model produced audio in time.

        `fast=True` is the live path (Flash), `fast=False` the prefetch path (most natural model first).
        `budget_s` bounds the whole call: it returns (None) before the budget runs out rather than being
        cut off by the caller's asyncio.wait_for, and each model gets only a share of it (see
        ROUTE_BUDGET_SHARE). None keeps the per-request timeouts alone.
        """
        if not text.strip() or not self.available:
            return None
        deadline = None if budget_s is None else time.monotonic() + max(0.0, budget_s)
        routes = self.routes(fast)
        for index, route in enumerate(routes):
            route_deadline = self._route_deadline(deadline, index, len(routes))
            if route_deadline is not None and route_deadline - time.monotonic() < MIN_ATTEMPT_S:
                log.info("elevenlabs out of time before %s; giving up on this line", route.model)
                return None
            try:
                pcm = await self._request(route, text, fast, route_deadline)
            except ElevenLabsError as exc:
                if exc.status in PROVIDER_DOWN_STATUS or (exc.status == 403 and route.model == FAST_MODEL):
                    self._paused_until = time.monotonic() + PROVIDER_PAUSE_S
                    log.warning("elevenlabs paused for %.0f s (%s)", PROVIDER_PAUSE_S, exc)
                    return None
                if exc.status is not None and 400 <= exc.status < 500:
                    self._note_route_failure(route)
                log.warning("elevenlabs %s failed (%s); trying the next model", route.model, exc)
                continue
            self._route_fails.pop(route, None)
            return pcm_to_wav(pcm, sample_rate=TTS_SAMPLE_RATE)
        return None

    async def warm(self) -> bool:
        """Open the pooled TLS connection ahead of the first live line (GET /v1/models bills no characters).

        Call it when a session starts so calibration lines and early misses skip the handshake.
        """
        if not self.available:
            return False
        try:
            response = await self._http().get(
                f"{self.base_url}/v1/models",
                headers={"xi-api-key": self._api_key},
                timeout=httpx.Timeout(FAST_READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            log.info("elevenlabs warm-up failed (%s)", type(exc).__name__)
            return False
        if response.status_code in PROVIDER_DOWN_STATUS:
            self._paused_until = time.monotonic() + PROVIDER_PAUSE_S
            log.warning("elevenlabs paused for %.0f s (warm-up status %d)", PROVIDER_PAUSE_S, response.status_code)
            return False
        return response.status_code == 200

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None
        self._client_loop = None

    # -- internals ----------------------------------------------------------------------------------

    @staticmethod
    def _route_deadline(deadline: float | None, index: int, count: int) -> float | None:
        """When the model at `index` (of `count`) must give up, leaving time for the models after it."""
        if deadline is None:
            return None
        now = time.monotonic()
        remaining = deadline - now
        if index >= count - 1 or remaining <= 0:
            return deadline
        share = ROUTE_BUDGET_SHARE[min(index, len(ROUTE_BUDGET_SHARE) - 1)]
        return now + remaining * share

    def _note_route_failure(self, route: Route) -> None:
        fails = self._route_fails.get(route, 0) + 1
        self._route_fails[route] = fails
        if fails >= ROUTE_FAILS_BEFORE_PAUSE:
            self._route_paused_until[route] = time.monotonic() + ROUTE_PAUSE_S
            self._route_fails.pop(route, None)
            log.warning("elevenlabs model %s set aside for %.0f s", route.model, ROUTE_PAUSE_S)

    def _http(self) -> httpx.AsyncClient:
        """A pooled client bound to the running loop (keep-alive saves the TLS handshake on the live path)."""
        loop = asyncio.get_running_loop()
        if self._client is None or self._client_loop is not loop or self._client.is_closed:
            kwargs: dict = {"timeout": httpx.Timeout(NATURAL_READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            else:
                kwargs["verify"] = _tls_context()
                kwargs["limits"] = httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120.0)
            self._client = httpx.AsyncClient(**kwargs)
            self._client_loop = loop
        return self._client

    def _build(self, route: Route, text: str, fast: bool) -> tuple[str, dict]:
        if route.endpoint == "dialogue":
            url = f"{self.base_url}/v1/text-to-dialogue"
            body: dict = {
                "inputs": [{"text": text, "voice_id": self.voice_id}],
                "model_id": route.model,
                "settings": dict(DIALOGUE_SETTINGS),
                "seed": SEED,
            }
            return url, body
        url = f"{self.base_url}/v1/text-to-speech/{self.voice_id}"
        settings = FAST_VOICE_SETTINGS if fast or route.model == FAST_MODEL else NATURAL_VOICE_SETTINGS
        body = {"text": text, "model_id": route.model, "voice_settings": dict(settings), "seed": SEED}
        if route.model == FAST_MODEL:
            body["language_code"] = LANGUAGE_CODE  # language enforcement is a Flash/Turbo v2.5 feature
        return url, body

    async def _request(self, route: Route, text: str, fast: bool, deadline: float | None = None) -> bytes:
        """Raw PCM16 for one model, with one retry on 429, 5xx, timeouts and dropped connections.

        Every attempt (connect, upload and the whole download) ends by `deadline`. A timeout on the
        natural path is not retried: a slow model is better replaced by the next one. A retry is skipped
        when the time left cannot cover its backoff plus MIN_ATTEMPT_S.
        """
        url, body = self._build(route, text, fast)
        headers = {"xi-api-key": self._api_key, "Content-Type": "application/json"}
        read_s = FAST_READ_TIMEOUT_S if fast else NATURAL_READ_TIMEOUT_S
        attempts = 2

        def time_left() -> float:
            return read_s if deadline is None else min(read_s, deadline - time.monotonic())

        def can_retry(attempt: int, backoff_s: float) -> bool:
            return attempt < attempts and time_left() >= backoff_s + MIN_ATTEMPT_S

        for attempt in range(1, attempts + 1):
            window = time_left()
            if window < MIN_ATTEMPT_S:
                raise ElevenLabsError("out of time")
            timeout = httpx.Timeout(window, connect=min(CONNECT_TIMEOUT_S, window))
            started = time.perf_counter()
            try:
                # httpx's read timeout is per chunk; wait_for bounds the whole exchange.
                response = await asyncio.wait_for(
                    self._http().post(
                        url, params={"output_format": OUTPUT_FORMAT}, json=body, headers=headers, timeout=timeout
                    ),
                    timeout=window,
                )
            except (httpx.TimeoutException, TimeoutError) as exc:
                log.info(
                    "elevenlabs %s %s timed out after %.0f ms",
                    route.endpoint, route.model, (time.perf_counter() - started) * 1000,
                )
                if (fast or route.model == FAST_MODEL) and can_retry(attempt, RETRY_BACKOFF_S):
                    await asyncio.sleep(RETRY_BACKOFF_S)
                    continue
                raise ElevenLabsError(type(exc).__name__) from None
            except httpx.TransportError as exc:
                if can_retry(attempt, RETRY_BACKOFF_S):
                    await asyncio.sleep(RETRY_BACKOFF_S)
                    continue
                raise ElevenLabsError(type(exc).__name__) from None
            ms = (time.perf_counter() - started) * 1000
            rid = _request_id(response)
            if response.status_code != 200:
                code = _error_code(response)
                log.info(
                    "elevenlabs %s %s -> %d %s in %.0f ms (request %s)",
                    route.endpoint, route.model, response.status_code, code or "-", ms, rid,
                )
                backoff_s = RATE_LIMIT_BACKOFF_S if response.status_code == 429 else RETRY_BACKOFF_S
                if response.status_code in RETRYABLE_STATUS and can_retry(attempt, backoff_s):
                    await asyncio.sleep(backoff_s)
                    continue
                raise ElevenLabsError(f"status {response.status_code} {code}".strip(), response.status_code)
            pcm = self._pcm_from(response.content)
            log.info(
                "elevenlabs %s %s ok in %.0f ms (%d bytes, %.2f s audio, request %s)",
                route.endpoint, route.model, ms, len(pcm), len(pcm) / (2 * TTS_SAMPLE_RATE), rid,
            )
            return pcm
        raise ElevenLabsError("no attempt made")  # unreachable: the loop always returns or raises

    @staticmethod
    def _pcm_from(content: bytes) -> bytes:
        raw = content
        if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
            marker = raw.find(b"data", 12)
            raw = raw[marker + 8:] if marker >= 0 else b""  # a WAV answer: keep only its samples
        pcm = trim_silence(raw)
        if len(pcm) < MIN_AUDIO_BYTES:
            raise ElevenLabsError(f"too little audio ({len(raw)} bytes)")
        return pcm


_singleton: ElevenLabs | None = None
_keyless: ElevenLabs | None = None

# Set to 1 to let a deliberate live test (pytest) use the real key from .env. Off by default.
LIVE_TESTS_ENV = "BREADCRUMBS_ELEVENLABS_LIVE_TESTS"


def _hermetic_test_run() -> bool:
    """True inside a pytest run that has not opted in to real ElevenLabs calls.

    pytest sets PYTEST_VERSION for the whole session and PYTEST_CURRENT_TEST while a test runs. Most of
    the suite reaches ai.tts.synthesize through this singleton; with a real key in .env every such test
    would make a real, billed request.
    """
    in_pytest = "PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ
    return in_pytest and os.environ.get(LIVE_TESTS_ENV, "").strip() != "1"


def get_elevenlabs() -> ElevenLabs:
    """Process-wide client, built from config (unavailable when ELEVENLABS_API_KEY is empty).

    Under pytest (without the LIVE_TESTS_ENV opt-in) it is always keyless, whatever .env holds; a
    client a test installs as `_singleton` on purpose (with its own fake transport) is used as is.
    """
    global _singleton, _keyless
    if _singleton is not None:
        return _singleton
    if _hermetic_test_run():
        if _keyless is None:
            _keyless = ElevenLabs(api_key="")
        return _keyless
    _singleton = ElevenLabs()
    return _singleton
