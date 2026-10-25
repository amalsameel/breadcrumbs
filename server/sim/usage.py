"""Usage measurement harness (dev tool): what one scripted 10-minute social session costs, and what is wasted.

Drives a realistic session through the REAL code paths: `SessionRunner` (tick loop, effect executor,
prefetch schedule), `Engine`, `CrumbService`, `ai.classifiers`, `ai.tts.synthesize`, the `ElevenLabs`
client and the `Gemini` gateway with its `Claude` client. Only the network edge is fake:

  * Claude: a fake `anthropic` client (`beta.messages.parse/create`) behind the real `Claude` class, with
    realistic latencies (crumb JSON ~3.7 s, classifiers ~2.5 s, profiles ~7.5 s, web search ~9 s).
  * ElevenLabs: the real `ElevenLabs` class over an `httpx.MockTransport` that records the model and the
    billed characters of every request (v4 / multilingual v2 = 1 credit per char, flash v2.5 = 0.5).
  * Gemini: no key, so the gateway treats it as unavailable (the live server has it paused after a 402).
  * Muse: Muse-shaped turns go in through the runner's dev-turn path; the Table is a fake connection that
    plays every line (chime 0.74 s, then the audio) and reports `tts_state` like web/src/lib/useTable.ts.

Time is virtual: an event loop whose clock jumps to the next timer, so 10 minutes run in a few seconds
and every `asyncio.sleep` / `wait_for` budget behaves as on the wall clock. No real keys are read and no
request leaves the process. Nothing prints transcript or crumb text: only counts, ids and timings.

Usage (from server/):
    .venv/bin/python -m sim.usage            # table on stdout
    .venv/bin/python -m sim.usage --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextvars
import json
import logging
import os
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Never let this tool see a real key: blank them before config is imported (it loads .env on import).
for _key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "MODEL_API_KEY"):
    os.environ[_key] = ""

import httpx  # noqa: E402

import config  # noqa: E402

config.ANTHROPIC_API_KEY = config.GEMINI_API_KEY = config.ELEVENLABS_API_KEY = config.MODEL_API_KEY = ""

import ai.elevenlabs as elevenlabs_mod  # noqa: E402
import ai.gemini as gemini_mod  # noqa: E402
import ai.tts as tts_mod  # noqa: E402
from ai.claude import Claude  # noqa: E402
from ai.classifiers import mock_energy, mock_hold  # noqa: E402
from ai.crumbs import CrumbDraft  # noqa: E402
from ai.elevenlabs import ElevenLabs  # noqa: E402
from ai.gemini import Gemini  # noqa: E402
from ai.profiles import (  # noqa: E402
    OverlapDraft,
    OverlapDraftSet,
    ProfileDraft,
    allowed_items,
    extract_profile,
    find_overlaps,
    mock_overlaps,
    mock_profile_items,
)
from ai.trail import TrailDraft, generate_trail, mock_trail  # noqa: E402
from engine.hold import hold_vetoed, is_ambiguous_fragment, match_hold  # noqa: E402
from engine.runner import SessionRunner  # noqa: E402
from models import Consent, EnergyTag, HoldDecision  # noqa: E402
from session_store import Session  # noqa: E402

# --- Latency model (virtual seconds) ------------------------------------------------------------------

CLAUDE_LATENCY_S = {
    "CrumbDraft": 3.7,
    "HoldDecision": 2.5,
    "EnergyTag": 2.5,
    "ProfileDraft": 7.5,
    "OverlapDraftSet": 8.0,
    "TrailDraft": 7.0,
    "grounded": 7.0,  # unmeasured; at 8 s or more every prefetch fact lookup times out (FACT_BUDGET_S)
    "text": 3.0,
}
ELEVEN_LATENCY_S = {"eleven_flash_v2_5": 0.45, "eleven_multilingual_v2": 0.85, "eleven_v4": 1.45}
CREDITS_PER_CHAR = {"eleven_flash_v2_5": 0.5, "eleven_multilingual_v2": 1.0, "eleven_v4": 1.0}
FREE_TIER_CREDITS = 10_000
CHARS_PER_AUDIO_S = 14.0  # ~150 words per minute
CHIME_S = 0.74  # web/src/audio/player.ts: last note at 0.14 s + 0.55 s decay + 0.05 s
TABLE_NET_S = 0.05
FADE_S = 0.25
BROWSER_VOICE_S_PER_WORD = 0.4
SECONDS_PER_WORD = 0.36
REPLY_AFTER_CRUMB_S = 1.0
TARGET_HIT_S = 0.7  # PRD 8.10: threshold crossed to audio start on a cache hit
NAMES = {1: "Maya", 2: "Dev"}
ANSWERS = {
    1: [
        "I grew up in Austin and I just started training for my first half marathon in November.",
        "I work as a product designer at a small studio and I love sketching buildings on weekends.",
        "Lately I have been learning to make fresh pasta from scratch with my roommate.",
    ],
    2: [
        "I moved here from Houston two years ago and I spend most weekends rock climbing.",
        "I teach high school chemistry and I am trying to learn the guitar this year.",
        "I am obsessed with finding the best breakfast tacos in town, I keep a ranked list.",
    ],
}

# --- Scenario ---------------------------------------------------------------------------------------
# Beats: ("turns", [(seat, text, gap_after_s), ...]); ("pause", max_s) answered if a crumb plays;
# ("silent", s) nobody answers anything; ("hold", seat, text, silence_s); ("drop",); ("noise", s).

BLOCK_RUNNING = [
    (1, "Okay so that is funny, we both picked the outdoors thing.", 0.9),
    (2, "Right? So how is the half marathon training actually going?", 0.7),
    (1, "Honestly better than I expected, I did ten miles on Saturday.", 0.8),
    (2, "Ten miles is a lot, where do you even run that far around here?", 0.6),
    (1, "Mostly the trail around Lady Bird Lake, it is flat and there is shade.", 1.1),
    (2, "Oh nice.", 0.5),
    (1, "Do you run at all or is it only climbing for you?", 0.9),
    (2, "I used to run before I hurt my knee, now I mostly climb at the gym downtown.", 1.3),
    (1, "Is climbing hard on the knees too or is it more of an arms thing?", 0.8),
    (2, "It is more fingers and forearms, my knees are totally fine on the wall.", 3.3),
    (1, "I have always wanted to try it but it looks so scary from the ground.", 0.9),
    (2, "The first time is scary, but the bouldering area is only like twelve feet.", 1.0),
    (1, "Okay that sounds a lot more doable than I pictured", 0.7),
    (2, "You should come with our group sometime, we go every other Thursday.", 1.2),
]
BLOCK_FOOD = [
    (1, "Okay but you said you keep a ranked list of breakfast tacos?", 0.6),
    (2, "I do, it is a spreadsheet with forty two places and a scoring system.", 0.9),
    (1, "That is amazing, what is number one right now?", 0.7),
    (2, "A tiny truck on the east side, the migas taco is unreal.", 3.4),
    (1, "I need the name, I have been going to the same place for years.", 0.8),
    (2, "I will send it to you, what is your usual order?", 1.0),
    (1, "Bean and cheese with potato, I am very basic.", 0.6),
    (2, "That is not basic, that is a classic.", 0.8),
    (1, "Do you score the salsa separately or is it all one number?", 1.2),
    (2, "Salsa is its own column, it is worth twenty percent of the score.", 0.9),
    (1, "Wait, that is so serious, I love it.", 1.0),
]
BLOCK_TRAVEL = [
    (2, "Oh wow, is that the coast? Where was that?", 0.8),
    (1, "Big Sur, we drove the whole Pacific Coast Highway last spring.", 1.0),
    (2, "I have always wanted to do that drive, how many days did it take?", 0.7),
    (1, "Five days, and we stopped at every single overlook.", 0.9),
    (2, "Did you camp or stay in hotels along the way?", 0.6),
    (1, "A mix, two nights camping and the rest in little motels.", 1.2),
    (2, "Camping on the coast sounds cold though.", 0.9),
    (1, "It was freezing, I wore every layer I packed.", 1.1),
]
BLOCK_MUSIC = [
    (2, "Oh that is a good one.", 0.8),
    (1, "So you mentioned guitar earlier, how long have you been learning?", 0.6),
    (2, "About eight months, mostly from videos at night after grading.", 0.9),
    (1, "What kind of music are you trying to play?", 0.7),
    (2, "Old folk songs mostly, I am working on a Townes Van Zandt song right now.", 1.4),
    (1, "My dad loves him, he used to play him on road trips.", 0.8),
    (2, "No way, which songs?", 0.7),
    (1, "Pancho and Lefty for sure, he would sing along every time.", 3.2),
    (2, "That is the one I am learning, the chord changes are tricky.", 0.9),
    (1, "You have to play it for me sometime, I promise I will not judge.", 1.1),
]
BLOCK_WEEKEND = [
    (1, "Okay sorry, we kind of drifted there.", 0.8),
    (2, "All good, so what are you doing this weekend?", 0.7),
    (1, "Another long run on Saturday, then pasta night with my roommate.", 0.9),
    (2, "Wait, you make pasta from scratch?", 0.6),
    (1, "We are learning, the first batch was a disaster but it is getting better.", 1.0),
    (2, "What went wrong with the first batch?", 0.8),
    (1, "Too much flour, it was basically cardboard.", 0.7),
    (2, "That is hilarious, I would still eat it.", 1.2),
    (1, "You can be the official taste tester then.", 0.9),
    (2, "Deal, I will bring tacos for backup.", 1.0),
]
BLOCK_CLOSE = [
    (2, "Sorry, I zoned out for a second there.", 0.8),
    (1, "Same, it is getting loud in here.", 0.7),
    (2, "So when is the race exactly?", 0.6),
    (1, "November ninth, I am already nervous about it.", 0.9),
    (2, "You will crush it, ten miles already is great.", 1.0),
    (1, "Thank you, that actually helps a lot", 0.8),
    (2, "We should do the climbing thing after the race then.", 0.7),
    (1, "Yes, that is my reward, climbing and tacos.", 1.1),
    (2, "Perfect plan.", 0.9),
]
BLOCK_LAST = [
    (1, "Okay that one is easy, my grandma's porch in San Antonio.", 0.9),
    (2, "That is sweet, what made it so good?", 0.7),
    (1, "She always had lemonade and a radio playing old Tejano songs.", 1.0),
    (2, "I love that, my grandpa did the same thing with baseball games.", 0.9),
]

SCENARIO: list[tuple] = [
    ("turns", BLOCK_RUNNING),
    ("pause", 8.0),  # unusually long pause: a steer (or switch) fires and gets answered
    ("turns", BLOCK_FOOD),
    ("hold", 1, "Hold on, I have a photo from that trip, let me find it.", 16.0),
    ("turns", [(1, "Okay here it is, look at this view.", 0.8)] + BLOCK_TRAVEL),
    ("drop",),  # manual Drop a crumb, answered
    ("turns", BLOCK_MUSIC),
    ("pause", 9.0),
    ("turns", BLOCK_WEEKEND),
    ("silent", 30.0),  # an unanswered crumb (then a second one, then quiet)
    ("turns", BLOCK_CLOSE),
    ("noise", 45.0),  # a quiet stretch with background noise at the venue
    ("pause", 10.0),
    ("turns", BLOCK_LAST),
    ("pause", 9.0),
    ("turns", [(2, "Okay I think they are calling time, this was really fun.", 0.9),
               (1, "It was, thank you for the taco list in advance.", 1.0)]),
]
SESSION_LIVE_S = 600.0  # the session is ended at 10:00 of live time (idle silence fills any remainder)


# --- Virtual time -------------------------------------------------------------------------------------


class VirtualClockLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock jumps to the next timer whenever nothing is ready to run."""

    def __init__(self) -> None:
        super().__init__()
        self._virtual_now = 0.0

    def time(self) -> float:
        return self._virtual_now

    def _run_once(self) -> None:  # noqa: D401 - asyncio internal hook
        if not self._ready and self._scheduled:
            when = self._scheduled[0]._when
            if when > self._virtual_now:
                self._virtual_now = when
        super()._run_once()


# --- Ledger ---------------------------------------------------------------------------------------------

PATH: contextvars.ContextVar[str] = contextvars.ContextVar("usage_path", default="other")
OWNER: contextvars.ContextVar[list | None] = contextvars.ContextVar("usage_owner", default=None)


@dataclass
class Call:
    provider: str  # claude | elevenlabs | gemini_tts
    purpose: str
    path: str
    started: float
    model: str = ""
    chars: int = 0  # ElevenLabs: billed characters; Claude: input characters (system + user)
    ended: float | None = None
    abandoned: bool = False  # cancelled by a caller's timeout: billed, result thrown away
    owner: str | None = None  # crumb id or system speech id the result went into
    useful: bool | None = None  # classifiers: did the answer change anything


@dataclass
class Gen:
    crumb_id: str
    kind: str
    path: str  # prefetch | live
    at: float
    voiced: bool
    cache_hit: bool = False
    context: str = ""  # prefetch only: the engine state when it started (why no automatic crumb could use it)


@dataclass
class Played:
    crumb_id: str
    kind: str
    source: str
    at: float
    latency_ms: float
    cache_hit: bool
    has_audio: bool
    interrupted: bool = False


@dataclass
class Ledger:
    calls: list[Call] = field(default_factory=list)
    gens: list[Gen] = field(default_factory=list)
    played: dict[str, Played] = field(default_factory=dict)
    system_lines: list[str] = field(default_factory=list)
    prefetch_effects: Counter = field(default_factory=Counter)
    prefetch_runs: int = 0
    prefetch_runs_empty: int = 0
    deliver_requests: Counter = field(default_factory=Counter)
    hold_triggers: Counter = field(default_factory=Counter)
    hold_outcomes: list[tuple[int, bool, str]] = field(default_factory=list)  # (turn_id, changed, why)
    energy_outcomes: list[tuple[bool, bool]] = field(default_factory=list)  # (topic changed, energy changed)
    warm_requests: int = 0
    voice_requests: Counter = field(default_factory=Counter)  # lazy voicing requests at the warming band, by kind
    log_events: Counter = field(default_factory=Counter)
    beats: Counter = field(default_factory=Counter)
    beat_times: list[tuple[str, float]] = field(default_factory=list)
    decisions: Counter = field(default_factory=Counter)
    human_turns: int = 0
    live_at: float | None = None
    ended_at: float | None = None

    def new_call(self, **kw: Any) -> Call:
        call = Call(path=PATH.get(), **kw)
        self.calls.append(call)
        bucket = OWNER.get()
        if bucket is not None:
            bucket.append(call)
        return call


class _LogCounter(logging.Handler):
    """Counts breadcrumbs log events by their static message template (never the formatted args)."""

    def __init__(self, ledger: Ledger) -> None:
        super().__init__(logging.INFO)
        self.ledger = ledger

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("breadcrumbs"):
            self.ledger.log_events[f"{record.name.rsplit('.', 1)[-1]}: {record.msg}"] += 1


# --- Fake Claude (anthropic SDK surface used by ai/claude.py) ---------------------------------------------


class FakeAnthropic:
    """`client.beta.messages.parse/create` with realistic latency and schema-valid answers."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse, create=self._create))
        self.overlap_source: Any = None  # set by the harness before find_overlaps
        self._n = 0

    async def _timed(self, call: Call, latency: float) -> None:
        loop = asyncio.get_running_loop()
        try:
            await asyncio.sleep(latency)
        except asyncio.CancelledError:
            call.abandoned = True
            call.ended = loop.time()
            raise
        call.ended = loop.time()

    async def _parse(self, **body: Any) -> Any:
        schema = body["output_format"]
        user = body["messages"][0]["content"]
        system = body.get("system") or ""
        payload = _loads(user)
        name = schema.__name__
        purpose = {
            "CrumbDraft": f"crumb {payload.get('kind', '?') if isinstance(payload, dict) else '?'}",
            "HoldDecision": "hold classifier",
            "EnergyTag": "energy tagger",
            "ProfileDraft": "profile",
            "OverlapDraftSet": "overlaps",
            "TrailDraft": "trail",
        }.get(name, name)
        loop = asyncio.get_running_loop()
        call = self.ledger.new_call(provider="claude", purpose=purpose, started=loop.time(), model=config.CLAUDE_MODEL,
                                    chars=len(system) + len(user))
        await self._timed(call, CLAUDE_LATENCY_S.get(name, 3.0))
        self._n += 1
        parsed = self._answer(name, payload, user)
        return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn", content=[], _request_id=f"fake_{self._n}")

    async def _create(self, **body: Any) -> Any:
        user = body["messages"][0]["content"]
        system = body.get("system") or ""
        grounded = bool(body.get("tools"))
        loop = asyncio.get_running_loop()
        call = self.ledger.new_call(provider="claude", purpose="grounded fact" if grounded else "text",
                                    started=loop.time(), model=config.CLAUDE_MODEL, chars=len(system) + len(user))
        await self._timed(call, CLAUDE_LATENCY_S["grounded" if grounded else "text"])
        self._n += 1
        if grounded:
            cite = SimpleNamespace(type="web_search_result_location", url="https://example.org/fact", title="Example source")
            block = SimpleNamespace(type="text", text="Austin is home to the largest urban bat colony in North America.",
                                    citations=[cite])
        else:
            block = SimpleNamespace(type="text", text="Okay.", citations=[])
        return SimpleNamespace(content=[block], stop_reason="end_turn", _request_id=f"fake_{self._n}")

    def _answer(self, name: str, payload: Any, user: str) -> Any:
        if name == "CrumbDraft":
            return self._crumb(payload)
        if name == "HoldDecision":
            return mock_hold(user)
        if name == "EnergyTag":
            return mock_energy(user)
        if name == "ProfileDraft":
            return ProfileDraft(items=mock_profile_items(payload["mode"], payload["answers"]), vibe="warm, curious")
        if name == "OverlapDraftSet":
            overlaps = self.overlap_source() if self.overlap_source else []
            return OverlapDraftSet(overlaps=[OverlapDraft(**o.model_dump()) for o in overlaps])
        if name == "TrailDraft":
            names = {int(k): v for k, v in payload["names"].items()}
            return TrailDraft(items=mock_trail(names, payload.get("transcript", ""), {}))
        raise ValueError(name)

    def _crumb(self, p: dict) -> CrumbDraft:
        """A valid ~24-word crumb for the plan in the payload (real crumbs are 20 to 28 words)."""
        n1, n2 = p["names"]["1"], p["names"]["2"]
        kind, src = p.get("kind"), p.get("requested_source")
        ids = list(p.get("requested_item_ids") or [])
        fact = p.get("verified_fact")
        allowed = p.get("allowed_formats") or ["question"]
        k = self._n
        if fact and "fun_fact" in allowed:
            text, fmt = f"{fact['text']} {n1}, had you heard that? {n2}, what would you add?", "fun_fact"
        elif kind == "steer":
            text, fmt = (f"{n2}, {n1} just said something worth digging into. What part of that would you want "
                         f"to hear more about, and what would you ask first?"), "question"
        else:
            text, fmt = (f"{n1}, here is a fresh one for you both: what is a small thing from this week that made "
                         f"you laugh out loud? {n2}, you go right after."), "question"
        topic = p.get("current_topic") if kind == "steer" and p.get("current_topic") else f"fresh thread {k}"
        addressed = "both" if src in ("overlap", "outside", "conversation") else ("seat1" if src == "profile_seat1" else "seat2")
        return CrumbDraft(format=fmt, text=text, addressed_to=addressed, source_item_ids=ids, topic=topic, depth=1,
                          why="Chosen to fit this pause.")


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


# --- Fake ElevenLabs network ------------------------------------------------------------------------------


def eleven_transport(ledger: Ledger) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        loop = asyncio.get_running_loop()
        if request.method == "GET":
            ledger.warm_requests += 1
            return httpx.Response(200, json={"models": []})
        body = json.loads(request.content)
        if "inputs" in body:
            text = "".join(i.get("text", "") for i in body["inputs"])
        else:
            text = body.get("text", "")
        model = body.get("model_id", "?")
        call = ledger.new_call(provider="elevenlabs", purpose=_eleven_purpose(), started=loop.time(), model=model,
                               chars=len(text))
        try:
            await asyncio.sleep(ELEVEN_LATENCY_S.get(model, 1.0))
        except asyncio.CancelledError:
            call.abandoned = True
            call.ended = loop.time()
            raise
        call.ended = loop.time()
        seconds = max(1.0, len(text) / CHARS_PER_AUDIO_S)
        pcm = b"\x10\x27" * int(seconds * 24_000)  # a steady tone: survives trim_silence untouched
        return httpx.Response(200, content=pcm)

    return httpx.MockTransport(handler)


def _eleven_purpose() -> str:
    return {"prefetch": "crumb audio (prefetch)", "live": "crumb audio (live)", "system": "system line",
            "voice": "crumb audio (voiced at warming)"}.get(PATH.get(), PATH.get())


# --- Fake Table -------------------------------------------------------------------------------------------


class FakeTable:
    """A Table events connection: plays crumbs (chime + audio) and system lines, reports tts_state."""

    role = "table"

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.closed = False
        self.runner: SessionRunner | None = None
        self.current: str | None = None
        self.finished: set[str] = set()
        self.interrupted: set[str] = set()
        self.crumbs_seen = 0
        self._tasks: set[asyncio.Task] = set()

    @property
    def playing(self) -> bool:
        return self.current is not None

    def send_text(self, text: str) -> None:
        evt = json.loads(text)
        kind = evt.get("type")
        loop = asyncio.get_running_loop()
        if kind == "crumb":
            crumb = evt["crumb"]
            self.crumbs_seen += 1
            self.ledger.played[crumb["id"]] = Played(
                crumb_id=crumb["id"], kind=crumb["kind"], source=crumb["source"], at=loop.time(),
                latency_ms=float(evt.get("latency_ms") or 0.0), cache_hit=bool(evt.get("cache_hit")),
                has_audio=bool(evt.get("audio_b64")),
            )
            self._spawn(self._play(crumb["id"], evt.get("audio_b64") or "", len(crumb["text"].split()), True))
        elif kind == "system_say":
            self.ledger.system_lines.append(evt["id"])
            self._spawn(self._play(evt["id"], evt.get("audio_b64") or "", len(evt["text"].split()), False))
        elif kind == "decision":
            self.ledger.decisions[evt["decision"].get("action", "?")] += 1
        elif kind == "crumb_interrupted":
            self.interrupted.add(evt["crumb_id"])
            if evt["crumb_id"] in self.ledger.played:
                self.ledger.played[evt["crumb_id"]].interrupted = True

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _play(self, line_id: str, audio_b64: str, words: int, chime: bool) -> None:
        assert self.runner is not None
        await asyncio.sleep(TABLE_NET_S)
        self.current = line_id  # a newer line supersedes the old one (it ends at its next step)
        self.runner.on_tts_state(line_id, True)
        if audio_b64:
            seconds = max(0.0, (len(base64.b64decode(audio_b64)) - 44) / 48_000)
        else:
            seconds = words * BROWSER_VOICE_S_PER_WORD
        total = (CHIME_S if chime else 0.0) + seconds
        step, elapsed = 0.05, 0.0
        while elapsed < total:
            if line_id in self.interrupted or self.current != line_id:
                await asyncio.sleep(FADE_S)
                break
            await asyncio.sleep(step)
            elapsed += step
        self.runner.on_tts_state(line_id, False)
        self.finished.add(line_id)
        if self.current == line_id:
            self.current = None


# --- Instrumentation of the real services ------------------------------------------------------------------


def instrument(runner: SessionRunner, ledger: Ledger, gateway: Gemini) -> None:
    svc = runner.crumbs
    engine = runner.engine
    loop = asyncio.get_running_loop()

    orig_prefetch_one = svc._prefetch_one
    orig_voice_text, orig_voice = svc._voice_text, svc.voice
    orig_get, orig_opener = svc.get, svc.opener
    orig_say, orig_classify, orig_tag = runner._say, runner._classify, runner._tag_energy
    orig_deliver, orig_prefetch = runner._deliver, runner._prefetch
    orig_execute = runner._execute
    orig_apply_hold, orig_apply_energy = engine.apply_hold_decision, engine.apply_energy_tag
    orig_tts = gateway.tts

    def prefetch_context(key: str) -> str:
        now = loop.time()
        inp = engine._tick_inputs(now)
        last_dispatch = max((p.at for p in ledger.played.values()), default=None)
        if last_dispatch is not None and now - last_dispatch < 2.0:
            return "refill right after a dispatch"
        if not inp.rate_ok:
            return "auto rate cap reached"
        if inp.crumbs_this_silence >= runner.session.params.MAX_UNANSWERED_CRUMBS:
            return "quiet after unanswered crumbs"
        if inp.hold in ("show", "external"):
            return "show/external hold"
        if inp.in_warmup:
            return "opener warm-up"
        if key == "steer" and not inp.regular_ok:
            return "regular cooldown active"
        if inp.turn_active or inp.vad_active:
            return "someone talking"
        return "eligible"

    async def prefetch_one(key: str, convo: Any) -> Any:
        context = prefetch_context(key)
        PATH.set("prefetch")
        bucket: list[Call] = []
        OWNER.set(bucket)
        result = await orig_prefetch_one(key, convo)
        crumb = result[0]
        audio = result[1] if len(result) == 3 else None  # (crumb, audio, ms) before lazy voicing, (crumb, ms) after
        for c in bucket:
            c.owner = crumb.id
        ledger.gens.append(Gen(crumb.id, key, "prefetch", loop.time(), audio is not None, context=context))
        return result

    async def voice_text(text: str, fast: bool, budget_s: float) -> Any:
        # A crumb voiced ahead of its delivery (warming band). On the live path (a hit voiced on the spot)
        # the live bucket already owns the call.
        if PATH.get() == "live":
            return await orig_voice_text(text, fast, budget_s)
        PATH.set("voice")
        bucket: list[Call] = []
        OWNER.set(bucket)
        owner = next((e.crumb.id for e in svc._cache.values() if e.crumb.text == text), None)
        try:
            return await orig_voice_text(text, fast, budget_s)
        finally:
            for c in bucket:
                c.owner = owner

    async def voice(kind: str, convo: Any, now: float, lead_s: float) -> None:
        ledger.voice_requests[kind] += 1
        await orig_voice(kind, convo, now, lead_s)

    async def scoped_live(fn: Any, *args: Any) -> Any:
        outer = OWNER.get()
        bucket: list[Call] = outer if outer is not None else []
        t1, t2 = PATH.set("live"), OWNER.set(bucket)
        try:
            delivery = await fn(*args)
        finally:
            PATH.reset(t1)
            OWNER.reset(t2)
        if outer is None:
            for c in bucket:
                c.owner = delivery.crumb.id
            ledger.gens.append(Gen(delivery.crumb.id, delivery.crumb.kind, "live", loop.time(),
                                   delivery.audio_wav is not None, delivery.cache_hit))
        return delivery

    async def get(kind: str, convo: Any, now: float) -> Any:
        return await scoped_live(orig_get, kind, convo, now)

    async def opener(convo: Any) -> Any:
        return await scoped_live(orig_opener, convo)

    async def say(speech_id: str, text: str) -> None:
        PATH.set("system")
        bucket: list[Call] = []
        OWNER.set(bucket)
        await orig_say(speech_id, text)
        for c in bucket:
            c.owner = speech_id

    async def classify(turn_id: int, context: str) -> None:
        PATH.set("classifier")
        bucket: list[Call] = []
        OWNER.set(bucket)
        before = len(ledger.hold_outcomes)
        await orig_classify(turn_id, context)
        changed = any(t == turn_id and ch for t, ch, _ in ledger.hold_outcomes[before:])
        for c in bucket:
            c.useful = changed

    async def tag(context: str) -> None:
        PATH.set("classifier")
        bucket: list[Call] = []
        OWNER.set(bucket)
        before = len(ledger.energy_outcomes)
        await orig_tag(context)
        changed = any(tc or ec for tc, ec in ledger.energy_outcomes[before:])
        for c in bucket:
            c.useful = changed

    def apply_hold(turn_id: int, d: HoldDecision, now: float) -> list:
        rec = engine._tracker.by_id(turn_id)
        spoke = rec is not None and engine._last_human_activity_at > rec.turn.ended_at + 1e-6
        effects = orig_apply_hold(turn_id, d, now)
        why = "someone spoke before the answer" if spoke else ("changed the hold" if effects else "no change")
        ledger.hold_outcomes.append((turn_id, bool(effects), why))
        return effects

    def apply_energy(tag_: EnergyTag, now: float) -> list:
        prev = engine._energy
        effects = orig_apply_energy(tag_, now)
        topic_changed = prev is None or prev.topic != tag_.topic
        energy_changed = prev is None or prev.energy != tag_.energy or prev.winding_down != tag_.winding_down
        ledger.energy_outcomes.append((topic_changed, energy_changed))
        return effects

    async def deliver(kind: str, *, instruction: str | None = None) -> None:
        ledger.deliver_requests[kind] += 1
        await orig_deliver(kind, instruction=instruction)

    async def prefetch(now: float, steer: bool = True, fresh: bool = False, slots: tuple = ("steer", "switch")) -> None:
        ledger.prefetch_runs += 1
        before = len(ledger.gens)
        await orig_prefetch(now, steer, fresh, slots)
        if len(ledger.gens) == before:
            ledger.prefetch_runs_empty += 1

    def execute(effect: dict) -> None:
        kind = effect.get("type")
        if kind == "prefetch":
            ledger.prefetch_effects[effect.get("reason", "?")] += 1
        elif kind == "classify_hold":
            rec = engine._tracker.by_id(int(effect["turn_id"]))
            text = rec.best_text if rec is not None else ""
            if match_hold(text, runner.session.params.HOLD_TAIL_WORDS) is not None:
                ledger.hold_triggers["lexical hit"] += 1
            elif hold_vetoed(text, runner.session.params.HOLD_TAIL_WORDS):
                ledger.hold_triggers["vetoed hit"] += 1
            elif is_ambiguous_fragment(text):
                ledger.hold_triggers["ambiguous fragment"] += 1
        orig_execute(effect)

    async def tts(text: str) -> bytes | None:
        ledger.new_call(provider="gemini_tts", purpose="gemini tts (unavailable, no request)", started=loop.time())
        return await orig_tts(text)

    svc._prefetch_one = prefetch_one  # type: ignore[method-assign]
    svc._voice_text = voice_text  # type: ignore[method-assign]
    svc.voice = voice  # type: ignore[method-assign]
    svc.get = get  # type: ignore[method-assign]
    svc.opener = opener  # type: ignore[method-assign]
    runner._say = say  # type: ignore[method-assign]
    runner._classify = classify  # type: ignore[method-assign]
    runner._tag_energy = tag  # type: ignore[method-assign]
    runner._deliver = deliver  # type: ignore[method-assign]
    runner._prefetch = prefetch  # type: ignore[method-assign]
    runner._execute = execute  # type: ignore[method-assign]
    engine.apply_hold_decision = apply_hold  # type: ignore[method-assign]
    engine.apply_energy_tag = apply_energy  # type: ignore[method-assign]
    gateway.tts = tts  # type: ignore[method-assign]


# --- Driver -------------------------------------------------------------------------------------------------


class Driver:
    """Plays the two people: Muse-shaped turns (runner.dev_turn), local VAD at 10 Hz, controls."""

    def __init__(self, runner: SessionRunner, table: FakeTable, ledger: Ledger) -> None:
        self.runner = runner
        self.table = table
        self.ledger = ledger
        self.speaking = False
        self.noise = False
        self.loop = asyncio.get_running_loop()

    async def vad_loop(self) -> None:
        while True:
            active = self.speaking or self.noise
            self.runner.on_vad(active, 0.08 if active else 0.002)
            await asyncio.sleep(0.1)

    async def until(self, cond: Any, timeout: float) -> bool:
        end = self.loop.time() + timeout
        while self.loop.time() < end:
            if cond():
                return True
            await asyncio.sleep(0.05)
        return cond()

    async def wait_table_quiet(self) -> None:
        await self.until(lambda: not self.table.playing, 40.0)

    async def say(self, seat: int, text: str) -> None:
        await self.wait_table_quiet()
        words = len(text.split())
        duration = max(0.8, SECONDS_PER_WORD * words)
        self.speaking = True
        self.ledger.human_turns += 1
        self.runner.dev_turn(seat, text, duration)
        await asyncio.sleep(duration)
        self.speaking = False
        await asyncio.sleep(0.25)  # speechEnd -> speechComplete

    async def calibrate(self) -> None:
        self.runner.ensure_calibration()
        await self.until(lambda: "sys_cal1" in self.table.finished, 20.0)
        await asyncio.sleep(0.6)
        await self.say(1, "Hi Dev, I'm Maya and I'm from Austin, born and raised.")
        await self.until(lambda: "sys_cal2" in self.table.finished, 20.0)
        await asyncio.sleep(0.6)
        await self.say(2, "Hey Maya, I'm Dev, I grew up in Houston but I live here now.")
        await self.until(lambda: self.runner.engine.phase == "live", 20.0)
        self.ledger.live_at = self.loop.time()
        # The opener goes out at the first quiet tick; both people listen to it, then answer.
        await self.until(lambda: self.table.crumbs_seen >= 1, 15.0)
        await self.answer_after_crumb()

    async def answer_after_crumb(self) -> None:
        await asyncio.sleep(0.2)
        await self.wait_table_quiet()
        await asyncio.sleep(REPLY_AFTER_CRUMB_S)

    async def pause(self, max_s: float) -> None:
        """Silence until a crumb plays (then answer it) or max_s passes."""
        seen = self.table.crumbs_seen
        heard = await self.until(lambda: self.table.crumbs_seen > seen, max_s)
        if heard:
            await self.answer_after_crumb()

    async def run(self) -> None:
        await self.calibrate()
        for beat in SCENARIO:
            name = beat[0]
            self.ledger.beats[name] += 1
            self.ledger.beat_times.append((name, round(self.loop.time() - (self.ledger.live_at or 0.0), 1)))
            if name == "turns":
                for seat, text, gap in beat[1]:
                    await self.say(seat, text)
                    await asyncio.sleep(gap)
            elif name == "pause":
                await self.pause(beat[1])
            elif name == "silent":
                await asyncio.sleep(beat[1])
                await self.wait_table_quiet()
                await asyncio.sleep(0.8)
            elif name == "hold":
                _, seat, text, silence = beat
                await self.say(seat, text)
                await asyncio.sleep(silence)
            elif name == "drop":
                seen = self.table.crumbs_seen
                self.runner.on_control("spark")
                if await self.until(lambda: self.table.crumbs_seen > seen, 15.0):
                    await self.answer_after_crumb()
            elif name == "noise":
                self.noise = True
                await asyncio.sleep(beat[1])
                self.noise = False
                await self.wait_table_quiet()
                await asyncio.sleep(0.5)
        assert self.ledger.live_at is not None
        left = self.ledger.live_at + SESSION_LIVE_S - self.loop.time()
        if left > 0:
            await asyncio.sleep(left)
            await self.wait_table_quiet()


# --- Session ---------------------------------------------------------------------------------------------------


async def run_session() -> tuple[Ledger, dict]:
    ledger = Ledger()
    loop = asyncio.get_running_loop()
    counter = _LogCounter(ledger)
    root = logging.getLogger("breadcrumbs")
    saved = (root.level, root.propagate)
    root.addHandler(counter)
    root.setLevel(logging.INFO)
    root.propagate = False
    try:
        return await _run_session(ledger, loop)
    finally:
        root.removeHandler(counter)
        root.setLevel(saved[0])
        root.propagate = saved[1]


async def _run_session(ledger: Ledger, loop: asyncio.AbstractEventLoop) -> tuple[Ledger, dict]:

    fake_claude = FakeAnthropic(ledger)
    gateway = Gemini(mock=False, api_key="", meta_api_key="",
                     claude=Claude(api_key="", client=fake_claude, model=config.CLAUDE_MODEL))
    voice = ElevenLabs(api_key="fake-not-a-real-key", voice_id="", model="", transport=eleven_transport(ledger))
    gemini_mod._singleton = gateway
    elevenlabs_mod._singleton = voice

    session = Session(sid="usage-sim", mode="social", quick=False, ttl_s=7200)
    for seat, name in NAMES.items():
        session.seats[seat].consent = Consent(typed_name=name, agreed=True, at=0.0)
        session.seats[seat].ready = True

    # Setup (before the Table goes live): two profiles and the overlap finder.
    PATH.set("setup")
    for seat in (1, 2):
        session.seats[seat].profile = await extract_profile(seat, "social", NAMES[seat], ANSWERS[seat], gemini=gateway)
    p1, p2 = session.seats[1].profile, session.seats[2].profile
    assert p1 is not None and p2 is not None
    fake_claude.overlap_source = lambda: mock_overlaps(allowed_items(p1), allowed_items(p2), dict(NAMES),
                                                       {1: p1, 2: p2})
    overlaps = await find_overlaps(p1, p2, "social", gemini=gateway)

    table = FakeTable(ledger)
    session.register(table)  # type: ignore[arg-type]
    session.phase = "calibrating"

    async def on_round_end() -> None:
        return None

    runner = SessionRunner(session, overlaps=overlaps, on_round_end=on_round_end, clock=loop.time)
    table.runner = runner
    instrument(runner, ledger, gateway)
    PATH.set("other")
    runner.start()
    driver = Driver(runner, table, ledger)
    vad = loop.create_task(driver.vad_loop())
    try:
        await driver.run()
    finally:
        vad.cancel()
    ledger.ended_at = loop.time()
    runner.end()
    transcript = runner.full_transcript()
    await runner.stop()
    for task in list(table._tasks):
        task.cancel()

    PATH.set("end")
    trail = await generate_trail("social", session.names, transcript, session.profiles, gemini=gateway)
    metrics = runner.engine.metrics()
    return ledger, {"metrics": metrics, "trail_items": len(trail.items), "overlaps": len(overlaps.overlaps)}


# --- Report ---------------------------------------------------------------------------------------------------


def _credits(model: str, chars: int) -> float:
    return chars * CREDITS_PER_CHAR.get(model, 1.0)


def build_report(ledger: Ledger, extra: dict) -> dict:
    played = ledger.played
    delivered_ids = {cid for cid, p in played.items() if not p.interrupted}
    dispatched_ids = set(played)
    system_ids = set(ledger.system_lines)

    def delivered(call: Call) -> bool:
        return call.owner is not None and (call.owner in delivered_ids or call.owner in system_ids) and not call.abandoned

    claude = [c for c in ledger.calls if c.provider == "claude"]
    eleven = [c for c in ledger.calls if c.provider == "elevenlabs"]
    gem = [c for c in ledger.calls if c.provider == "gemini_tts"]

    claude_rows: dict[tuple[str, str], dict] = defaultdict(lambda: Counter())
    for c in claude:
        row = claude_rows[(c.purpose, c.path)]
        row["calls"] += 1
        row["abandoned"] += int(c.abandoned)
        row["input_chars"] += c.chars
        if c.purpose.startswith("crumb") or c.purpose == "grounded fact":
            row["delivered"] += int(delivered(c))
            row["wasted"] += int(not delivered(c))
        elif c.useful is not None:
            row["delivered"] += int(c.useful)
            row["wasted"] += int(not c.useful)
        else:
            row["delivered"] += int(not c.abandoned)
            row["wasted"] += int(c.abandoned)

    eleven_rows: dict[tuple[str, str], dict] = defaultdict(lambda: Counter())
    for c in eleven:
        row = eleven_rows[(c.purpose, c.model)]
        row["calls"] += 1
        row["chars"] += c.chars
        row["credits"] += _credits(c.model, c.chars)
        row["abandoned"] += int(c.abandoned)
        if delivered(c):
            row["delivered_calls"] += 1
            row["delivered_credits"] += _credits(c.model, c.chars)
        else:
            row["wasted_calls"] += 1
            row["wasted_credits"] += _credits(c.model, c.chars)

    by_model: dict[str, Counter] = defaultdict(Counter)
    for c in eleven:
        by_model[c.model]["calls"] += 1
        by_model[c.model]["chars"] += c.chars
        by_model[c.model]["credits"] += _credits(c.model, c.chars)
    total_credits = sum(r["credits"] for r in by_model.values())
    wasted_credits = sum(_credits(c.model, c.chars) for c in eleven if not delivered(c))

    gens_by_path = Counter((g.path, g.kind) for g in ledger.gens)
    prefetched = [g for g in ledger.gens if g.path == "prefetch"]
    prefetched_used = [g for g in prefetched if g.crumb_id in delivered_ids]
    live = [g for g in ledger.gens if g.path == "live"]
    live_not_sent = [g for g in live if g.crumb_id not in dispatched_ids]

    crumbs = sorted(played.values(), key=lambda p: p.at)
    live_at = ledger.live_at or 0.0
    hits = [p.latency_ms for p in crumbs if p.cache_hit]
    misses = [p.latency_ms for p in crumbs if not p.cache_hit]

    report = {
        "session": {
            "live_minutes": round(((ledger.ended_at or 0) - live_at) / 60.0, 2),
            "human_turns": ledger.human_turns,
            "beat_starts_live_s": ledger.beat_times,
            "decisions": dict(ledger.decisions),
            "engine_metrics": extra.get("metrics"),
            "overlaps": extra.get("overlaps"),
            "trail_items": extra.get("trail_items"),
            "beats": dict(ledger.beats),
        },
        "claude": {f"{p} [{path}]": dict(v) for (p, path), v in sorted(claude_rows.items())},
        "claude_total": {"calls": len(claude), "abandoned": sum(c.abandoned for c in claude),
                         "input_chars": sum(c.chars for c in claude),
                         "est_input_tokens": round(sum(c.chars for c in claude) / 4)},
        "elevenlabs": {f"{p} [{m}]": {k: round(v, 1) if isinstance(v, float) else v for k, v in r.items()}
                       for (p, m), r in sorted(eleven_rows.items())},
        "elevenlabs_by_model": {m: {k: round(v, 1) if isinstance(v, float) else v for k, v in r.items()}
                                for m, r in sorted(by_model.items())},
        "elevenlabs_total": {
            "calls": len(eleven), "chars": sum(c.chars for c in eleven), "credits": round(total_credits, 1),
            "wasted_credits": round(wasted_credits, 1), "warm_requests": ledger.warm_requests,
            "sessions_per_free_month": round(FREE_TIER_CREDITS / total_credits, 1) if total_credits else None,
        },
        "gemini_tts": {"invocations": len(gem)},
        "muse": {"rest_transcriptions": sum(len(a) for a in ANSWERS.values()), "realtime_minutes":
                 round(((ledger.ended_at or 0) - live_at) / 60.0 + 0.3, 1)},
        "crumbs_delivered": [
            {"t_live_s": round(p.at - live_at, 1), "kind": p.kind, "source": p.source, "cache_hit": p.cache_hit,
             "latency_ms": round(p.latency_ms), "audio": p.has_audio, "interrupted": p.interrupted}
            for p in crumbs
        ],
        "latency": {
            "cache_hits": len(hits), "misses": len(misses),
            "hit_p50_ms": round(statistics.median(hits)) if hits else None,
            "hit_max_ms": round(max(hits)) if hits else None,
            "miss_p50_ms": round(statistics.median(misses)) if misses else None,
            "miss_max_ms": round(max(misses)) if misses else None,
            "hits_over_target": sum(1 for x in hits if x / 1000.0 + TABLE_NET_S > TARGET_HIT_S),
        },
        "generation": {
            "prefetch_runs": ledger.prefetch_runs, "prefetch_runs_that_generated_nothing": ledger.prefetch_runs_empty,
            "prefetch_triggers": dict(ledger.prefetch_effects),
            "prefetched_crumbs": dict(Counter(g.kind for g in prefetched)),
            "prefetched_crumbs_delivered": len(prefetched_used),
            "prefetch_context": {f"{k}: {ctx}": n for (k, ctx), n in
                                 sorted(Counter((g.kind, g.context) for g in prefetched).items())},
            "voice_requests": dict(ledger.voice_requests),
            "live_generated": dict(Counter(g.kind for g in live)),
            "live_generated_not_sent": len(live_not_sent),
            "deliver_requests": dict(ledger.deliver_requests),
            "by_path_kind": {f"{p}:{k}": n for (p, k), n in sorted(gens_by_path.items())},
        },
        "classifiers": {
            "hold_triggers": dict(ledger.hold_triggers),
            "hold_outcomes": dict(Counter(why for _, _, why in ledger.hold_outcomes)),
            "energy_tags": len(ledger.energy_outcomes),
            "energy_topic_changed": sum(1 for tc, _ in ledger.energy_outcomes if tc),
            "energy_nothing_changed": sum(1 for tc, ec in ledger.energy_outcomes if not tc and not ec),
        },
        "system_lines": {"count": len(ledger.system_lines), "ids": sorted(set(ledger.system_lines))},
        "log_events": dict(sorted(ledger.log_events.items())),
    }
    return report


def print_report(r: dict) -> None:
    out = sys.stdout.write
    s = r["session"]
    out(f"\nBreadcrumbs usage, scripted social session: {s['live_minutes']} live minutes, "
        f"{s['human_turns']} human turns (calibration included)\n")
    out("  beats (live s): " + ", ".join(f"{n}@{t}" for n, t in s["beat_starts_live_s"]) + "\n")
    out(f"  engine decisions: {s['decisions']}\n")

    out("\nClaude calls by purpose [path]                     calls  abandon  useful  wasted  input chars\n")
    for name, v in r["claude"].items():
        out(f"  {name:<48} {v.get('calls', 0):>5}  {v.get('abandoned', 0):>7}  {v.get('delivered', 0):>6}"
            f"  {v.get('wasted', 0):>6}  {v.get('input_chars', 0):>11}\n")
    t = r["claude_total"]
    out(f"  {'TOTAL':<48} {t['calls']:>5}  {t['abandoned']:>7}  {'':>6}  {'':>6}  {t['input_chars']:>11}"
        f"  (~{t['est_input_tokens']} input tokens)\n")

    out("\nElevenLabs by purpose [model]                      calls  chars  credits  delivered cr  wasted cr\n")
    for name, v in r["elevenlabs"].items():
        out(f"  {name:<48} {v.get('calls', 0):>5}  {v.get('chars', 0):>5}  {v.get('credits', 0):>7}"
            f"  {v.get('delivered_credits', 0):>12}  {v.get('wasted_credits', 0):>9}\n")
    for model, v in r["elevenlabs_by_model"].items():
        out(f"  by model {model:<39} {v['calls']:>5}  {v['chars']:>5}  {v['credits']:>7}\n")
    e = r["elevenlabs_total"]
    out(f"  TOTAL {e['calls']} calls, {e['chars']} chars, {e['credits']} credits ({e['wasted_credits']} wasted), "
        f"warm-up GETs {e['warm_requests']}; free tier = {e['sessions_per_free_month']} sessions/month\n")
    out(f"\nGemini TTS invocations: {r['gemini_tts']['invocations']} (no key/paused: no request sent)\n")
    out(f"Muse: {r['muse']['rest_transcriptions']} REST transcriptions (one per recorded answer), "
        f"~{r['muse']['realtime_minutes']} realtime minutes\n")

    out("\nCrumbs sent to the Table\n   t(s)  kind      source          hit  latency  audio  faded\n")
    for c in r["crumbs_delivered"]:
        out(f"  {c['t_live_s']:>5}  {c['kind']:<8}  {c['source']:<14}  {'y' if c['cache_hit'] else 'n':>3}"
            f"  {c['latency_ms']:>5} ms  {'y' if c['audio'] else 'n':>5}  {'y' if c['interrupted'] else 'n':>5}\n")
    lat = r["latency"]
    out(f"  cache hits {lat['cache_hits']} (p50 {lat['hit_p50_ms']} ms, max {lat['hit_max_ms']} ms, over 0.7 s target:"
        f" {lat['hits_over_target']}); misses {lat['misses']} (p50 {lat['miss_p50_ms']} ms, max {lat['miss_max_ms']} ms)\n")

    g = r["generation"]
    out("\nGeneration and waste\n")
    out(f"  prefetch runs {g['prefetch_runs']} ({g['prefetch_runs_that_generated_nothing']} generated nothing); "
        f"triggers {g['prefetch_triggers']}\n")
    out(f"  prefetched crumbs {g['prefetched_crumbs']}, of which delivered {g['prefetched_crumbs_delivered']}\n")
    out(f"  lazy voicing requests (warming band) {g.get('voice_requests', {})}\n")
    out("  engine state when each prefetched crumb started:\n")
    for k, v in g["prefetch_context"].items():
        out(f"    {v:>4}  {k}\n")
    out(f"  live-generated {g['live_generated']}, generated but never sent {g['live_generated_not_sent']}\n")
    out(f"  deliver requests {g['deliver_requests']}\n")
    c = r["classifiers"]
    out(f"  hold classifier triggers {c['hold_triggers']}; outcomes {c['hold_outcomes']}\n")
    out(f"  energy tags {c['energy_tags']} (topic changed {c['energy_topic_changed']}, nothing changed "
        f"{c['energy_nothing_changed']})\n")
    out(f"  system lines voiced {r['system_lines']['count']} {r['system_lines']['ids']}\n")
    out("\nLog events (template counts)\n")
    for k, v in r["log_events"].items():
        out(f"  {v:>4}  {k}\n")


def measure(*, repeat_session: bool = False) -> dict:
    """Run the scripted session on a fresh virtual-time loop and return the report dict.

    The process-wide system line cache starts empty (a first session after a restart). With
    `repeat_session` it keeps what an earlier session in this process voiced (a later session)."""
    if not repeat_session:
        tts_mod.SYSTEM_LINES.clear()
    loop = VirtualClockLoop()
    previous_singletons = (gemini_mod._singleton, elevenlabs_mod._singleton)
    try:
        ledger, extra = loop.run_until_complete(run_session())
    finally:
        loop.close()
        gemini_mod._singleton, elevenlabs_mod._singleton = previous_singletons
    return build_report(ledger, extra)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--json", help="also write the full report as JSON to this path")
    parser.add_argument("--repeat", action="store_true",
                        help="run a second session in the same process too (static system lines cached)")
    args = parser.parse_args(argv)
    report = measure()
    print_report(report)
    if args.repeat:
        second = measure(repeat_session=True)
        e = second["elevenlabs_total"]
        sys.stdout.write(f"\nSecond session in the same process: ElevenLabs {e['calls']} calls, {e['credits']} credits; "
                         f"system lines voiced {second['system_lines']}\n")
        report["repeat_session"] = {"elevenlabs_total": e, "system_lines": second["system_lines"]}
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
