"""Muse REST transcription (PRD 11.2) with httpx.MockTransport, plus the mock answer bank."""
from __future__ import annotations

import io
import json
import re
import wave

import httpx
import pytest

import config
from muse import transcribe
from muse.transcribe import GENERIC_ANSWER, MOCK_ANSWERS, TranscribeError, mock_answer, transcribe_wav

MODES = ("social", "networking", "speed_dating")


def make_wav(seconds: float = 0.2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(b"\x00\x00" * int(24_000 * seconds))
    return buf.getvalue()


def parse_multipart(request: httpx.Request) -> dict[str, dict]:
    """Split a multipart body into {name: {"headers": str, "body": bytes}}."""
    ctype = request.headers["content-type"]
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=", 1)[1].encode()
    parts: dict[str, dict] = {}
    for chunk in request.content.split(b"--" + boundary):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        head, _, body = chunk.partition(b"\r\n\r\n")
        headers = head.decode()
        name = re.search(r'name="([^"]+)"', headers).group(1)
        parts[name] = {"headers": headers, "body": body}
    return parts


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MUSE_MOCK", False)
    monkeypatch.setattr(config, "MODEL_API_KEY", "test-key")
    monkeypatch.setattr(transcribe, "RETRY_DELAYS_S", (0.0, 0.0))


async def test_request_shape_is_exact(live: None) -> None:
    wav = make_wav()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"sessionId": "s", "transcript": " I love climbing. ", "audioDurationMs": 200, "turns": []})

    text = await transcribe_wav(wav, transport=httpx.MockTransport(handler))
    assert text == "I love climbing."
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://api.meta.ai/v1/asr/transcribe"
    assert req.headers["authorization"] == "Bearer test-key"
    assert req.headers["accept"] == "application/json"
    parts = parse_multipart(req)
    assert set(parts) == {"request", "audio"}
    assert "filename" not in parts["request"]["headers"]
    assert "Content-Type: application/json" in parts["request"]["headers"]
    assert json.loads(parts["request"]["body"]) == {
        "model": "muse-voice-transcribe-1.0",
        "audioEncoding": "WAV",
        "mode": "PUSH_TO_TALK",
        "languageBias": ["English"],
    }
    assert 'filename="answer.wav"' in parts["audio"]["headers"]
    assert "Content-Type: audio/wav" in parts["audio"]["headers"]
    assert parts["audio"]["body"] == wav


async def test_retries_429_then_succeeds(live: None) -> None:
    statuses = iter([429, 503, 200])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = next(statuses)
        if status != 200:
            return httpx.Response(status, json={"error": "slow down"})
        return httpx.Response(200, json={"transcript": "ok"})

    assert await transcribe_wav(make_wav(), transport=httpx.MockTransport(handler)) == "ok"
    assert calls == 3


async def test_retries_timeouts_then_gives_up(live: None) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(TranscribeError, match="reach Muse"):
        await transcribe_wav(make_wav(), transport=httpx.MockTransport(handler))
    assert calls == 3


async def test_exhausted_5xx_raises(live: None) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    with pytest.raises(TranscribeError, match="HTTP 500"):
        await transcribe_wav(make_wav(), transport=transport)


async def test_client_error_is_not_retried(live: None) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(TranscribeError, match="HTTP 401"):
        await transcribe_wav(make_wav(), transport=httpx.MockTransport(handler))
    assert calls == 1


async def test_bad_response_body_raises(live: None) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"sessionId": "s"}))
    with pytest.raises(TranscribeError, match="no transcript"):
        await transcribe_wav(make_wav(), transport=transport)


async def test_rejects_non_wav_without_calling_muse(live: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called")

    with pytest.raises(TranscribeError, match="not a WAV"):
        await transcribe_wav(b"\x1aE\xdf\xa3 webm bytes" * 10, transport=httpx.MockTransport(handler))


async def test_mock_mode_returns_canned_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MUSE_MOCK", True)
    first = await transcribe_wav(b"", mock_key="social:q1:seat1")
    assert first == await transcribe_wav(b"anything", mock_key="Social:Q1:Seat1")
    assert "half marathon" in first
    assert await transcribe_wav(b"", mock_key="nope") == GENERIC_ANSWER
    assert await transcribe_wav(b"") == GENERIC_ANSWER


def test_mock_bank_covers_every_mode_question_and_seat() -> None:
    keys = {f"{m}:q{q}:seat{s}" for m in MODES for q in (1, 2, 3) for s in (1, 2)}
    assert set(MOCK_ANSWERS) == keys
    for mode in MODES:
        for q in (1, 2, 3):
            a1, a2 = mock_answer(f"{mode}:q{q}:seat1"), mock_answer(f"{mode}:q{q}:seat2")
            assert a1 != a2
    for text in MOCK_ANSWERS.values():
        assert "\u2014" not in text
        assert 20 <= len(text.split()) <= 60  # about 20 seconds of speech


def test_mock_personas_share_threads() -> None:
    seat1 = " ".join(v for k, v in MOCK_ANSWERS.items() if k.startswith("social") and k.endswith("seat1")).lower()
    seat2 = " ".join(v for k, v in MOCK_ANSWERS.items() if k.startswith("social") and k.endswith("seat2")).lower()
    assert "half marathon" in seat1 and "climb" in seat2 and "knee" in seat2  # PRD 3.1 contrast
    for shared in ("ramen", "tokyo", "japan"):
        assert shared in seat1 or shared in seat2
    assert "ramen" in seat1 and "ramen" in seat2  # a direct overlap for the opener
