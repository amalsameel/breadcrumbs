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
# Fast mode (research preview, Claude API only): the same model at up to 2.5x output speed, at premium
# pricing. Asked only for the jobs in config.CLAUDE_FAST_JOBS (the crumb draft and the hold classifier);
# a 429 retries once at standard speed, a 400/403 about speed turns it off for the process.
FAST_BETA = "fast-mode-2026-02-01"
FAST_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8")
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


def _about_fast_mode(exc: Exception) -> bool:
    text = str(exc).lower()
    return "speed" in text or "fast" in text


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
        self._fast_off = False  # the account or model refused fast mode once: standard speed from then on

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

    async def json(self, *, system: str, user: str, schema: type[T], max_tokens: int, effort: str | None = None,
                   job: str | None = None) -> T:
        """Structured output validated into `schema` (SDK `parse` + `output_format`). `job` names the caller
        ("crumb", "hold", ...): latency-critical jobs ask for fast mode (config.CLAUDE_FAST_JOBS)."""
        started = time.monotonic()
        body = self._common(system=system, user=user, max_tokens=max_tokens)
        if job == "crumb" and config.CLAUDE_CRUMB_MODEL != config.CLAUDE_MODEL:
            body["model"] = config.CLAUDE_CRUMB_MODEL  # the user's override for the crumb draft only
        body["output_format"] = schema
        body["output_config"] = {"effort": effort or self.effort}
        try:
            message = await self._parse(body, fast=self._fast_for(job, body["model"]))
        except Exception as exc:  # noqa: BLE001 - classified by the gateway, which falls back
            raise ClaudeError(f"json call failed: {type(exc).__name__}") from exc
        _check_stop(message)
        parsed = getattr(message, "parsed_output", None)
        if parsed is None:
            raise ClaudeError("no parsed output")
        speed = getattr(getattr(message, "usage", None), "speed", None)
        log.info("claude json %s ok in %.0f ms (speed %s, req %s)", schema.__name__, (time.monotonic() - started) * 1000,
                 speed or "standard", message._request_id)
        return parsed

    def _fast_for(self, job: str | None, model: str) -> bool:
        return job is not None and job in config.CLAUDE_FAST_JOBS and not self._fast_off and model in FAST_MODELS

    async def _parse(self, body: dict[str, Any], *, fast: bool) -> Any:
        """`messages.parse`, in fast mode when asked; a fast-mode refusal falls back to standard speed once."""
        if fast:
            try:
                return await self._messages.parse(**{**body, "betas": [*body["betas"], FAST_BETA]}, speed="fast")
            except Exception as exc:  # noqa: BLE001
                status = getattr(exc, "status_code", None)
                if status == 429:  # fast mode has its own rate limit: this call goes at standard speed
                    log.info("claude fast mode rate limited (429); retrying at standard speed")
                elif status in (400, 403) and _about_fast_mode(exc):
                    self._fast_off = True
                    log.warning("claude fast mode unavailable (%s %s); standard speed from now on", status, type(exc).__name__)
                else:
                    raise
        return await self._messages.parse(**body)

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
