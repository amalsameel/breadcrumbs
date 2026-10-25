"""Crumb voice (PRD 9.8): Gemini TTS to WAV bytes, or None so the Table falls back to speechSynthesis."""
from __future__ import annotations

import logging

from ai.gemini import Gemini, get_gemini
from ai.safety import clean_text

log = logging.getLogger("breadcrumbs.ai.tts")


async def synthesize(text: str, gemini: Gemini | None = None) -> bytes | None:
    """WAV bytes (24 kHz mono 16-bit) or None (mock mode, empty text or TTS failure)."""
    g = gemini or get_gemini()
    spoken = clean_text(text)
    if g.mock or not spoken:
        return None
    try:
        return await g.tts(spoken)
    except Exception as exc:  # noqa: BLE001 - voice is optional; never break delivery
        log.warning("tts failed (%s)", type(exc).__name__)
        return None
