"""Shared data models (PRD Section 12). Build-first contract: every workstream imports from here.

Mirrored in web/src/lib/types.ts. Pydantic models double as Gemini JSON schemas.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["social", "networking", "speed_dating"]
Seat = Literal[1, 2]
Phase = Literal["lobby", "profiles", "calibrating", "live", "ended"]
CrumbKind = Literal["opener", "steer", "switch", "manual", "wildcard"]
CrumbFormat = Literal["question", "fun_fact", "would_you_rather", "callback", "collab"]
CrumbSource = Literal["overlap", "profile_seat1", "profile_seat2", "conversation", "outside"]
HoldKind = Literal["show", "think", "external"]
Band = Literal["normal", "warming", "long", "exceptional"]


class Consent(BaseModel):
    typed_name: str
    agreed: bool
    at: float


class ProfileItem(BaseModel):
    id: str  # e.g. "s1_i3"
    kind: Literal["interest", "story", "goal", "fun_fact", "value", "preference", "offer", "seeking"]
    text: str = Field(max_length=100)
    hook: str = Field(max_length=140)
    depth: int = Field(ge=1, le=4)


class Profile(BaseModel):
    seat: Seat
    display_name: str
    items: list[ProfileItem]
    vibe: str = Field(default="", max_length=80)  # e.g. "curious, dry humor"
    off_limits: list[str] = []
    removed_item_ids: list[str] = []


class Overlap(BaseModel):
    kind: Literal["direct", "adjacent", "contrast"]
    item_ids_seat1: list[str]
    item_ids_seat2: list[str]
    summary: str
    opener: str


class OverlapSet(BaseModel):
    overlaps: list[Overlap]


class Crumb(BaseModel):
    id: str = ""
    kind: CrumbKind
    format: CrumbFormat
    text: str  # <= 28 words, spoken
    addressed_to: Literal["both", "seat1", "seat2"]
    source: CrumbSource
    source_item_ids: list[str] = []
    topic: str
    depth: int = Field(ge=1, le=4)
    why: str  # one sentence for X-ray
    citation_url: str | None = None
    citation_title: str | None = None


class HoldDecision(BaseModel):
    hold: bool
    kind: Literal["show", "think", "external", "none"]
    expected_seconds: int
    confidence: float


class EnergyTag(BaseModel):
    topic: str
    energy: Literal["high", "medium", "low"]
    winding_down: bool


class TrailItem(BaseModel):
    for_seat: Seat
    text: str


class Trail(BaseModel):
    items: list[TrailItem]


# ---------------------------------------------------------------------------
# Engine-side models (PRD 8.2) and request bodies (PRD 13)
# ---------------------------------------------------------------------------


class Turn(BaseModel):
    turn_id: int
    seat: Seat | None = None  # None until speaker label is mapped
    label: str | None = None  # Muse diarization label, e.g. "A"
    text: str = ""
    words: int = 0
    is_question: bool = False  # ends with "?" or starts with a question word
    is_backchannel: bool = False  # "yeah", "totally", "haha", "for sure", "mm"
    started_at: float = 0.0  # server monotonic seconds
    ended_at: float = 0.0
    gap_before: float | None = None  # silence between previous turn end and this start
    dropped_as_bot: bool = False  # self-hearing guard (PRD 8.11)

    @property
    def is_short(self) -> bool:
        return self.words <= 3 or self.is_backchannel


class CreateSessionBody(BaseModel):
    mode: Mode = "social"
    quick: bool = False  # Quick profile: only Q1 (PRD 6.3 demo mode)


class ConsentBody(BaseModel):
    typed_name: str = Field(min_length=1, max_length=60)
    agreed: bool


class ProfilePatchBody(BaseModel):
    removed_item_ids: list[str] = []
    off_limits: list[str] = []


class TrailOptInBody(BaseModel):
    share: bool
    contact: str | None = Field(default=None, max_length=200)


class InterviewQuestion(BaseModel):
    id: str
    text: str


class TrailResponse(BaseModel):
    matched: bool
    partner_name: str | None = None
    partner_contact: str | None = None
    trail: list[TrailItem] = []
