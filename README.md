# Breadcrumbs

> **Never lose the thread.**

Breadcrumbs is an AI conversation companion for two people meeting in person for the first time. A laptop on the table (the **Table**) listens with Muse Voice Transcribe, stays quiet while the conversation flows, and drops a well-timed **crumb** (a question, a shared thread, a grounded fun fact) only when a pause runs long. It also knows when *not* to talk: say "wait, let me show you this" and it holds. Each person sets up a short voice profile on their own phone, and at the end both can privately choose to swap contacts and get a **trail** of threads to pick up next time. Nothing is stored: transcripts live in memory and are wiped when the trail is shown, on request, or after 2 hours.

Built at HackTX 2026 for the Meta track ("Bringing People Closer Together with AI"). The full product spec is [`PRDHack1.md`](PRDHack1.md), the module contracts are in [`docs/CONTRACTS.md`](docs/CONTRACTS.md), and the build history is in [`BUILD_LOG.md`](BUILD_LOG.md).

---

## Quick start (mock mode, no keys needed)

```bash
# 1. server
cd server
python3 -m venv .venv
.venv/bin/pip install -U fastapi "uvicorn[standard]" websockets httpx google-genai pydantic \
    python-multipart python-dotenv pytest pytest-asyncio

# 2. web app (built once, then served by FastAPI on the same origin)
cd ../web && npm install && npm run build

# 3. run
cd ../server && .venv/bin/uvicorn main:app --port 8000
# open http://localhost:8000 and click "Set the table"
```

With no API keys the server runs in **mock mode** (see below), so the whole flow works offline on one laptop: open the two join links from the Table in two browser tabs to play both phones.

## Setup

| Need | Install | Why |
|---|---|---|
| Python 3.11+ | `python3 -m venv server/.venv` + the `pip install` line above | FastAPI server, engine, AI and Muse clients |
| Node 24 / npm | `cd web && npm install` | React + Vite web app (the frontend unit tests use Node 24 built-in TypeScript stripping) |
| **ffmpeg** | `brew install ffmpeg` (or `apt-get install ffmpeg`) | Converts phone recordings (webm/opus, mp4/aac) to the 24 kHz mono WAV that Muse needs |
| **cloudflared** | `brew install cloudflared` | HTTPS tunnel: phones only allow the microphone over HTTPS |
| `.env` | `cp .env.example .env` | Keys and the public URL (below) |

`.env` (at the repo root, never committed):

| Variable | Used for |
|---|---|
| `MODEL_API_KEY` | Meta Model API: Muse Voice Transcribe (realtime + REST), optional Muse Spark text fallback |
| `GEMINI_API_KEY` | Gemini: profiles, overlaps, crumbs, hold classifier, energy tags, grounded facts, TTS, trail |
| `PUBLIC_BASE_URL` | The base URL printed in the QR codes, e.g. `https://<random>.trycloudflare.com` |
| `ASR_MODEL` | Default `muse-voice-transcribe-1.0` |
| `EVENT_LOCATION` | Used for "outside the box" crumbs (default: Austin, Texas) |
| `DEV_TOOLS` | `true` enables the X-ray "inject turn" box (dev turns), default on |

## Running

**Dev mode** (hot reload on both sides):

```bash
cd server && .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
cd web && npm run dev          # http://localhost:5173, proxies /api and /ws to :8000
```

**Single-origin mode** (what the demo uses): `cd web && npm run build`, then run uvicorn without `--reload`. FastAPI serves `web/dist` at `/`, with an SPA fallback for `/table/...` and `/join/...`. It never shadows `/api` or `/ws`.

**Tunnel mode** (real phones):

```bash
cloudflared tunnel --url http://localhost:8000      # prints https://<random>.trycloudflare.com
# put that URL in .env as PUBLIC_BASE_URL, then restart uvicorn so the QR codes point at it
```

Open the Table at the tunnel URL (or `http://localhost:8000`) in **Chrome** on the laptop, then scan the two QR codes.

## Mock mode

Each service switches to a deterministic offline mock when its key is missing (`GET /api/config` reports which):

| Service | Without its key |
|---|---|
| Muse realtime | `MockMuseRealtime`: energy-based turn detection on the Table mic, labels alternate A/B, text is a placeholder. Use **dev turns** (X-ray, Dev inject) to type real lines as either seat. |
| Muse REST (profile answers) | Canned answers per mode, question and seat (two people with real overlaps: running and climbing, ramen, Japan, board games) |
| Gemini | Rule-based profiles, overlaps, crumbs, classifiers and trail. Mock TTS returns nothing, so the Table speaks with the browser's speech synthesis. Mock "facts" never exist: outside crumbs become questions. |
| ffmpeg missing | Mock mode skips conversion. With a real Muse key, the answers endpoint returns a clear 503 telling the operator to `brew install ffmpeg`. |

Mocks never present an ungrounded fact as a fact.

## How to demo (PRD 18.1, about 2 minutes)

1. **Hook (10 s).** "Once upon a time, two strangers sat down at a table, and the conversation died after 'what's your major.' Breadcrumbs makes sure it doesn't."
2. **Setup (30 s).** On Home, pick a mode, turn on **Quick profile**, click **Set the table**. Both judges scan a QR code, type their name, tick consent and answer one question. Show the profile chips and the X button: "you control what it knows." Both tap **I'm ready**.
3. **Calibration + opener (20 s).** Click **Start listening** on the Table. It asks Seat 1, then Seat 2, to say hi (this maps the voices to seats), then drops the overlap opener about the two of them.
4. **Hold (20 s).** A teammate says "wait, let me show you something" and scrolls a phone. The orb turns blue: *Holding (show)*. Nothing interrupts.
5. **Steer and switch (30 s).** Everyone goes quiet. At 5 s a steer lands on the current topic; if nobody answers, about 6.5 s after it ends a context switch arrives.
6. **X-ray (10 s).** Press `X`: pause timer vs thresholds, staleness and its parts, hold state, cached crumbs, and the reasoning for the last crumb.
7. **Close (10 s).** Tap **End**. Both phones ask "Keep in touch?". On a mutual yes each phone shows the other's contact and a short trail, and the session is deleted. "Every AI icebreaker helps you start a conversation. Breadcrumbs keeps it going, and knows when to stay quiet."

Demo hygiene: a quieter corner, the laptop between the two speakers, volume about 60%. Table shortcuts: `Space` drop a crumb, `H` hold, `P` pause listening, `X` X-ray.

## Architecture (PRD 10.2)

```
            ┌─────────────────────── Phone (Seat 1/2) ────────────────────────┐
            │ Join → Consent → Record answers (MediaRecorder) → Review → Trail │
            └───────────────┬──────────────────────────────────────▲───────────┘
                    HTTPS REST (upload answers, profile edits)     │ WS /ws/events (state, trail)
                            │                                      │
┌──────────────────── FastAPI server (Table laptop) ──────────────────────────────────────────┐
│  REST /api/*      Session store (in-memory, TTL)        Static web app (React build)        │
│                                                                                              │
│  Profile pipeline: ffmpeg → Muse REST transcribe → Gemini profile extract → overlap finder  │
│                                                                                              │
│  Live pipeline:                                                                              │
│   /ws/audio/{sid} (PCM 24k) ──► MuseRealtimeClient (DIARIZATION) ──► events ──► Engine       │
│   /ws/events/{sid} (VAD, controls, tts_state) ─────────────────────────────────► Engine      │
│   Engine (10 Hz): pauses, thresholds, staleness, holds, policy ──► CrumbService              │
│   CrumbService: cache, Gemini crumb gen, grounded facts, validation ──► TTS (Gemini)         │
│   Broadcast: transcript, engine snapshot, crumbs (+audio) ──► /ws/events/{sid}               │
└───────────────▲──────────────────────────────────────────────────────────────┬──────────────┘
                │ WSS wss://api.meta.ai/v1/asr/realtime                        │ HTTPS Gemini API
                │ HTTPS https://api.meta.ai/v1/asr/transcribe                  │ (interactions)
            Meta Model API                                                 Google Gemini API

            ┌─────────────────────────── Table (Chrome) ───────────────────────────┐
            │ Mic → AudioWorklet (Int16, 80 ms frames) → /ws/audio                  │
            │ RMS VAD (10 Hz) → /ws/events      Crumb Orb UI, controls, X-ray, TTS │
            └──────────────────────────────────────────────────────────────────────┘
```

| Path | What it is |
|---|---|
| `server/main.py` | FastAPI app: REST (PRD 13 + health, config, metrics, live params), `/ws/events`, `/ws/audio`, static SPA |
| `server/session_store.py` | In-memory sessions, per-socket send queues, broadcast, wipe, 2 h TTL sweeper |
| `server/engine/runner.py` | Per-session async shell: 10 Hz tick, 4 Hz snapshot broadcast, effect executor, prefetch schedule, Muse lifecycle, dev turns |
| `server/engine/*.py` | The pure, deterministic conversation engine (pause timer, staleness, holds, policy, calibration) |
| `server/ai/*.py` | Gemini client, prompts, profiles + overlaps, crumbs + validator + cache, classifiers, facts, TTS, trail |
| `server/muse/*.py`, `server/audio/convert.py` | Muse realtime + REST clients, ffmpeg wrapper |
| `server/sim/` | Engine simulator (13 scripted conversations) and the end-to-end smoke test |
| `web/src/pages` | `Home`, `Table` (orb, controls, X-ray), `Join` (phone flow) |
| `web/src/audio`, `web/public/pcm-worklet.js` | Mic capture + VAD, crumb player with barge-in, phone recorder |

Lifecycle: `lobby` (created) → `profiles` (first consent) → `calibrating` (both ready: overlaps computed, engine started; calibration begins when the Table's audio arrives) → `live` (voices mapped, opener delivered) → `ended` (End or the speed-dating round timer) → wiped (both phones fetched the trail, `DELETE`, or the 2 h TTL).

## The engine in one page

The engine (`server/engine/`) is pure and clock-injected, so the simulator can test it without a mic. Every 100 ms it decides whether to speak, using the PRD Section 8 policy:

| Pause band | Silence | What Breadcrumbs does |
|---|---|---|
| Normal | under 3.0 s (`T_NORMAL_S`) | Nothing |
| Warming | 3.0 to 5.0 s | Nothing spoken; orb glows; makes sure a steer and a switch are cached |
| Unusually long | 5.0 s (`T_LONG_S`) | **Steer** on the current topic, or **switch** if the conversation is stale (staleness 0.60 or more) |
| Exceptionally long | 6.5 s (`T_EXC_S`) | **Context switch** (when the 5 s steer was blocked, or 6.5 s after a crumb nobody answered) |

- **Silence** resets on any speech (Muse `speechStart` or the Table's local VAD) and restarts at 0 when a crumb finishes playing.
- **Staleness** `S = 0.35 L + 0.25 R + 0.15 G + 0.10 Q + 0.15 E` over the last 6 human turns against the session baseline: reply-length decay, short-reply rate, gap growth, question deficit and an LLM energy tag.
- **Holds** (Appendix A lexicon on the last 8 words, plus silence, confirmed by a Gemini Flash-Lite classifier): `show` suppresses crumbs for up to 60 s, `think` doubles the thresholds (10 s / 13 s) for up to 20 s, `external` suppresses for up to 90 s. Holds clear when speech resumes. The Hold button is a 60 s `show` hold.
- **Guards:** 20 s warm-up after the opener; regular cooldown of 30 s since the last crumb ended plus 2 human turns; exceptional cooldown of 15 s; at most 6 automatic crumbs per rolling 10 minutes; after 2 unanswered crumbs in a row it goes quiet and shows a "Drop a crumb" hint.
- **Self-hearing guard:** a Muse turn that overlaps Breadcrumbs' own voice (TTS plus a 300 ms tail) by more than 50% is dropped as bot speech. VAD barge-in is ignored for the first 300 ms of playback (the chime).
- **Barge-in:** anyone speaking during a crumb fades it out within 250 ms.
- **Drop a crumb** is an immediate context switch that ignores cooldowns but never fires while someone is talking.
- **Prefetch:** a ready steer and a ready switch (text plus pre-synthesized audio) are refreshed 1.5 s after every turn and every 30 s, so most crumbs start in milliseconds.

Every parameter (PRD 8.12) is live-tunable from the X-ray panel (`PATCH /api/sessions/{sid}/params`).

## Privacy design

- **Consent first.** No audio is accepted for a seat until it has a typed name and a ticked box (consent is stored with a timestamp in memory). The Table's audio socket is refused (close code 4403) until **both** seats have consented, and after the session ends.
- **No storage.** Live audio is streamed to Muse and never written to disk. Profile answers are converted in a temp dir that is deleted immediately. Transcripts, profiles and crumbs live only in server memory.
- **Wiped by default.** Everything is deleted as soon as both phones have fetched their trail, on `DELETE /api/sessions/{sid}` (the Table's "Wipe now"), or after 2 hours, whichever comes first. Every client then gets `wiped` and the sockets close with 4404.
- **Your profile, your rules.** Removed chips and off-limits topics never reach a crumb prompt and are checked again on Gemini's output.
- **Mutual opt-in.** Contacts and the trail are shown only if both people say yes. The session summary sent to both phones carries only an "answered" flag, never the choice itself, so nobody learns who declined.
- **No transcript text in logs.** The server logs decisions, counts and timings only.
- **Safety.** Distress phrases pause automatic crumbs for the session; speed dating prompts never reference bodies, appearance or sexual topics.

## What we built during HackTX vs third-party libraries (PRD 15.2)

**Built by our team during HackTX 2026** (everything in this repo):
- The conversation engine: pause timer, thresholds and bands, staleness score, intentional-hold detection, decision policy, cooldowns, calibration, self-hearing guard, simulator and its 13 test conversations.
- The server: session store, per-session runner, REST and WebSocket API, consent gating, wipe and TTL, static hosting.
- The AI layer: every prompt, the crumb validator, source rotation, depth ladder, prefetch cache, fallback chain, profile and overlap pipeline, trail generation and all mock modes.
- The Muse realtime and REST clients (handshake, pacing, reconnect, re-sync) and the ffmpeg wrapper.
- The web app: Home, the Table (Crumb Orb, controls, X-ray, crumb trail), the phone flow (consent, voice interview, profile review, opt-in, trail), the audio worklet, VAD, crumb player and recorder.
- Tests: pytest suites for every module, the end-to-end smoke test, frontend reducer and flow tests.

**Third-party libraries and services we used:**
- Python: FastAPI, Starlette, Uvicorn, Pydantic, websockets, httpx, google-genai, python-dotenv, python-multipart, pytest, pytest-asyncio.
- Web: React, React Router, Vite, TypeScript, Tailwind CSS, framer-motion, qrcode.react.
- Services: Meta Model API (Muse Voice Transcribe realtime + REST, optional Muse Spark), Google Gemini API (Gemini 3.8 Flash, 3.5 Flash-Lite, 3.8 Flash TTS, Google Search grounding).
- Tools: ffmpeg, Cloudflare quick tunnels (cloudflared).
- AI coding tools: the team built with Claude Code as a coding assistant.

## Testing

```bash
cd server
.venv/bin/pytest -q                          # every server test (the 2 real-ffmpeg tests skip without ffmpeg)
.venv/bin/pytest tests/test_api.py -q        # integration: full flow over REST + WebSockets in mock mode
.venv/bin/python -m sim.replay               # simulator: all 13 PRD 16.1 scripts with a decision timeline
.venv/bin/python -m sim.replay sim/scripts/show_hold_then_resume.json

# end-to-end smoke test against a real running server (DEV_TOOLS=true)
.venv/bin/uvicorn main:app --port 8000 &
.venv/bin/python -m sim.smoke_e2e            # create, consent, profiles, calibration, opener, steer, spark, end, trail, wipe

cd ../web
npm run build                                # typecheck + production bundle
node --import ./src/components/table/__tests__/register.mjs --test 'src/components/table/__tests__/*.test.mjs'
node --test src/components/phone/flow.test.mjs
```

The smoke test plays both phones and the Table over real HTTP and WebSockets: it streams 80 ms silence frames in real time, sends VAD at 10 Hz, answers every crumb with `tts_state` like the browser player, and checks that a lively conversation gets no crumb, a steer lands about 5 s into the silence, and Drop a crumb answers in under 3 s.

## Known limitations

- **ffmpeg and cloudflared are not installed on the build laptop yet.** Without ffmpeg, real phone recordings (webm/mp4) cannot be converted when a Muse key is set (the server says so with a 503); mock mode skips conversion.
- **Not yet run against the live Muse and Gemini APIs** (no keys during the build). Request shapes follow PRD Section 11 and were checked against the installed google-genai SDK and a local fake Muse server.
- **Thresholds are untuned with real pairs.** `T_LONG_S`, `T_EXC_S` and `STALE_SWITCH` need the live tuning pass described in PRD 16.2 (use the X-ray tuning panel).
- **The browser audio path has not been exercised in a real browser session yet** (mic capture, echo cancellation, barge-in timing); it is type-checked and unit-tested.
- **Single process, in memory.** Restarting the server ends every session (by design: nothing is persisted). One Table per session: a second Table tab that streams audio takes over the Muse session and triggers a voice re-sync.
- **If a phone never fetches its trail**, that session stays in memory until the 2 hour TTL (or the Table's "Wipe now").
- **Keyword-based off-limits and safety filters can over-block** (for example the word "body"); over-blocking falls back to a safer crumb rather than speaking anything unsafe.
- **Mock mode is for development**: mock profile extraction is rule-based and mock speech has placeholder text, so real conversations need the keys.
- The production JS bundle is about 530 kB (Vite warns above 500 kB); route code-splitting is a possible follow-up.
