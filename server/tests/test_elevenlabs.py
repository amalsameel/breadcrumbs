"""ElevenLabs voice (ai/elevenlabs.py) and the TTS provider chain (ai/tts.py): ElevenLabs -> Gemini -> None.

Every request goes through httpx.MockTransport; nothing here reaches the network.
"""
from __future__ import annotations

import asyncio
import json
import math
import struct
import time

import httpx
import pytest

import ai.crumbs as crumbs_mod
import ai.elevenlabs as el_mod
import ai.tts as tts_mod
import config
from ai.elevenlabs import (
    DEFAULT_VOICE_ID,
    FAST_MODEL,
    NATURAL_FALLBACK_MODEL,
    NATURAL_MODEL,
    ElevenLabs,
    Route,
    trim_silence,
)
from ai.gemini import Gemini
from ai.tts import speech_text, synthesize

KEY = "test-key-not-real"
RATE = 24_000


def _tone(seconds: float, *, lead_s: float = 0.0, tail_s: float = 0.0, amp: int = 8000) -> bytes:
    """PCM16 mono: silence, a 220 Hz tone, silence."""
    out = bytearray(b"\x00\x00" * int(lead_s * RATE))
    for i in range(int(seconds * RATE)):
        out += struct.pack("<h", int(amp * math.sin(2 * math.pi * 220 * i / RATE)))
    out += b"\x00\x00" * int(tail_s * RATE)
    return bytes(out)


PCM = _tone(0.5)


class Recorder:
    """A MockTransport handler that answers from a script and records every request."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.script = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.script.pop(0) if self.script else httpx.Response(200, content=PCM)
        if isinstance(item, Exception):
            raise item
        return item

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def _client(rec: Recorder, **kwargs) -> ElevenLabs:  # noqa: ANN003
    kwargs.setdefault("api_key", KEY)
    kwargs.setdefault("voice_id", "")
    kwargs.setdefault("model", "")
    return ElevenLabs(base_url="https://api.elevenlabs.io", transport=httpx.MockTransport(rec), **kwargs)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(el_mod, "RETRY_BACKOFF_S", 0.0)
    monkeypatch.setattr(el_mod, "RATE_LIMIT_BACKOFF_S", 0.0)


def _wav_parts(wav: bytes) -> tuple[int, int, int, bytes]:
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    channels, rate = struct.unpack("<HI", wav[22:28])
    bits = struct.unpack("<H", wav[34:36])[0]
    assert wav[36:40] == b"data"
    size = struct.unpack("<I", wav[40:44])[0]
    return channels, rate, bits, wav[44:44 + size]


# --- request shapes -----------------------------------------------------------------------------


async def test_fast_path_uses_flash_on_text_to_speech():
    rec = Recorder()
    wav = await _client(rec).synthesize("Maya, tell Dev about Tokyo.", fast=True)
    assert wav is not None
    (req,) = rec.requests
    assert req.method == "POST"
    assert req.url.scheme == "https" and req.url.host == "api.elevenlabs.io"
    assert req.url.path == f"/v1/text-to-speech/{DEFAULT_VOICE_ID}"
    assert dict(req.url.params) == {"output_format": "pcm_24000"}
    assert req.headers["xi-api-key"] == KEY
    assert req.headers["content-type"] == "application/json"
    body = rec.bodies()[0]
    assert body["text"] == "Maya, tell Dev about Tokyo."
    assert body["model_id"] == FAST_MODEL == "eleven_flash_v2_5"
    assert body["language_code"] == "en"
    assert isinstance(body["seed"], int)
    vs = body["voice_settings"]
    assert set(vs) == {"stability", "similarity_boost", "style", "use_speaker_boost", "speed"}
    assert 0 <= vs["stability"] <= 1 and 0 <= vs["similarity_boost"] <= 1
    assert vs["style"] == 0.0 and 0.7 <= vs["speed"] <= 1.2


async def test_natural_path_uses_v4_on_text_to_dialogue():
    rec = Recorder()
    wav = await _client(rec).synthesize("You both brought up spring.", fast=False)
    assert wav is not None
    (req,) = rec.requests
    assert req.url.path == "/v1/text-to-dialogue"
    assert dict(req.url.params) == {"output_format": "pcm_24000"}
    assert req.headers["xi-api-key"] == KEY
    body = rec.bodies()[0]
    assert body["model_id"] == NATURAL_MODEL == "eleven_v4"
    assert body["inputs"] == [{"text": "You both brought up spring.", "voice_id": DEFAULT_VOICE_ID}]
    assert set(body["settings"]) == {"stability", "similarity"}
    assert "voice_settings" not in body and "text" not in body


async def test_configured_voice_and_model_override():
    rec = Recorder()
    client = _client(rec, voice_id="21m00Tcm4TlvDq8ikWAM", model="eleven_multilingual_v2")
    await client.synthesize("Hi there.", fast=False)
    req = rec.requests[0]
    assert req.url.path == "/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
    body = rec.bodies()[0]
    assert body["model_id"] == "eleven_multilingual_v2"
    assert "language_code" not in body  # only Flash v2.5 takes language enforcement
    assert body["voice_settings"]["use_speaker_boost"] is True
    assert client.natural_routes == (Route(NATURAL_FALLBACK_MODEL, "speech"), Route(FAST_MODEL, "speech"))


async def test_malformed_voice_id_falls_back_to_default():
    rec = Recorder()
    await _client(rec, voice_id="../../v1/user").synthesize("Hi.", fast=True)
    assert rec.requests[0].url.path == f"/v1/text-to-speech/{DEFAULT_VOICE_ID}"


# --- audio handling -----------------------------------------------------------------------------


async def test_pcm_is_wrapped_as_24k_mono_16bit_wav():
    pcm = _tone(0.8)
    rec = Recorder(httpx.Response(200, content=pcm, headers={"content-type": "audio/pcm"}))
    wav = await _client(rec).synthesize("Hello.", fast=True)
    channels, rate, bits, data = _wav_parts(wav)
    assert (channels, rate, bits) == (1, 24_000, 16)
    assert data == pcm  # nothing to trim: the tone starts and ends at full level


async def test_leading_and_trailing_silence_is_trimmed():
    pcm = _tone(0.6, lead_s=0.4, tail_s=0.8)
    rec = Recorder(httpx.Response(200, content=pcm))
    wav = await _client(rec).synthesize("Hello.", fast=True)
    _, _, _, data = _wav_parts(wav)
    seconds = len(data) / (2 * RATE)
    assert 0.6 <= seconds <= 0.6 + (el_mod.LEAD_KEEP_MS + el_mod.TAIL_KEEP_MS) / 1000 + 0.01


def test_trim_silence_keeps_all_silent_input_and_odd_bytes():
    assert trim_silence(b"\x00\x00" * 100) == b"\x00\x00" * 100
    assert len(trim_silence(_tone(0.1) + b"\x01")) % 2 == 0


async def test_wav_answer_is_unwrapped_then_rewrapped():
    from ai.gemini import pcm_to_wav

    pcm = _tone(0.5)
    rec = Recorder(httpx.Response(200, content=pcm_to_wav(pcm)))
    wav = await _client(rec).synthesize("Hello.", fast=True)
    assert _wav_parts(wav)[3] == pcm


async def test_too_little_audio_counts_as_failure():
    rec = Recorder(httpx.Response(200, content=b"\x10\x00" * 10))
    assert await _client(rec).synthesize("Hello.", fast=True) is None


# --- retries and fallbacks ----------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_one_retry_on_rate_limit_and_server_errors(status):
    rec = Recorder(httpx.Response(status, json={"detail": {"status": "busy"}}), httpx.Response(200, content=PCM))
    assert await _client(rec).synthesize("Hello.", fast=True) is not None
    assert len(rec.requests) == 2


async def test_one_retry_on_timeout_then_give_up():
    rec = Recorder(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    assert await _client(rec).synthesize("Hello.", fast=True) is None
    assert len(rec.requests) == 2  # fast path has a single model: one try plus one retry


async def test_connect_error_is_retried():
    rec = Recorder(httpx.ConnectError("reset"), httpx.Response(200, content=PCM))
    assert await _client(rec).synthesize("Hello.", fast=True) is not None


async def test_natural_path_falls_back_through_models():
    rec = Recorder(
        httpx.Response(422, json={"detail": {"status": "invalid_model"}}),  # v4 refused: no retry
        httpx.Response(503), httpx.Response(503),  # multilingual v2 down twice
        httpx.Response(200, content=PCM),  # flash answers
    )
    wav = await _client(rec).synthesize("Hello.", fast=False)
    assert wav is not None
    assert [b["model_id"] for b in rec.bodies()] == [NATURAL_MODEL, NATURAL_FALLBACK_MODEL, NATURAL_FALLBACK_MODEL, FAST_MODEL]
    assert rec.requests[0].url.path == "/v1/text-to-dialogue"
    assert rec.requests[-1].url.path.startswith("/v1/text-to-speech/")


async def test_repeatedly_refused_model_is_set_aside():
    rec = Recorder(httpx.Response(400), httpx.Response(200, content=PCM), httpx.Response(400), httpx.Response(200, content=PCM))
    client = _client(rec)
    await client.synthesize("One.", fast=False)
    await client.synthesize("Two.", fast=False)
    assert Route(NATURAL_MODEL, "dialogue") not in client.routes(fast=False)
    await client.synthesize("Three.", fast=False)
    assert rec.bodies()[-1]["model_id"] == NATURAL_FALLBACK_MODEL


async def test_single_refusal_does_not_set_a_model_aside():
    rec = Recorder(httpx.Response(400), httpx.Response(200, content=PCM), httpx.Response(200, content=PCM))
    client = _client(rec)
    await client.synthesize("One.", fast=False)
    await client.synthesize("Two.", fast=False)
    assert rec.bodies()[-1]["model_id"] == NATURAL_MODEL


@pytest.mark.parametrize("status", [401, 402])
async def test_auth_or_quota_failure_pauses_the_provider(status):
    rec = Recorder(httpx.Response(status, json={"detail": {"status": "quota_exceeded", "message": "x"}}))
    client = _client(rec)
    assert await client.synthesize("Hello.", fast=False) is None
    assert len(rec.requests) == 1  # no retry and no other model
    assert not client.available
    assert await client.synthesize("Again.", fast=True) is None
    assert len(rec.requests) == 1


async def test_forbidden_newer_model_only_sets_it_aside():
    rec = Recorder(httpx.Response(403), httpx.Response(200, content=PCM))
    client = _client(rec)
    assert await client.synthesize("Hello.", fast=False) is not None
    assert client.available


async def test_forbidden_flash_pauses_the_provider():
    rec = Recorder(httpx.Response(403))
    client = _client(rec)
    assert await client.synthesize("Hello.", fast=True) is None
    assert not client.available


async def test_no_key_never_sends():
    rec = Recorder()
    client = _client(rec, api_key="")
    assert not client.available
    assert await client.synthesize("Hello.", fast=True) is None
    assert rec.requests == []


async def test_key_and_text_never_logged(caplog):
    caplog.set_level("DEBUG")
    rec = Recorder(httpx.Response(500, json={"detail": {"status": "server_error", "message": "Hello secret"}}), httpx.Response(500))
    text = "Maya's secret line about Tokyo."
    await _client(rec).synthesize(text, fast=True)
    rec2 = Recorder(httpx.Response(200, content=PCM, headers={"request-id": "req_abc"}))
    await _client(rec2).synthesize(text, fast=True)
    logs = caplog.text
    assert KEY not in logs and "Tokyo" not in logs and "secret" not in logs
    assert "req_abc" in logs  # request ids are logged for support


def test_client_uses_certifi_tls():
    ctx = el_mod._tls_context()
    assert ctx.verify_mode.name == "CERT_REQUIRED"
    assert ctx.cert_store_stats()["x509_ca"] > 0


# --- the provider chain (ai/tts.py) -------------------------------------------------------------


class FakeVoice:
    def __init__(self, result: bytes | None | Exception, available: bool = True) -> None:
        self.result = result
        self.available = available
        self.calls: list[tuple[str, bool]] = []
        self.budgets: list[float | None] = []

    async def synthesize(self, text: str, *, fast: bool, budget_s: float | None = None) -> bytes | None:
        self.calls.append((text, fast))
        self.budgets.append(budget_s)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeGemini:
    def __init__(self, result: bytes | None = b"GEMINI", mock: bool = False) -> None:
        self.mock = mock
        self.result = result
        self.calls: list[str] = []

    async def tts(self, text: str) -> bytes | None:
        self.calls.append(text)
        return self.result


async def test_chain_prefers_elevenlabs():
    voice, gem = FakeVoice(b"ELEVEN"), FakeGemini()
    assert await synthesize("Hi Maya.", gem, elevenlabs=voice) == b"ELEVEN"
    assert voice.calls == [("Hi Maya.", True)]  # the default is the live (fast) path
    assert gem.calls == []


async def test_chain_passes_fast_false_for_prefetch():
    voice = FakeVoice(b"ELEVEN")
    await synthesize("Hi Maya.", FakeGemini(), fast=False, elevenlabs=voice)
    assert voice.calls == [("Hi Maya.", False)]


@pytest.mark.parametrize("result", [None, RuntimeError("boom")])
async def test_chain_falls_back_to_gemini(result):
    voice, gem = FakeVoice(result), FakeGemini(b"GEMINI")
    assert await synthesize("Hi Maya.", gem, elevenlabs=voice) == b"GEMINI"
    assert gem.calls == ["Hi Maya."]


async def test_chain_skips_unavailable_elevenlabs():
    voice, gem = FakeVoice(b"ELEVEN", available=False), FakeGemini(b"GEMINI")
    assert await synthesize("Hi Maya.", gem, elevenlabs=voice) == b"GEMINI"
    assert voice.calls == []


async def test_chain_returns_none_when_nothing_can_speak():
    voice, gem = FakeVoice(None), FakeGemini(mock=True)
    assert await synthesize("Hi Maya.", gem, elevenlabs=voice) is None
    assert gem.calls == []


async def test_chain_elevenlabs_works_even_when_text_ai_is_mock():
    assert await synthesize("Hi.", Gemini(mock=True), elevenlabs=FakeVoice(b"ELEVEN")) == b"ELEVEN"


async def test_chain_with_real_client_over_mock_transport():
    rec = Recorder()
    wav = await synthesize("[laughs] *Maya*, try <b>this</b>!", Gemini(mock=True), elevenlabs=_client(rec))
    assert wav and wav[:4] == b"RIFF"
    assert rec.bodies()[0]["text"] == "Maya, try this!"


async def test_empty_text_never_calls_a_provider():
    voice, gem = FakeVoice(b"ELEVEN"), FakeGemini()
    assert await synthesize("  [pause]  ", gem, elevenlabs=voice) is None
    assert voice.calls == [] and gem.calls == []


def test_speech_text_normalization():
    assert speech_text("Maya \u2014 meet Dev!") == "Maya, meet Dev!"
    assert speech_text("[whispers] Hi **Dev** & Maya") == "Hi Dev and Maya"
    assert speech_text("See https://example.com now.") == "See now."
    assert len(speech_text("word " * 500)) <= 1000


async def test_default_singleton_is_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(el_mod, "_singleton", None)
    assert not el_mod.get_elevenlabs().available
    assert await synthesize("Hi.", Gemini(mock=True)) is None


# --- crumbs.py picks the path from its budget --------------------------------------------------


async def test_crumbs_synth_uses_fast_on_live_budgets_and_natural_on_prefetch(monkeypatch):
    seen: list[bool] = []
    budgets: list[float | None] = []

    async def fake_synth(text, gemini=None, *, fast=True, budget_s=None):  # noqa: ANN001
        seen.append(fast)
        budgets.append(budget_s)
        return b"WAV"

    monkeypatch.setattr(crumbs_mod, "synthesize", fake_synth)
    svc = crumbs_mod.CrumbService.__new__(crumbs_mod.CrumbService)
    svc.gemini = Gemini(mock=True)
    for budget in (crumbs_mod.MISS_TTS_BUDGET_S, crumbs_mod.OPENER_TTS_BUDGET_S, crumbs_mod.PREFETCH_TTS_BUDGET_S):
        assert await svc._synth("Hi.", budget) == b"WAV"
    assert seen == [True, True, False]
    # The budget reaches the chain, so it can leave time for the next model and for Gemini.
    assert budgets == [crumbs_mod.MISS_TTS_BUDGET_S, crumbs_mod.OPENER_TTS_BUDGET_S, crumbs_mod.PREFETCH_TTS_BUDGET_S]


async def test_pooled_client_is_reused_within_a_loop():
    """Keep-alive matters on the 1 s live budget: consecutive lines share one connection pool."""
    rec = Recorder()
    client = _client(rec)
    assert await client.synthesize("One.", fast=True)
    first = client._client
    assert await client.synthesize("Two.", fast=True)
    assert client._client is first
    await client.aclose()
    assert client._client is None


def test_pooled_client_is_rebuilt_on_a_new_event_loop():
    """A client bound to a finished loop must not be reused (it would fail with 'Event loop is closed')."""
    import asyncio

    rec = Recorder()
    client = _client(rec)
    assert asyncio.run(client.synthesize("One.", fast=True))
    first = client._client
    assert asyncio.run(client.synthesize("Two.", fast=True))
    assert client._client is not first
    assert len(rec.requests) == 2


async def test_warm_opens_the_pool_without_synthesizing():
    rec = Recorder(httpx.Response(200, json=[]))
    client = _client(rec)
    assert await client.warm() is True
    (req,) = rec.requests
    assert req.method == "GET" and req.url.path == "/v1/models"
    assert req.headers["xi-api-key"] == KEY


async def test_warm_with_bad_key_pauses_the_provider():
    rec = Recorder(httpx.Response(401))
    client = _client(rec)
    assert await client.warm() is False
    assert not client.available


async def test_warm_without_key_is_a_no_op():
    rec = Recorder()
    assert await _client(rec, api_key="").warm() is False
    assert rec.requests == []


# --- budgets: a slow model never starves the rest of the chain ----------------------------------


class Hanging:
    """An async MockTransport handler: requests whose path contains `hang` never answer in time."""

    def __init__(self, hang: str, hang_s: float = 60.0) -> None:
        self.hang = hang
        self.hang_s = hang_s
        self.paths: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if self.hang in request.url.path:
            await asyncio.sleep(self.hang_s)
        return httpx.Response(200, content=PCM)


def _hanging_client(handler: Hanging) -> ElevenLabs:
    return ElevenLabs(api_key=KEY, voice_id="", model="", base_url="https://api.elevenlabs.io",
                      transport=httpx.MockTransport(handler))


async def test_prefetch_with_stuck_v4_still_returns_audio_within_the_crumbs_budget(monkeypatch):
    """Review defect: v4 on text-to-dialogue hangs, and the old 12 s read timeout used the whole prefetch
    budget. Through the real CrumbService._synth the natural path must reach the next model in time."""
    budget = 1.5  # PREFETCH_TTS_BUDGET_S scaled down so the test stays fast; _synth reads it at call time
    monkeypatch.setattr(crumbs_mod, "PREFETCH_TTS_BUDGET_S", budget)
    handler = Hanging("/v1/text-to-dialogue")
    monkeypatch.setattr(el_mod, "_singleton", _hanging_client(handler))
    svc = crumbs_mod.CrumbService.__new__(crumbs_mod.CrumbService)
    svc.gemini = Gemini(mock=True)
    started = time.monotonic()
    wav = await svc._synth("Ask Maya about the trip.", budget)
    elapsed = time.monotonic() - started
    assert wav is not None and wav[:4] == b"RIFF"
    assert elapsed < budget
    assert handler.paths[0] == "/v1/text-to-dialogue"
    assert handler.paths.count("/v1/text-to-dialogue") == 1  # a natural-path timeout is not retried
    assert handler.paths[1].startswith("/v1/text-to-speech/")


async def test_natural_path_splits_the_budget_across_models():
    handler = Hanging("/v1/text-to-")  # every model hangs
    started = time.monotonic()
    assert await _hanging_client(handler).synthesize("Hello.", fast=False, budget_s=2.0) is None
    assert time.monotonic() - started < 2.1
    # v4 gets half, Multilingual v2 60% of the rest, and Flash what is left (enough for one attempt).
    assert len(handler.paths) == 3


async def test_fast_path_returns_inside_its_budget_when_flash_stalls():
    handler = Hanging("/v1/text-to-speech/")
    started = time.monotonic()
    assert await _hanging_client(handler).synthesize("Hello.", fast=True, budget_s=0.5) is None
    assert time.monotonic() - started < 0.6
    assert len(handler.paths) == 1  # no time for a retry


async def test_retry_is_skipped_when_the_budget_cannot_cover_it():
    calls: list[str] = []

    async def slow_503(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        await asyncio.sleep(0.3)
        return httpx.Response(503)

    client = ElevenLabs(api_key=KEY, voice_id="", model="", base_url="https://api.elevenlabs.io",
                        transport=httpx.MockTransport(slow_503))
    assert await client.synthesize("Hello.", fast=True, budget_s=0.5) is None
    assert len(calls) == 1


async def test_without_a_budget_the_old_timeouts_and_retry_still_apply():
    rec = Recorder(httpx.Response(503), httpx.Response(200, content=PCM))
    assert await _client(rec).synthesize("Hello.", fast=True) is not None
    assert len(rec.requests) == 2


async def test_chain_keeps_time_for_gemini_when_elevenlabs_stalls(monkeypatch):
    monkeypatch.setattr(tts_mod, "GEMINI_MIN_BUDGET_S", 0.5)
    handler = Hanging("/v1/text-to-speech/")
    gem = FakeGemini(b"GEMINI")
    started = time.monotonic()
    out = await synthesize("Hi Maya.", gem, fast=True, elevenlabs=_hanging_client(handler), budget_s=1.0)
    assert out == b"GEMINI" and gem.calls == ["Hi Maya."]
    assert time.monotonic() - started < 1.0


async def test_chain_gives_elevenlabs_the_whole_short_budget_when_gemini_cannot_speak():
    voice = FakeVoice(b"ELEVEN")
    await synthesize("Hi.", FakeGemini(mock=True), elevenlabs=voice, budget_s=1.0)
    (given,) = voice.budgets
    assert 0.9 < given <= 1.0


async def test_chain_bounds_a_slow_gemini_by_the_budget():
    class SlowGemini(FakeGemini):
        async def tts(self, text: str) -> bytes | None:
            await asyncio.sleep(60)
            return b"LATE"

    started = time.monotonic()
    out = await synthesize("Hi.", SlowGemini(), elevenlabs=FakeVoice(None), budget_s=0.4)
    assert out is None and time.monotonic() - started < 0.5


async def test_crumbs_synth_cuts_off_a_slow_tts_at_the_miss_budget(monkeypatch):
    """The slow-TTS bound in _synth (tests/test_ai_regressions.py's fake no longer accepts the keywords)."""
    async def slow_tts(text, gemini=None, **kwargs):  # noqa: ANN001, ANN003
        await asyncio.sleep(100)
        return b"LATE"

    monkeypatch.setattr(crumbs_mod, "synthesize", slow_tts)
    svc = crumbs_mod.CrumbService.__new__(crumbs_mod.CrumbService)
    svc.gemini = Gemini(mock=True)
    started = time.monotonic()
    assert await svc._synth("Hi.", crumbs_mod.MISS_TTS_BUDGET_S) is None
    assert time.monotonic() - started < crumbs_mod.MISS_TTS_BUDGET_S + 0.2


# --- hermetic: a real key in .env never reaches the network from pytest -----------------------


async def test_singleton_is_keyless_under_pytest_even_with_a_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "sk_real_looking_key")
    monkeypatch.setattr(el_mod, "_singleton", None)
    monkeypatch.delenv(el_mod.LIVE_TESTS_ENV, raising=False)
    assert not el_mod.get_elevenlabs().available
    assert el_mod._singleton is None  # never cached, so a later opt-in still builds from config


async def test_live_test_opt_in_builds_the_configured_client(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "sk_real_looking_key")
    monkeypatch.setattr(el_mod, "_singleton", None)
    monkeypatch.setenv(el_mod.LIVE_TESTS_ENV, "1")
    assert el_mod.get_elevenlabs().available


async def test_chain_sends_nothing_to_the_real_network_under_pytest(monkeypatch):
    """Review defect: with ELEVENLABS_API_KEY in .env the suite made 661 real requests through the chain."""
    hits: list[str] = []

    async def blocked(self, request):  # noqa: ANN001
        hits.append(str(request.url))
        raise httpx.ConnectError("blocked in tests", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "sk_real_looking_key")
    monkeypatch.setattr(el_mod, "_singleton", None)
    monkeypatch.delenv(el_mod.LIVE_TESTS_ENV, raising=False)
    assert await synthesize("Maya, tell Dev about Tokyo.", Gemini(mock=True)) is None
    assert await synthesize("Maya, tell Dev about Tokyo.", Gemini(mock=True), fast=False) is None
    assert hits == []
