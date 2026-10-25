# Breadcrumbs: Internal Contracts

The PRD (`PRDHack1.md`) is the product spec. This file pins the **module interfaces** so the workstreams can build in parallel and still fit together. If the PRD and this file conflict on product behavior, the PRD wins. If they conflict on a function signature, this file wins.

Shared files (lead-owned; do not rename fields): `server/models.py`, `server/config.py`, `web/src/lib/types.ts`.

## 0. Conventions

- The server runs from `server/` (`uvicorn main:app`). Imports are top-level: `from models import Crumb`, `from config import Params`, `from engine.core import Engine`. pytest runs from `server/` (`.venv/bin/pytest`); `conftest.py` puts `server/` on `sys.path`.
- Python 3.11+, fully async. **Never block the event loop** (no sync network calls; ffmpeg via `asyncio.create_subprocess_exec`).
- **Clock injection:** engine code never calls `time.*`. Every engine method takes `now: float` (monotonic seconds). The runner passes `time.monotonic()`; the simulator passes a fake clock.
- **Never log transcript text to disk or stdout** (PRD 17.3). Log decisions, counts and timings only. Text lives in memory.
- **Mock mode:** `config.MUSE_MOCK` / `config.GEMINI_MOCK` are True when the key is missing. Every external call has a deterministic mock path, so the whole app works without keys. Mocks must never present an ungrounded "fact" as a fact.
- No em dash characters (U+2014) in any user-facing or model-facing string. Replace them with commas.
- Build log: append a short entry to `/Users/omkherde/breadcrumbs/BUILD_LOG.md` when you finish a meaningful piece (see section 9).

## 1. Engine (workstream B): `server/engine/*`

Pure and deterministic. No asyncio, no network, no `time`. Files: `state.py` (turn tracking, pause timer, calibration map, bot-speaking intervals), `staleness.py`, `hold.py`, `policy.py`, `core.py` (the `Engine` facade below).

```python
# engine/core.py
class Engine:
    def __init__(self, mode: Mode, names: dict[int, str], params: Params, *, setup: Setup = "profiles"): ...

    # lifecycle
    def start_calibration(self, now: float) -> list[Effect]      # emits calibration step seat1 + a `say` prompt
                                                                  # setup "quick": goes live at once instead (decision
                                                                  # step "auto" + `live`; no `say`, no `calibration`, no opener)
    def on_muse_reconnect(self, now: float) -> list[Effect]      # labels reset: clear label map, pause auto crumbs, re-run calibration ("Re-syncing voices")
    def end(self, now: float) -> list[Effect]

    # inputs
    def on_muse_event(self, evt: dict, now: float) -> list[Effect]   # raw Muse realtime event dicts (PRD 11.1)
    def on_vad(self, active: bool, rms: float, now: float, level_db: float | None = None,
               quiet_ms: int | None = None) -> list[Effect]   # level_db: speech-band dBFS (missing: 20 log10 rms)
    def on_tts_state(self, crumb_id: str, playing: bool, now: float) -> list[Effect]   # crumb ids starting "sys_" are system lines (calibration): bot speech, but NOT crumbs
    def on_control(self, action: str, now: float) -> list[Effect]   # spark | skip | hold | resume | pause_listening | resume_listening
    def tick(self, now: float) -> list[Effect]                       # 10 Hz; implements PRD 8.7 exactly

    # async results fed back by the runner
    def apply_hold_decision(self, turn_id: int, d: HoldDecision, now: float) -> list[Effect]
    def apply_energy_tag(self, tag: EnergyTag, now: float) -> list[Effect]
    def crumb_dispatched(self, crumb: Crumb, now: float, latency_ms: float, cache_hit: bool) -> None   # runner sent the crumb to the Table
    def crumb_failed(self, now: float) -> None                       # generation failed: clear pending_delivery
    def dispatch_blocked(self, now: float) -> bool                  # someone may be talking: an unreleased open Muse turn,
                                                                    # a VAD onset that still defers, or the settle after a background turn
    def dispatch_timed_out(self, now: float, waited_s: float | None = None) -> list[Effect]
        # the runner held a generated crumb DISPATCH_WAIT_S (2 s) and dropped it: like crumb_cancelled (still-wanted
        # requests re-queued, an automatic one leaves the rate cap) plus decision "dispatch_blocked"

    # read-only views for the runner / crumb context
    def snapshot(self, now: float) -> dict        # EngineSnapshot (types.ts) minus `cached` (runner fills it)
                                                  # round 2: `vad_active` stays the raw local VAD; `noise_suspected`
                                                  # = raw VAD active, no longer deferring and not a near voice
                                                  # round 3: `defer` ("turn"|"vad"|"settle"|None), `foreground_open`,
                                                  # `seat_level_db` (None until 2 seat turns), `background_turns`;
                                                  # metrics add `endpoint_lag_ms_p50/p95` (numbers only)
    def recent_transcript(self, now: float, seconds: float = 120) -> str   # "Maya: ...\nDev: ..." (human seat turns only, mapped seats; never background)
    @property current_topic -> str | None         # from the latest EnergyTag
    def depth_max(self, now: float) -> int        # min(MAX_DEPTH[mode], 1 + floor(minutes_live / RAMP_MINUTES[mode]))
    def minutes_live(self, now: float) -> float
    @property phase -> Literal["calibrating", "live", "ended"]
    @property safety_paused -> bool               # a distress phrase paused automatic crumbs (PRD 15.3)
    @property round_over -> bool                  # the round timer ran out: nothing is worth preparing
    def round_left_s(self, now: float) -> float | None   # None: no round timer for this mode
    # usage: what is worth preparing (no I/O)
    def next_auto_crumb(self, now: float) -> tuple[CrumbKind, float] | None   # (kind, seconds until it fires) if nobody speaks
    def steer_wanted(self, now: float) -> bool    # a steer could fire in the pause that starts now
    def auto_blocked_for(self, now: float) -> float   # seconds automatic crumbs stay blocked (inf: safety pause)
    def hold_check_failed(self, turn_id: int) -> None # the deferred classifier call failed: crumbs stop waiting for it
    @property turns -> list[Turn]
    def metrics(self) -> dict                     # EngineMetrics
```

`Effect` is a plain dict with a `type`. The runner executes effects; the engine never does I/O.

| Effect `type` | Fields | Runner does |
|---|---|---|
| `partial` | `text`, `label` | broadcast `partial` to the Table |
| `turn` | `turn` (Turn as dict + `hold`) | broadcast `turn` |
| `calibration` | `step`, `label_map` | broadcast `calibration` |
| `say` | `id` (`sys_...`), `text` | TTS it and send `system_say` (calibration prompts) |
| `deliver` | `kind` (`opener`/`steer`/`switch`/`manual`/`wildcard`), `reason` | get a crumb from CrumbService; while `dispatch_blocked(now)` hold it (poll 0.1 s, up to `DISPATCH_WAIT_S` 2 s, re-checking `pending_cancelled`), then send `crumb` and call `crumb_dispatched` (or `crumb_failed`); on the timeout call `dispatch_timed_out` and `CrumbService.restore(delivery)` |
| `barge_in` | `crumb_id` | broadcast `crumb_interrupted` (Table fades out within 250 ms) |
| `skip` | `crumb_id` or None | broadcast `crumb_interrupted` for the current crumb; engine also emits a `deliver` switch |
| `classify_hold` | `turn_id`, `context` | call `classify_hold(context)` then `apply_hold_decision` (on failure or timeout: `hold_check_failed`). A lexical hit is asked at once; an ambiguous fragment or vetoed phrase only after `HOLD_CHECK_SILENCE_S` (0.8 s) of silence, and automatic crumbs then wait for the answer up to `T_LONG + HOLD_CHECK_SILENCE_S` (3.8 s) |
| `tag_energy` | `context` | call `tag_energy(context)` then `apply_energy_tag` |
| `prefetch` | `reason`, optional `voice` (`steer`/`switch`), `due_in_s` | refresh the TEXT slots that need it (`slots_to_refresh`): at once for a dispatch, topic change, warming band, or a committed turn that opens a pause a steer could fill (`steer_wanted`; that steer must follow this turn, `fresh=True`); other committed turns debounce 1.5 s; a 30 s check runs only when a slot needs text. With `voice`, also call `CrumbService.voice(kind, convo, now, due_in_s)`: the pause entered the warming band and that one crumb fires in `due_in_s` |
| `decision` | `decision` (Decision dict) | broadcast `decision` (X-ray log) |
| `round_warning` | | deliver a 1-minute-left crumb (speed dating, F21) |
| `round_end` | | end the session (as if End was pressed) |
| `live` | | calibration finished; phase becomes `live` (runner updates the session phase and broadcasts `session`) |

Engine rules, all from the PRD: pause timer (8.3), thresholds and bands (8.4, adaptive optional), staleness (8.5), holds (8.6 + Appendix A lexicon + negatives; check partials too; a hold needs the phrase in the last 8 words AND silence afterwards), policy (8.7 pseudocode, verbatim semantics), cooldowns (8.8), steer-after-steer upgrade (8.7), warm-up 20 s after the opener, max 6 auto crumbs per rolling 10 min, `crumbs_this_silence`, quiet after 2 unanswered, self-hearing guard (8.11: a Muse turn overlapping a bot-speaking interval, TTS + 300 ms tail, by more than 50% is `dropped_as_bot`), VAD barge-in ignored for the first 300 ms of playback, calibration (6.5: seat1 prompt → first new label = seat1, seat2 prompt → next new label = seat2, 15 s fallback maps the first two distinct labels by order of appearance), safety lexicon (15.3: distress phrases → `auto_paused` for the session), manual Hold = `show` hold for 60 s, Spark = immediate context switch that ignores cooldowns but **never while someone is talking** (queue it until silence), Skip = interrupt the current crumb + deliver a different switch, pause_listening stops the pause timer and all automatic crumbs. Key all turn state on `turnId`, never arrival order. A stuck turn (speechStart with no speechEnd for 30 s) is force-closed. The first deliver after calibration is the opener (`kind="opener"`). Wildcard (F22) only when `params.WILDCARD_ENABLED`.

Timestamps: the engine stamps turn start/end with `now` on arrival of `speechStart` / `speechEnd`.

**"Just talk" (`setup="quick"`).** `start_calibration` goes live at once: `_live_at` = now, the 20 s warm-up runs from
there, and no opener is requested. `Calibration` runs step `"auto"`: each new label of a turn judged a **seat** takes
the next free seat, by order of first appearance (a dev turn's speechStart may carry `"seat"`: its label takes that seat
when free). While the map is incomplete, `classify_turn(..., ordering=True)` replaces the "ungated" rule: a mapped label
follows the seat-label rules; a new label is a seat only when near (or with no VAD evidence and 2+ words), else
background (`far voice before mapping` / `new voice, not near`), so a bystander never becomes Person 2. Once both are
mapped, later labels follow the normal foreground rules. Automatic crumbs never wait for the map (`seats_unmapped` is
False). **Icebreaker:** while no seat turn has closed since go-live, `T_LONG_S` of silence (and `_can_deliver_now`)
delivers `kind="opener"` with reason `"Nobody has spoken yet: a light icebreaker"` (no cooldowns, like the opener). Speech
cancels a generating icebreaker without re-queueing it (it re-arms if no seat turn closed); any delivered crumb or seat
turn disarms it. A Muse reconnect re-maps labels the same way (decision only, no prompts, no pause). Mapping decisions
are `calibration` decisions with `step` and `label_map`; the Table gets no `calibration` events in this setup. The energy tag
(which also feeds the live memory) keeps running under the rate cap in this setup, every `2 × ENERGY_EVERY_N_TURNS` turns with
every turn since the last tag as context (8 to 16 turns); with profiles it is skipped while automatic crumbs stay blocked 60 s
or more. Both setups skip it during the safety pause. Warming-band voicing (both setups) is skipped only while a blocking turn is open or a near voice
is on; a pending hold answer or far VAD no longer stops it.

**Foreground and the pause (round 3, background-noise immunity and the 3 s crumb).** Thresholds are `T_NORMAL_S` 1.5 / `T_LONG_S` 3.0 / `T_EXC_S` 4.5 (think hold: 6.0 / 9.0); the follow-up after an unanswered crumb waits `max(t_exc, UNANSWERED_SWITCH_S` 6.0, doubled under a think hold); the wildcard window is `[max(t_normal, t_long - 1), t_long)`.
1. The **pause anchor** is the end of the last **seat-classified** human turn, or of bot speech, a crumb dispatch, a hold timeout or a resume. Local VAD frames never move it.
2. Every non-echo Muse turn is `fg="pending"` while open. A pending turn blocks firing (`turn_active`) but does not cancel a crumb being generated, release holds, reset `crumbs_this_silence`, clear the noise latch or zero `silence_s`. The exception is a turn that starts while the Table VAD hears a near voice (`near_at_start`), or a dev turn: it acts at once, as before (including the echo-verdict paths).
3. At speechEnd the turn is classified once (`engine.state.classify_turn`) from its Muse label (mapped or not), its words and the Table VAD over it (`VadLog.stats` from the first active frame in the 1 s before Muse's start: coverage, the 80th-percentile level, the voice end from `quiet_ms`), against the seated level (`SeatLevel`, an EMA seeded by the calibration turns). Rules, in order: dev → seat; gating off or calibration incomplete → seat; 0 words → background (upgradable); mapped label: far → background, a 1-word filler not near → background (upgradable), else seat; unmapped label: near with `DRIFT_MIN_WORDS`+ words (default 1: a drifted seat's short reply still restarts the pause) and 50%+ coverage → seat, no VAD evidence with no label and 2+ words → seat, else background. Near = coverage ≥ `NEAR_ACTIVE_FRAC` and (unknown seated level, or level ≥ seat − `NEAR_MARGIN_DB`); far = coverage < `FAR_ACTIVE_FRAC`, or level < seat − `FAR_MARGIN_DB`.
4. A **seat** close counts as human activity from the turn's start (a crumb decided after that start is cancelled), clears the noise latch, feeds the seated level and resets the anchor to the end of voice: `speechEnd` if a near voice is still on, else the client-measured voice end (with noise still on: the last frame at the seated level), never more than `ENDPOINT_BACKDATE_MAX_S` (1.2 s) before speechEnd. A **background** close changes nothing (no reset, no activity, no hold, no label, no `_live_turns`, no prefetch, no staleness, no energy tag; the distress check still runs) except a `BACKGROUND_SETTLE_S` (0.4 s) settle, and emits decision `background_turn` with numbers only (`turn_id, duration_s, words, active_frac, level_db, seat_db, label_mapped, cause`). An upgradable background turn is re-judged on its final transcript.
5. A VAD onset **defers** firing for `VAD_DEFER_S` (1.5 s, bursts share one window), or up to `VAD_NEAR_MAX_S` (4.0 s) while it sounds near; it never resets the anchor. A stretch that outlives `VAD_DEFER_S` with no Muse turn open latches the room as noisy (later onsets stop deferring) until a seat turn closes or the VAD rests 4 s; the latch records the noise floor (the level of that defer window). In a latched room, a stretch at the floor with no Muse turn stops counting as near `VAD_NEAR_MAX_S` after its onset or the last seat turn (so steady noise close to the seats' level cannot freeze the pause or barge in), while a voice `NOISE_RISE_DB` (6 dB) above the floor is near; before the seated level is known ("Just talk" before two seat turns) that rise is the only near test. Only a near voice cancels a crumb being prepared or barges in on a playing one.
6. **Open-turn release:** a pending turn open for `OPEN_TURN_MIN_S` (2.5 s) stops blocking once the Table VAD has had no near voice for `OPEN_TURN_RELEASE_S` (2 s) (VAD evidence required: during bot speech the Table holds frames back, so nothing is released then).
7. `silence_s` reads 0 while the bot plays, a `near_at_start` turn is open or a near voice is on; it keeps growing through pending bystander turns and far noise. `BYSTANDER_GATING=False` treats every turn as a seat (the old behaviour, minus the VAD reset).

`sim/replay.py` scripts may set `"setup": "quick"` (use with `"skip_warmup": false`: script time 0 = go-live) and `"params"` (Params overrides, see section 7).
`sim/replay.py`: `run_script(script: dict, params: Params | None = None) -> SimResult` feeds a script (PRD 16.1 JSON format) into `Engine` on a fake clock at 10 Hz, emitting Muse-shaped events (`speechStart`, `transcript`, `speaker`, `speechEnd`, `speechComplete`) and VAD frames, and simulates TTS playback (a delivered crumb plays for `script.get("tts_seconds", 3.0)` s, with `tts_state` start/end). The sim auto-completes calibration (labels A→seat 1, B→seat 2) and skips the opener warm-up unless the script says otherwise. `SimResult.actions` = list of `{t, action, kind, reason}`. Script keys: `name`, `mode`, `turns[{seat, text, start, end}]`, `expect[{between:[a,b], action: "none"|"steer"|"switch"|..., count?}]`, optional `duration`, `prior_crumb_at`, `hold_results`, `tts_seconds`.

## 2. AI services (workstream C): `server/ai/*`

```python
# ai/gemini.py
class Gemini:
    mock: bool
    async def json(self, *, model: str, system: str, payload: dict | str, schema: type[BaseModel],
                   max_tokens: int = 400, thinking_level: str = "low", job: str | None = None) -> BaseModel
        # job: "crumb" (CrumbDraft) and "hold" (HoldDecision) ask Claude for fast mode (config.CLAUDE_FAST_JOBS):
        # speed="fast" + beta fast-mode-2026-02-01 next to the fallback beta; a 429 retries once at standard
        # speed, a 400/403 about speed turns it off for the process. Other jobs are byte-identical.
    async def text(self, *, model: str, input: str, system: str | None = None, max_tokens: int = 300) -> str
    async def grounded(self, prompt: str) -> tuple[str, str | None, str | None]   # (text, url, title)
    async def tts(self, text: str) -> bytes | None                                # WAV bytes (24 kHz mono 16-bit)
def get_gemini() -> Gemini        # process-wide singleton
```
Uses `genai.Client().aio.interactions.create(...)` with the exact shapes in PRD 11.3 (verified against google-genai 2.33.0: `response_format={"type":"text","mime_type":"application/json","schema":...}`, `generation_config={"thinking_level":"low","max_output_tokens":N}`, grounding `tools=[{"type":"google_search"}]` with citations at `interaction.steps[*].content[*].annotations[*]` where `type=="url_citation"` has `url`, `title`; TTS `response_format={"type":"audio","mime_type":"audio/wav"}` and `generation_config={"speech_config":[{"voice":"Kore"}]}`, audio at `interaction.output_audio.data` base64; if its mime is `audio/l16` wrap the raw PCM in a WAV header). Never send temperature/top_p/top_k. Retries with backoff on 429/5xx/timeouts (2 retries). Optional fallback to Muse Spark (`https://api.meta.ai/v1/chat/completions`, OpenAI-compatible, model `muse-spark-1.3`, bearer `MODEL_API_KEY`) for `json`/`text` when Gemini fails and a Meta key exists. TTS falls back to `gemini-3.8-flash-lite-tts`, then returns None (the Table then uses browser speech synthesis).

```python
# ai/prompts.py: every system instruction from PRD Section 9 verbatim + canned crumbs (Appendix B) + mode tone
# ai/profiles.py
async def extract_profile(seat: int, mode: Mode, display_name: str, answers: list[str]) -> Profile
def allowed_items(profile: Profile) -> list[ProfileItem]          # minus removed ids and off-limits keyword matches
async def find_overlaps(p1: Profile, p2: Profile, mode: Mode) -> OverlapSet   # validated: item ids exist and are allowed; openers pass validate_crumb_text

# ai/classifiers.py
async def classify_hold(context: str) -> HoldDecision               # gemini-3.5-flash-lite
async def tag_energy(context: str) -> EnergyTag                     # gemini-3.5-flash-lite
async def tag_energy_memory(context: str, gemini=None, *, names: dict[int, str], known: dict | None = None,
                            off_limits: list[str] | None = None) -> tuple[EnergyTag, MemoryDelta]
    # ONE call (schema EnergyMemoryDraft: topic, energy, winding_down, person1, person2, shared, threads; system
    # ENERGY_SYSTEM + MEMORY_SYSTEM; payload {names, known, off_limits, turns}). The runner uses this for every
    # periodic energy tag, so the live memory costs no extra call. Mock / failure: mock_energy + mock_memory.

# ai/memory.py (the live memory: learned from the conversation, wiped with the session, never logged)
MEMORY_KEYS = ("1", "2", "shared", "threads")      # wire shape {"1": [...], "2": [...], "shared": [...], "threads": [...]}
class MemoryDelta(BaseModel): person1, person2, shared, threads: list[str]
class Memory: merge(delta, *, blocked=()) -> bool   # clean, safety + off-limits filter, de-dup by stems, caps 8/8/6/6
              to_dict(), items(key), empty, count(), clear(), drop(item)
def mock_memory(context: str, names: dict[int, str]) -> MemoryDelta   # self-statements, shared words, latest questions
def prompt_view(memory, names, per_list=3) -> dict | None            # newest 3 per list, keyed by name ("both", "open_threads")

# ai/facts.py
@dataclass class Fact: text: str; url: str; title: str
async def get_fact(topic: str | None, location: str) -> Fact | None  # None if no citation (never an ungrounded fact); mock returns None

# ai/tts.py  (round 2: ElevenLabs, then Gemini TTS, then None so the Table uses the browser voice)
async def synthesize(text: str, gemini: Gemini | None = None, *, fast: bool = True,
                     elevenlabs: ElevenLabs | None = None, budget_s: float | None = None) -> bytes | None   # WAV bytes or None
# fast=True: live lines (misses, opener, calibration) on eleven_flash_v2_5; fast=False: lazy voicing of the next
# crumb with at least 1.8 s of lead (eleven_v4, then multilingual_v2, then flash). budget_s is the caller's whole time budget; the chain splits it across routes.
# ai/elevenlabs.py
def get_elevenlabs() -> ElevenLabs   # process-wide; keyless (unavailable) under pytest unless BREADCRUMBS_ELEVENLABS_LIVE_TESTS=1
class ElevenLabs: available: bool; async synthesize(text, *, fast, budget_s=None) -> bytes | None; async warm() -> bool

# ai/trail.py
async def generate_trail(mode: Mode, names: dict[int, str], transcript: str, profiles: dict[int, Profile], gemini=None, *,
                         safety_paused=False, memory=None, anonymous=frozenset(), table=False) -> Trail   # <= 3 items per seat
    # table=True ("Just talk"): shown on the Table, built from memory + transcript; anonymous seats' labels never written
    # safety_paused=True: the memory is emptied first (nothing it learned reaches the trail); a quick table's trail is then empty

# ai/crumbs.py
@dataclass
class ConvoCtx:
    recent_transcript: str
    current_topic: str | None
    depth_max: int
    minutes_live: float
    instruction: str | None = None      # e.g. "one minute left in the round"
    memory: dict[str, list[str]] | None = None   # the live memory (payload "memory" = prompt_view, only when non-empty)

@dataclass
class Delivery:
    crumb: Crumb
    audio_wav: bytes | None
    cache_hit: bool
    gen_ms: float

def validate_crumb(crumb: Crumb, *, names: dict[int, str], profiles: dict[int, Profile], max_words: int = 28,
                   require_name: bool = True, placeholders: list[str] = ()) -> tuple[bool, Crumb, list[str]]
    # word count, at least one name present, no removed item ids, no off-limits terms (keyword check), em dashes -> commas
    # require_name=False and placeholders=["Person 1", ...] for people who typed no name: the labels are never spoken

class CrumbService:     # one per session; owns crumb history and the prefetch cache
    def __init__(self, *, mode: Mode, names: dict[int, str], profiles: dict[int, Profile], overlaps: OverlapSet, location: str,
                 gemini: Gemini | None = None, setup: Setup = "profiles", anonymous: frozenset[int] = frozenset()): ...
    # setup "quick": opener() is the fixed icebreaker (prompts.ICEBREAKERS, no model call, voiced via the process-wide
    # SYSTEM_LINES cache); switches alternate a memory item (newest open thread, then the less-featured person, then
    # shared; source "conversation", payload requested_memory) with an outside question; payload adds setup "just talk".
    # anonymous seats: payload names_unknown, crumb_system(anonymous=True), anonymous canned/mock templates.
    used_memory: set[str]                       # memory items a delivered crumb picked up (lowercase)
    used_topics: list[str]; used_item_ids: set[str]; featured_count: dict[int, int]; history: list[Crumb]
    async def opener(self, convo: ConvoCtx) -> Delivery                       # best overlap opener (F11)
    async def get(self, kind: CrumbKind, convo: ConvoCtx, now: float) -> Delivery   # steer/switch/manual/wildcard; uses cache when valid
        # a miss whose slot is being prefetched joins it, inside the same 2 s miss budget as the live call (F8);
        # a STEER miss joins only if its prefetch is due within MISS_JOIN_S (by the recent draft median), else it
        # serves at once the stale steer (<= 3 lines old), then the ready switch (kind "switch", cache_hit), then the fallback
    def restore(self, delivery: Delivery) -> None    # a cache-hit crumb the runner dropped (dispatch timeout) goes back to its empty slot
    async def prefetch(self, convo: ConvoCtx, now: float, *, steer: bool = True, fresh: bool = False,
                       slots: tuple[str, ...] = ("steer", "switch")) -> None   # keep ready_steer + ready_switch TEXT fresh (F14)
        # only missing/invalid/stale slots, never one slot twice at once; steer=False keeps a stale steer;
        # fresh=True rebuilds a steer not built on the latest line (queued behind a running one, never parallel)
    def slots_to_refresh(self, convo, now, *, steer=True, fresh=False, slots=...) -> list[str]   # what prefetch would make (no call)
    async def voice(self, kind: CrumbKind, convo: ConvoCtx, now: float, lead_s: float) -> None
        # voice the next crumb once: eleven_v4 with enough lead, else Flash; a late natural take is cancelled for Flash;
        # a fresher steer being written is waited for until VOICE_FRESH_RESERVE_S (0.6 s) before the deadline; the steer
        # join never runs past the deadline, and on a miss it voices what get() will serve (the stale steer or the ready switch)
    def invalidate(self, reason: str) -> None
    def close(self) -> None                                                   # session over: cancel work, drop cached text and audio
    def cached_summary(self, now: float) -> dict                              # {"steer": text | None, "switch": text | None}
    def mark_delivered(self, crumb: Crumb) -> None                            # used topics/items, featured_count, overlap usage, source rotation
```
Crumb ids: `c_<n>`. Source rotation and steer preferences per PRD 8.9. Allowed formats per mode: `config.ALLOWED_FORMATS`. Outside + fun_fact uses the two-step grounded flow (9.5). Validation failure → regenerate once → best unused overlap opener → canned crumb (Appendix B). Cache entries are invalid when older than 90 s, the topic changed, or their items were used. Prompt payload shape = PRD 9.4 (only allowed items, never removed ones).

## 3. Muse + audio (workstream A)

```python
# muse/realtime.py
class MuseRealtimeClient:
    def __init__(self, on_event: Callable[[dict], Awaitable[None]], keywords: list[str],
                 on_status: Callable[[str, str], Awaitable[None]] | None = None): ...
    async def run(self) -> None      # connect + handshake (PRD 11.1) + sender/receiver; reconnect with backoff 0.5/1/2 s on 1011/1013 and network errors; on_status("reconnecting"|"connected"|"resyncing"|"closed"|"error", detail). After a reconnect it calls on_status("resyncing", ...) so the runner calls engine.on_muse_reconnect.
    def push_audio(self, frame: bytes) -> None   # drop-oldest queue (maxsize 40)
    async def close(self) -> None    # sends {"type":"endStream"}, then closes
    connected: bool
class MockMuseRealtime(same interface)   # used when MUSE_MOCK: energy-based endpointing on the PCM; emits speechStart/transcript/speaker/speechEnd/speechComplete with text "(mock speech, 2.1 s)" and labels alternating A, B per turn
def make_realtime_client(...) -> MuseRealtimeClient | MockMuseRealtime

# muse/transcribe.py
async def transcribe_wav(wav_bytes: bytes, *, mock_key: str = "") -> str   # PRD 11.2; mock returns a canned answer keyed by mock_key (e.g. "social:q1:seat1")

# audio/convert.py
def ffmpeg_available() -> bool
class ConversionError(Exception)
async def to_wav_24k(data: bytes, filename_hint: str) -> bytes   # temp dir, ffmpeg -ac 1 -ar 24000 -c:a pcm_s16le -map_metadata -1, files deleted immediately
```

Browser side (TypeScript, consumed by workstream D):
```ts
// web/public/pcm-worklet.js: PRD 14.1 verbatim (80 ms Int16 frames + RMS every 100 ms)
// web/src/audio/capture.ts
export interface CaptureHandle { stop(): void; setPaused(paused: boolean): void; readonly ctx: AudioContext }
export async function startCapture(opts: {
  onFrame: (pcm: ArrayBuffer) => void;            // 3,840 bytes per 80 ms
  onVad: (active: boolean, rms: number, reading?: VadReading) => void;  // 10 Hz
}): Promise<CaptureHandle>
// round 2: speech-aware VAD (1.5 s room calibration, adaptive floor, SNR hysteresis 10/5 dB, voice checks, 200 ms attack / 500 ms release)
export interface VadReading { active: boolean; rms: number; levelDb: number; floorDb: number; snrDb: number; voiced: boolean; calibrating: boolean;
                             quietMs: number }   // round 3: ms since the last onset/sustain frame (0 while sustaining)
// web/src/audio/player.ts
export class CrumbPlayer {
  constructor(ctx?: AudioContext)
  playChime(): Promise<void>
  play(id: string, audioB64: string, text: string, cb: { onStart(): void; onEnd(interrupted: boolean): void }): Promise<void>   // empty audioB64 → window.speechSynthesis fallback
  fadeOut(ms?: number): void          // 250 ms default (barge-in)
  get playing(): boolean
  get playingSinceMs(): number | null // for the 300 ms VAD grace
  get outputKind(): "audio" | "speech" | "silent" | null   // round 2, for echo-aware barge-in
  outputLevel(): number | null        // RMS 0..1 of WebAudio output; null for browser speech
}
// web/src/components/table/playback.ts (round 2: echo-aware barge-in, replaces forward/shouldFade)
export class PlaybackGate { observe(sample: { active: boolean; levelDb: number; voiced?: boolean }, now: number): { send: boolean; fade: boolean }; get currentId(): string | null }
// web/src/audio/recorder.ts
export interface RecorderHandle { stop(): Promise<Blob>; cancel(): void; readonly analyser: AnalyserNode; readonly mimeType: string }
export async function startRecording(): Promise<RecorderHandle>
export function extForMime(mime: string): string    // "webm" | "mp4" | "ogg" | "wav"
```

## 4. Session store, runner, API (integration)

`session_store.py` (in-memory dict, TTL sweeper every 60 s, `wipe(sid)`), `engine/runner.py` (per session: 10 Hz tick task, 4 Hz `engine` broadcast, prefetch debounce + 30 s refresh, effect executor, Muse client lifecycle bound to `/ws/audio`), `main.py` (REST from PRD 13 + `/ws/audio/{sid}` + `/ws/events/{sid}?role=table|seat1|seat2` + static `web/dist` with SPA fallback for `/join/...` and `/table/...`).

Extra REST beyond PRD 13: `GET /api/health`, `GET /api/config` (AppConfig), `GET /api/sessions/{sid}/seats/{seat}/profile`, `PATCH /api/sessions/{sid}/params` (live tuning), `GET /api/sessions/{sid}/metrics` (aggregate only, no text).

Phase transitions (profiles): `lobby` (created) → `profiles` (first seat consents) → `calibrating` (both ready: overlaps computed, engine created, calibration starts when the Table audio socket is connected) → `live` (calibration done, opener delivered) → `ended` (End, round end) → wiped after both phones fetched the trail, `DELETE`, or the 2 h TTL.

Phase transitions ("Just talk", `setup="quick"`): `lobby` → `calibrating` (POST `/start`: consent for both seats from the Table, engine with empty profiles) → `live` (the first Table audio frame, or a dev turn; no prompts) → `ended` (End) → the Table trail is generated and sent → wiped on `DELETE` (the Done button), `QUICK_WIPE_AFTER_END_S` (600 s) after the end, or the TTL.

Routes added for the two setups:
- `POST /api/sessions` body `{setup?: "quick"|"profiles" (default "quick"), mode?: Mode (default "social"), quick?: bool (profiles only)}` → `{session_id, join_urls, setup}`. `join_urls` is `{}` for quick.
- `POST /api/sessions/{sid}/start` body `{names?: {"1"?: str, "2"?: str}}` (quick only; 409 for profiles or once started): consent for both seats (`Consent.source = "table"`, timestamped), names (blank → `"Person 1"` / `"Person 2"`, `SeatState.anonymous`), both ready, runner started, phase `calibrating`. `/ws/audio` gating (both consented) is unchanged.
- `GET /api/sessions/{sid}/trail` (quick only, after the end; 409 otherwise) → `{trail: Trail | null}` (null while generating).
- Every seat route (`/seats/{seat}/...`) answers 409 "This table doesn't need phones..." for a quick session; seat routes and cookies are unchanged for profiles.

Consent: every audio endpoint (`answers`, `/ws/audio`) checks consent; `/ws/audio` requires BOTH seats consented.

## 5. WebSocket protocol

PRD 12.1, with these additions (all typed in `web/src/lib/types.ts`):
- Every server event is `{"type": ..., ...payload}`. `session` wraps the summary as `{"type":"session","session":{...}}`; `engine` as `{"type":"engine","engine":{...}}`; `turn` as `{"type":"turn","turn":{...}}`.
- Server → Table: `system_say {id, text, audio_b64}`, `decision {decision}`, `params {params}`, `muse_status {status, detail}`.
- Server → all: `wiped {}`.
- `crumb` carries `cache_hit` and `latency_ms`; `audio_b64` may be `""` (mock / TTS failure) → the Table speaks with `speechSynthesis`.
- Client → server: `params {changes}` (X-ray tuning), `dev_turn {seat, text, duration_s}` (only when `DEV_TOOLS`; the runner turns it into Muse-shaped events with labels A/B per the current label map, each carrying `"dev": true`: always a seat, no VAD needed).
- Client → server `vad` (10 Hz): `{type: "vad", active, rms, level_db?, quiet_ms?}`. `level_db` is the speech-band level in dBFS rounded to 0.1 (`VadReading.levelDb`); `quiet_ms` is integer ms since the last voiced or sustain frame, capped at 60000, 0 while sustaining (`VadReading.quietMs`). Both optional: older Tables send `{active, rms}` and the server falls back to `20 log10(rms)` and the last active frame. Built by `web/src/lib/vadMessage.ts`; no other fields (no pitch, no text).
- `turn` events carry `background` (a voice away from the table, greyed in the X-ray). Decisions add `background_turn` and `dispatch_blocked`.
- `session` summaries carry `setup` (`"quick"` | `"profiles"`).
- Server → Table: `memory {memory}` whenever the live memory changes (`{"1": [], "2": [], "shared": [], "threads": []}`, short phrases); the `engine` snapshot also carries `memory` (X-ray). Both setups.
- Server → Table (quick only): `table_trail {trail}` once the trail is ready after End (also re-sent to a Table that connects later, until the wipe).
- A seat socket (`role=seat1|seat2`) on a quick session only receives the `session` summary (the phone shows "This table doesn't need phones"); it does not mark the seat joined.
- `dev_turn` speechStart events carry `"seat"` (quick: the label takes that seat when free).
- Seats connect with `role=seat1|seat2`; joining marks the seat `joined`.
- Close codes: `4404` unknown or wiped session, `4403` forbidden (e.g. `/ws/audio` before both seats consented). Clients do not reconnect on these (`web/src/lib/ws.ts`).
- On connect, the server immediately sends `session` (and to the Table: the latest `engine` snapshot and `params`).

## 6. Frontend (workstreams D1 Table, D2 Phone + Home)

Routes: `/` Home (one orb, setup segmented control "Just talk" (default) / "With profiles" (+ Quick / Full profile), "Start a conversation", status pills; no mode picker, no side orbs or carousel) → `/table/:sid` (quick: consent card with two optional names and "We're both in" (POST `/start`; the mic opens in the same click, and nothing is sent until the server confirms consent) → a short "starting" screen (Start listening only if the mic did not start) → live → the Table trail with "Done" (DELETE); profiles: QR codes for both seats until both ready, then calibration → live Orb UI, controls, X-ray drawer, crumb trail dots) and `/join/:sid/:seat` (profiles: consent → interview → profile review → waiting/"phones face down" → keep-in-touch opt-in → trail; quick: "This table doesn't need phones"). Visual design: PRD 14.3 tokens. Uses `lib/api.ts` (typed REST), `lib/ws.ts` (auto-reconnecting typed events socket), the audio modules in section 3, `qrcode.react`, `framer-motion`, Tailwind v4 (`@import "tailwindcss";` in `src/index.css`, theme tokens via `@theme`).

## 7. Tests (each workstream writes its own; all run from `server/`)

- B: `tests/test_hold.py`, `tests/test_staleness.py`, `tests/test_policy.py`, `tests/test_sim_scripts.py` (all 13 PRD 16.1 scripts in `sim/scripts/*.json`)
- C: `tests/test_validator.py`, `tests/test_ai_mock.py` (profiles/overlaps/crumbs/trail in mock mode, cache invalidation, source rotation)
- A: `tests/test_muse_realtime.py` (local fake Muse WS server: handshake shape, pacing, endStream, reconnect on 1011), `tests/test_transcribe.py` (httpx MockTransport), `tests/test_convert.py` (skips without ffmpeg)
- Integration: `tests/test_api.py` (full flow in mock mode with FastAPI TestClient, consent gating, wipe, TTL)
- Round 2: `tests/test_elevenlabs.py`, `tests/test_noise_echo.py`; sim adds 5 scripts beyond the 13 PRD ones:
  `self_echo_transcript`, `real_barge_in_still_works`, `background_noise`, `bursty_noise`, `echo_then_reply_same_turn`.
  Sim script keys added: `noise[{start, end}]` (local VAD with no Muse turn), `crumb_text`, per-turn `vad_from` (echo turns only).
- Round 4 ("Just talk"): `tests/test_quick_setup.py` (engine ordering + icebreaker, memory, quick crumbs, table trail, the
  full API flow, the 10-minute wipe, no memory text in logs, a safety pause keeping memory out of the trail); sim scripts
  `quick_icebreaker`, `quick_talk_right_away`, `quick_bystander_first`, `quick_noise_then_person`, `quick_burst_then_person`
  (script key `setup`); `sim.usage --setup quick|both`; `sim.smoke_e2e --setup quick|both`.
- Round 3 (foreground + 3 s): `tests/test_bystander.py`, `tests/test_runner_dispatch.py`; the 18 earlier scripts are pinned with
  `params` to their pre-3 s timings (3.0 / 5.0 / 6.5 s, `UNANSWERED_SWITCH_S` 6.5, `HOLD_CHECK_SILENCE_S` 1.0, `VAD_DEFER_S` 2.5);
  14 new scripts run on the defaults: `bystander_fragments`, `bystander_on_seat_label`, `seated_backchannel_resets`,
  `noise_defers_not_resets`, `muse_late_seat_start`, `split_turn_settle`, `long_bystander_turn_release`, `three_second_steer`,
  `natural_gaps_under_3s`, `unanswered_fast`, `endpoint_lag_backdate`, `endpoint_lag_cap`, `think_hold_fast`, `label_drift_near`.
  Review fixes added 4 more on the defaults: `near_level_noise`, `near_level_noise_throughout`, `tv_speech_mid_level`,
  `label_drift_short_reply` (41 scripts in all: 18 pinned, 18 on the 3 s defaults, 5 quick).
  Sim script keys added: `params` (Params overrides when the caller passes none), per-turn `level_db` (-30), `vad` (true),
  `muse_lag_s` (0), any `label`; per-noise `level_db` (-42). VAD frames carry `level_db` and `quiet_ms`; the sim holds a
  generated crumb while `dispatch_blocked`, like the runner.
- Web (run from `web/`): table `node --import ./src/components/table/__tests__/register.mjs --test 'src/components/table/__tests__/*.test.mjs'` (includes `vadMessage.test.mjs`);
  player `node --import ./src/components/table/__tests__/register.mjs --test 'src/audio/__tests__/*.test.mjs'`;
  phone `node --test src/components/phone/flow.test.mjs`.

## 8. File ownership (do not edit other workstreams' files)

| Owner | Files |
|---|---|
| lead | `server/models.py`, `server/config.py`, `web/src/lib/{types,api,ws}.ts`, `web/src/{main,App}.tsx`, `web/src/index.css`, `docs/CONTRACTS.md`, `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/index.html` |
| A | `server/muse/*`, `server/audio/*`, `web/public/pcm-worklet.js`, `web/src/audio/*`, A's tests |
| B | `server/engine/{state,staleness,hold,policy,core}.py`, `server/sim/*`, B's tests |
| C | `server/ai/*`, C's tests |
| D1 (Table) | `web/src/pages/Table.tsx`, `web/src/components/{CrumbOrb,Controls,XRay,CrumbTrail,SeatQr,Sparkline}.tsx`, `web/src/components/table/*`, `web/src/lib/useTable.ts` |
| D2 (Phone + Home) | `web/src/pages/{Home,Join}.tsx`, `web/src/components/{ConsentForm,ProfileChips,TrailCard,RecordButton,Waveform}.tsx`, `web/src/components/phone/*`, `web/src/components/ui/*` (shared primitives: Button, Card, Pill; D1 may import them) |
| integration | `server/main.py`, `server/session_store.py`, `server/engine/runner.py`, `tests/test_api.py`, `README.md` |

If you need a change in a file you don't own, write it in your BUILD_LOG entry under "Requests" instead of editing.

## 9. Build log entries

Append (never rewrite) with a shell heredoc so concurrent writers don't clobber each other:

```bash
cat >> /Users/omkherde/breadcrumbs/BUILD_LOG.md <<'EOF'

### 2026-10-24 · <workstream> · <title>
- ✅ <what was implemented> (`path/to/file.py`)
- ⚠️ <deviation from PRD and why>
- Tests: <command> → <result>
- Requests: <changes needed in files owned by others, or "none">
EOF
```
