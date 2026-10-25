"""Async AI gateway: Claude (when ANTHROPIC_API_KEY is set), then Gemini (PRD 9.1, 11.3), then Muse Spark (11.4).

Text calls (json/text/grounded) try the providers in that order; TTS is Gemini only. Mock mode applies
only when no text provider has a key.

Request shapes are the ones verified against google-genai 2.33.0 (`client.aio.interactions.create`).
Never send temperature, top_p or top_k. Never log prompt or transcript text: only models, timings
and error classes.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json as jsonlib
import logging
import re
import struct
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

import config
from ai.claude import Claude, ClaudeError

log = logging.getLogger("breadcrumbs.ai.gemini")

T = TypeVar("T", bound=BaseModel)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
# Billing or auth failures do not fix themselves mid-session: pause Gemini and skip straight to the
# next provider instead of paying a doomed round trip on every call.
UNAVAILABLE_STATUS = {401, 402, 403}
GEMINI_PAUSE_S = 600.0
# Muse Spark is a reasoning model: hidden reasoning tokens count against max_tokens, so a Gemini-sized
# cap (400 to 600) left no room for the answer and the content came back empty (finish_reason "length").
SPARK_REASONING_EFFORT = "minimal"  # Spark rejects "none"; minimal answered in ~1.8 s vs ~3.8 s default
SPARK_REASONING_HEADROOM_TOKENS = 3000
SPARK_TIMEOUT_S = 20.0
_RETRYABLE_ERROR_NAMES = {"APIConnectionError", "APITimeoutError", "ServerError"}

TTS_SAMPLE_RATE = 24_000
_DROP_SCHEMA_KEYS = {"title", "default", "examples"}


class GeminiError(RuntimeError):
    """Raised when Gemini (and the Muse Spark fallback, if any) could not produce a result."""


# --- Helpers ------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read `key` from an SDK object or a plain dict."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def schema_for(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema with every $ref inlined and titles/defaults dropped (Gemini-friendly)."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                target = defs.get(ref.rsplit("/", 1)[-1], {})
                return resolve(copy.deepcopy(target))
            return {k: resolve(v) for k, v in node.items() if k not in _DROP_SCHEMA_KEYS}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(raw)


def pcm_to_wav(pcm: bytes, sample_rate: int = TTS_SAMPLE_RATE, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw little-endian PCM16 in a RIFF/WAVE header."""
    byte_rate = sample_rate * channels * sample_width
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm), b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, channels * sample_width, sample_width * 8,
        b"data", len(pcm),
    )
    return header + pcm


def _rate_from_mime(mime: str) -> int | None:
    match = re.search(r"rate=(\d+)", mime or "")
    return int(match.group(1)) if match else None


def audio_to_wav(output_audio: Any) -> bytes | None:
    """Decode `interaction.output_audio` into WAV bytes (wrapping audio/l16 raw PCM)."""
    data = _get(output_audio, "data")
    if not data:
        return None
    raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    if raw[:4] == b"RIFF":
        return raw
    # Anything without a RIFF header (audio/l16, or a mislabeled payload) is raw PCM16.
    mime = str(_get(output_audio, "mime_type") or "").lower()
    rate = _get(output_audio, "sample_rate") or _rate_from_mime(mime) or TTS_SAMPLE_RATE
    channels = _get(output_audio, "channels") or 1
    return pcm_to_wav(raw, sample_rate=int(rate), channels=int(channels))


def extract_citation(interaction: Any) -> tuple[str | None, str | None]:
    """First url_citation (url, title) from interaction.steps[*].content[*].annotations[*]."""
    for step in _get(interaction, "steps") or []:
        for block in _get(step, "content") or []:
            for ann in _get(block, "annotations") or []:
                if _get(ann, "type") != "url_citation":
                    continue
                url = _get(ann, "url")
                if url:
                    return str(url), (str(_get(ann, "title")) if _get(ann, "title") else None)
    return None, None


def output_text(interaction: Any) -> str:
    """interaction.output_text, falling back to joining model_output text blocks."""
    text = _get(interaction, "output_text")
    if isinstance(text, str) and text:
        return text
    parts: list[str] = []
    for step in _get(interaction, "steps") or []:
        if _get(step, "type") not in (None, "model_output"):
            continue
        for block in _get(step, "content") or []:
            if _get(block, "type") == "text" and isinstance(_get(block, "text"), str):
                parts.append(_get(block, "text"))
    return "".join(parts)


def parse_json(text: str, schema: type[T]) -> T:
    """Parse model text into `schema`, tolerating code fences and leading/trailing chatter."""
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    try:
        return schema.model_validate_json(cleaned)
    except ValidationError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return schema.model_validate_json(cleaned[start : end + 1])


def status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "code"):
        status = getattr(exc, attr, None)
        if isinstance(status, int):
            return status
    return None


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException, httpx.TransportError)):
        return True
    status = status_of(exc)
    if status is not None:
        return status in RETRYABLE_STATUS or status >= 500
    return type(exc).__name__ in _RETRYABLE_ERROR_NAMES


def is_rate_limited(exc: BaseException) -> bool:
    return status_of(exc) == 429 or type(exc).__name__ == "RateLimitError"


def build_client(api_key: str) -> Any:
    """genai.Client whose Interactions resource never retries on its own.

    `_create` owns retries (and the Muse Spark fallback on a 429). Left at its defaults the SDK also
    retries 408/409/429/5xx with 0.5 to 8 s backoff inside every one of our attempts, so one rate
    limited call became 12 requests and reached Spark only after ~10 s. `HttpRetryOptions(attempts=1)`
    turns SDK retries off for the classic API; the Interactions client in google-genai 2.33 maps
    `attempts` to a retry *count* (so 1 still means one retry), hence its retry config is cleared too.
    """
    from google import genai  # imported lazily so mock mode never needs credentials
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
    )
    try:
        client.aio.interactions.sdk_configuration.retry_config = None
    except AttributeError:  # a future SDK without this attribute: attempts=1 still bounds it
        log.warning("could not disable SDK-level interactions retries")
    return client


# --- Client ---------------------------------------------------------------------------


class Gemini:
    """Thin async wrapper over the Interactions API. One instance per process (see get_gemini)."""

    def __init__(
        self,
        *,
        mock: bool | None = None,
        api_key: str | None = None,
        meta_api_key: str | None = None,
        client: Any = None,
        timeout_s: float = 8.0,
        retries: int = 2,
        backoff_s: tuple[float, ...] = (0.25, 0.75),
        claude: Claude | None = None,
    ) -> None:
        self.mock: bool = config.GEMINI_MOCK if mock is None else mock
        self._api_key = api_key if api_key is not None else config.GEMINI_API_KEY
        self._meta_key = meta_api_key if meta_api_key is not None else config.MODEL_API_KEY
        self._client = client
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self._http: httpx.AsyncClient | None = None
        self.claude = claude
        self._gemini_paused_until = 0.0

    # -- plumbing ----------------------------------------------------------------------

    @property
    def _interactions(self) -> Any:
        if self._client is None:
            self._client = build_client(self._api_key)
        return self._client.aio.interactions

    async def _create(self, *, timeout_s: float | None = None, retries: int | None = None, **body: Any) -> Any:
        """interactions.create with a hard timeout and backoff retries on 429/5xx/timeouts.

        Every call sets store=False: Breadcrumbs never reads an interaction back (no
        previous_interaction_id), so nothing it sends should be kept on Google's side (PRD 15.1).
        A 429 is not retried when Muse Spark is configured: the caller goes straight to Spark (11.4).
        """
        if not self.gemini_available:
            raise GeminiError("gemini unavailable (no key, or paused after a billing or auth error)")
        body["store"] = False
        timeout = timeout_s or self.timeout_s
        attempts = 1 + (self.retries if retries is None else retries)
        last: BaseException | None = None
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self._interactions.create(**body, timeout=timeout), timeout=timeout + 1.0
                )
                log.debug("gemini %s ok in %.0f ms", body.get("model"), (time.monotonic() - started) * 1000)
                return result
            except Exception as exc:  # noqa: BLE001 - classified below, never swallowed silently
                last = exc
                retry = is_retryable(exc) and attempt < attempts - 1
                if retry and self.spark_available and is_rate_limited(exc):
                    retry = False  # rate limited: hand over to Muse Spark now instead of backing off
                log.warning(
                    "gemini %s failed (%s), attempt %d/%d%s",
                    body.get("model"), type(exc).__name__, attempt + 1, attempts, ", retrying" if retry else "",
                )
                if status_of(exc) in UNAVAILABLE_STATUS:
                    self._gemini_paused_until = time.monotonic() + GEMINI_PAUSE_S
                    log.warning("gemini unavailable (HTTP %s); pausing it for %.0f s", status_of(exc), GEMINI_PAUSE_S)
                    break
                if not retry:
                    break
                await asyncio.sleep(self.backoff_s[min(attempt, len(self.backoff_s) - 1)])
        raise GeminiError(f"gemini call failed: {type(last).__name__}") from last

    async def _spark(self, *, system: str | None, user: str, max_tokens: int) -> str:
        """Muse Spark via the OpenAI-compatible Chat Completions endpoint (PRD 11.4)."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=SPARK_TIMEOUT_S)
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        resp = await self._http.post(
            f"{config.META_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {self._meta_key}"},
            json={
                "model": config.MUSE_SPARK_MODEL,
                "messages": messages,
                "max_tokens": max_tokens + SPARK_REASONING_HEADROOM_TOKENS,
                "reasoning_effort": SPARK_REASONING_EFFORT,
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise GeminiError("empty Muse Spark response")
        return content

    @property
    def spark_available(self) -> bool:
        return bool(self._meta_key)

    @property
    def gemini_available(self) -> bool:
        has_key = bool(self._api_key) or self._client is not None
        return has_key and time.monotonic() >= self._gemini_paused_until

    @property
    def claude_available(self) -> bool:
        return self.claude is not None and self.claude.available

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # -- public API ----------------------------------------------------------------------

    async def json(
        self,
        *,
        model: str,
        system: str,
        payload: dict | str,
        schema: type[T],
        max_tokens: int = 400,
        thinking_level: str = "low",
    ) -> T:
        """Structured output parsed into `schema`. Raises GeminiError on failure (or in mock mode)."""
        if self.mock:
            raise GeminiError("mock mode: callers use their deterministic mock path")
        user = payload if isinstance(payload, str) else jsonlib.dumps(payload, ensure_ascii=False)
        if self.claude_available:
            try:
                return await self.claude.json(system=system, user=user, schema=schema, max_tokens=max_tokens)
            except ClaudeError as exc:
                log.warning("claude json failed (%s); trying the next provider", exc)
        json_schema = schema_for(schema)
        try:
            interaction = await self._create(
                model=model,
                system_instruction=system,
                input=user,
                response_format={"type": "text", "mime_type": "application/json", "schema": json_schema},
                generation_config={"thinking_level": thinking_level, "max_output_tokens": max_tokens},
            )
            return parse_json(output_text(interaction), schema)
        except (GeminiError, ValidationError, ValueError) as exc:
            if not self.spark_available:
                raise GeminiError(f"json generation failed: {type(exc).__name__}") from exc
            log.warning("gemini json failed (%s); trying Muse Spark", type(exc).__name__)
        spark_system = (
            f"{system}\n\nReturn only one JSON object that matches this JSON schema, with no other text:\n"
            f"{jsonlib.dumps(json_schema)}"
        )
        try:
            return parse_json(await self._spark(system=spark_system, user=user, max_tokens=max_tokens), schema)
        except Exception as exc:  # noqa: BLE001
            raise GeminiError(f"Muse Spark json failed: {type(exc).__name__}") from exc

    async def text(self, *, model: str, input: str, system: str | None = None, max_tokens: int = 300) -> str:
        if self.mock:
            raise GeminiError("mock mode: callers use their deterministic mock path")
        if self.claude_available:
            try:
                return await self.claude.text(system=system, user=input, max_tokens=max_tokens)
            except ClaudeError as exc:
                log.warning("claude text failed (%s); trying the next provider", exc)
        body: dict[str, Any] = {
            "model": model,
            "input": input,
            "generation_config": {"thinking_level": "low", "max_output_tokens": max_tokens},
        }
        if system:
            body["system_instruction"] = system
        try:
            text = output_text(await self._create(**body)).strip()
            if text:
                return text
            raise GeminiError("empty text")
        except GeminiError as exc:
            if not self.spark_available:
                raise
            log.warning("gemini text failed (%s); trying Muse Spark", type(exc).__name__)
        try:
            return (await self._spark(system=system, user=input, max_tokens=max_tokens)).strip()
        except Exception as exc:  # noqa: BLE001
            raise GeminiError(f"Muse Spark text failed: {type(exc).__name__}") from exc

    async def grounded(self, prompt: str) -> tuple[str, str | None, str | None]:
        """Google Search grounded text: (text, citation_url, citation_title). Never raises."""
        if self.mock:
            return "", None, None
        if self.claude_available:
            try:
                text, url, title = await self.claude.grounded(prompt)
                if text and url:
                    return text, url, title
            except ClaudeError as exc:
                log.warning("claude grounded failed (%s); trying Gemini", exc)
        try:
            interaction = await self._create(
                model=config.GEMINI_MODEL,
                input=prompt,
                tools=[{"type": "google_search"}],
                timeout_s=max(self.timeout_s, 10.0),
                retries=1,
            )
        except GeminiError:
            return "", None, None
        url, title = extract_citation(interaction)
        return output_text(interaction).strip(), url, title

    async def tts(self, text: str) -> bytes | None:
        """WAV bytes (24 kHz mono 16-bit) from Gemini TTS, falling back to the lite TTS model, else None."""
        if self.mock or not text.strip():
            return None
        for model in (config.GEMINI_TTS_MODEL, config.GEMINI_TTS_FALLBACK_MODEL):
            try:
                interaction = await self._create(
                    model=model,
                    input=[{
                        "type": "user_input",
                        "content": [{
                            "type": "text",
                            "text": text,
                            "annotations": [{"type": "speech_metadata", "style": config.TTS_STYLE}],
                        }],
                    }],
                    response_format={"type": "audio", "mime_type": "audio/wav"},
                    generation_config={"speech_config": [{"voice": config.TTS_VOICE}]},
                    retries=1,
                )
                wav = audio_to_wav(_get(interaction, "output_audio"))
                if wav:
                    return wav
                log.warning("tts %s returned no audio", model)
            except GeminiError:
                continue
            except Exception as exc:  # noqa: BLE001 - bad audio payloads must not break delivery
                log.warning("tts %s decode failed (%s)", model, type(exc).__name__)
        return None


_singleton: Gemini | None = None


def get_gemini() -> Gemini:
    """Process-wide AI gateway (mock only when neither GEMINI_API_KEY nor ANTHROPIC_API_KEY is set)."""
    global _singleton
    if _singleton is None:
        _singleton = Gemini(mock=config.AI_MOCK, claude=Claude() if config.CLAUDE_ENABLED else None)
    return _singleton
