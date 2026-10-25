"""Crumb voice (PRD 9.8): text to WAV bytes, or None so the Table falls back to its browser voice.

Provider chain: ElevenLabs (when ELEVENLABS_API_KEY is set) -> Gemini TTS (through the AI gateway) ->
None. `fast=True` (the default) is the live path: cache misses, Drop a crumb, the opener and calibration
lines. Prefetched crumbs pass `fast=False` so ElevenLabs can use its most natural model while the latency
is hidden. Never log the text: only providers and error classes.

`budget_s` is the caller's whole time budget (the same number it gives asyncio.wait_for). The chain
spends it so that every provider still gets a turn: when Gemini can speak and the budget is long enough
for it, part of the budget is kept back for Gemini, and the chain returns None just before the budget
ends instead of being cancelled mid-provider.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict

from ai.elevenlabs import ElevenLabs, get_elevenlabs
from ai.gemini import Gemini, get_gemini
from ai.safety import clean_text

log = logging.getLogger("breadcrumbs.ai.tts")

MAX_SPOKEN_CHARS = 1_000  # crumbs are about 25 words; this only guards against a runaway string

# Budgets when the caller passes none: the runner's system lines use a 5 s wait_for (SAY_TIMEOUT_S), and
# prefetch uses 12 s (crumbs.PREFETCH_TTS_BUDGET_S).
DEFAULT_FAST_BUDGET_S = 5.0
DEFAULT_NATURAL_BUDGET_S = 12.0
# Kept back for Gemini TTS when it is usable. Below GEMINI_MIN_BUDGET_S (the 1 s miss budget) Gemini could
# not answer in time anyway, so ElevenLabs gets the whole budget.
GEMINI_RESERVE_SHARE = 0.3
GEMINI_MIN_BUDGET_S = 2.5
DEADLINE_MARGIN_S = 0.05  # finish just inside the caller's wait_for so its timeout never fires first

_BRACKETED_RE = re.compile(r"\[[^\]]*\]|<[^>]*>")  # [audio tags] would be performed, <markup> read out
_MARKUP_RE = re.compile(r"[*_`#~|]+")
_URL_RE = re.compile(r"https?://\S+")


def speech_text(text: str) -> str:
    """The line as it should be spoken: no markup, tags, links, emojis or em dashes; one line of prose."""
    spoken = _URL_RE.sub(" ", text or "")
    spoken = _BRACKETED_RE.sub(" ", spoken)
    spoken = _MARKUP_RE.sub(" ", spoken)
    spoken = spoken.replace("&", " and ")
    spoken = clean_text(spoken)
    if len(spoken) > MAX_SPOKEN_CHARS:
        cut = spoken[:MAX_SPOKEN_CHARS]
        spoken = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return spoken


async def synthesize(
    text: str,
    gemini: Gemini | None = None,
    *,
    fast: bool = True,
    elevenlabs: ElevenLabs | None = None,
    budget_s: float | None = None,
) -> bytes | None:
    """WAV bytes (24 kHz mono 16-bit) or None (no voice provider, empty text, out of time or every
    provider failed). `budget_s` defaults to DEFAULT_FAST_BUDGET_S or DEFAULT_NATURAL_BUDGET_S."""
    spoken = speech_text(text)
    if not spoken:
        return None
    budget = (DEFAULT_FAST_BUDGET_S if fast else DEFAULT_NATURAL_BUDGET_S) if budget_s is None else budget_s
    deadline = time.monotonic() + max(0.0, budget - DEADLINE_MARGIN_S)
    g = gemini or get_gemini()
    # Gemini only counts when it can actually speak: a gateway paused after a 401/402 (or with no Gemini
    # key) would otherwise keep 30% of every budget back for a call that is never made.
    gemini_usable = not g.mock and getattr(g, "gemini_available", True)
    voice = elevenlabs or get_elevenlabs()
    if voice.available:
        reserve = budget * GEMINI_RESERVE_SHARE if gemini_usable and budget >= GEMINI_MIN_BUDGET_S else 0.0
        try:
            wav = await voice.synthesize(spoken, fast=fast, budget_s=deadline - time.monotonic() - reserve)
            if wav:
                return wav
        except Exception as exc:  # noqa: BLE001 - voice is optional; never break delivery
            log.warning("elevenlabs tts failed (%s); trying Gemini", type(exc).__name__)
    if not gemini_usable:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        log.info("tts out of time before Gemini")
        return None
    try:
        return await asyncio.wait_for(g.tts(spoken), timeout=remaining)
    except Exception as exc:  # noqa: BLE001 - voice is optional; never break delivery (incl. TimeoutError)
        log.warning("tts failed (%s)", type(exc).__name__)
        return None


# --- System line cache (usage) -------------------------------------------------------------------------
# Calibration and re-sync prompts are the same words every session. A line with no personal data is
# voiced once per process (SYSTEM_LINES); a line with a name in it is voiced once per session (the
# runner's own AudioCache, dropped when the session ends or is wiped). Keys are the spoken text plus the
# path, never logged.

SYSTEM_LINE_CACHE_ITEMS = 32
SESSION_LINE_CACHE_ITEMS = 8


class AudioCache:
    """A small LRU of voiced lines: (spoken text, fast) -> WAV bytes."""

    def __init__(self, max_items: int) -> None:
        self.max_items = max_items
        self._items: OrderedDict[tuple[str, bool], bytes] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: tuple[str, bool]) -> bytes | None:
        wav = self._items.get(key)
        if wav is not None:
            self._items.move_to_end(key)
        return wav

    def put(self, key: tuple[str, bool], wav: bytes) -> None:
        self._items[key] = wav
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


SYSTEM_LINES = AudioCache(SYSTEM_LINE_CACHE_ITEMS)


async def synthesize_cached(
    text: str,
    cache: AudioCache,
    gemini: Gemini | None = None,
    *,
    fast: bool = True,
    elevenlabs: ElevenLabs | None = None,
    budget_s: float | None = None,
) -> bytes | None:
    """`synthesize`, but a line already voiced into `cache` is served from it (no provider call).
    Only real audio is kept: a failed line is tried again next time."""
    key = (speech_text(text), fast)
    if not key[0]:
        return None
    wav = cache.get(key)
    if wav is not None:
        return wav
    wav = await synthesize(text, gemini, fast=fast, elevenlabs=elevenlabs, budget_s=budget_s)
    if wav:
        cache.put(key, wav)
    return wav
