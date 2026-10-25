# Breadcrumbs

> **Never lose the thread.**

Breadcrumbs is an AI conversation companion for two people meeting in person for the first time. A laptop on the table (the **Table**) listens with Muse Voice Transcribe, stays quiet while the conversation flows, and drops a well-timed **crumb** (a question, a shared thread, a grounded fun fact) only when a pause runs long. Claude writes the crumbs and ElevenLabs speaks them. It also knows when *not* to talk: say "wait, let me show you this" and it holds, and voices away from the table (a bystander, a TV, room noise) do not hold it back. There are two setups:

- **Just talk** (the default): no phones and no forms. Both people agree on one consent card on the laptop (names optional), the conversation goes live at once, and Breadcrumbs learns about both people from the conversation itself (a live memory) to suggest follow-ups. At the end the laptop shows a **trail** of threads to pick up next time.
- **With profiles**: each person sets up a short voice profile on their own phone, the Table maps their voices and opens with something they have in common, and at the end both can privately choose to swap contacts and get a trail on their phones.

Nothing is stored: transcripts and the live memory stay in server memory and are wiped when the trail is done with, on request, or after 2 hours.

Built at HackTX 2026 for the Meta track ("Bringing People Closer Together with AI"). The full product spec is [`PRDHack1.md`](PRDHack1.md), the module contracts are in [`docs/CONTRACTS.md`](docs/CONTRACTS.md), and the step-by-step build history is in [`BUILD_LOG.md`](BUILD_LOG.md).

---

## Status (as of 2026-10-25)

**Works live with real keys** (run end to end on the build laptop, see `BUILD_LOG.md`):

| Part | Live result |
|---|---|
| Muse Voice Transcribe, REST (profile answers) | 1.3 to 2.2 s per answer |
| Muse Voice Transcribe, realtime diarization | Calibration mapped voices `{A: 1, B: 2}` |
| Claude (`claude-opus-5-5`, effort `low`) | Profile 6.3 to 9.1 s, overlaps 8.9 s, crumb JSON about 3.7 s |
| ElevenLabs voice | Flash 491 ms, `eleven_v4` 1,443 ms, multilingual v2 849 ms (direct tests); calibration lines voiced in 154 to 366 ms, the opener in 445 ms |
| Real browser (Chrome Table + phones) | User testing found self barge-in and a background-noise timer freeze (fixed in `9bceb73`), then that ordinary background noise still kept the pause from ever reaching the crumb threshold. This round's noise rules and the 3 s crumb fix that in the simulator and unit tests, but have not yet been retested in a real room |

**Not verified live:** Gemini (text, TTS and Google Search grounding), Claude's web-search grounded facts, Claude fast mode on this account, the "Just talk" setup, and this round's noise rules (the 8 dB near / 12 dB far margins and the 6 dB noise-floor rise have never been tuned in a live room). The Gemini key returned 402 (no prepaid credits); Gemini and Claude grounding have only been exercised through tests and mocks, and the echo and noise rules through sim scripts, unit tests and the mock smoke test. The fast-mode check needs a billed probe (10 standard vs 10 fast crumb drafts) that has not been run.

**Latest verified numbers** (all run with every API key blanked, no billed calls):

| Check | Result |
|---|---|
| `pytest -q` (server) | 762 passed |
| `python -m sim.replay` | 41 of 41 scripts pass |
| `python -m sim.smoke_e2e --setup both` (mock server) | 46 of 46 checks pass; steers land about 3.1 s into the silence, and the Just talk icebreaker about 3.1 s after going live |
| Web tests (Table, player, phone flow) | 130 + 14 + 26 = 170 pass |
| `npm run build` | passes; JS bundle 575.73 kB (183.72 kB gzip) |
| `python -m sim.usage` (one scripted 10-minute session) | 63 Claude calls, 812.5 ElevenLabs credits (Just talk: 62 calls, 769.5 credits), 7 of 8 crumbs served from cache at 0 ms; steers decided at 3.05 s of silence (p50), words at 3.84 s |

## Quick start (mock mode, no keys needed)

```bash
# 1. server (Python 3.12)
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. web app (built once, then served by FastAPI on the same origin)
cd ../web && npm install && npm run build

# 3. run
cd ../server && .venv/bin/uvicorn main:app --port 8000
# open http://localhost:8000 and click "Start a conversation"
```

With no API keys the server runs in **mock mode** (see below), so the whole flow works offline on one laptop. The default **Just talk** table needs nothing else: tick through the consent card and use the X-ray's "Dev: inject turn" box to type lines as either person. For **With profiles**, open the two join links from the Table in two browser tabs to play both phones.

## Setup

| Need | Install | Why |
|---|---|---|
| Python 3.12 (tested on 3.12.6) | `python3 -m venv server/.venv` then `server/.venv/bin/pip install -r server/requirements.txt` | FastAPI server, engine, AI, voice and Muse clients |
| Node 24 / npm | `cd web && npm install` | React + Vite web app (the frontend tests use Node 24's built-in TypeScript stripping) |
| **ffmpeg** | `brew install ffmpeg` (or `apt-get install ffmpeg`) | Converts phone recordings (webm/opus, mp4/aac) to the 24 kHz mono WAV that Muse needs |
| **cloudflared** | `brew install cloudflared` | HTTPS tunnel: phones only allow the microphone over HTTPS |
| `.env` | `cp .env.example .env` | Keys and the public URL (below) |

`server/requirements.txt` lists the top-level server dependencies with minimum versions that match the tested venv: fastapi, uvicorn[standard], python-multipart, pydantic, python-dotenv, websockets, httpx, certifi, anthropic, google-genai, pytest and pytest-asyncio.

`certifi` is required, not optional: the python.org macOS build of Python has no system root certificates, so every `wss://` handshake to Muse failed with `SSLCertVerificationError` until the realtime client was given the certifi CA bundle.

### Environment

`.env` lives at the repo root and is never committed (`.gitignore` also covers `.env.*`). It is loaded by `server/config.py` with python-dotenv. Real environment variables win over `.env`.

| Variable | Default | Used for |
|---|---|---|
| `MODEL_API_KEY` | empty | Meta Model API: Muse Voice Transcribe (realtime + REST) and the Muse Spark text fallback. Empty means mock Muse. |
| `ANTHROPIC_API_KEY` | empty | Claude, the first text provider: profiles, overlaps, crumbs, hold classifier, energy tags, grounded facts (web search), trail |
| `ELEVENLABS_API_KEY` | empty | ElevenLabs, the first voice provider for crumbs and system lines |
| `GEMINI_API_KEY` | empty | Gemini, the second text provider and the second voice provider (Gemini TTS) |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Base URL printed in the phone QR codes (the tunnel URL when testing on phones) |
| `ASR_MODEL` | `muse-voice-transcribe-1.0` | Muse speech model |
| `EVENT_LOCATION` | `Austin, Texas (HackTX at UT Austin)` | Used for "outside the box" crumbs |
| `DEV_TOOLS` | `true` | Enables the X-ray "Dev: inject turn" box (dev turns) |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Claude model for every Claude call |
| `CLAUDE_EFFORT` | `low` | Claude effort level |
| `CLAUDE_FAST_JOBS` | `crumb,hold` | Jobs that ask for Opus fast mode (the crumb draft and the hold classifier); empty turns it off |
| `CLAUDE_CRUMB_MODEL` | `CLAUDE_MODEL` | Override for the crumb draft model only (fast mode applies to Opus models only) |
| `ELEVENLABS_VOICE_ID` | empty (Sarah, `EXAVITQu4vr4xnSDxMaL`) | ElevenLabs voice |
| `ELEVENLABS_MODEL` | empty (`eleven_v4`) | Overrides the first model on the natural voice path |
| `TTS_VOICE` | `Kore` | Gemini TTS voice |

Tests never use the keys in `.env`: `server/conftest.py` forces mock text AI and a keyless ElevenLabs client. ElevenLabs also refuses live calls under pytest unless `BREADCRUMBS_ELEVENLABS_LIVE_TESTS=1`.

## Running

**Dev mode** (hot reload on both sides; two terminals, both started from the repo root):

```bash
# terminal 1
cd server && .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# terminal 2
cd web && npm run dev          # http://localhost:5173 (also on the LAN), proxies /api and /ws to :8000
```

**Single-origin mode** (what the demo uses): `cd web && npm run build`, then run uvicorn without `--reload`. FastAPI serves `web/dist` at `/`, with an SPA fallback (`index.html`, `Cache-Control: no-cache`) for `/table/...` and `/join/...`. It never shadows `/api` or `/ws`.

### Testing on real phones

Only the **With profiles** setup uses phones; a Just talk table runs entirely on the laptop at `http://localhost:8000` (Chrome allows the mic on localhost). Phones only grant the microphone over HTTPS, so for With profiles the server is reached through a Cloudflare quick tunnel:

```bash
cloudflared tunnel --url http://localhost:8000      # prints https://<random>.trycloudflare.com
```

1. Copy the printed URL into `.env` as `PUBLIC_BASE_URL`.
2. Restart uvicorn. `PUBLIC_BASE_URL` is read once at startup, and it is what the QR codes point at.
3. Open the Table at the tunnel URL (or `http://localhost:8000`) in **Chrome** on the laptop, then scan the two QR codes.

The quick-tunnel URL changes every time cloudflared restarts. Each time, update `PUBLIC_BASE_URL` and restart uvicorn again, or the QR codes will point at a dead URL.

## Providers

Text jobs go through one gateway (`server/ai/gemini.py`) that tries Claude, then Gemini, then Muse Spark, and moves to the next on any error, refusal or timeout. Voice has its own chain in `server/ai/tts.py` (ElevenLabs, then Gemini TTS, then the browser). Speech to text is Muse only (`server/muse/`).

| Job | 1st | 2nd | 3rd | Last resort |
|---|---|---|---|---|
| Text (profiles, overlaps, crumbs, classifiers, trail) | Claude (`ANTHROPIC_API_KEY`) | Gemini 3.8 Flash / 3.5 Flash-Lite (`GEMINI_API_KEY`) | Muse Spark 1.3 (`MODEL_API_KEY`) | Deterministic heuristics, then canned crumbs |
| Grounded fun facts (searched only during prefetch, once per topic per session, then cached) | Claude web search (must return a citation) | Gemini Google Search grounding | none | No fact: the crumb becomes a question |
| Voice | ElevenLabs (`ELEVENLABS_API_KEY`) | Gemini 3.8 Flash TTS, then 3.8 Flash-Lite TTS (`GEMINI_API_KEY`) | none | The browser's `speechSynthesis` on the Table |
| Speech to text | Muse Voice Transcribe realtime (diarization) and REST | none | none | Mock Muse |

Provider details:

- **Claude** runs `CLAUDE_MODEL` for every call and `CLAUDE_EFFORT` for every JSON and text call, including the classifiers. Grounded-fact searches always use effort `low`. 4,000 tokens of thinking headroom are added to each request. JSON jobs use structured output (`messages.parse`). Every request uses server-side fallback. A refusal or `max_tokens` stop hands the job to the next provider. Grounded facts use the `web_search` tool (at most 2 searches), and an answer without a citation URL is never used as a fact. The crumb draft and the hold classifier ask for **fast mode** (same model, up to 2.5x output speed, $8 / $40 per MTok instead of $4 / $20, Claude API only): a 429 retries once at standard speed, and a 400/403 about speed turns fast mode off for the process. Fast mode on this account is unverified live; set `CLAUDE_FAST_JOBS=` to turn it off.
- **Gemini** has an 8 s timeout and 2 retries for text and JSON (10 s and 1 retry for grounded search, 1 retry for TTS). A 401/402/403 pauses Gemini for 10 minutes. On text and JSON a 429 goes straight to Muse Spark when Spark is configured.
- **Muse Spark** uses `reasoning_effort: minimal`, 3,000 extra tokens of headroom and a 20 s timeout.
- **ElevenLabs** voice is Sarah by default. The natural path (used when there is time) tries `eleven_v4` (Text to Dialogue), then `eleven_multilingual_v2`, then `eleven_flash_v2_5`. The fast path (live lines) uses `eleven_flash_v2_5` only. Output is `pcm_24000`, edge-trimmed and wrapped as WAV. A 401/402 (or a 403 on Flash) pauses ElevenLabs for 10 minutes. A model that gets two 4xx answers in a row is set aside for 10 minutes, and the chain moves on to the next model. The TLS connection is warmed with a free `GET /v1/models` when each session starts.
- `GET /api/config` reports `ai_provider` (`claude`, `gemini` or `mock`). It does not report the voice provider.

## Mock mode

Each service switches to a deterministic offline mock when its key is missing. `GET /api/config` reports the Muse and text-AI mocks (`mock.muse`, `mock.gemini`) and `ffmpeg`, but not the voice provider:

| Service | Mocked when | Behavior |
|---|---|---|
| Muse realtime | no `MODEL_API_KEY` | `MockMuseRealtime`: energy-based turn detection on the Table mic, labels alternate A/B, text is a placeholder. Use **dev turns** (X-ray, Dev: inject turn) to type real lines as either seat. |
| Muse REST (profile answers) | no `MODEL_API_KEY` | Canned answers per mode, question and seat (two people with real overlaps: running and climbing, ramen, Japan, board games) |
| Text AI | neither `ANTHROPIC_API_KEY` nor `GEMINI_API_KEY` | Rule-based profiles, overlaps, crumbs, classifiers, live memory (self-statements, shared words, the latest questions) and trail. Mock "facts" never exist: outside crumbs become questions. `mock.gemini` in `/api/config` means "text AI mocked". |
| Voice | neither `ELEVENLABS_API_KEY` nor a usable Gemini key | The Table speaks with the browser's speech synthesis. ElevenLabs still voices crumbs when only the text AI is mocked. |
| ffmpeg missing | not installed | Mock Muse skips conversion. With a real Muse key, the answers endpoint returns a 503 telling the operator to `brew install ffmpeg`. |

Mocks never present an ungrounded fact as a fact.

## How to demo (PRD 18.1, about 2 minutes)

Home has one orb and one choice: **Just talk** (the default) or **With profiles**. Either one, then **Start a conversation**.

**Just talk** (no phones):

1. **Hook (10 s).** "Once upon a time, two strangers sat down at a table, and the conversation died after 'what's your major.' Breadcrumbs makes sure it doesn't."
2. **Start (15 s).** On Home, leave **Just talk** selected and click **Start a conversation**. The Table shows one consent card ("Breadcrumbs listens and transcribes your conversation live. Nothing is stored.") with two optional first names. Click **We're both in**: that records both consents and turns the mic on in the same click (a **Start listening** button appears only if the mic did not start). It goes live at once, with no say-hi prompts.
3. **Icebreaker (10 s).** If nobody at the table speaks for 3 s, a light icebreaker lands, captioned "to get you started".
4. **Talk, hold, steer (60 s).** Talk normally: nothing interrupts, and the X-ray's Memory section fills with what it learned about each of you and the open threads. Say "wait, let me show you something" and it holds (lilac orb). Go quiet: at 3 s a steer lands on the current topic; if nobody answers, about 6 s after it ends a switch picks up something from the memory or an outside question. A bystander behind the table or a noisy room does not hold it back.
5. **Close (15 s).** Tap **End**, then **Yes, end**. The laptop shows **Threads to pick up** for each person. **Done** deletes the session right away (otherwise it is deleted 10 minutes after the end).

**With profiles** (two phones):

1. **Hook (10 s).** As above.
2. **Setup (30 s).** On Home, pick **With profiles**, choose **Quick profile**, click **Start a conversation**. Both judges scan a QR code, type their name, tick consent and answer one question. Show the profile chips and the X button: "you control what it knows." Both tap **I'm ready**.
3. **Calibration + opener (20 s).** Click **Start listening** on the Table. It asks Seat 1, then Seat 2, to say hi (this maps the voices to seats), then drops the overlap opener about the two of them.
4. **Hold (20 s).** A teammate says "wait, let me show you something" and scrolls a phone. The orb turns lilac: *Holding (show)*. Nothing interrupts.
5. **Steer and switch (30 s).** Everyone goes quiet. At 3 s a steer lands on the current topic (the chime at 3 s, the words just after); if nobody answers, about 6 s after it ends a context switch arrives. A bystander chatting behind the table or a noisy room does not hold it back.
6. **X-ray (10 s).** Press `X`: pause timer vs thresholds, staleness and its parts, hold state, what a crumb is waiting for, cached crumbs, the memory, and the reasoning for the last crumb.
7. **Close (10 s).** Tap **End**, then **Yes, end**. Both phones ask "Keep in touch?". On a mutual yes each phone shows the other's contact and a short trail, and the session is deleted. "Every AI icebreaker helps you start a conversation. Breadcrumbs keeps it going, and knows when to stay quiet."

Demo hygiene: a quieter corner, the laptop between the two speakers, volume about 60%.

## The web app

**Design.** An ElevenLabs-style light design replaced the dark PRD 14.3 palette: a warm off-white canvas (`#F7F5F2`), near-black ink (`#0C0B0A`), black and white pill buttons and 28 px cards. Fonts are Inter Tight (display), Inter (text) and JetBrains Mono (numbers), from Google Fonts. Motion respects `prefers-reduced-motion`.

**Crumb Orb.** A raw WebGL 1 shader draws domain-warped color clouds on a shaded sphere, with film grain and a white specular tip that stays white on every palette. Palettes follow the Table state only (there are no per-mode colors any more): ember (idle, listening, warming, crumb), lilac (holds), sage (listening paused) and cocoa (safety pause); citrus is used only for decorative trail dots. Home shows a single orb with no side orbs or carousel. Still orbs share one WebGL context. Orbs under 40 px, browsers without WebGL and the first frame use a CSS gradient orb with SVG grain.

**Screens.**

| Route | Screen |
|---|---|
| `/` | Home: one orb, the setup control (Just talk / With profiles, with Quick profile (1 question) or Full profile (3 questions) under With profiles), Start a conversation, and status pills (`Muse: mock/live`, `AI: Claude, live` / `AI: Gemini, live` / `AI: mock`, `ffmpeg missing`, `Dev tools on`) |
| `/table/:sid` | The Table. **Just talk:** one consent card (the consent line, two optional first names, "We're both in"), a short "Listening. Just talk." screen until the audio arrives, the live stage (with a welcome hint until someone speaks), then **Threads to pick up** with a **Done** button that deletes the session. **With profiles:** lobby with QR codes, calibration, live stage (orb, captions, crumb trail, controls), end screen with "Wipe now". No mode badge or mode picker in either. |
| `/join/:sid/:seat` | The phone: consent, voice interview (up to 20 s per answer, at least 1.2 s), profile review (remove chips, off-limits presets: work, family, dating history, politics, religion, health), waiting, live, opt-in, trail. A reload resumes where it left off. A Just talk table's link shows "This table doesn't need phones" and never claims a seat. |

**Table controls and shortcuts.**

| Control | Key | What it does |
|---|---|---|
| Drop a crumb | `Space` | Immediate context switch (see the engine section) |
| Hold 60 s / Resume | `H` | Manual 60 s show hold |
| Pause / Resume listening | `P` | Stops the mic and freezes the pause timer |
| X-ray | `X` | Opens the debug drawer; `Escape` closes it |
| Skip | | Stops the current crumb, removes its caption and asks for a different switch |
| End | | Two-step confirm (`Yes, end` / `Keep talking`, auto-cancels after 5 s; `Escape` cancels) |

`Space`, `H` and `P` work in the live phase; `X` also works during calibration. Shortcuts are ignored while typing and with Cmd/Ctrl/Alt held.

**Captions.** The whole line lands in 0.3 s. It stays up while it is voiced and then for max(4 s, 0.35 s per word + 2 s), at least 6 s in total. A small "last crumb" recap then stays for 30 s. Skip removes the caption at once. The small source line under a crumb never claims a profile on a Just talk table; the icebreaker there reads "to get you started".

**X-ray.** Pause timer (with a 60 s sparkline), staleness parts, engine state (including what a crumb is waiting for and the learned seated level) and cached crumbs, last crumb, the live memory (each person, shared, open threads), transcript (voices away from the table greyed), decisions (with the numbers behind each `background_turn`), label map, metrics, tuning sliders (`T_NORMAL_S` 0.5 to 5 s, `T_LONG_S` 1.5 to 10 s, `T_EXC_S` 2.5 to 12 s, `STALE_SWITCH` 0.3 to 0.9, `NEAR_MARGIN_DB` and `FAR_MARGIN_DB` 4 to 20 dB), the wildcard and bystander-gating switches and, with `DEV_TOOLS`, "Dev: inject turn".

**Browser voice fallback.** When the server sends no audio, the player picks the most natural English voice available (Natural/Neural, Premium, Enhanced, Google US English, Ava, Samantha and others, with macOS novelty voices excluded), splits long lines into sentences, and falls back to a silent caption timer if speech fails.

## Architecture (PRD 10.2)

```
            ┌───────────── Phone (Seat 1/2, With profiles only) ──────────────┐
            │ Join → Consent → Record answers (MediaRecorder) → Review → Trail │
            └───────────────┬──────────────────────────────────────▲───────────┘
                    HTTPS REST (answers, profile edits; seat cookie) │ WS /ws/events (state, trail)
                            │                                      │
┌──────────────────── FastAPI server (Table laptop) ──────────────────────────────────────────┐
│  REST /api/*      Session store (in-memory, TTL)        Static web app (React build)        │
│                                                                                              │
│  Profile pipeline: ffmpeg → Muse REST transcribe → profile extract → overlap finder         │
│                                                                                              │
│  Live pipeline:                                                                              │
│   /ws/audio/{sid} (PCM 24k) ──► MuseRealtimeClient (DIARIZATION, paced) ──► Engine          │
│   /ws/events/{sid} (VAD, controls, tts_state, params) ─────────────────────► Engine         │
│   Engine (10 Hz): pauses, staleness, holds, policy, echo/noise/foreground rules              │
│   CrumbService: prefetch cache, validation, facts, lazy voicing; live memory                 │
│   AI gateway: Claude → Gemini → Muse Spark → heuristics    Voice: ElevenLabs → Gemini TTS    │
│   Broadcast: transcript, engine snapshot (4 Hz), crumbs (+audio) ──► /ws/events/{sid}        │
└──────▲───────────────────────────▲──────────────────────────▲──────────────────▲────────────┘
       │ WSS + HTTPS                │ HTTPS                    │ HTTPS            │ HTTPS
   Meta Model API             Anthropic API             ElevenLabs API      Google Gemini API
   (Muse ASR, Spark)          (Claude, web search)      (TTS)               (fallback text, TTS)

            ┌─────────────────────────── Table (Chrome) ────────────────────────────┐
            │ Mic → AudioWorklet (Int16 80 ms frames + voice features) → /ws/audio   │
            │ Speech-aware VAD → echo-aware PlaybackGate → /ws/events                │
            │ Crumb Orb (WebGL), captions, controls, X-ray, CrumbPlayer             │
            └───────────────────────────────────────────────────────────────────────┘
```

| Path | What it is |
|---|---|
| `server/main.py` | FastAPI app: REST, `/ws/events`, `/ws/audio`, seat ownership cookie, static SPA |
| `server/config.py` | Environment, provider selection, PRD 8.12 defaults and the live-tunable `Params` |
| `server/session_store.py` | In-memory sessions, per-socket send queues, broadcast, wipe, 2 h TTL sweeper |
| `server/engine/runner.py` | Per-session async shell: 10 Hz tick, 4 Hz snapshot, effect executor, need-based prefetch, lazy voicing, Muse lifecycle, ElevenLabs warm-up, dev turns |
| `server/engine/*.py` | The pure, deterministic conversation engine (pause timer, staleness, holds, policy, calibration, echo and noise rules) |
| `server/ai/gemini.py` | The AI gateway (Claude → Gemini → Muse Spark) and the Gemini client |
| `server/ai/claude.py` | Claude client: structured output, server-side fallback, web search grounding |
| `server/ai/elevenlabs.py` | ElevenLabs voice client: model chain, retries, pauses, warm-up |
| `server/ai/tts.py` | Voice chain, speech text cleanup, system-line audio cache |
| `server/ai/memory.py` | The live memory (each person, shared, open threads) learned from the periodic energy-tag call, with safety and off-limits filters |
| `server/ai/*.py` (other) | Prompts (including the fixed icebreakers), profiles + overlaps, crumbs + validator + cache, classifiers, facts, trail, heuristics |
| `server/muse/*.py`, `server/audio/convert.py` | Muse realtime + REST clients, ffmpeg wrapper |
| `server/sim/` | Engine simulator (`replay.py`, 41 scripts), end-to-end smoke test (`smoke_e2e.py`), usage and cost harness (`usage.py`), each with `--setup profiles`, `quick` or `both` |
| `web/src/pages` | `Home`, `Table`, `Join` |
| `web/src/components/table` | Table state machine, the Just talk screens (`QuickSetup.tsx`), orb renderer (`orbGl.ts`, `orbLook.ts`), caption timing, echo-aware `PlaybackGate` (`playback.ts`), shortcuts |
| `web/src/components/phone` | Phone flow steps |
| `web/src/audio`, `web/public/pcm-worklet.js` | Mic capture + speech-aware VAD, `CrumbPlayer` (chime, WAV, browser voice), phone recorder |
| `web/src/lib` | REST client, WebSocket client (reconnect with backoff), `useTable`, the VAD message builder (`vadMessage.ts`), shared types |

Lifecycle, With profiles: `lobby` (created) → `profiles` (first consent) → `calibrating` (both ready: overlaps computed, engine started; calibration begins when the Table's audio arrives) → `live` (voices mapped, opener delivered) → `ended` (End, or the round timer of the API-only speed-dating mode) → wiped (both phones fetched the trail, `DELETE`, or the 2 h TTL). Profiles lock once calibration starts.

Lifecycle, Just talk: `lobby` (created, no join links) → `calibrating` (`POST /start`: both consents recorded from the Table, engine started with empty profiles) → `live` (the first Table audio, with no prompts and no opener) → `ended` (End: the Table's trail is written from the memory and transcript and sent as `table_trail`) → wiped (Done / `DELETE`, 10 minutes after the end, or the 2 h TTL).

## The engine in one page

The engine (`server/engine/`) is pure and clock-injected, so the simulator can test it without a mic. Every 100 ms it decides whether to speak, using the PRD Section 8 policy:

| Pause band | Silence | What Breadcrumbs does |
|---|---|---|
| Normal | under 1.5 s (`T_NORMAL_S`) | Nothing |
| Warming | 1.5 to 3.0 s | Nothing spoken; the orb glows; the one crumb this pause would get is voiced in advance |
| Unusually long | 3.0 s (`T_LONG_S`) | **Steer** on the current topic, or **switch** if the conversation is stale (staleness 0.60 or more) or the last steer did not land |
| Exceptionally long | 4.5 s (`T_EXC_S`) | **Context switch** (when the 3 s steer was blocked); after a crumb nobody answered the switch waits 6 s (`UNANSWERED_SWITCH_S`) |

- **Silence** counts from the end of the last turn by someone at the table (backdated to where the Table mic heard the voice stop, at most 1.2 s before Muse's speechEnd), or from the end of bot speech. It reads 0 while someone near the mic is talking or while Breadcrumbs is speaking. Bystanders and background noise never restart it: they only defer a crumb while they last (see the noise rules below). It restarts at 0 when a crumb finishes, when a show or external hold times out, and on Resume (a think hold that times out leaves the timer running). It is frozen while listening is paused.
- **Staleness** `S = 0.35 L + 0.25 R + 0.15 G + 0.10 Q + 0.15 E` over the last 6 human turns against the session baseline (first 10 turns or 180 s): reply-length decay, short-reply rate, gap growth, question deficit and an LLM energy tag. The energy tagger runs every 4 seat turns (`ENERGY_EVERY_N_TURNS`), only on new words. It is skipped while automatic crumbs stay blocked for 60 s or more (the rate cap) and during the safety pause; in Just talk it keeps running under the rate cap at half rate (every 8 turns, with every turn since the last tag as context), because it also feeds the live memory.
- **Live memory** (both setups): the same energy-tag call also returns short items about each person, what they share and open threads (`server/ai/memory.py`), so it costs no extra call. Items pass the safety and off-limits filters, are de-duplicated and capped (8 per person, 6 shared, 6 threads), go to the Table as `memory` events, and feed the crumb prompt (newest 3 per list). In Just talk, switches alternate between a memory item (the newest open thread, then the person featured less, then shared) and an outside question. People who typed no name are "Person 1" / "Person 2" on screen, and those labels are never spoken or written in a crumb or thread.
- **Holds.** The Appendix A lexicon is matched on the last 8 words. A lexical hit is sent to the hold classifier at once. An ambiguous fragment (no final punctuation, a trailing "...", or under 4 words) or a vetoed phrase ("wait, really?") is classified only after 0.8 s of silence (`HOLD_CHECK_SILENCE_S`), and automatic crumbs wait for that answer until `T_LONG_S` + 0.8 s. Backchannels never trigger this deferred check (a backchannel that matches the lexicon, such as "mm hmm", is still a lexical think hit). Answers below 0.5 confidence are ignored. `show` suppresses crumbs for up to 60 s, `think` doubles the thresholds (6 s / 9 s) for up to 20 s, `external` suppresses for up to 90 s. Speech releases every hold except the manual one. The Hold button is a 60 s `show` hold.
- **Guards:** 20 s warm-up after the opener (Just talk: after going live); regular cooldown of 30 s since the last crumb ended plus 2 human turns; exceptional cooldown of 15 s; at most 6 automatic crumbs per rolling 10 minutes; after 2 unanswered crumbs in a row it goes quiet and shows a "Drop a crumb" hint.
- **Speech cancels in-flight crumbs.** If someone at the table talks while a crumb is being generated, it is never sent. A Muse turn the Table mic does not hear as near holds the ready crumb (up to 2 s) until it is judged: a bystander lets it go after a 0.4 s settle, a seat cancels it. Manual, opener and round-warning crumbs are re-queued; cancelled automatic crumbs do not count toward the rate cap.
- **Drop a crumb** releases any hold and serves the cached switch at once, ignoring cooldowns, warm-up and the rate cap. It waits for silence, ignores a double tap, and still works during the safety pause. A cache miss gets 2.0 s for text and 1.0 s for audio and never runs a web search (it can only reuse a fact already found this session), so it lands within 3 s, falling back to an overlap or canned crumb and the browser voice.
- **Skip** discards the current crumb and asks for a different switch.
- **Prefetch is text only and need-based.** Two slots (a ready steer and a ready switch) are regenerated only when missing, invalid, or (for the steer) 3 or more lines stale. A turn that opens a pause a steer could fill refreshes at once; other turns refresh after 1.5 s; a 30 s check runs while live. Cached text expires after 90 s.
- **Lazy voicing.** When a pause enters the warming band, only the crumb that pause will get is voiced: `eleven_v4` when it is due at least 1.8 s ahead, otherwise Flash. With the 1.5 s warming lead, steers are voiced by Flash and switches by `eleven_v4`. If the natural take is not ready when the crumb fires, Flash voices it on the spot.
- **A steer miss never waits.** The 3 s steer is served from the cache. If its refill is still being written and is not due within 0.5 s, the slightly older steer (at most 3 lines old) goes out at once, else the ready switch, else an overlap or canned crumb.
- **Calibration (With profiles)** asks Seat 1 to say hi, then Seat 2, and maps the first new Muse label after each prompt. After 15 s it maps labels by order of appearance. A Muse reconnect re-runs a quick voice re-sync, and automatic crumbs wait until both voices are mapped again.
- **Just talk** has no calibration screen and no opener: it goes live with the first Table audio, and each new Muse label of a voice judged to be at the table takes the next free person, by order of appearance (a far voice never takes a free seat, so a bystander cannot become Person 2). Crumbs never wait for both voices. If nobody at the table has spoken after `T_LONG_S` (3 s) of silence, a fixed icebreaker (one of three lines, no model call) goes out. A Muse reconnect re-maps labels the same way, with no prompts.
- **Safety.** A distress-lexicon hit pauses automatic crumbs for the rest of the session. Drop a crumb still works.
- **Modes.** Home no longer offers social / networking / speed dating: every new session is `social`. The other modes still exist in the API (`mode` on `POST /api/sessions`); speed dating there has a 6-minute round with a "one minute left" switch crumb at 60 s left.
- **Wildcard crumbs** exist but are off by default (`WILDCARD_ENABLED`).

### Echo and noise handling

The laptop's mic hears the laptop's speaker, and a real room has other voices and noise. Without these guards, Breadcrumbs cut off its own crumbs after one word, and background noise kept restarting (or freezing) the pause timer so a crumb never came.

| Layer | Rule |
|---|---|
| Table VAD (`capture.ts`, `pcm-worklet.js`) | Speech-aware, not RMS-only: 300 to 3,400 Hz band energy, zero-crossing rate and an impulse measure, an adaptive noise floor, syllable-rate modulation, SNR hysteresis (10 dB on, 5 dB hold), 200 ms attack, 500 ms release, 1.5 s room calibration |
| Table echo gate (`playback.ts`) | While a line plays, the gate learns the echo level. A person counts only when voiced and 6 dB louder than the echo (12 dB while the estimate is cold) for 200 ms. The chime and the first 300 ms of voice are a grace period. Echo-level onsets are not sent to the server, including a 400 ms tail. Barge-in fades the line over 250 ms. |
| Server, by time | A Muse turn that overlaps bot speech plus a 300 ms tail by more than 50% is dropped as bot speech. A Muse `speechStart` alone during a crumb never barges in; local VAD must agree. |
| Server, by text | A Muse turn that starts during a bot line, or within 1.0 s after it, is echo when at least 50% of its words (`ECHO_TEXT_OVERLAP`) fall in word pairs shared with a bot line that was playing (or ended under 1.0 s earlier) when the turn started. Three or more new words after the echo turn it into a human reply. |
| Server, noise rule | Local VAD never restarts the pause timer. A VAD onset only defers a crumb, for 1.5 s (`VAD_DEFER_S`, bursts share one window) or up to 4 s (`VAD_NEAR_MAX_S`) while it sounds like someone at the table. A stretch that outlives the window with no Muse turn marks the room as noisy until a seat turn closes or the VAD rests for 4 s, and records the noise floor. In a noisy room, steady noise at the floor with no Muse turn open stops counting as near 4 s after its onset or the last seat turn, even when it is close to the seats' level, while a voice 6 dB (`NOISE_RISE_DB`) above the floor still counts as someone at the table (before the seated level is known, as at the start of Just talk, that rise is the only near test). Only a near voice cancels a crumb being prepared or barges in. The Table then shows "Background noise ignored" (never while a near voice is talking). |
| Server, foreground rule | Every Muse turn is judged once at speechEnd: its label (mapped to a seat or not), its words, and the Table VAD over it (coverage and level against the seated level learned at calibration: near within 8 dB, far below 12 dB). Seat turns count; voices away from the table (bystanders, fillers the mic hardly heard, empty fragments) are `background`: greyed in the X-ray and never part of the pause, the transcript sent to the model, staleness, holds or prefetch. An unmapped label counts as a seat only when near with at least 1 word (`DRIFT_MIN_WORDS`), so a seat whose label drifted still restarts the pause with a short reply. While a turn is open it only blocks firing; one that has been open 2.5 s (`OPEN_TURN_MIN_S`) stops blocking once the mic has heard no near voice for 2 s (`OPEN_TURN_RELEASE_S`), so a TV or a bystander Muse keeps open cannot hold a crumb. A ready crumb waits up to 2 s while a turn is still being judged, then goes back to the cache. `BYSTANDER_GATING` is the kill switch. |

### Live tuning

The `Params` fields in `server/config.py` can be changed per session from the X-ray tuning panel or `PATCH /api/sessions/{sid}/params`: the thresholds and `ADAPTIVE_THRESHOLDS`, `STALE_SWITCH` and the staleness weights, the baseline and recent-turn windows, `DEFAULT_GAP_S`, `SHORT_TURN_WORDS`, `ENERGY_EVERY_N_TURNS`, warm-up, cooldowns, rate cap, unanswered limit, hold durations and tail, `TTS_TAIL_MS`, `WILDCARD_ENABLED`, `UNANSWERED_SWITCH_S`, `VAD_DEFER_S` (the old `VAD_UNCONFIRMED_S` key still sets it), `VAD_NEAR_MAX_S`, the foreground fields (`BYSTANDER_GATING`, `NEAR_ACTIVE_FRAC`, `FAR_ACTIVE_FRAC`, `NEAR_MARGIN_DB`, `FAR_MARGIN_DB`, `FILLER_MAX_WORDS`, `DRIFT_MIN_WORDS`, `OPEN_TURN_MIN_S`, `OPEN_TURN_RELEASE_S`, `BACKGROUND_SETTLE_S`, `ENDPOINT_BACKDATE_MAX_S`), `ECHO_TEXT_OVERLAP` and `HOLD_CHECK_SILENCE_S`. Unknown keys are ignored. `NOISE_RISE_DB` (6 dB), `NOISE_LATCH_CLEAR_S` (4 s), `DISPATCH_WAIT_S` (2 s), `CRUMB_MAX_WORDS` (28), the prefetch timings, `CACHE_MAX_AGE_S`, `BARGE_IN_FADE_MS`, `SESSION_TTL_S`, the ramp and depth limits, `GAP_CLAMP` and `MODE_MULTIPLIER` are fixed constants.

## API reference

All routes are in `server/main.py`. Every seat route (`/seats/{seat}/...`) belongs to the With profiles setup and answers 409 "This table doesn't need phones" on a Just talk table. Answer, profile build/edit and ready routes require consent first (403). Every seat route after consent requires that seat's cookie (403 "This seat is already in use on another phone"). Profile edits return 409 once the conversation has started.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | `{ok, sessions, ...config}` |
| GET | `/api/config` | `mock {muse, gemini}`, `ai_provider`, `dev_tools`, `public_base_url`, `ffmpeg` |
| GET | `/api/interview?mode=&quick=` | Interview questions |
| POST | `/api/sessions` | Create a session. Body `{setup?: "quick" \| "profiles" (default "quick", Just talk), mode?: (default "social"), quick?: bool (Quick profile, profiles only)}`. Returns `{session_id, join_urls, setup}`; `join_urls` is `{}` for Just talk |
| GET / DELETE | `/api/sessions/{sid}` | Summary / wipe now |
| POST | `/api/sessions/{sid}/start` | Just talk only: "We're both in". Body `{names?: {"1"?, "2"?}}` (blank = Person 1 / Person 2, at most 60 characters). Records consent for both seats from the Table (`source: "table"`), marks both ready and starts the engine; 409 for a profiles table or once started |
| POST | `/api/sessions/{sid}/end` | End the conversation |
| GET | `/api/sessions/{sid}/trail` | Just talk only, after the end: `{trail}` (`null` while it is being written); 409 otherwise |
| PATCH | `/api/sessions/{sid}/params` | Live-tune `Params` (422 on a bad value) |
| GET | `/api/sessions/{sid}/metrics` | Phase, turn count, counts and latencies (no text) |
| POST | `/api/sessions/{sid}/seats/{seat}/consent` | Name + consent; mints the seat cookie |
| POST | `/api/sessions/{sid}/seats/{seat}/answers` | Multipart `question_id` + audio (32 MB max) |
| POST / GET / PATCH | `/api/sessions/{sid}/seats/{seat}/profile` | Build (30 s, then heuristic) / read / edit (`removed_item_ids`, `off_limits`) |
| POST | `/api/sessions/{sid}/seats/{seat}/ready` | Seat is ready |
| POST | `/api/sessions/{sid}/seats/{seat}/trail-optin` | `{share, contact}` after the session ends |
| GET | `/api/sessions/{sid}/seats/{seat}/trail` | Contact + trail on a mutual yes |

WebSockets:

- `/ws/events/{sid}?role=table|seat1|seat2`: state, transcript, engine snapshot, crumbs. The Table also gets `memory {memory}` whenever the live memory changes (both setups; the snapshot carries it too) and, on a Just talk table, `table_trail {trail}` once the trail is ready (re-sent to a Table that connects later). `turn` events carry `background` and `session` summaries carry `setup`. A seat socket on a Just talk table only receives the `session` summary. The Table sends `control` (`end`, `spark`, `skip`, `hold`, `resume`, `pause_listening`, `resume_listening`, `xray`), `vad` (`{active, rms, level_db?, quiet_ms?}`: the speech-band level and the ms since the voice stopped, nothing else), `tts_state`, `params` and, with `DEV_TOOLS`, `dev_turn`.
- `/ws/audio/{sid}`: binary PCM16 24 kHz frames from the Table.
- Close codes: 4404 unknown or wiped session, 4403 bad role or audio before both seats consented (Just talk: before `/start`) or after the end, 1013 a client too slow to keep up.

## Privacy design

- **Consent first.** With profiles, no audio is accepted for a seat until it has a typed name and a ticked box on that person's phone. In Just talk, both people agree together on the Table's consent card ("Breadcrumbs listens and transcribes your conversation live. Nothing is stored."); "We're both in" records a timestamped consent for both seats with `source: "table"`, and the browser sends no audio or VAD until the server confirms it. Either way the Table's audio socket is refused (close code 4403) until **both** seats have consented, and after the session ends.
- **One phone per seat.** The first consent mints a per-seat secret, set as an HttpOnly, SameSite=Lax cookie (`bc_seat1` / `bc_seat2`) scoped to that seat's API path. Another phone cannot act for that seat. The secret never appears in the session summary. The Table itself is not authenticated (see Known limitations).
- **No storage.** Live audio is streamed to Muse and never written to disk. Profile answers are converted in a private temp dir that is deleted immediately. Transcripts, profiles, the live memory and crumbs live only in server memory.
- **The live memory** (what Breadcrumbs learned about each person from the conversation) lives in the session's runner, passes the same safety and off-limits filters as crumbs, is shown only on the Table (X-ray), and is wiped with the session. It is never logged (counts only). After a safety pause it is kept out of the trail entirely.
- **Wiped by default.** With profiles, everything is deleted as soon as both phones have fetched their trail, on `DELETE /api/sessions/{sid}` (the Table's "Wipe now"), after 2 hours, or when the server shuts down, whichever comes first. A Just talk session is deleted by the trail screen's **Done** button (`DELETE`), 10 minutes after the conversation ended, after 2 hours, or at shutdown. Every client then gets `wiped` and the sockets close with 4404. Voiced lines that contain a name are cached per session and wiped with it.
- **Your profile, your rules.** Removed chips, and profile items that match an off-limits topic, are left out of every prompt. The off-limits terms are sent to the model only as an avoid-list, and every crumb and trail item is checked against them again (Claude, Gemini or Spark). Words spoken aloud reach the model through the recent transcript.
- **Mutual opt-in (With profiles).** Contacts and the trail are shown only if both people say yes. A Just talk trail has no contacts and is shown on the shared laptop to both people. The session summary sent to both phones only says whether each seat has answered (`opted_in: true` once answered, never `false`), so nobody learns who declined.
- **No transcript text in logs.** The server logs decisions, counts and timings only (a `background_turn` decision carries numbers only: duration, words, coverage, levels). Gemini calls use `store=False`.
- **Safety.** Distress phrases pause automatic crumbs for the session; speed dating prompts never reference bodies, appearance or sexual topics.

## Cost and usage

`python -m sim.usage` runs one scripted 10-minute social session (72 human turns with profiles, 70 in Just talk) through the real runner, engine, crumb service, classifiers and voice clients. Only the network edge is fake: a fake Claude client with modeled latencies, a mock ElevenLabs transport, no Gemini key (as on the live server after its 402), Muse turns injected as dev turns, and a virtual clock. It bills nothing. `--setup profiles` (the default), `quick` or `both` picks the setup.

| Per 10-minute session | Before usage cuts | With profiles | Just talk |
|---|---|---|---|
| Claude calls | 125 | 63 (about 54,900 input tokens) | 62 (about 55,300 input tokens) |
| ElevenLabs calls | 90 | 10 | 8 |
| ElevenLabs credits | 11,058.5 | 812.5 (181 wasted) | 769.5 (187 wasted) |
| Sessions per 10,000-credit free month | 0.9 | 12.3 | 13.0 |
| Crumbs served from cache at 0 ms | 3 of 8 | 7 of 8 | 7 of 8 |

Where the Claude calls go. With profiles: steer prefetch 29, switch prefetch 9, energy tags 13, hold classifier 6, grounded facts 2, profiles 2, overlaps 1, trail 1. Just talk: steer prefetch 29, switch prefetch 9, energy tags 15 (it keeps tagging at half rate under the rate cap, for the memory), hold classifier 6, grounded facts 2, trail 1, and no profile or overlap calls; its icebreaker is a fixed line (no model call). The live memory rides on the energy-tag call, so it adds no calls, but the prompts now carry it: input tokens with profiles went from about 44,200 before this round to about 54,900 per session.

The harness models standard-speed latencies; fast mode (crumb drafts and the hold classifier) bills those two jobs at twice the token price but adds no calls. In both setups automatic steers are decided at 3.05 s of silence (p50, max 3.07 s; words at 3.84 s), and all automatic crumbs at 3.79 s p50 (max 6.05 s, the follow-up after an unanswered crumb). `--bystanders` replays the session with a voice behind the table every 4 s of quiet: with gating on, 77 "turn committed" prefetches and 64 Claude calls (steers decided at 3.3 s p50); with gating off, 123 and 95. The Muse figures (6 REST transcriptions with profiles, 0 in Just talk; about 10.3 realtime minutes) are derived from the script, not measured. Gemini TTS shows 0 calls because the harness has no Gemini key.

The cuts (commit `c0422ab`): text-only prefetch, voicing only the next crumb, regenerating text only when it is stale or invalid, caching system lines, deferring ambiguous hold checks to 1 s of silence, and skipping idle energy tags.

## Testing

```bash
cd server
.venv/bin/pytest -q                          # every server test (762); conftest.py keeps AI and ElevenLabs offline
.venv/bin/pytest tests/test_api.py -q        # integration: full flow over REST + WebSockets in mock mode
.venv/bin/pytest tests/test_quick_setup.py tests/test_bystander.py -q   # Just talk, and the foreground (bystander) rules
.venv/bin/python -m sim.replay               # simulator: all 41 scripts with a decision timeline
.venv/bin/python -m sim.replay sim/scripts/show_hold_then_resume.json
.venv/bin/python -m sim.usage                # usage and cost report for one 10-minute session (--setup profiles|quick|both, --json out.json, --repeat, --bystanders)

# end-to-end smoke test against a running mock server (DEV_TOOLS=true)
ANTHROPIC_API_KEY= ELEVENLABS_API_KEY= MODEL_API_KEY= GEMINI_API_KEY= .venv/bin/uvicorn main:app --port 8133 &
.venv/bin/python -m sim.smoke_e2e --base http://127.0.0.1:8133 --setup both   # profiles: create, consent, profiles, calibration, opener, steer, spark, end, trail, wipe; then Just talk

cd ../web
npm run build                                # typecheck + production bundle
node --import ./src/components/table/__tests__/register.mjs --test 'src/components/table/__tests__/*.test.mjs'   # 130
node --import ./src/components/table/__tests__/register.mjs --test 'src/audio/__tests__/*.test.mjs'              # 14
node --test src/components/phone/flow.test.mjs                                                                  # 26
```

To be sure nothing bills, prefix any server command with `ANTHROPIC_API_KEY= ELEVENLABS_API_KEY= MODEL_API_KEY= GEMINI_API_KEY=` (blank environment variables beat `.env`).

The 41 simulator scripts are the 13 PRD 16.1 conversations plus `background_noise`, `bursty_noise`, `self_echo_transcript`, `real_barge_in_still_works` and `echo_then_reply_same_turn` (these 18 are pinned with `params` to the pre-3 s timings, 3.0 / 5.0 / 6.5 s), 18 on the 3 s defaults: `three_second_steer`, `natural_gaps_under_3s`, `unanswered_fast`, `think_hold_fast`, `bystander_fragments`, `bystander_on_seat_label`, `long_bystander_turn_release`, `split_turn_settle`, `label_drift_near`, `label_drift_short_reply`, `seated_backchannel_resets`, `noise_defers_not_resets`, `near_level_noise`, `near_level_noise_throughout`, `tv_speech_mid_level`, `muse_late_seat_start`, `endpoint_lag_backdate` and `endpoint_lag_cap`, and 5 "Just talk" scripts: `quick_icebreaker`, `quick_talk_right_away`, `quick_bystander_first`, `quick_noise_then_person` and `quick_burst_then_person`.

The smoke test plays both phones and the Table over real HTTP and WebSockets: it streams 80 ms silence frames in real time, sends VAD at 10 Hz, answers every crumb with `tts_state` like the browser player, and checks that a lively conversation gets no crumb, a steer lands about 3 s into the silence, and Drop a crumb answers in under 3 s. `--setup quick` drives Just talk instead (no join links, phone routes refused, `POST /start`, live with no prompts, the icebreaker about 3 s into the first silence, the memory arriving, the Table's trail, Done wipes the session; the Person labels are never spoken or written); `--setup both` runs profiles, then Just talk.

The frontend tests use Node's built-in `node:test` (no test framework). They cover the Table state reducer (including the Just talk stages, memory and the Table trail), VAD and the VAD message, the echo-aware playback gate, mic-loss handling, caption timing and the caption slot, orb looks, `CrumbPlayer` on fake WebAudio and speechSynthesis, and the phone flow.

## Known limitations

- **Gemini and Claude grounding are untested live.** The Gemini key returned 402 (no prepaid credits). Gemini text, TTS and grounding, and Claude's web-search grounded facts, have only run in tests and mocks.
- **Thresholds are untuned with real pairs.** `T_LONG_S`, `T_EXC_S` and `STALE_SWITCH` need the live tuning pass described in PRD 16.2 (use the X-ray tuning panel). `VAD_DEFER_S`, `VAD_NEAR_MAX_S`, `NOISE_RISE_DB`, `ECHO_TEXT_OVERLAP` and `HOLD_CHECK_SILENCE_S` are also untuned.
- **The bystander margins are first guesses.** Chrome's auto gain control narrows the level gap between the seats and the room, and 8 dB (near) / 12 dB (far) come from the simulator. Tune them once live from the `background_turn` numbers in the X-ray; `BYSTANDER_GATING` off restores the old behaviour (minus the VAD reset). At the start of Just talk, before two seat turns have set the seated level, a voice counts as near in a noisy room only when it is 6 dB over the noise floor. A seated person who speaks very softly or turned away can be judged far: their words then stay out of the crumb context (a crumb still never fires while they talk).
- **3 s crumbs are more eager.** Thinking gaps of 3.0 to 3.5 s now get a steer, bounded by the 30 s cooldown, 2 human turns between crumbs, 6 per 10 minutes and the think hold (6 s / 9 s). Watch `false_interruptions` in the X-ray metrics; set `T_LONG_S` to 2.5 if the words themselves (not the chime) should start by 3 s.
- **Quiet barge-in.** Someone speaking at or below the speaker-echo level cannot interrupt a crumb locally; Skip is the way out. The first second of each line uses the cold 12 dB margin.
- **Echo captions.** When a reply is merged into an echo turn, its caption includes the echo words.
- **ElevenLabs free tier.** 10,000 credits a month covers about 12 scripted 10-minute sessions. A 401/402 pauses ElevenLabs for 10 minutes, so restart the server after fixing a key or quota.
- **The tunnel URL changes** every time cloudflared restarts; update `PUBLIC_BASE_URL` and restart uvicorn.
- **Claude fast mode is unverified on this account.** It falls back to standard speed on its own (a 429 retries at standard speed, a 400/403 about speed turns it off), and `CLAUDE_FAST_JOBS=` turns it off. The usage harness does not model fast-mode latency.
- **Not done this round, on purpose:** a pitch or music classifier in the audio worklet, auto-gain changes, sending audio while listening is paused, and changes to the Muse send queue.
- **The Just talk memory learns in batches.** It is updated with the energy tag every 4 seat turns (every 8 under the rate cap), so the first items appear after a few exchanges, and it stops learning during a safety pause. The icebreaker is one of three fixed lines.
- **Speculative steers** still make up most of the Claude calls (26 of 29 steer prefetches unused in the usage run). Claude prompt caching is not set up, and the energy tag is not yet folded into the crumb call.
- **The Table is not authenticated.** Anyone who has the session id, including both phones since it is in the join links, can open `/ws/events/{sid}?role=table` and receive the live transcript, memory and controls, or call `DELETE`, `/end`, `/start`, `GET /trail` and `PATCH /params`. Only the seat routes are cookie-protected.
- **Single process, in memory.** Restarting the server ends every session (by design: nothing is persisted). One Table per session: a second Table tab that streams audio takes over the Muse session and triggers a voice re-sync.
- **If a phone never fetches its trail**, that session stays in memory until the 2 hour TTL (or the Table's "Wipe now").
- **Keyword-based off-limits and safety filters can over-block** (for example the word "body"; the work toggle covers occupations; a 4+ letter free-text word matches as a prefix). Over-blocking falls back to a safer crumb rather than speaking anything unsafe.
- **Mock mode is for development**: mock profile extraction is rule-based and mock speech has placeholder text, so real conversations need the keys.
- **Speed dating and networking are API-only now.** Home always creates a `social` session; the other modes (and the speed-dating round timer) are reached only through `POST /api/sessions` with `mode`.
- **The production JS bundle is 576 kB** (184 kB gzip); Vite warns above 500 kB. Route code-splitting is a possible follow-up.
- There is no `npm test` script; run the `node --test` commands above.

## What we built during HackTX vs third-party libraries (PRD 15.2)

**Built by our team during HackTX 2026** (everything in this repo):
- The conversation engine: pause timer, thresholds and bands, staleness score, intentional-hold detection, decision policy, cooldowns, calibration and re-sync, the Just talk ordering and icebreaker, self-hearing, echo-by-text and noise rules, the foreground (bystander) classifier and seated-level model, the simulator and its 41 test conversations.
- The server: session store, per-session runner, REST and WebSocket API, the two setups (Table consent for Just talk), consent gating, per-seat ownership cookie, wipe and TTL, static hosting.
- The AI layer: every prompt, the provider gateway (Claude → Gemini → Muse Spark → heuristics), the Claude and ElevenLabs clients, the crumb validator, source rotation, depth ladder, text-only prefetch cache, lazy voicing, the system-line audio cache, fallback chains, the profile and overlap pipeline, the live memory, trail generation (phones and the Table), Claude fast-mode handling, all mock modes and the usage harness.
- The Muse realtime and REST clients (handshake, pacing, reconnect, re-sync, certifi TLS) and the ffmpeg wrapper.
- The web app: Home, the Table (Just talk consent and trail screens, WebGL Crumb Orb, captions, controls, X-ray, crumb trail), the phone flow (consent, voice interview, profile review, opt-in, trail), the audio worklet, speech-aware VAD, echo-aware playback gate, crumb player and recorder, and the light visual design.
- Tests: pytest suites for every module, the end-to-end smoke test, the usage harness, and frontend tests for state, audio, captions, orb and flow.

**Third-party libraries and services we used:**
- Python: FastAPI, Starlette, Uvicorn, Pydantic, websockets, httpx, certifi, anthropic, google-genai, python-dotenv, python-multipart, pytest, pytest-asyncio.
- Web: React, React Router, Vite, TypeScript, Tailwind CSS, framer-motion, qrcode.react; Inter, Inter Tight and JetBrains Mono from Google Fonts.
- Services: Meta Model API (Muse Voice Transcribe realtime + REST, Muse Spark 1.3), Anthropic Claude API (`claude-opus-5-5`, web search tool), ElevenLabs API (`eleven_v4`, `eleven_multilingual_v2`, `eleven_flash_v2_5`), Google Gemini API (Gemini 3.8 Flash, 3.5 Flash-Lite, 3.8 Flash TTS and 3.8 Flash-Lite TTS, Google Search grounding).
- Tools: ffmpeg, Cloudflare quick tunnels (cloudflared).
- AI coding tools: the team built with Claude Code as a coding assistant.

## Project history

| Commit | Milestone |
|---|---|
| `a7f7c68` | Scaffold: PRD, module contracts, shared models, config, web foundations (Vite 8, React 19, TS 7, Tailwind v4) |
| `cce75cb` | Workstream A: audio capture and the Muse realtime + REST clients, ffmpeg wrapper |
| `48181ea` | Workstream B: the conversation engine and simulator (13 PRD scripts) |
| `0f1ec7c` | Workstream C: Gemini AI services, prompts, crumb cache, mock heuristics |
| `3578430` | Workstream D1: Table screen (Crumb Orb, controls, X-ray) |
| `6899099` | Workstream D2: Home and phone join flow |
| `19d6bff` | Integration: FastAPI app, session store, engine runner, smoke test, first README |
| `da7c54d`, `9044e97`, `c318d41` | Review round 1 fixes: cancel crumbs on speech, queued calibration opener, safety filters, per-seat auth cookie, playback timing |
| `76454ab` | Claude as the primary text provider; Muse Spark fallback fixed; certifi TLS for Muse realtime |
| `bed7d38` | ElevenLabs crumb voice with the provider chain; a player that never truncates |
| `9bceb73` | Echo-aware barge-in and noise-robust speech detection (5 new sim scripts) |
| `98b32bc` | ElevenLabs-style light UI with the grainy white-tip WebGL orb |
| `56a20a5`, `86c0118` | Round 2 docs, contracts and build log: round 2 verified (564 tests); ElevenLabs key still blocked at quota 0 |
| `c0422ab` | ElevenLabs live (credit limit fixed, calibration PASS with real keys) and usage cuts: voice only the next crumb, regenerate only stale text, `sim.usage` harness |
| `7b52971` | README refresh (status, providers, setup, history) and `server/requirements.txt` |
| `92a4746` | Live-test round: background-noise immunity (noise defers instead of resetting the pause, noise floor, foreground/bystander classifier, endpoint backdating), the 3 s crumb (1.5 / 3.0 / 4.5 s, Opus fast mode for crumb drafts and holds), the "Just talk" setup (no phones, Table consent, live memory, icebreaker, Table trail) beside "With profiles", and a one-orb Home with no mode categories (23 new sim scripts, 762 tests) |

Commits that only update the build log (`c58d7c6`, `cd4e0f6`) are left out. `BUILD_LOG.md` has the details of every step.

## Keeping this README current

This README is updated with every milestone commit, alongside `BUILD_LOG.md`.
