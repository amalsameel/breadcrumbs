"""Gemini client against a fake SDK: exact request kwargs, citation parsing, WAV wrapping, retries, fallback."""
from __future__ import annotations

import base64
import io
import json
import wave
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

import config
from ai.crumbs import CrumbDraft
from ai.gemini import Gemini, GeminiError, audio_to_wav, extract_citation, pcm_to_wav, schema_for
from ai.profiles import OverlapDraftSet, ProfileDraft
from models import EnergyTag, HoldDecision

FORBIDDEN = {"temperature", "top_p", "top_k"}


class _Status(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class FakeInteractions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _client(responses: list[Any]) -> tuple[Any, FakeInteractions]:
    fake = FakeInteractions(responses)
    return SimpleNamespace(aio=SimpleNamespace(interactions=fake)), fake


def _gemini(responses: list[Any], **kw: Any) -> tuple[Gemini, FakeInteractions]:
    client, fake = _client(responses)
    return Gemini(mock=False, api_key="test", meta_api_key=kw.pop("meta", ""), client=client, backoff_s=(0.0,), **kw), fake


def _keys_deep(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys_deep(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys_deep(v)}
    return set()


class Small(BaseModel):
    name: str
    count: int


async def test_json_request_shape_and_parse():
    g, fake = _gemini([SimpleNamespace(output_text='{"name": "a", "count": 2}')])
    result = await g.json(model=config.GEMINI_MODEL, system="sys", payload={"x": 1}, schema=Small, max_tokens=123)
    assert result == Small(name="a", count=2)
    call = fake.calls[0]
    assert set(call) == {"model", "system_instruction", "input", "response_format", "generation_config", "timeout"}
    assert call["model"] == "gemini-3.8-flash" and call["system_instruction"] == "sys"
    assert json.loads(call["input"]) == {"x": 1}
    assert call["response_format"] == {"type": "text", "mime_type": "application/json", "schema": schema_for(Small)}
    assert call["generation_config"] == {"thinking_level": "low", "max_output_tokens": 123}
    assert not (_keys_deep(call) & FORBIDDEN)


@pytest.mark.parametrize("model", [ProfileDraft, OverlapDraftSet, CrumbDraft, HoldDecision, EnergyTag])
def test_schemas_are_flat(model: type[BaseModel]):
    text = json.dumps(schema_for(model))
    assert "$ref" not in text and "$defs" not in text and '"title"' not in text


async def test_text_request_shape():
    g, fake = _gemini([SimpleNamespace(output_text=" hello ")])
    assert await g.text(model=config.GEMINI_LITE_MODEL, input="hi", system="s", max_tokens=50) == "hello"
    call = fake.calls[0]
    assert call["generation_config"] == {"thinking_level": "low", "max_output_tokens": 50}
    assert not (_keys_deep(call) & FORBIDDEN)


def _grounded_interaction(as_dicts: bool) -> Any:
    ann = {"type": "url_citation", "url": "https://example.org/bats", "title": "Bats", "start_index": 0, "end_index": 5}
    other = {"type": "speech_metadata", "style": "x"}
    if as_dicts:
        return {"output_text": "Austin has bats.", "steps": [
            {"type": "google_search_call"},
            {"type": "model_output", "content": [{"type": "text", "text": "Austin has bats.", "annotations": [other, ann]}]},
        ]}
    return SimpleNamespace(output_text="Austin has bats.", steps=[
        SimpleNamespace(type="google_search_call", content=None),
        SimpleNamespace(type="model_output", content=[SimpleNamespace(
            type="text", text="Austin has bats.", annotations=[SimpleNamespace(**other), SimpleNamespace(**ann)])]),
    ])


@pytest.mark.parametrize("as_dicts", [False, True])
async def test_grounded_request_and_citation_parsing(as_dicts: bool):
    g, fake = _gemini([_grounded_interaction(as_dicts)])
    text, url, title = await g.grounded("Find one fact.")
    assert (text, url, title) == ("Austin has bats.", "https://example.org/bats", "Bats")
    call = fake.calls[0]
    assert call["tools"] == [{"type": "google_search"}] and call["model"] == config.GEMINI_MODEL
    assert "response_format" not in call and not (_keys_deep(call) & FORBIDDEN)


def test_extract_citation_none_when_missing():
    assert extract_citation(SimpleNamespace(steps=[SimpleNamespace(content=[SimpleNamespace(annotations=None)])])) == (None, None)
    assert extract_citation(SimpleNamespace(steps=None)) == (None, None)


async def test_tts_request_shape_and_l16_wav_wrapping():
    pcm = (b"\x01\x00\xff\x7f") * 600
    audio = SimpleNamespace(data=base64.b64encode(pcm).decode(), mime_type="audio/l16", sample_rate=None, channels=None)
    g, fake = _gemini([SimpleNamespace(output_audio=audio)])
    wav = await g.tts("Maya, hi!")
    call = fake.calls[0]
    assert call["model"] == "gemini-3.8-flash-tts"
    assert call["input"] == [{"type": "user_input", "content": [{
        "type": "text", "text": "Maya, hi!", "annotations": [{"type": "speech_metadata", "style": config.TTS_STYLE}],
    }]}]
    assert call["response_format"] == {"type": "audio", "mime_type": "audio/wav"}
    assert call["generation_config"] == {"speech_config": [{"voice": config.TTS_VOICE}]}
    assert not (_keys_deep(call) & FORBIDDEN)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)
        assert w.readframes(w.getnframes()) == pcm


def test_audio_to_wav_passes_through_riff_and_reads_rate_from_mime():
    wav = pcm_to_wav(b"\x00\x00" * 10)
    assert audio_to_wav({"data": base64.b64encode(wav).decode(), "mime_type": "audio/wav"}) == wav
    wrapped = audio_to_wav({"data": base64.b64encode(b"\x00\x00" * 10).decode(), "mime_type": "audio/l16;rate=16000"})
    with wave.open(io.BytesIO(wrapped)) as w:
        assert w.getframerate() == 16000
    assert audio_to_wav(None) is None


async def test_tts_falls_back_to_lite_model_then_none():
    audio = SimpleNamespace(data=base64.b64encode(pcm_to_wav(b"\x00\x00")).decode(), mime_type="audio/wav")
    g, fake = _gemini([_Status(400), SimpleNamespace(output_audio=audio)])
    assert (await g.tts("Dev, go."))[:4] == b"RIFF"
    assert [c["model"] for c in fake.calls] == ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
    g2, _ = _gemini([_Status(400), _Status(400)])
    assert await g2.tts("Dev, go.") is None


async def test_retries_on_429_and_5xx_then_succeeds():
    g, fake = _gemini([_Status(429), _Status(503), SimpleNamespace(output_text='{"name": "a", "count": 1}')])
    assert (await g.json(model="m", system="s", payload="p", schema=Small)).count == 1
    assert len(fake.calls) == 3


async def test_timeouts_are_retried():
    g, fake = _gemini([TimeoutError(), SimpleNamespace(output_text='{"name": "a", "count": 1}')])
    assert (await g.json(model="m", system="s", payload="p", schema=Small)).name == "a"
    assert len(fake.calls) == 2


async def test_client_errors_are_not_retried():
    g, fake = _gemini([_Status(400), SimpleNamespace(output_text="{}")])
    with pytest.raises(GeminiError):
        await g.json(model="m", system="s", payload="p", schema=Small)
    assert len(fake.calls) == 1


async def test_gives_up_after_two_retries():
    g, fake = _gemini([_Status(500)] * 5)
    with pytest.raises(GeminiError):
        await g.text(model="m", input="x")
    assert len(fake.calls) == 3


async def test_muse_spark_fallback_for_json():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        content = '```json\n{"name": "spark", "count": 3}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})

    g, fake = _gemini([_Status(429)] * 3, meta="meta-key")
    g._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await g.json(model="m", system="sys", payload={"a": 1}, schema=Small, max_tokens=99)
    assert result == Small(name="spark", count=3)
    request = seen[0]
    assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer meta-key"
    body = json.loads(request.content)
    assert body["model"] == "muse-spark-1.3" and body["max_tokens"] == 99
    assert not (set(body) & FORBIDDEN)
    assert body["messages"][0]["role"] == "system" and "JSON schema" in body["messages"][0]["content"]
    await g.aclose()


async def test_no_spark_without_meta_key():
    g, _ = _gemini([SimpleNamespace(output_text="not json")])
    with pytest.raises(GeminiError):
        await g.json(model="m", system="s", payload="p", schema=Small)


async def test_mock_mode_never_calls_the_sdk():
    g = Gemini(mock=True, client=SimpleNamespace())  # attribute access would fail if used
    with pytest.raises(GeminiError):
        await g.json(model="m", system="s", payload="p", schema=Small)
    assert await g.grounded("x") == ("", None, None)
    assert await g.tts("x") is None
