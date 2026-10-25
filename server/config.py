"""Engine parameters (PRD 8.12) + environment loading.

Engine parameters live on a mutable `Params` object so the X-ray panel can tune them live (PRD 8.12, P1).
Module-level constants are kept for direct imports and as the defaults.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- Environment -------------------------------------------------------------
MODEL_API_KEY = os.environ.get("MODEL_API_KEY", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
ASR_MODEL = os.environ.get("ASR_MODEL", "muse-voice-transcribe-1.0")
EVENT_LOCATION = os.environ.get("EVENT_LOCATION", "Austin, Texas (HackTX at UT Austin)")
DEV_TOOLS = os.environ.get("DEV_TOOLS", "true").lower() in ("1", "true", "yes")

# Mock mode: a service without a key returns deterministic canned data (see BUILD_LOG.md).
MUSE_MOCK = not MODEL_API_KEY
GEMINI_MOCK = not GEMINI_API_KEY
CLAUDE_ENABLED = bool(ANTHROPIC_API_KEY)
# Text AI (profiles, overlaps, crumbs, classifiers, trail) is mocked only when no text provider has a key.
AI_MOCK = not (GEMINI_API_KEY or ANTHROPIC_API_KEY)
AI_PROVIDER = "claude" if ANTHROPIC_API_KEY else "gemini" if GEMINI_API_KEY else "mock"  # first text provider

# --- Models (PRD 9.1) --------------------------------------------------------
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_LITE_MODEL = "gemini-3.5-flash-lite"
GEMINI_TTS_MODEL = "gemini-3.8-flash-tts"
GEMINI_TTS_FALLBACK_MODEL = "gemini-3.8-flash-lite-tts"
TTS_VOICE = os.environ.get("TTS_VOICE", "Kore")
TTS_STYLE = "warm, playful, curious, like a friendly host speaking softly at a small table"
MUSE_SPARK_MODEL = "muse-spark-1.3"
# Claude is the first text provider when ANTHROPIC_API_KEY is set (Gemini, then Muse Spark, are fallbacks).
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", "low")  # live path: fast, short crumbs
# Latency-critical jobs (the crumb draft and the hold classifier) ask for Opus fast mode (same model, faster
# output); every other job stays at standard speed. Empty disables it. Falls back to standard on refusal.
CLAUDE_FAST_JOBS = frozenset(j.strip() for j in os.environ.get("CLAUDE_FAST_JOBS", "crumb,hold").split(",") if j.strip())
# An override for the crumb draft model only (the user's choice; the default is CLAUDE_MODEL).
CLAUDE_CRUMB_MODEL = os.environ.get("CLAUDE_CRUMB_MODEL", "").strip() or CLAUDE_MODEL
# Crumb voice: ElevenLabs first when ELEVENLABS_API_KEY is set, then Gemini TTS, else the Table's browser voice.
ELEVENLABS_API_BASE = "https://api.elevenlabs.io"
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()  # empty = the built-in default voice
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "").strip()  # empty = the built-in default model
TTS_PROVIDER = "elevenlabs" if ELEVENLABS_API_KEY else "gemini" if GEMINI_API_KEY else "browser"
MUSE_REALTIME_URL = "wss://api.meta.ai/v1/asr/realtime?sessionId={sid}"
MUSE_TRANSCRIBE_URL = "https://api.meta.ai/v1/asr/transcribe"
META_API_BASE = "https://api.meta.ai/v1"

# --- Engine defaults (PRD 8.12) ------------------------------------------------
TICK_HZ = 10
N_RECENT_TURNS = 6
BASELINE_TURNS = 10
BASELINE_SECONDS = 180
DEFAULT_GAP_S = 1.2
GAP_CLAMP = (0.4, 2.5)
T_NORMAL_S = 1.5  # normal pause: never intervene below this (the orb starts to glow here)
T_LONG_S = 3.0  # unusually long pause: steer (or switch if stale)
T_EXC_S = 4.5  # exceptionally long pause: context switch
UNANSWERED_SWITCH_S = 6.0  # follow-up switch after a crumb nobody answered (at least T_EXC_S)
ADAPTIVE_THRESHOLDS = False  # Section 8.4 optional tuning
MODE_MULTIPLIER = {"social": 1.0, "networking": 1.0, "speed_dating": 1.0}
STALE_SWITCH = 0.60
STALENESS_WEIGHTS = {"L": 0.35, "R": 0.25, "G": 0.15, "Q": 0.10, "E": 0.15}
SHORT_TURN_WORDS = 3
ENERGY_EVERY_N_TURNS = 4
HOLD_MAX_S = {"show": 60, "think": 20, "external": 90}
HOLD_TAIL_WORDS = 8
WARMUP_S = 20
MIN_GAP_BETWEEN_CRUMBS_S = 30
MIN_HUMAN_TURNS_BETWEEN = 2
EXC_SWITCH_MIN_GAP_S = 15
MAX_UNANSWERED_CRUMBS = 2
MAX_CRUMBS_PER_10_MIN = 6
CRUMB_MAX_WORDS = 28
PREFETCH_DEBOUNCE_S = 1.5
PREFETCH_REFRESH_S = 30
CACHE_MAX_AGE_S = 90
TTS_TAIL_MS = 300
BARGE_IN_FADE_MS = 250
SESSION_TTL_S = 7200
RAMP_MINUTES = {"social": 5, "networking": 6, "speed_dating": 2}
MAX_DEPTH = {"social": 4, "networking": 3, "speed_dating": 3}

# --- Non-engine defaults -------------------------------------------------------
CALIBRATION_TIMEOUT_S = 15  # PRD 6.5 fallback
ROUND_TIMER_S = {"social": None, "networking": None, "speed_dating": 360}  # PRD 4.2 (networking optional 600)
ROUND_WARNING_S = 60
WILDCARD_MIN_GAP_S = 300  # F22
WILDCARD_MIN_SILENCE_S = 3.0
ENGINE_BROADCAST_HZ = 4  # PRD 12.1 `engine` event rate
MANUAL_HOLD_S = 60

ALLOWED_FORMATS = {
    "social": ["question", "fun_fact", "would_you_rather", "callback", "collab"],
    "networking": ["question", "fun_fact", "callback", "collab"],
    "speed_dating": ["question", "would_you_rather", "fun_fact", "callback"],
}
TONE = {
    "social": "warm, curious",
    "networking": "professional, generous, specific",
    "speed_dating": "playful, light, flirty at most; never sexual",
}


RENAMED_PARAMS = {"VAD_UNCONFIRMED_S": "VAD_DEFER_S"}  # old X-ray keys still tune their replacement


@dataclass
class Params:
    """Live-tunable engine parameters (one instance per session; X-ray can PATCH them)."""

    T_NORMAL_S: float = T_NORMAL_S
    T_LONG_S: float = T_LONG_S
    T_EXC_S: float = T_EXC_S
    ADAPTIVE_THRESHOLDS: bool = ADAPTIVE_THRESHOLDS
    STALE_SWITCH: float = STALE_SWITCH
    N_RECENT_TURNS: int = N_RECENT_TURNS
    BASELINE_TURNS: int = BASELINE_TURNS
    BASELINE_SECONDS: float = BASELINE_SECONDS
    DEFAULT_GAP_S: float = DEFAULT_GAP_S
    SHORT_TURN_WORDS: int = SHORT_TURN_WORDS
    ENERGY_EVERY_N_TURNS: int = ENERGY_EVERY_N_TURNS
    HOLD_TAIL_WORDS: int = HOLD_TAIL_WORDS
    WARMUP_S: float = WARMUP_S
    MIN_GAP_BETWEEN_CRUMBS_S: float = MIN_GAP_BETWEEN_CRUMBS_S
    MIN_HUMAN_TURNS_BETWEEN: int = MIN_HUMAN_TURNS_BETWEEN
    EXC_SWITCH_MIN_GAP_S: float = EXC_SWITCH_MIN_GAP_S
    MAX_UNANSWERED_CRUMBS: int = MAX_UNANSWERED_CRUMBS
    MAX_CRUMBS_PER_10_MIN: int = MAX_CRUMBS_PER_10_MIN
    TTS_TAIL_MS: int = TTS_TAIL_MS
    WILDCARD_ENABLED: bool = False  # F22, off by default
    UNANSWERED_SWITCH_S: float = UNANSWERED_SWITCH_S
    # Background noise: a local VAD onset only defers a crumb (it never restarts the pause): for this many
    # seconds, or up to VAD_NEAR_MAX_S while it sounds like someone at the table (covers Muse lag).
    VAD_DEFER_S: float = 1.5
    VAD_NEAR_MAX_S: float = 4.0
    # Bystanders: each Muse turn is judged at speechEnd from its label, words and the Table VAD over it.
    # Only a seat turn restarts the pause timer and counts as conversation; False treats every turn as a seat.
    BYSTANDER_GATING: bool = True
    NEAR_ACTIVE_FRAC: float = 0.35  # VAD coverage of a near voice
    FAR_ACTIVE_FRAC: float = 0.15  # below this coverage a voice is far
    NEAR_MARGIN_DB: float = 8.0  # near: level at least the seated level minus this
    FAR_MARGIN_DB: float = 12.0  # far: level below the seated level minus this
    FILLER_MAX_WORDS: int = 1  # a seat-label "uh" the Table mic did not hear as near is background
    DRIFT_MIN_WORDS: int = 1  # an unmapped label counts as a seat only when near, with this many words
    OPEN_TURN_MIN_S: float = 2.5  # an open turn the Table mic does not hear stops blocking after this long
    OPEN_TURN_RELEASE_S: float = 2.0  # ... once there was no near voice at the table for this long
    BACKGROUND_SETTLE_S: float = 0.4  # after a background turn closes (Muse may split one speaker's turn)
    ENDPOINT_BACKDATE_MAX_S: float = 1.2  # the pause starts at the end of voice, at most this before speechEnd
    # Self-hearing by text: a Muse turn during or just after bot speech whose words overlap the line being
    # spoken by at least this fraction is the bot's own echo (no barge-in, never a human turn).
    ECHO_TEXT_OVERLAP: float = 0.5
    # Usage: an ambiguous fragment (or a vetoed hold phrase) goes to the hold classifier only once the
    # silence after it reaches this many seconds (PRD 8.6: "followed by silence"); a reply within it
    # makes the call pointless. While that answer is out, automatic crumbs wait for it up to this many
    # seconds past the long threshold, so the classifier keeps the same window as when it was asked at
    # the commit. A lexical hold hit is classified at once, as before.
    HOLD_CHECK_SILENCE_S: float = 0.8
    HOLD_MAX_S: dict = field(default_factory=lambda: dict(HOLD_MAX_S))
    STALENESS_WEIGHTS: dict = field(default_factory=lambda: dict(STALENESS_WEIGHTS))

    def to_dict(self) -> dict:
        return asdict(self)

    def update(self, changes: dict) -> None:
        """Apply a partial update from the X-ray panel, coercing to each field's type."""
        names = {f.name: f for f in fields(self)}
        for key, value in changes.items():
            key = RENAMED_PARAMS.get(key, key)
            if key not in names:
                continue
            current = getattr(self, key)
            if isinstance(current, bool):
                setattr(self, key, bool(value))
            elif isinstance(current, int):
                setattr(self, key, int(value))
            elif isinstance(current, float):
                setattr(self, key, float(value))
            elif isinstance(current, dict) and isinstance(value, dict):
                current.update(value)
