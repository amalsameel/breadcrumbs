"""Claude provider (ai/claude.py) and the gateway's provider order (Claude -> Gemini -> Muse Spark)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

import config
from ai.claude import Claude, ClaudeError
from ai.gemini import Gemini, GeminiError

FORBIDDEN = {"temperature", "top_p", "top_k", "thinking"}


class Small(BaseModel):
    name: str
    count: int


class _Status(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def _message(*, content: list[Any] | None = None, parsed: Any = None, stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=content or [], parsed_output=parsed, stop_reason=stop, stop_details=None, _request_id="req_test"
    )


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def _next(self, kind: str, kwargs: dict) -> Any:
        self.calls.append((kind, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def parse(self, **kwargs: Any) -> Any:
        return await self._next("parse", kwargs)

    async def create(self, **kwargs: Any) -> Any:
        return await self._next("create", kwargs)


def _claude(responses: list[Any]) -> tuple[Claude, FakeMessages]:
    fake = FakeMessages(responses)
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    return Claude(api_key="test", client=client), fake


class FakeInteractions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _gateway(claude: Claude | None, gemini_responses: list[Any], *, meta: str = "") -> tuple[Gemini, FakeInteractions]:
    fake = FakeInteractions(gemini_responses)
    client = SimpleNamespace(aio=SimpleNamespace(interactions=fake))
    return Gemini(mock=False, api_key="g", meta_api_key=meta, client=client, backoff_s=(0.0,), claude=claude), fake


# --- Claude request shapes --------------------------------------------------------------


async def test_json_request_shape():
    claude, fake = _claude([_message(parsed=Small(name="a", count=1))])
    result = await claude.json(system="sys", user='{"x": 1}', schema=Small, max_tokens=300)
    assert result == Small(name="a", count=1)
    kind, body = fake.calls[0]
    assert kind == "parse"
    assert body["model"] == config.CLAUDE_MODEL == "claude-opus-5-5"
    assert body["output_format"] is Small
    assert body["output_config"] == {"effort": config.CLAUDE_EFFORT}
    # Opus 5.5 always thinks: the cap carries headroom on top of the caller's answer budget.
    assert body["max_tokens"] == 300 + 4000
    assert body["betas"] == ["server-side-fallback-2026-07-01"] and body["fallbacks"] == "default"
    assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": '{"x": 1}'}]
    assert not (set(body) & FORBIDDEN)


async def test_refusal_and_truncation_raise():
    claude, _ = _claude([_message(stop="refusal"), _message(stop="max_tokens", parsed=Small(name="a", count=1))])
    with pytest.raises(ClaudeError, match="refused"):
        await claude.json(system="s", user="u", schema=Small, max_tokens=10)
    with pytest.raises(ClaudeError, match="max_tokens"):
        await claude.json(system="s", user="u", schema=Small, max_tokens=10)


async def test_grounded_uses_web_search_and_reads_the_citation():
    cite = SimpleNamespace(type="web_search_result_location", url="https://example.org/bats", title="Austin bats")
    content = [
        SimpleNamespace(type="server_tool_use"),
        SimpleNamespace(type="web_search_tool_result"),
        SimpleNamespace(type="text", text="Austin hosts the largest urban bat colony.", citations=[cite]),
    ]
    claude, fake = _claude([_message(content=content)])
    text, url, title = await claude.grounded("Find a fact")
    assert (text, url, title) == ("Austin hosts the largest urban bat colony.", "https://example.org/bats", "Austin bats")
    _, body = fake.calls[0]
    assert body["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}]


async def test_grounded_without_citation_is_not_a_fact():
    content = [SimpleNamespace(type="text", text="Something unsourced.", citations=None)]
    claude, _ = _claude([_message(content=content)])
    g, gemini = _gateway(claude, [_Status(402)])
    assert await g.grounded("Find a fact") == ("", None, None)  # no citation from either provider
    assert gemini.calls == 1


# --- Gateway order ---------------------------------------------------------------------


async def test_claude_answers_first_and_gemini_is_not_called():
    claude, _ = _claude([_message(parsed=Small(name="claude", count=2))])
    g, gemini = _gateway(claude, [])
    assert await g.json(model=config.GEMINI_MODEL, system="s", payload={"a": 1}, schema=Small) == Small(name="claude", count=2)
    assert gemini.calls == 0


async def test_claude_failure_falls_back_to_gemini():
    claude, _ = _claude([RuntimeError("boom")])
    g, gemini = _gateway(claude, [SimpleNamespace(output_text='{"name": "gemini", "count": 3}')])
    assert await g.json(model=config.GEMINI_MODEL, system="s", payload="p", schema=Small) == Small(name="gemini", count=3)
    assert gemini.calls == 1


async def test_gemini_billing_error_pauses_gemini_and_spark_answers():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"name": "spark", "count": 4}'}}]})

    g, gemini = _gateway(None, [_Status(402), _Status(402)], meta="meta-key")
    g._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    for _ in range(2):
        assert await g.json(model="m", system="s", payload="p", schema=Small) == Small(name="spark", count=4)
    assert gemini.calls == 1  # the 402 paused Gemini: the second call went straight to Spark
    assert not g.gemini_available
    assert all(body["reasoning_effort"] == "minimal" for body in seen)
    await g.aclose()


async def test_tts_returns_none_while_gemini_is_paused():
    g, gemini = _gateway(None, [_Status(402)])
    with pytest.raises(GeminiError):
        await g.text(model="m", input="hi")
    assert await g.tts("hello there") is None  # the Table falls back to browser speech
    assert gemini.calls == 1


def test_gateway_without_claude_key_does_not_use_claude():
    g, _ = _gateway(Claude(api_key=""), [])
    assert not g.claude_available


# --- fast mode for the latency-critical jobs (crumb draft, hold classifier) -------------------------------


def _plain_body(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "output_format"}


@pytest.mark.parametrize("job", ["crumb", "hold"])
async def test_crumb_and_hold_jobs_ask_for_fast_mode(job: str):
    claude, fake = _claude([_message(parsed=Small(name="a", count=1))])
    await claude.json(system="s", user="u", schema=Small, max_tokens=100, job=job)
    _, body = fake.calls[0]
    assert body["speed"] == "fast"
    assert body["betas"] == ["server-side-fallback-2026-07-01", "fast-mode-2026-02-01"]
    assert body["fallbacks"] == "default" and body["model"] == "claude-opus-5-5"


@pytest.mark.parametrize("job", [None, "energy", "profile", "overlaps", "trail"])
async def test_other_jobs_are_unchanged(job: str | None):
    claude, fake = _claude([_message(parsed=Small(name="a", count=1)), _message(parsed=Small(name="a", count=1))])
    await claude.json(system="s", user="u", schema=Small, max_tokens=100, job=job)
    await claude.json(system="s", user="u", schema=Small, max_tokens=100)
    assert _plain_body(fake.calls[0][1]) == _plain_body(fake.calls[1][1])
    assert "speed" not in fake.calls[0][1] and fake.calls[0][1]["betas"] == ["server-side-fallback-2026-07-01"]


async def test_a_fast_mode_429_retries_once_at_standard_speed_and_keeps_fast_mode():
    claude, fake = _claude([_Status(429), _message(parsed=Small(name="a", count=1)), _message(parsed=Small(name="b", count=2))])
    assert await claude.json(system="s", user="u", schema=Small, max_tokens=100, job="crumb") == Small(name="a", count=1)
    assert [("speed" in body) for _, body in fake.calls] == [True, False]
    assert not claude._fast_off
    await claude.json(system="s", user="u", schema=Small, max_tokens=100, job="crumb")
    assert fake.calls[-1][1]["speed"] == "fast"


class _SpeedRefused(_Status):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.args = (f"Error code: {status_code} - speed 'fast' is not available for this organization",)


@pytest.mark.parametrize("status", [400, 403])
async def test_a_fast_mode_refusal_retries_without_it_and_turns_it_off(status: int):
    claude, fake = _claude([_SpeedRefused(status), _message(parsed=Small(name="a", count=1)),
                            _message(parsed=Small(name="b", count=2))])
    assert await claude.json(system="s", user="u", schema=Small, max_tokens=100, job="hold") == Small(name="a", count=1)
    assert claude._fast_off
    await claude.json(system="s", user="u", schema=Small, max_tokens=100, job="hold")
    assert [("speed" in body) for _, body in fake.calls] == [True, False, False]


async def test_an_unrelated_400_is_not_retried():
    claude, fake = _claude([_Status(400)])
    with pytest.raises(ClaudeError):
        await claude.json(system="s", user="u", schema=Small, max_tokens=100, job="crumb")
    assert len(fake.calls) == 1 and not claude._fast_off


async def test_empty_fast_jobs_keeps_every_body_unchanged(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "CLAUDE_FAST_JOBS", frozenset())
    claude, fake = _claude([_message(parsed=Small(name="a", count=1)), _message(parsed=Small(name="a", count=1))])
    await claude.json(system="s", user="u", schema=Small, max_tokens=100, job="crumb")
    await claude.json(system="s", user="u", schema=Small, max_tokens=100)
    assert _plain_body(fake.calls[0][1]) == _plain_body(fake.calls[1][1])


async def test_the_gateway_passes_the_job_to_claude():
    claude, fake = _claude([_message(parsed=Small(name="a", count=1))])
    g, _ = _gateway(claude, [])
    await g.json(model=config.GEMINI_MODEL, system="s", payload="p", schema=Small, job="crumb")
    assert fake.calls[0][1]["speed"] == "fast"


async def test_crumb_model_override_applies_to_the_crumb_draft_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "CLAUDE_CRUMB_MODEL", "claude-sonnet-5-5")
    claude, fake = _claude([_message(parsed=Small(name="a", count=1)), _message(parsed=Small(name="a", count=1))])
    await claude.json(system="s", user="u", schema=Small, max_tokens=100, job="crumb")
    await claude.json(system="s", user="u", schema=Small, max_tokens=100, job="hold")
    assert fake.calls[0][1]["model"] == "claude-sonnet-5-5" and "speed" not in fake.calls[0][1]  # no fast mode there
    assert fake.calls[1][1]["model"] == "claude-opus-5-5" and fake.calls[1][1]["speed"] == "fast"
