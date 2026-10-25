"""Muse Voice Transcribe REST client for profile answers (PRD 11.2) + deterministic mock answers.

Input is a 24 kHz (or 16 kHz) mono 16-bit WAV, already converted by `audio.convert.to_wav_24k`.
Transcript text is returned to the caller and never logged.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

import config

log = logging.getLogger("breadcrumbs.muse")

REQUEST_TIMEOUT_S = 60.0
RETRY_DELAYS_S: tuple[float, ...] = (0.5, 1.5)  # 2 retries
MAX_BODY_BYTES = 32 * 1024 * 1024  # Muse REST limit
RETRYABLE_STATUS = frozenset({408, 429})  # plus every 5xx


class TranscribeError(Exception):
    """Transcription failed for a reason the caller should surface (bad audio, auth, exhausted retries)."""


def build_request() -> dict:
    """The `request` multipart part: handshake settings minus authorization, plus WAV encoding."""
    return {
        "model": config.ASR_MODEL,
        "audioEncoding": "WAV",
        "mode": "PUSH_TO_TALK",
        "languageBias": ["English"],
    }


async def transcribe_wav(
    wav_bytes: bytes, *, mock_key: str = "", transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Return the transcript of one recorded answer. `transport` exists for tests (httpx.MockTransport)."""
    if config.MUSE_MOCK:
        return mock_answer(mock_key)
    _check_wav(wav_bytes)
    files = {
        "request": (None, json.dumps(build_request()), "application/json"),
        "audio": ("answer.wav", wav_bytes, "audio/wav"),
    }
    headers = {"Authorization": f"Bearer {config.MODEL_API_KEY}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, transport=transport) as client:
        response = await _post_with_retries(client, files, headers)
    return _parse_transcript(response)


async def _post_with_retries(client: httpx.AsyncClient, files: dict, headers: dict) -> httpx.Response:
    attempts = len(RETRY_DELAYS_S) + 1
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = await client.post(config.MUSE_TRANSCRIBE_URL, headers=headers, files=files)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            log.warning("muse transcribe network error (attempt %d/%d): %s", attempt + 1, attempts, type(exc).__name__)
            if last:
                raise TranscribeError("Could not reach Muse to transcribe the answer") from exc
        else:
            if response.status_code < 400:
                return response
            if not _retryable(response.status_code):
                raise TranscribeError(f"Muse transcribe rejected the request (HTTP {response.status_code})")
            log.warning("muse transcribe HTTP %d (attempt %d/%d)", response.status_code, attempt + 1, attempts)
            if last:
                raise TranscribeError(f"Muse transcribe kept failing (HTTP {response.status_code})")
        await asyncio.sleep(RETRY_DELAYS_S[attempt])
    raise TranscribeError("unreachable")  # pragma: no cover


def _retryable(status: int) -> bool:
    return status >= 500 or status in RETRYABLE_STATUS


def _parse_transcript(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError as exc:
        raise TranscribeError("Muse transcribe returned a non-JSON response") from exc
    transcript = body.get("transcript") if isinstance(body, dict) else None
    if not isinstance(transcript, str):
        raise TranscribeError("Muse transcribe response has no transcript")
    return transcript.strip()


def _check_wav(wav_bytes: bytes) -> None:
    if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise TranscribeError("Audio is not a WAV file")
    if len(wav_bytes) > MAX_BODY_BYTES:
        raise TranscribeError("Audio is too large to transcribe (32 MB limit)")


# ---------------------------------------------------------------------------
# Mock answers (MUSE_MOCK). Seat 1: training for a first half marathon, loves cooking, product designer.
# Seat 2: stopped running after a knee injury and switched to climbing, backend engineer, board games.
# They share Japan travel, cooking and building for local communities; running vs climbing is the contrast.
# ---------------------------------------------------------------------------

GENERIC_ANSWER = (
    "Honestly I just love meeting new people and trying new things around Austin. Lately I have been "
    "exploring coffee shops and live music, and I am always up for a good recommendation."
)

MOCK_ANSWERS: dict[str, str] = {
    # Social: Q1 talk for an hour, Q2 story friends make you tell, Q3 looking forward to.
    "social:q1:seat1": (
        "I could talk for an hour about training for my first half marathon. I started with couch to 5K last "
        "spring and now I do long runs every Saturday along Lady Bird Lake. I am weirdly into running playlists."
    ),
    "social:q1:seat2": (
        "Climbing, for sure. I used to run a lot until I hurt my knee, and a friend dragged me to a bouldering "
        "gym. Now I go three times a week and I love that every route is basically a puzzle you solve with your body."
    ),
    "social:q2:seat1": (
        "The time I got lost in Tokyo with no phone. My battery died in Shinjuku, I did not speak any Japanese, "
        "and a grandmother at a ramen shop drew me a map on a napkin. I still have the napkin."
    ),
    "social:q2:seat2": (
        "My friends always make me tell the story of the first time I climbed outdoors. I got halfway up, froze, "
        "and a ten year old kid on the next route talked me through it. He was a much better coach than my friends."
    ),
    "social:q3:seat1": (
        "My race in November. It is the Austin half and my goal is just to finish without walking. "
        "After that I am planning a big ramen night for everyone who put up with my training schedule."
    ),
    "social:q3:seat2": (
        "I am going to Japan in the spring to climb near Tokyo and eat my way through every noodle shop I can find. "
        "I have been teaching myself to cook ramen at home to get ready, with mixed results."
    ),
    # Networking: Q1 working on, Q2 trying to learn, Q3 can help with + hoping to meet.
    "networking:q1:seat1": (
        "I am a product designer, and on the side I am building an app that helps people find local farmers "
        "markets and what is in season. It started as a weekend project and now a few hundred people use it."
    ),
    "networking:q1:seat2": (
        "I am a backend engineer at a small startup that builds booking software for climbing gyms. "
        "I am excited about a new feature that predicts how crowded the gym will be so people can plan their sessions."
    ),
    "networking:q2:seat1": (
        "I am trying to figure out how to scale the backend of my app. Right now it is held together with "
        "spreadsheets and hope, and I want to learn how to design a real API without overbuilding it."
    ),
    "networking:q2:seat2": (
        "I am trying to learn product design. I can build almost anything, but I struggle to figure out what "
        "users actually want, so I have been reading about user interviews and trying to run my first few."
    ),
    "networking:q3:seat1": (
        "I can help with user research, prototyping and making things feel simple. I am hoping to meet an "
        "engineer who enjoys building tools for local communities and would want to trade ideas."
    ),
    "networking:q3:seat2": (
        "I can help with APIs, databases and getting a side project to scale. I am hoping to meet a designer "
        "who could give me honest feedback on our app, and maybe someone who knows the Austin startup scene."
    ),
    # Speed dating: Q1 perfect Saturday, Q2 small thing that makes you happy, Q3 surprising fact.
    "speed_dating:q1:seat1": (
        "A long run in the morning, then the farmers market, then cooking a huge dinner for friends while "
        "someone picks the music. Ideally there is a board game at the end that gets way too competitive."
    ),
    "speed_dating:q1:seat2": (
        "Sleeping in a little, a morning session at the climbing gym, tacos from a food truck, and then a "
        "board game night with friends. I take strategy games very seriously, which is a red flag I own."
    ),
    "speed_dating:q2:seat1": (
        "Finding a perfectly ripe avocado at the store. Also when a song I love comes on shuffle right as I "
        "start the last mile of a run. That timing makes me unreasonably happy."
    ),
    "speed_dating:q2:seat2": (
        "When a houseplant I almost gave up on puts out a new leaf. I have about fifteen plants and I name "
        "all of them. Also finally sending a climbing route I have been stuck on for weeks."
    ),
    "speed_dating:q3:seat1": (
        "People are surprised that I competed in spelling bees all through middle school. I made it to "
        "the state finals once and I still spell words in my head when I get nervous."
    ),
    "speed_dating:q3:seat2": (
        "People are usually surprised that I lived in Japan for a year teaching English after college. "
        "I can still order ramen like a local, but my Japanese mostly stops there."
    ),
}


def mock_answer(mock_key: str) -> str:
    """Deterministic canned answer for "mode:qN:seatS"; unknown keys get a generic answer."""
    key = mock_key.strip().lower().replace(" ", "")
    return MOCK_ANSWERS.get(key, GENERIC_ANSWER)
