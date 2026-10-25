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
T_NORMAL_S = 3.0  # normal pause: never intervene below this
T_LONG_S = 5.0  # unusually long pause: steer (or switch if stale)
T_EXC_S = 6.5  # exceptionally long pause: context switch (tune within 6.0 to 7.0)
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
    HOLD_MAX_S: dict = field(default_factory=lambda: dict(HOLD_MAX_S))
    STALENESS_WEIGHTS: dict = field(default_factory=lambda: dict(STALENESS_WEIGHTS))

    def to_dict(self) -> dict:
        return asdict(self)

    def update(self, changes: dict) -> None:
        """Apply a partial update from the X-ray panel, coercing to each field's type."""
        names = {f.name: f for f in fields(self)}
        for key, value in changes.items():
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
