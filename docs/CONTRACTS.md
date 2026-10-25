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
    def __init__(self, mode: Mode, names: dict[int, str], params: Params): ...

    # lifecycle
    def start_calibration(self, now: float) -> list[Effect]      # emits calibration step seat1 + a `say` prompt
    def on_muse_reconnect(self, now: float) -> list[Effect]      # labels reset: clear label map, pause auto crumbs, re-run calibration ("Re-syncing voices")
    def end(self, now: float) -> list[Effect]

    # inputs
    def on_muse_event(self, evt: dict, now: float) -> list[Effect]   # raw Muse realtime event dicts (PRD 11.1)
    def on_vad(self, active: bool, rms: float, now: float) -> list[Effect]
    def on_tts_state(self, crumb_id: str, playing: bool, now: float) -> list[Effect]   # crumb ids starting "sys_" are system lines (calibration): bot speech, but NOT crumbs
    def on_control(self, action: str, now: float) -> list[Effect]   # spark | skip | hold | resume | pause_listening | resume_listening
    def tick(self, now: float) -> list[Effect]                       # 10 Hz; implements PRD 8.7 exactly

    # async results fed back by the runner
    def apply_hold_decision(self, turn_id: int, d: HoldDecision, now: float) -> list[Effect]
    def apply_energy_tag(self, tag: EnergyTag, now: float) -> list[Effect]
    def crumb_dispatched(self, crumb: Crumb, now: float, latency_ms: float, cache_hit: bool) -> None   # runner sent the crumb to the Table
    def crumb_failed(self, now: float) -> None                       # generation failed: clear pending_delivery

    # read-only views for the runner / crumb context
    def snapshot(self, now: float) -> dict        # EngineSnapshot (types.ts) minus `cached` (runner fills it)
    def recent_transcript(self, now: float, seconds: float = 120) -> str   # "Maya: ...\nDev: ..." (human turns only, mapped seats)
    @property current_topic -> str | None         # from the latest EnergyTag
    def depth_max(self, now: float) -> int        # min(MAX_DEPTH[mode], 1 + floor(minutes_live / RAMP_MINUTES[mode]))
    def minutes_live(self, now: float) -> float
    @property phase -> Literal["calibrating", "live", "ended"]
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
| `deliver` | `kind` (`opener`/`steer`/`switch`/`manual`/`wildcard`), `reason` | get a crumb from CrumbService, send `crumb`, then call `crumb_dispatched` (or `crumb_failed`) |
| `barge_in` | `crumb_id` | broadcast `crumb_interrupted` (Table fades out within 250 ms) |
| `skip` | `crumb_id` or None | broadcast `crumb_interrupted` for the current crumb; engine also emits a `deliver` switch |
| `classify_hold` | `turn_id`, `context` | call `classify_hold(context)` then `apply_hold_decision` |
| `tag_energy` | `context` | call `tag_energy(context)` then `apply_energy_tag` |
| `prefetch` | `reason` | debounce 1.5 s, then refresh the CrumbService cache |
| `decision` | `decision` (Decision dict) | broadcast `decision` (X-ray log) |
| `round_warning` | | deliver a 1-minute-left crumb (speed dating, F21) |
| `round_end` | | end the session (as if End was pressed) |
| `live` | | calibration finished; phase becomes `live` (runner updates the session phase and broadcasts `session`) |

Engine rules, all from the PRD: pause timer (8.3), thresholds and bands (8.4, adaptive optional), staleness (8.5), holds (8.6 + Appendix A lexicon + negatives; check partials too; a hold needs the phrase in the last 8 words AND silence afterwards), policy (8.7 pseudocode, verbatim semantics), cooldowns (8.8), steer-after-steer upgrade (8.7), warm-up 20 s after the opener, max 6 auto crumbs per rolling 10 min, `crumbs_this_silence`, quiet after 2 unanswered, self-hearing guard (8.11: a Muse turn overlapping a bot-speaking interval, TTS + 300 ms tail, by more than 50% is `dropped_as_bot`), VAD barge-in ignored for the first 300 ms of playback, calibration (6.5: seat1 prompt → first new label = seat1, seat2 prompt → next new label = seat2, 15 s fallback maps the first two distinct labels by order of appearance), safety lexicon (15.3: distress phrases → `auto_paused` for the session), manual Hold = `show` hold for 60 s, Spark = immediate context switch that ignores cooldowns but **never while someone is talking** (queue it until silence), Skip = interrupt the current crumb + deliver a different switch, pause_listening stops the pause timer and all automatic crumbs. Key all turn state on `turnId`, never arrival order. A stuck turn (speechStart with no speechEnd for 30 s) is force-closed. The first deliver after calibration is the opener (`kind="opener"`). Wildcard (F22) only when `params.WILDCARD_ENABLED`.

Timestamps: the engine stamps turn start/end with `now` on arrival of `speechStart` / `speechEnd`.

`sim/replay.py`: `run_script(script: dict, params: Params | None = None) -> SimResult` feeds a script (PRD 16.1 JSON format) into `Engine` on a fake clock at 10 Hz, emitting Muse-shaped events (`speechStart`, `transcript`, `speaker`, `speechEnd`, `speechComplete`) and VAD frames, and simulates TTS playback (a delivered crumb plays for `script.get("tts_seconds", 3.0)` s, with `tts_state` start/end). The sim auto-completes calibration (labels A→seat 1, B→seat 2) and skips the opener warm-up unless the script says otherwise. `SimResult.actions` = list of `{t, action, kind, reason}`. Script keys: `name`, `mode`, `turns[{seat, text, start, end}]`, `expect[{between:[a,b], action: "none"|"steer"|"switch"|..., count?}]`, optional `duration`, `prior_crumb_at`, `hold_results`, `tts_seconds`.

## 2. AI services (workstream C): `server/ai/*`

```python
# ai/gemini.py
class Gemini:
    mock: bool
    async def json(self, *, model: str, system: str, payload: dict | str, schema: type[BaseModel],
                   max_tokens: int = 400, thinking_level: str = "low") -> BaseModel
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

# ai/facts.py
@dataclass class Fact: text: str; url: str; title: str
async def get_fact(topic: str | None, location: str) -> Fact | None  # None if no citation (never an ungrounded fact); mock returns None

# ai/tts.py
async def synthesize(text: str) -> bytes | None                      # WAV bytes or None

# ai/trail.py
async def generate_trail(mode: Mode, names: dict[int, str], transcript: str, profiles: dict[int, Profile]) -> Trail   # <= 3 items per seat

# ai/crumbs.py
@dataclass
class ConvoCtx:
    recent_transcript: str
    current_topic: str | None
    depth_max: int
    minutes_live: float
    instruction: str | None = None      # e.g. "one minute left in the round"

@dataclass
class Delivery:
    crumb: Crumb
    audio_wav: bytes | None
    cache_hit: bool
    gen_ms: float

def validate_crumb(crumb: Crumb, *, names: dict[int, str], profiles: dict[int, Profile], max_words: int = 28) -> tuple[bool, Crumb, list[str]]
    # word count, at least one name present, no removed item ids, no off-limits terms (keyword check), em dashes -> commas

class CrumbService:     # one per session; owns crumb history and the prefetch cache
    def __init__(self, *, mode: Mode, names: dict[int, str], profiles: dict[int, Profile], overlaps: OverlapSet, location: str, gemini: Gemini | None = None): ...
    used_topics: list[str]; used_item_ids: set[str]; featured_count: dict[int, int]; history: list[Crumb]
    async def opener(self, convo: ConvoCtx) -> Delivery                       # best overlap opener (F11)
    async def get(self, kind: CrumbKind, convo: ConvoCtx, now: float) -> Delivery   # steer/switch/manual/wildcard; uses cache when valid
    async def prefetch(self, convo: ConvoCtx, now: float) -> None             # keep ready_steer + ready_switch (text + audio) fresh
    def invalidate(self, reason: str) -> None
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
  onVad: (active: boolean, rms: number) => void;  // 10 Hz
}): Promise<CaptureHandle>
export class Vad { update(rms: number, nowMs: number): boolean; get active(): boolean; get noiseFloor(): number }   // PRD 14.1 local VAD
// web/src/audio/player.ts
export class CrumbPlayer {
  constructor(ctx?: AudioContext)
  playChime(): Promise<void>
  play(id: string, audioB64: string, text: string, cb: { onStart(): void; onEnd(interrupted: boolean): void }): Promise<void>   // empty audioB64 → window.speechSynthesis fallback
  fadeOut(ms?: number): void          // 250 ms default (barge-in)
  get playing(): boolean
  get playingSinceMs(): number | null // for the 300 ms VAD grace
}
// web/src/audio/recorder.ts
export interface RecorderHandle { stop(): Promise<Blob>; cancel(): void; readonly analyser: AnalyserNode; readonly mimeType: string }
export async function startRecording(): Promise<RecorderHandle>
export function extForMime(mime: string): string    // "webm" | "mp4" | "ogg" | "wav"
```

## 4. Session store, runner, API (integration)

`session_store.py` (in-memory dict, TTL sweeper every 60 s, `wipe(sid)`), `engine/runner.py` (per session: 10 Hz tick task, 4 Hz `engine` broadcast, prefetch debounce + 30 s refresh, effect executor, Muse client lifecycle bound to `/ws/audio`), `main.py` (REST from PRD 13 + `/ws/audio/{sid}` + `/ws/events/{sid}?role=table|seat1|seat2` + static `web/dist` with SPA fallback for `/join/...` and `/table/...`).

Extra REST beyond PRD 13: `GET /api/health`, `GET /api/config` (AppConfig), `GET /api/sessions/{sid}/seats/{seat}/profile`, `PATCH /api/sessions/{sid}/params` (live tuning), `GET /api/sessions/{sid}/metrics` (aggregate only, no text).

Phase transitions: `lobby` (created) → `profiles` (first seat consents) → `calibrating` (both ready: overlaps computed, engine created, calibration starts when the Table audio socket is connected) → `live` (calibration done, opener delivered) → `ended` (End, round end) → wiped after both phones fetched the trail, `DELETE`, or the 2 h TTL.

Consent: every audio endpoint (`answers`, `/ws/audio`) checks consent; `/ws/audio` requires BOTH seats consented.

## 5. WebSocket protocol

PRD 12.1, with these additions (all typed in `web/src/lib/types.ts`):
- Every server event is `{"type": ..., ...payload}`. `session` wraps the summary as `{"type":"session","session":{...}}`; `engine` as `{"type":"engine","engine":{...}}`; `turn` as `{"type":"turn","turn":{...}}`.
- Server → Table: `system_say {id, text, audio_b64}`, `decision {decision}`, `params {params}`, `muse_status {status, detail}`.
- Server → all: `wiped {}`.
- `crumb` carries `cache_hit` and `latency_ms`; `audio_b64` may be `""` (mock / TTS failure) → the Table speaks with `speechSynthesis`.
- Client → server: `params {changes}` (X-ray tuning), `dev_turn {seat, text, duration_s}` (only when `DEV_TOOLS`; the runner turns it into Muse-shaped events with labels A/B per the current label map).
- Seats connect with `role=seat1|seat2`; joining marks the seat `joined`.
- Close codes: `4404` unknown or wiped session, `4403` forbidden (e.g. `/ws/audio` before both seats consented). Clients do not reconnect on these (`web/src/lib/ws.ts`).
- On connect, the server immediately sends `session` (and to the Table: the latest `engine` snapshot and `params`).

## 6. Frontend (workstreams D1 Table, D2 Phone + Home)

Routes: `/` Home (create session, choose mode, quick-profile toggle) → `/table/:sid` (QR codes for both seats until both ready, then calibration → live Orb UI, controls, X-ray drawer, crumb trail dots) and `/join/:sid/:seat` (phone: consent → interview → profile review → waiting/"phones face down" → keep-in-touch opt-in → trail). Visual design: PRD 14.3 tokens. Uses `lib/api.ts` (typed REST), `lib/ws.ts` (auto-reconnecting typed events socket), the audio modules in section 3, `qrcode.react`, `framer-motion`, Tailwind v4 (`@import "tailwindcss";` in `src/index.css`, theme tokens via `@theme`).

## 7. Tests (each workstream writes its own; all run from `server/`)

- B: `tests/test_hold.py`, `tests/test_staleness.py`, `tests/test_policy.py`, `tests/test_sim_scripts.py` (all 13 PRD 16.1 scripts in `sim/scripts/*.json`)
- C: `tests/test_validator.py`, `tests/test_ai_mock.py` (profiles/overlaps/crumbs/trail in mock mode, cache invalidation, source rotation)
- A: `tests/test_muse_realtime.py` (local fake Muse WS server: handshake shape, pacing, endStream, reconnect on 1011), `tests/test_transcribe.py` (httpx MockTransport), `tests/test_convert.py` (skips without ffmpeg)
- Integration: `tests/test_api.py` (full flow in mock mode with FastAPI TestClient, consent gating, wipe, TTL)

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
