"""Grounded outside facts (PRD 9.5, F15). No citation means no fact: the crumb becomes a question."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ai.gemini import Gemini, get_gemini
from ai.prompts import FACT_PROMPT
from ai.safety import first_sentence, is_sensitive, is_unsafe

log = logging.getLogger("breadcrumbs.ai.facts")

MAX_FACT_CHARS = 220


@dataclass
class Fact:
    text: str
    url: str
    title: str


async def get_fact(topic: str | None, location: str, gemini: Gemini | None = None) -> Fact | None:
    """One light, cited fun fact about `topic` (or the event location). None when not grounded."""
    g = gemini or get_gemini()
    if g.mock:
        return None
    subject = (topic or "").strip() or location
    text, url, title = await g.grounded(FACT_PROMPT.format(subject=subject))
    if not url:
        log.info("grounded fact had no citation; dropping it")
        return None
    sentence = first_sentence(text, MAX_FACT_CHARS)
    if not sentence or is_unsafe(sentence) or is_sensitive(sentence):
        return None
    return Fact(text=sentence, url=url, title=(title or url)[:120])
