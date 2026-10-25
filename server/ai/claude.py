"""Async Claude client (Anthropic Messages API): structured JSON, plain text, and web-search grounded facts.

Used by the gateway in ai/gemini.py as the first text provider whenever ANTHROPIC_API_KEY is set.
Claude Opus 5.5 always thinks (thinking cannot be disabled), so speed comes from `effort` and the
token cap carries headroom for that thinking. Never log prompt or transcript text: only models,
timings, request ids and error classes.
"""
from __future__ import annotations

import logging
import time
from typing import Any, TypeVar

from pydantic import BaseModel

import config

log = logging.getLogger("breadcrumbs.ai.claude")

T = TypeVar("T", bound=BaseModel)

# Thinking tokens count against max_tokens; callers size their caps for the visible answer only.
THINKING_HEADROOM_TOKENS = 4000
# Server-side refusal fallback (the scalar "default" form routes by refusal category).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 2}


class ClaudeError(RuntimeError):
    """Raised when Claude could not produce a usable result (the gateway then tries the next provider)."""


def _text_of(message: Any) -> str:
    return "".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()


def _first_citation(message: Any) -> tuple[str | None, str | None]:
    """First web_search_result_location citation (url, title) on any text block."""
    for block in message.content:
        if getattr(block, "type", None) != "text":
            continue
        for cite in getattr(block, "citations", None) or []:
            if getattr(cite, "type", None) == "web_search_result_location" and getattr(cite, "url", None):
                return cite.url, getattr(cite, "title", None)
    return None, None


def _check_stop(message: Any) -> None:
    if message.stop_reason == "refusal":
        category = getattr(getattr(message, "stop_details", None), "category", None)
        raise ClaudeError(f"refused (category={category})")
    if message.stop_reason == "max_tokens":
        raise ClaudeError("hit max_tokens before finishing")


class Claude:
    """Thin async wrapper over `client.beta.messages`. One instance per process (owned by the gateway)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        client: Any = None,
        timeout_s: float = 20.0,
        max_retries: int = 1,
    ) -> None:
        self._api_key = api_key if api_key is not None else config.ANTHROPIC_API_KEY
        self.model = model or config.CLAUDE_MODEL
        self.effort = effort or config.CLAUDE_EFFORT
        self._client = client
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    @property
    def available(self) -> bool:
        return bool(self._api_key) or self._client is not None

    @property
    def _messages(self) -> Any:
        if self._client is None:
            import anthropic  # imported lazily so mock mode never needs the SDK configured

            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key, timeout=self.timeout_s, max_retries=self.max_retries
            )
        return self._client.beta.messages

    def _common(self, *, system: str | None, user: str, max_tokens: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens + THINKING_HEADROOM_TOKENS,
            "messages": [{"role": "user", "content": user}],
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if system:
            body["system"] = system
        return body

    async def json(self, *, system: str, user: str, schema: type[T], max_tokens: int, effort: str | None = None) -> T:
        """Structured output validated into `schema` (SDK `parse` + `output_format`)."""
        started = time.monotonic()
        try:
            message = await self._messages.parse(
                **self._common(system=system, user=user, max_tokens=max_tokens),
                output_format=schema,
                output_config={"effort": effort or self.effort},
            )
        except Exception as exc:  # noqa: BLE001 - classified by the gateway, which falls back
            raise ClaudeError(f"json call failed: {type(exc).__name__}") from exc
        _check_stop(message)
        parsed = getattr(message, "parsed_output", None)
        if parsed is None:
            raise ClaudeError("no parsed output")
        log.info("claude json %s ok in %.0f ms (req %s)", schema.__name__, (time.monotonic() - started) * 1000, message._request_id)
        return parsed

    async def text(self, *, system: str | None, user: str, max_tokens: int, effort: str | None = None) -> str:
        try:
            message = await self._messages.create(
                **self._common(system=system, user=user, max_tokens=max_tokens),
                output_config={"effort": effort or self.effort},
            )
        except Exception as exc:  # noqa: BLE001
            raise ClaudeError(f"text call failed: {type(exc).__name__}") from exc
        _check_stop(message)
        text = _text_of(message)
        if not text:
            raise ClaudeError("empty text")
        return text

    async def grounded(self, prompt: str) -> tuple[str, str | None, str | None]:
        """One web-searched fact: (text, citation_url, citation_title). Raises ClaudeError on failure."""
        started = time.monotonic()
        try:
            message = await self._messages.create(
                **self._common(system=None, user=prompt, max_tokens=400),
                tools=[WEB_SEARCH_TOOL],
                output_config={"effort": "low"},
            )
        except Exception as exc:  # noqa: BLE001
            raise ClaudeError(f"grounded call failed: {type(exc).__name__}") from exc
        if message.stop_reason == "pause_turn":
            raise ClaudeError("web search paused before an answer")
        _check_stop(message)
        url, title = _first_citation(message)
        log.info("claude grounded ok in %.0f ms (cited=%s)", (time.monotonic() - started) * 1000, bool(url))
        return _text_of(message), url, title
