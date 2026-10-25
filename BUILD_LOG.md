# Breadcrumbs: Build Log

A running log of actions, decisions and implementations while building Breadcrumbs for HackTX 2026, built from `PRDHack1.md`.
Newest entries are at the bottom. Every entry has a timestamp, an author (`lead` or a workstream agent) and what changed.

**Status legend:** ✅ done · 🟡 in progress · ⛔ blocked · ⚠️ deviation from PRD (with reason)

---

## Environment snapshot (2026-10-24)

| Tool | Status |
|---|---|
| Python | 3.12.6 (venv at `server/.venv`) |
| Node / npm | v24.11.0 / 11.6.1 |
| google-genai | 2.33.0: `client.aio.interactions.create` present; `output_text`, `output_audio`, `steps[].content[].annotations[]` (`url_citation`: `url`, `title`) confirmed against SDK source |
| fastapi / uvicorn / websockets / httpx / pydantic | 0.162.0 / 0.54.0 / 17.3 / 0.28.1 / 2.14.1 |
| ffmpeg | ⛔ **not installed**: needed for phone answer conversion (`brew install ffmpeg`) |
| cloudflared | ⛔ **not installed**: needed for phone HTTPS (`brew install cloudflared`) |
| `MODEL_API_KEY`, `GEMINI_API_KEY` | ⛔ **not set**: the server runs in **mock mode** until they are added to `.env` |
| 21st.dev CLI | logged in (free tier: 2 component retrievals/day) |
| UI/UX skill | `ui-ux-pro-max` installed at `~/.claude/skills/` |

---

## Log

### 2026-10-24 · lead · Kickoff
- ✅ Read the full PRD (1,431 lines). Build spec = Sections 8 (engine), 9 (AI), 10 (stack/layout), 11 (APIs), 12 (models + WS protocol), 13 (REST), 14 (frontend), 15 (privacy), 16 (tests).
- ✅ Verified the Gemini Interactions API surface in the installed SDK before writing any AI code (PRD 17.3: "do not invent API fields").
- ✅ Created the repo (`git init`, branch `main`), directory layout per PRD 10.3, `.gitignore`, `.env.example`.
- ⚠️ Added **mock mode** (not in PRD): with no API keys, Muse and Gemini calls return deterministic canned data, so the full flow can be built and demoed now. Real calls switch on automatically when the keys are present.
- ⚠️ Added **dev turn injection** (gated by `DEV_TOOLS=true`): the X-ray panel can inject fake Muse turns to exercise the engine without a mic. This extends the PRD's simulator idea to the live UI.
- Plan: lead writes contracts (`models.py`, `config.py`, `types.ts`, `docs/CONTRACTS.md`) → 4 parallel workstreams (A audio+Muse, B engine, C AI, D frontend) → integration (main, session store, runner) → adversarial review → fixes.

### 2026-10-24 · lead · Contracts + scaffolding
- ✅ `server/models.py`: every PRD Section 12 model verbatim + `Turn` (8.2) + REST bodies (13).
- ✅ `server/config.py`: every PRD 8.12 default + env loading + a live-tunable `Params` dataclass (X-ray tuning, P1) + model names (9.1) + mode tables (4.2).
- ✅ `docs/CONTRACTS.md`: module interfaces (Engine effects API, CrumbService, Muse clients, browser audio modules), WS protocol additions, file ownership, test plan.
- ✅ Python venv (`server/.venv`): fastapi, uvicorn[standard], websockets, httpx, google-genai, pydantic v2, python-multipart, python-dotenv, pytest, pytest-asyncio.
- ✅ Web scaffold: Vite 8 + React 19 + TypeScript 7 + Tailwind v4 (`@tailwindcss/vite`) + qrcode.react + framer-motion + react-router-dom. `vite.config.ts` proxies `/api` and `/ws` (ws: true) to :8000.
- ✅ Shared frontend foundations: `lib/types.ts` (mirrors models + every WS event), `lib/api.ts` (typed REST), `lib/ws.ts` (auto-reconnecting event + audio sockets), `index.css` (PRD 14.3 color tokens via `@theme`), routes `/`, `/table/:sid`, `/join/:sid/:seat`.
- ⚠️ Engine is a pure facade (`engine/core.py`, not in the PRD layout) that returns *effects*. The runner does all I/O. This keeps PRD 17.3's "pure and deterministic, inject the clock" testable with the simulator.
- Next: build workflow with 5 parallel builders (A audio+Muse, B engine, C AI, D1 Table UI, D2 phone UI) → integration (main.py, session store, runner) → smoke test.

### 2026-10-24 · lead · Build workflow launched
- 🟡 Workflow `breadcrumbs-build` (run `wf_e0161504-6ae`): 5 parallel builders (A audio+Muse, B engine, C AI services, D1 Table UI, D2 phone+home UI), then an integration agent (main.py, session store, runner, API tests, README, end-to-end smoke test against a live server).
- Each builder appends its own entry below when it finishes.

### 2026-10-24 · A (audio + Muse) · Muse realtime + REST clients, ffmpeg conversion, browser audio modules
- ✅ `MuseRealtimeClient` (`server/muse/realtime.py`): PRD 11.1 handshake as the first frame (Bearer key in `authorization.accessToken`, `PCM_24KHZ`, `config.ASR_MODEL`, `DIARIZATION`, `CUMULATIVE`, `emitAudioProgress`, `languageBias ["English"]`, keywords deduped case-insensitively, capped at 50 x 48 chars). Ack must be `{"sessionId"}` with no `type`, within 10 s. Separate sender/receiver tasks (receiver end cancels the sender). Close 1008 = fatal `on_status("error")`, no retry. 1011/1013/1001/1006/1012/1014 and network errors = reconnect with backoff 0.5/1/2 s (then 2 s; the ladder resets after a session that lived 10 s), a new `bc-<uuid>` sessionId each time, then `on_status("connected")` + `on_status("resyncing")`. `close()` queues the endStream sentinel, waits up to 5 s for Muse to flush and close 1000, then `on_status("closed")`. Drop-oldest queue (maxsize 40 = 3.2 s), the sentinel is never dropped. Tracks `connected`, `audio_processed_ms`, `last_event_at`, `session_id`, `reconnects`, `dropped_frames`. Logs only codes and counts.
- ✅ `MockMuseRealtime` (same file): RMS of each PCM16 frame vs an adaptive noise floor (threshold `max(0.01, 3*floor)`), start after 160 ms above, end after 700 ms below (30 s cap), emits `speechStart` → partials every 480 ms → final `transcript` → `speaker` (A, B alternating per turn) → `speechEnd` → `speechComplete` with `"(mock speech, N.N s)"` (N = voiced duration), `audioProgress` every 1 s of audio, all with `audioProcessedMs`. An open turn is flushed on `close()`. `make_realtime_client()` reads `config.MUSE_MOCK` at call time.
- ✅ `transcribe_wav` (`server/muse/transcribe.py`): PRD 11.2 multipart (`request` JSON part with no filename: model, `WAV`, `PUSH_TO_TALK`, languageBias; `audio` part `answer.wav` / `audio/wav`), `Authorization: Bearer`, `Accept: application/json`, 2 retries (0.5 s, 1.5 s) on 408/429/5xx/timeouts/transport errors, `TranscribeError` otherwise (non-WAV input, 4xx, bad body, retries exhausted). Mock: 18 canned answers keyed `mode:qN:seatS` (3 modes x 3 questions x 2 seats). Seat 1 = first half marathon, ramen, lost in Tokyo, product designer with a farmers market app. Seat 2 = quit running after a knee injury and switched to climbing, Japan trip, ramen at home, backend engineer learning design. Direct overlaps (ramen, Japan, board games), adjacent pairs and offer/seeking matches. Unknown key = a generic answer.
- ✅ `server/audio/convert.py`: `ffmpeg_available()`, `ConversionError`, `wav_info()` → (channels, rate, bits, duration_s), async `to_wav_24k()` via `asyncio.create_subprocess_exec` with the exact ffmpeg args, in a `mkdtemp` dir that is always `rmtree`d (file I/O in `asyncio.to_thread`), 30 s timeout (kill on timeout), sanitized input extension, output format verified.
- ✅ `web/public/pcm-worklet.js` (PRD 14.1 verbatim). `web/src/audio/capture.ts`: `startCapture` (AudioContext 24 kHz, getUserMedia mono with EC/NS/AGC, worklet through a zero-gain sink so nothing is audible) + `Vad` (rolling 10 s, 10th-percentile floor, 150 ms attack, 400 ms release, `reset()`, `threshold`). `web/src/audio/player.ts`: `CrumbPlayer` (base64 WAV → WebAudio with a GainNode, `fadeOut(250)` linear ramp then stop, two-note sine chime from oscillators, `speechSynthesis` fallback with a calm English voice at rate 1.0, one playback at a time, `resume()`, `playingSinceMs`, `currentId`). `web/src/audio/recorder.ts`: `startRecording` (best of webm/opus, webm, mp4, ogg/opus, ogg; AnalyserNode fftSize 1024 not routed to the speakers; `stop()` resolves the Blob and releases the mic; `cancel()`), `extForMime`.
- ⚠️ Optional keyword-only test hooks added beyond the contract signatures (they don't change the contract calls): `MuseRealtimeClient(..., *, url, api_key, model, backoff, handshake_timeout)` and `transcribe_wav(..., *, transport)`.
- ⚠️ `to_wav_24k` passes an upload that is already 24 kHz mono s16 WAV through without ffmpeg (rewritten with only fmt+data chunks, so metadata is stripped as `-map_metadata -1` would). ffmpeg is still needed for real phone uploads (webm/mp4).
- ⚠️ A non-ack first frame is fatal unless the server then closes with a retryable code. Exceptions raised by `on_event` are logged (event type only) and swallowed so a consumer bug can't drop the Muse session and reset the labels.
- ⚠️ `CrumbPlayer`: `onEnd(interrupted)` fires exactly once; `onStart` fires at most once and never when a crumb is interrupted before it makes sound. `play()` resolves when the crumb ends. `capture.setPaused(true)` also disables the mic tracks and sends one `onVad(false, 0)` if speech was active.
- Tests: `cd server && .venv/bin/pytest tests/test_muse_realtime.py tests/test_transcribe.py tests/test_convert.py -q` → 37 passed, 2 skipped (the real-ffmpeg tests skip because ffmpeg isn't installed). TS: `npx tsc --ignoreConfig --noEmit --strict --isolatedModules --noUnusedLocals --noUnusedParameters ... src/audio/*.ts` → 0 errors (TS 7 needs `--ignoreConfig` when files are given). Vad smoke check in Node: attack 200 ms, release 400 ms, floor 0.002.
- Requests: (integration) build the transcribe `mock_key` as `f"{mode}:q{n}:seat{seat}"`. Map `ConversionError` / `TranscribeError` to a 4xx/502 with the message. On `on_status("resyncing")` call `engine.on_muse_reconnect(now)`, broadcast every status as `muse_status`, and `await client.close()` when `/ws/audio` disconnects. (D1) call `player.resume()` from the first user gesture on the Table (autoplay policy), and send `tts_state` from `onStart`/`onEnd`. (lead) consider documenting the optional test kwargs in CONTRACTS section 3.
