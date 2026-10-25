# Breadcrumbs: Product Requirements Document

> **Never lose the thread.**
> An AI conversation companion that sits between two people meeting for the first time, stays quiet while the conversation flows, and drops a well-timed "crumb" (a question, a shared thread, a surprising fact) only when the conversation starts to fade.

| | |
|---|---|
| **Event** | HackTX 2026 (Oct 24 to 25, 2026), UT Austin |
| **Primary track** | Meta: "Bringing People Closer Together with AI" |
| **Also entering** | MLH Best Use of Gemini API, Most Entertaining (Jester's Court), Best Overall (automatic) |
| **Deadline** | Devpost submission Sunday Oct 25, 11:30 AM CST |
| **Core AI** | Muse Voice Transcribe (live, diarized transcription) + Gemini API (crumb generation, profiles, classification, voice) |
| **Form factor** | One laptop on the table (the "Table") + each person's phone (setup only) |
| **Doc status** | v1.0, build-ready. Sections 8 to 13 are the build spec for coding agents. |

---

## 0. TL;DR

Two strangers sit down. Each scans a QR code, gives consent, and does a 60-second voice interview on their phone. Breadcrumbs turns those answers into two private profiles that each person reviews and edits. Then the phones go face down.

A laptop on the table listens through Muse Voice Transcribe, which labels who is speaking. Breadcrumbs measures the conversation in real time: how long the pauses are, whether replies are getting shorter, whether anyone is still asking questions. While the conversation is alive, it does nothing: normal pauses of up to about 3 seconds are left alone. When a pause runs **unusually long** (about 5 seconds), it gently **steers** with a question or fact tied to what they were just discussing. When a pause runs **exceptionally long** (about 6 to 7 seconds), or the conversation is clearly going stale, it makes a **context switch**: a new topic drawn from something both people shared, from one person's profile, or from outside both profiles (a fun fact, a fresh topic).

It also knows when *not* to talk. If someone says "wait, let me show you this" and starts scrolling through their phone, Breadcrumbs recognizes the intentional pause and holds.

At the end, each person privately decides whether to share contact info and a short "trail" of threads to pick back up next time. Nothing is shared unless both say yes, and the transcript is wiped.

---

## 1. Why this wins

### 1.1 Mapping to the official Meta judging criteria

From the Meta-Sponsored Track Official Rules:

| Criterion | Weight | How Breadcrumbs scores |
|---|---|---|
| **Human Connection** | 30% | Gets two real people past small talk, face to face, phones down. Research shows people underestimate how much they will enjoy deeper conversations with strangers (Kardas, Kumar & Epley, *JPSP*; 12 experiments, 1,800+ participants; one author is a UT Austin researcher). Breadcrumbs removes the social cost of going deeper: the crumb asked the question, not you. The Trail helps the connection outlast the conversation. |
| **AI Centrality** | 25% | Remove the AI and nothing is left. Muse Voice Transcribe provides live diarized turns. The engine reads conversational dynamics (pause length, reply-length decay, question rate, energy). Gemini builds profiles from voice, finds overlaps between two people, writes context-aware crumbs, grounds outside facts in Google Search, classifies intentional pauses, and speaks with a warm voice. |
| **Originality** | 20% | Existing AI icebreakers (Bumble For Friends, Langame, and many hackathon projects) only help you *open* a chat inside an app. Breadcrumbs lives inside the in-person moment, knows **when** to step in, knows **when not to** (intentional holds), and goes deeper over time. |
| **Execution & Demo** | 25% | A live demo with the judges as participants: they record profiles, talk, and watch Breadcrumbs hold during "let me show you," then step in when they go quiet. An X-ray panel shows the engine thinking in real time. |

### 1.2 Prize strategy

- **Meta track** (primary): Round 2 invite at Menlo Park.
- **MLH Best Use of Gemini API**: Gemini is central (generation, structured output, grounding, TTS).
- **Most Entertaining**: the Jubilee-style energy of a glowing orb that "drops a crumb" is fun to watch.
- **Best Overall** is automatic. **Best Pitch** requires a separate signup on Discord for the 5:30 PM Sunday session.
- Devpost allows **up to 3 tracks excluding Best Overall/Novice**. Choose: Meta, MLH Gemini, Most Entertaining.

### 1.3 Lessons from recent winners baked into this design

- Meta's recent winners (HackGT 13, HackMIT 2026) used **voice as the interface**, had a **tangible live demo**, and showed **privacy by design**.
- Judges reward projects that **quantify themselves** (one HackGT Meta winner reported its own precision and latency). We report silence-to-crumb latency, false-interruption rate, and crumb success rate (Section 16).
- AI is everywhere at hackathons. What stands out is AI used as a **mechanism** (timing, restraint, escalation), not a chatbot bolted on.

---

## 2. Problem and insight

**The problem.** Meeting new people is hard in a specific, fixable way: conversations die in the awkward middle. After "what's your major" and "where are you from," replies get shorter, pauses get longer, and both people reach for their phones. Networking events, speed dating, club socials, orientation, and new-roommate dinners all hit the same wall.

**Why it matters.** About one in two US adults report experiencing loneliness (U.S. Surgeon General's 2023 advisory), and the advisory names loneliness as a risk factor for depression and other conditions. Most people are not short on chances to meet others. They are short on conversations that go anywhere.

**The insight.** Kardas, Kumar & Epley found that people expect deep conversations with strangers to be more awkward and less connecting than they actually are, and they underestimate how interested the other person is in hearing about them. Small talk is not a preference. It is a miscalibration. The fix is not more questions on a card. It is **the right question, at the right moment, asked by someone else**, so nobody has to take the social risk.

**Inspiration.** The "button in the middle" format popularized by Jubilee's dating videos: a neutral third party that occasionally interjects and sparks conversation. Breadcrumbs makes that third party intelligent: it knows both people, it listens to the rhythm of the conversation, and it knows when to stay silent. (Reference the show verbally only. Use no Jubilee clips, logos, or the name "The Button" in any product or submission material.)

---

## 3. Product overview

### 3.1 A scene

> Maya and Dev sit down at a table at a networking mixer. A laptop between them shows a soft amber orb. They each scan a QR code, tap "I agree," and spend a minute telling Breadcrumbs about themselves. Maya mentions she is training for her first half marathon. Dev mentions he quit running after a knee injury and switched to climbing.
>
> The orb pulses once. A warm voice says: *"You two have something in common. You've both been chasing a finish line lately. Maya, what made you sign up?"* They laugh and start talking. Ten minutes in, the replies get shorter. "Yeah." "Totally." A long pause. The orb glows: *"Dev, you said you switched from running to climbing. What does climbing give you that running didn't?"* They are off again.
>
> Later Dev says, "Wait, let me show you this route I did," and spends twenty seconds scrolling for a photo. The orb dims to a slow blue: *holding*. It says nothing.
>
> At the end, both tap "keep in touch." Each phone shows a short trail: *"Ask Maya how the half marathon went (Nov 16)."* The transcript is gone.

### 3.2 The name and the theme

- **Breadcrumbs.** Each intervention is a **crumb**. The end-of-session recap is **the trail**. Like Hansel and Gretel, the crumbs help you find your way back to each other. This fits HackTX 2026's "Once Upon a Hack" fairy-tale theme; open the pitch with "Once upon a time, two strangers sat down at a table..."
- **Heads-up:** in dating slang, "breadcrumbing" means stringing someone along with sporadic messages. Expect a judge to joke about it. Defuse it in the pitch with one line: *"Most breadcrumbing leaves you hanging. Ours leads you somewhere."*

### 3.3 What Breadcrumbs is not

- Not a chatbot or companion. Success means the humans talk more and Breadcrumbs talks less.
- Not a recorder. Audio is never stored; transcripts live in memory only for the session.
- Not a matchmaker that reads your social media. It only knows what each person chose to tell it.

---

## 4. Use cases, personas, and modes

Breadcrumbs is broadly positioned for **meeting new people in person**. The three launch modes share one engine and differ in question style, depth pacing, and timing.

### 4.1 Personas

| Persona | Situation | What they want | Breadcrumbs gives them |
|---|---|---|---|
| **Priya, freshman** | Orientation week, club socials, new roommates | Real friends, not just "hey" in the hallway | Gets past "what's your major" to the stuff that makes friendships |
| **Marcus, job seeker** | Career fairs, alumni mixers, coffee chats | A memorable conversation and a real contact | Finds overlap between what he can offer and what they need; trail with follow-ups |
| **Sofia and James, speed dating** | 6-minute rounds at an event | To know quickly if there's a spark | Playful, escalating prompts; mutual "match" at the end, revealed only if both say yes |
| **Event organizer** | Hosts mixers, orientations, speed-dating nights | Attendees who actually connect | (Future) many tables running Breadcrumbs at once |

### 4.2 Mode presets

| Setting | **Social** (default: meeting new people) | **Networking** | **Speed dating** |
|---|---|---|---|
| Interview questions (Section 6.3) | Social set | Networking set | Dating set |
| Max depth level (Section 8.9) | 4 | 3 | 3 |
| Depth ramp | +1 level about every 5 min | +1 about every 6 min | +1 about every 2 min |
| Pause thresholds (normal / unusual / exceptional) | 3 / 5 / 6.5 s | 3 / 5 / 6.5 s | 3 / 5 / 6.5 s (tune after testing) |
| Round timer | None | Optional 10 min | 6 min (configurable) with a 1-minute warning crumb |
| Allowed crumb formats | All | question, fun_fact, callback, collab (what could you build or help each other with) | question, would_you_rather, fun_fact, callback |
| Tone | Warm, curious | Professional, generous, specific | Playful, light, flirty at most; never sexual |
| End of session | Mutual contact swap + trail | Mutual LinkedIn/email swap + trail with follow-ups | Private "match?" yes/no; contact revealed only on mutual yes |

---

## 5. Product principles (non-negotiable)

1. **Speak only into silence.** Never interrupt someone who is talking. If anyone starts speaking while a crumb plays, stop immediately.
2. **Know when to wait.** Intentional pauses ("wait," "let me show you," "let me think") are respected.
3. **Your profile, your rules.** Breadcrumbs never mentions anything a person did not put in their own profile, and never mentions items they removed or marked off-limits.
4. **Short and human.** Crumbs are 28 words or fewer, addressed to people by name, and hand the conversation straight back.
5. **Ephemeral by default.** No audio storage. Transcripts in memory only; wiped at session end or after 2 hours, whichever comes first.
6. **Consent up front, mutual consent at the end.** Express written consent before any audio is captured; the trail is shared only if both opt in.
7. **Show the brain.** An X-ray panel makes the engine's reasoning visible (great for judges and for debugging).

---

## 6. User experience and flows

### 6.1 Devices

- **Table** (laptop, Chrome): the shared screen with the Crumb Orb, the mic, and the speaker. Placed between the two people.
- **Phones** (each person's own): join, consent, voice profile, profile review, and the end-of-session trail. During the conversation phones can be face down; all live controls are on the Table.

### 6.2 Flow overview

```
Table: Create session (choose mode) ──> shows 2 QR codes (Seat 1, Seat 2)
Phone: Scan QR ──> Consent (typed name + checkbox) ──> 60s voice interview ──> Review profile (remove items, mark off-limits) ──> Ready
Table: Both ready ──> Calibration ("Seat 1, say hi and tell them your name") ──> Opening crumb (overlap)
Table: LIVE: listen, measure, hold, steer, switch, repeat
Either: End ──> Phones: Trail opt-in ──> (if both yes) Trail cards + contacts ──> Session wiped
```

### 6.3 Voice profile interview (phone, about 60 seconds)

Three questions shown one at a time, about 20 seconds each. Big record button, a visible countdown, re-record allowed. Each answer is captured as WAV and transcribed by Muse (Section 11.2).

| Mode | Q1 | Q2 | Q3 |
|---|---|---|---|
| Social | What's something you could talk about for an hour? | What's a story your friends always make you tell? | What's something you're looking forward to right now? |
| Networking | What are you working on that you're excited about? | What's something you're trying to learn or figure out right now? | What could you help someone with, and what kind of person are you hoping to meet? |
| Speed dating | What does a perfect Saturday look like for you? | What's a small thing that makes you unreasonably happy? | What's something people are usually surprised to learn about you? |

**Demo mode:** a "Quick profile" option asks only Q1 (about 25 seconds) so judges can set up fast.

### 6.4 Profile review (phone)

Gemini turns the transcripts into a short profile of chips (Section 9.2), for example "Training for first half marathon," "Story: got lost in Tokyo with no phone," "Looking for: product design mentors."

- Each chip has an **X** to remove it.
- An **Off-limits** field: free text and quick toggles (work, family, dating history, politics, religion, health).
- Copy: *"Breadcrumbs will only bring up what's on this list. You can remove anything."*
- Button: **I'm ready**.

### 6.5 Calibration (Table, about 10 seconds)

Muse diarization labels voices A, B and so on, and those labels are only valid within one realtime session. To map labels to seats, the Table says (TTS and on screen): *"Before we start: Seat 1, say hi and tell [Seat 2 name] where you're from."* The first diarized turn after the prompt maps its label to Seat 1. Then: *"Seat 2, your turn."* The next new label maps to Seat 2. This doubles as a natural warm-up. Fallback: if 15 seconds pass, map the first two distinct labels by order of appearance.

### 6.6 Live conversation (Table)

The **Crumb Orb** is the whole UI. States:

| State | Visual | Meaning |
|---|---|---|
| Listening | Slow breathing amber glow | Conversation flowing; Breadcrumbs is quiet |
| Warming | Glow intensifies slightly | Pause has passed the normal 3 s range; a crumb is ready if it reaches 5 s |
| Crumb | Bright pulse + chime, crumb text appears large | A crumb is being spoken |
| Holding | Slow blue, small "Holding..." label | Intentional pause detected; will not interrupt |
| Paused | Gray, "Listening paused" | Someone tapped Pause listening; mic stream stopped |

**Controls on the Table** (large, minimal):
- **Drop a crumb**: request a crumb now (the Jubilee moment). Always a context switch.
- **Skip**: discard the current crumb and fetch a different one.
- **Hold**: manual hold for 60 seconds.
- **Pause listening**: stops sending audio entirely until resumed.
- **End**: ends the session.
- **X-ray** toggle (small, for judges and debugging): shows the live transcript with seat labels, pause timer vs thresholds, staleness score and its parts, hold state, cooldown, the prefetched next crumbs, and *why* the last crumb was chosen.

### 6.7 Crumb delivery

1. The orb pulses and a soft chime plays.
2. The crumb text appears in large type, with the source shown subtly (for example "from something you both shared," or "outside the box" with a source link for facts).
3. A warm TTS voice speaks it (Gemini TTS, Section 11.3.4).
4. If anyone starts talking during playback, the audio fades out within 250 ms (barge-in). That counts as a success.

### 6.8 Ending and the Trail (phones)

- Either person taps **End** on the Table, or the round timer ends (speed dating).
- Each phone privately asks: **Keep in touch?** (Social/Networking: share contact; Speed dating: "Match?").
- If **both** say yes: each phone shows the other's chosen contact (Instagram handle, email, or LinkedIn) plus a **trail** of up to 3 threads to pick up next time, generated by Gemini from the conversation.
- If either says no: the other sees *"Thanks for the conversation!"* and nothing else. No one learns who declined.
- In all cases, transcripts, profiles and the session are deleted after the trail is shown (or after 2 hours).

---

## 7. Functional requirements

Priority: **P0** = must work for the demo; **P1** = should have by morning; **P2** = stretch.

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| F1 | Create session with mode; show 2 seat QR codes | P0 | Table shows QR codes linking to `/join/{sid}/{seat}` over HTTPS; phones open it |
| F2 | Express written consent screen | P0 | No audio is captured for a seat until it has a typed name + checked box; consent timestamp stored in session memory |
| F3 | Voice profile interview | P0 | 3 answers (or 1 in Quick mode) recorded as 24 kHz mono 16-bit WAV, transcribed by Muse REST, combined |
| F4 | Profile extraction + review | P0 | Gemini returns a valid `Profile` (Section 12); user can remove chips and add off-limits; removed items never reach crumb prompts |
| F5 | Live diarized transcription | P0 | Table mic streams to Muse realtime in `DIARIZATION` mode; turns appear in X-ray with seat labels within about 2 s of speech end |
| F6 | Calibration mapping labels to seats | P0 | After calibration, at least 90% of turns in a 2-minute test show the correct seat |
| F7 | Pause measurement | P0 | Pause timer resets on any speech (Muse `speechStart` or local VAD activity) and is visible in X-ray |
| F8 | Manual "Drop a crumb" | P0 | Tap produces a spoken context-switch crumb in 3 s or less (under 1 s when prefetched) |
| F9 | Automatic steer and context switch | P0 | Policy in Section 8.7 fires correctly on simulator scripts (Section 16.1) and live |
| F10 | Intentional-hold detection | P0 | Hold phrases (Appendix A) suppress interventions; X-ray shows "Holding (show)" etc.; hold clears when speech resumes |
| F11 | Opening crumb from overlap | P0 | First crumb after calibration references something both profiles share (or the closest adjacent pair) |
| F12 | TTS playback + barge-in | P0 | Crumb is spoken; playback stops within 250 ms of new speech |
| F13 | Staleness score | P1 | Score and its components update after every completed turn; drives switch vs steer |
| F14 | Prefetch | P1 | A ready steer crumb and a ready switch crumb (text + audio) are always cached during LIVE; regenerated after each turn when the topic or speakers change |
| F15 | Outside facts with grounding | P1 | Fun-fact crumbs use Google Search grounding and keep a source URL; if grounding fails, fall back to a question (never an ungrounded "fact") |
| F16 | Modes (Social, Networking, Speed dating) | P1 | Mode changes interview questions, prompts, thresholds, timer and trail per Section 4.2 |
| F17 | Trail with mutual opt-in | P1 | Trail shown only if both opt in; nothing revealed otherwise |
| F18 | X-ray panel | P1 | Shows transcript, timers, thresholds, staleness parts, hold state, cooldown, cached crumbs, last crumb's `why` |
| F19 | Self-hearing guard | P0 | Breadcrumbs' own TTS is never counted as a human turn |
| F20 | Session wipe | P0 | `DELETE /api/sessions/{sid}` and automatic 2-hour TTL clear all session data |
| F21 | Round timer (speed dating) | P2 | Countdown on Table; 1-minute-left crumb; end triggers trail |
| F22 | Wildcard drops | P2 | Optional toggle (off by default): the original Jubilee-style random interjection, at most once every 5 minutes, only during a natural pause of 3 s or more |
| F23 | Muse connector | P2 | Expose "start a Breadcrumbs session" / "show my trail" as a tool Muse can call (ask Meta reps first) |

---

## 8. Conversation intelligence engine (core spec)

This is the heart of the product. It runs server-side, one instance per session, ticking at **10 Hz**.

### 8.1 Inputs

| Signal | Source | Use |
|---|---|---|
| `speechStart` (turnId) | Muse realtime | Someone started talking: reset the pause timer, cancel any pending crumb, trigger barge-in |
| `transcript` partials (cumulative) | Muse realtime | Live caption in X-ray; early hold-phrase detection |
| `speaker` (label A, B...) | Muse realtime | Attribute the turn to a seat via the calibration map |
| `speechEnd` (turnId) | Muse realtime | Start the pause timer |
| `speechComplete` (turnId, final transcript) | Muse realtime | Commit the turn: words, question marks, hold phrases, staleness update, prefetch |
| `audioProgress` (audioProcessedMs) | Muse realtime | Liveness check that the stream is healthy |
| Local VAD frames (`{active, rms}` at 10 Hz) | Table browser | Fast, network-independent speech-activity check; resets the pause timer and drives barge-in |
| TTS playback state | Table browser | Self-hearing guard; barge-in |
| Controls (spark, skip, hold, pause, end) | Table UI | Manual overrides |

Turns can overlap and `speechComplete` can arrive out of order, so **key all turn state on `turnId`**, never on arrival order.

### 8.2 Turn model

```python
class Turn(BaseModel):
    turn_id: int
    seat: Literal[1, 2] | None   # None until speaker label is mapped
    label: str | None            # Muse diarization label, e.g. "A"
    text: str
    words: int
    is_question: bool            # ends with "?" or starts with a question word
    is_backchannel: bool         # e.g. "yeah", "totally", "haha", "for sure", "mm"
    started_at: float            # server monotonic seconds
    ended_at: float
    gap_before: float | None     # silence between previous turn end and this start
```

A turn is **short** if `words <= 3` or `is_backchannel`.

### 8.3 Pause measurement

```
silence_s = now - max(last_speech_end_at, last_local_vad_active_at)
```

- The pause timer only runs when **no turn is active** (no `speechStart` without a matching `speechEnd`) **and** local VAD reports inactive.
- Any `speechStart` or local VAD activity resets the timer to 0 immediately.
- Breadcrumbs' own TTS playback (plus a 300 ms tail) is excluded. When a crumb finishes playing, the silence timer **restarts at 0**, so people get a fresh window to respond.
- `crumbs_this_silence` counts crumbs delivered since the last human speech; it resets to 0 whenever a human speaks.

### 8.4 Pause thresholds

| Band | Silence | Meaning | Action |
|---|---|---|---|
| **Normal** | under 3 s (`T_NORMAL`) | A natural conversational pause | Never intervene |
| **Warming** | 3 to 5 s | Past the normal range | Still silent; orb glows slightly; make sure a steer and a switch are cached |
| **Unusually long** | 5 s (`T_LONG`) | The conversation needs a nudge | **Steer**, or **switch** if the conversation is stale |
| **Exceptionally long** | 6.5 s (`T_EXCEPTIONAL`; tune within 6 to 7 s) | The pause is dead | **Context switch** |

**Why the exceptional threshold works the way it does.** In real time, the engine can't know at 5 seconds whether a pause will reach 7. So "exceptionally long" is applied in two situations:

1. **No crumb has fired yet during this silence** and it reaches 6.5 s. This happens when the 5-second steer was blocked (for example by the cooldown, or a "thinking" hold that doubled the thresholds).
2. **A crumb was delivered and nobody answered** for 6.5 s after it finished. The steer didn't land, so the pause is genuinely dead and Breadcrumbs switches topics.

After **2 unanswered crumbs in a row**, Breadcrumbs goes quiet until someone speaks, and the orb shows a gentle "Drop a crumb" hint instead. It never talks into a void.

**Optional adaptive tuning (P2, off by default).** Nudge thresholds by up to about 1 s based on the pair's median gap `g` (clamped to 0.4 to 2.5 s, default 1.2 s):

```
T_LONG        = clamp(5.0 + (g - 1.2), 4.5, 6.0)
T_EXCEPTIONAL = max(T_LONG + 1.0, clamp(6.5 + (g - 1.2), 6.0, 7.5))
T_NORMAL      = T_LONG - 2.0
```

All modes use the same thresholds by default; adjust per mode only after live testing.

### 8.5 Staleness score

Computed after every committed turn over the last `N = 6` human turns, compared to the session's **baseline** (the first 10 turns, or the first 3 minutes, whichever comes first).

| Component | Formula | Weight |
|---|---|---|
| **L** reply-length decay | `clamp(1 - median_words(last N) / median_words(baseline), 0, 1)` | 0.35 |
| **R** short-reply rate | `count(short turns in last N) / N` | 0.25 |
| **G** gap growth | `clamp((median_gap(last N) / g) - 1, 0, 2) / 2` | 0.15 |
| **Q** question deficit | `1 - min(questions_in_last_N / 2, 1)` | 0.10 |
| **E** LLM energy | Gemini Flash-Lite tag every 4 turns: high = 0, medium = 0.5, low = 1 (Section 9.6) | 0.15 |

```
S = 0.35*L + 0.25*R + 0.15*G + 0.10*Q + 0.15*E          # 0 = lively, 1 = dead
STALE = S >= 0.60
```

Before the baseline is established, `L = 0` and `G = 0`.

### 8.6 Intentional-hold detection

Goal: never interrupt someone who is pulling up a photo, thinking, or dealing with something else.

**Step 1: fast lexical match (immediate).** When a turn completes (and also on partials, for speed), check the **last 8 words** of the most recent turn against the hold lexicon (Appendix A). Matching near the end matters: "Wait, really? That's insane, how did you..." continues talking and is not a hold. A hold requires that the phrase is in the tail of the turn **and** silence follows.

| Hold kind | Example phrases | Effect | Max duration |
|---|---|---|---|
| `show` | "let me show you," "let me pull this up," "let me find it," "look at this," "hold on," "wait" (at end of turn), "one sec," "hang on," "where is it" | Suppress all automatic crumbs | 60 s |
| `think` | "let me think," "hmm," "good question," "that's a tough one," "how do I put this" | Double both thresholds | 20 s |
| `external` | "sorry, I have to take this," "excuse me," "be right back," "brb," "one second" + phone ringing context | Suppress all automatic crumbs; orb shows Holding | Until speech resumes, then 90 s cap |

**Step 2: LLM confirmation (async, about 1 s).** For every lexical hit, and for any turn ending in an ambiguous fragment (ends without punctuation, or under 4 words followed by silence), call the hold classifier (Section 9.6). It can **confirm, upgrade or cancel** the hold. The lexical hold stays in effect while the classifier runs.

**Step 3: release.** A hold clears on the next `speechStart` or local VAD activity, or at its max duration. After a `show` hold times out, the engine resumes normal thresholds counting from 0 (it does not instantly fire).

**Manual hold:** the Hold button applies a `show` hold for 60 s.

### 8.7 Decision policy

Interventions only ever fire into silence. Pseudocode run at 10 Hz:

```python
def tick(state, now):
    if state.phase != "LIVE" or state.listening_paused or state.seats_unmapped:
        return
    if state.turn_active or state.local_vad_active or state.tts_playing:
        return
    if state.hold and state.hold.kind in ("show", "external") and now < state.hold.until:
        return

    t_normal, t_long, t_exc = thresholds(state)      # 3.0, 5.0, 6.5 s by default (Section 8.4)
    if state.hold and state.hold.kind == "think" and now < state.hold.until:
        t_long, t_exc = t_long * 2, t_exc * 2        # 10 s / 13 s while someone is thinking

    s = state.silence_s(now)                         # resets on human speech; restarts at 0 after a crumb
    state.band = band(s, t_normal, t_long, t_exc)    # normal / warming / long / exceptional (UI + X-ray)

    if state.crumbs_this_silence >= MAX_UNANSWERED_CRUMBS:   # 2 unanswered in a row
        return                                               # go quiet; orb shows "Drop a crumb" hint

    if state.crumbs_this_silence >= 1:
        # A crumb already played and nobody has spoken since.
        if s >= t_exc:
            deliver(state, "switch", reason="last crumb got no reply (exceptionally long pause)")
        return

    # No crumb yet during this silence.
    if s >= t_exc and exceptional_cooldown_ok(state, now):
        deliver(state, "switch", reason="exceptionally long pause")
    elif s >= t_long and regular_cooldown_ok(state, now):
        if state.staleness >= STALE_SWITCH:          # 0.60
            deliver(state, "switch", reason="unusually long pause + stale conversation")
        else:
            deliver(state, "steer", reason="unusually long pause, conversation still lively")
```

Plain-language summary:

| Situation | Action |
|---|---|
| Normal pause (under 3 s) | Nothing |
| Warming (3 to 5 s) | Nothing spoken; orb glows slightly; crumbs ready |
| Unusually long pause (5 s), conversation still lively | **Steer**: a follow-up question or relevant fact about the current topic |
| Unusually long pause (5 s), conversation stale | **Context switch**: new topic |
| Exceptionally long pause (6.5 s) with no crumb yet | **Context switch** |
| A crumb got no reply for 6.5 s | **Context switch** |
| 2 unanswered crumbs in a row | Go quiet until someone speaks; show "Drop a crumb" hint |
| Intentional hold (`show`/`external`) | Nothing until speech resumes or the hold expires |
| Thinking hold | Thresholds double (10 s / 13 s) |
| Someone taps "Drop a crumb" | **Context switch** immediately (ignores cooldown; never while someone is talking) |
| Someone starts talking during a crumb | Fade out (barge-in); mark crumb `interrupted` |

**Steer after steer:** if a steer is followed by 2 or fewer human turns before the next long pause, the next intervention is upgraded to a switch (the steer didn't land).

### 8.8 Cooldowns and guards

| Guard | Default |
|---|---|
| Warm-up after opening crumb (no automatic crumbs) | 20 s |
| Regular cooldown (5-second interventions) | 30 s since the last crumb ended **and** 2 or more human turns since |
| Exceptional cooldown (6.5-second switch, no crumb yet this silence) | 15 s since the last crumb ended (human-turn requirement waived) |
| Unanswered follow-up | One switch 6.5 s after an unanswered crumb; max 2 unanswered in a row |
| Max automatic crumbs per rolling 10 minutes | 6 |
| Never repeat a crumb topic within a session | Enforced via `used_topics` and `used_item_ids` |
| Never use an item the user removed or that matches off-limits | Enforced server-side before prompting AND by validating Gemini output |

### 8.9 Choosing the crumb

**Sources** (`source` field):

| Source | What it means | Example |
|---|---|---|
| `overlap` | Something both profiles share, directly or adjacently | "You both grew up near Chicago. Deep dish or tavern style, and defend it." |
| `profile_seat1` / `profile_seat2` | An item from one person's profile, addressed so the other person asks or the owner tells | "Dev, Maya mentioned a story about getting lost in Tokyo. Want to hear it?" |
| `conversation` | A thread from earlier in this conversation (callback) | "Earlier you said you almost became a chef. What stopped you?" |
| `outside` | A grounded fun fact or new topic outside both profiles, ideally linked to the current topic or the place | "Fun fact about Austin's bats, plus a question about the strangest place you've seen wildlife." |

**Selection rules:**
- **Steer** prefers `conversation` (same topic), then a related `outside` fact, then a related profile item. It must stay on the current topic.
- **Switch** rotates sources to stay balanced: `overlap` first if unused overlaps remain, then alternate between the less-featured person's profile and `outside`. Track `featured_count[seat]` so neither person is the subject every time.
- **Depth ladder** (inspired by the "fast friends" procedure, which escalates self-disclosure gradually):
  - Level 1: light and situational (the event, the place, favorites)
  - Level 2: personal preferences and stories
  - Level 3: goals, values, turning points
  - Level 4: reflective (what matters to you, what you'd change); Social mode only, after 10 or more minutes
  - Current max depth = `min(mode_max, 1 + floor(minutes_live / ramp_minutes))`. A crumb may be at or below the current max.
- **Formats:** `question` (default), `fun_fact` (always ends with a question back to them), `would_you_rather`, `callback`, `collab` (networking: "what could you two build or trade?").

### 8.10 Prefetch and latency budget

- After every `speechComplete` (debounced 1.5 s) and every 30 s during LIVE, refresh the cache in the background:
  - `ready_steer`: one steer crumb for the current topic.
  - `ready_switch`: one switch crumb from the next source in rotation.
  - Each with **pre-synthesized TTS audio**.
- A cached crumb is invalidated if it is older than 90 s, the topic changed, or its item was used.
- **Targets:** silence threshold crossed to audio starting: 700 ms or less with a cache hit, 3 s or less on a miss. Manual "Drop a crumb": 1 s or less when cached.

### 8.11 Self-hearing guard

The Table both plays TTS and listens, so:
1. Capture with `echoCancellation: true`, `noiseSuppression: true`, `autoGainControl: true`.
2. While TTS plays (plus a 300 ms tail), mark the interval as `bot_speaking`; any Muse turn whose span overlaps it by more than 50% is dropped as a human turn (shown grayed in X-ray).
3. Local VAD is ignored for barge-in during the first 300 ms of playback (avoids the chime triggering it), then active.

### 8.12 Default parameters (put these in `server/config.py`)

```python
TICK_HZ = 10
N_RECENT_TURNS = 6
BASELINE_TURNS = 10
BASELINE_SECONDS = 180
DEFAULT_GAP_S = 1.2
GAP_CLAMP = (0.4, 2.5)
T_NORMAL_S = 3.0             # normal pause: never intervene below this
T_LONG_S = 5.0               # unusually long pause: steer (or switch if stale)
T_EXC_S = 6.5                # exceptionally long pause: context switch (tune within 6.0 to 7.0)
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
```

All of these should be live-tunable from the X-ray panel (P1) so the team can tune with real people overnight.

---

## 9. AI components

### 9.1 Model map

| Job | Model | Why | Settings |
|---|---|---|---|
| Live diarized transcription | **Muse Voice Transcribe** `muse-voice-transcribe-1.0`, realtime WebSocket, `DIARIZATION` mode | Real-time turns with speaker labels, built-in turn detection | 24 kHz PCM, `emitAudioProgress: true`, `keywords` = both names + key profile terms |
| Profile answer transcription | **Muse Voice Transcribe** REST `/v1/asr/transcribe`, `PUSH_TO_TALK` | One clean transcript per recorded answer | WAV mono 16-bit 24 kHz |
| Profile extraction, overlap finding, crumb writing, trail | **Gemini** `gemini-3.8-flash` | Best quality Flash model; structured JSON output | `thinking_level: "low"`, `max_output_tokens` capped, JSON schema via Pydantic |
| Hold classifier, energy/topic tagger | **Gemini** `gemini-3.5-flash-lite` | Fastest, cheapest; runs often | Structured output, small token caps |
| Grounded outside facts | **Gemini** `gemini-3.8-flash` + `google_search` tool | Fresh, citable facts | Keep `url_citation` annotations |
| Crumb voice | **Gemini TTS** `gemini-3.8-flash-tts` (fallback `gemini-3.8-flash-lite-tts`) | Natural, expressive, style-controllable voice | Voice `Kore` or `Puck`; style "warm, playful, curious, like a friendly host speaking softly" |
| Fallback text model (optional) | **Muse Spark** `muse-spark-1.3` via Meta Model API (OpenAI-compatible) | Only if Gemini rate-limits; also strengthens the Meta story | Same prompts |

Note: Gemini 4 and Gemini 3.6+ models ignore `temperature`, `top_p` and `top_k`. Do not send them.

### 9.2 Profile extraction

**Input:** the seat's mode, display name (from consent), and the 1 to 3 transcribed answers.
**Output:** `Profile` (schema in Section 12).

System instruction:

```text
You turn a short spoken self-introduction into a conversation profile for Breadcrumbs,
an app that helps two people in person have a better conversation.

Rules:
- Only include things the person actually said. Never infer or guess anything.
- Never include sensitive categories even if mentioned: health, religion, politics,
  sexuality, immigration status, finances, criminal history, trauma. Drop them silently.
- Write each item as a short third-person phrase (max 12 words), e.g. "training for first half marathon".
- For each item write a "hook": one natural question someone could ask about it (max 18 words).
- Assign depth 1 to 4: 1 = light fact or favorite, 2 = personal story or preference,
  3 = goal, value or turning point, 4 = reflective or vulnerable.
- kinds: interest | story | goal | fun_fact | value | preference | offer | seeking
  (offer/seeking are for networking mode: what they can help with and who they want to meet).
- 4 to 10 items. Plain language, no emojis, no em dashes.
```

### 9.3 Overlap finder

Run once when both profiles are ready. Input: both profiles (after removals). Output: `OverlapSet` with up to 6 overlaps, each `{kind: direct | adjacent | contrast, item_ids_seat1, item_ids_seat2, summary, opener}`. `contrast` pairs are great conversation fuel ("one of you is a night owl, one an early bird"). The best overlap becomes the **opening crumb**.

```text
You find conversation-worthy connections between two people's profiles.
direct = both share the same thing. adjacent = related but different (running vs climbing).
contrast = interesting opposites that would make a fun conversation.
Write an "opener" for each: one crumb (max 28 words) that names the connection without
revealing anything not in the profiles, and ends by handing the conversation to one person by name.
Never reference removed items or off-limits topics. No em dashes.
```

### 9.4 Crumb generator

Called by prefetch and on cache miss. Input (assembled server-side):

```json
{
  "mode": "networking",
  "kind": "switch",
  "requested_source": "profile_seat2",
  "current_depth_max": 2,
  "names": {"1": "Maya", "2": "Dev"},
  "profiles": {"1": "...items (allowed only)...", "2": "..."},
  "overlaps_unused": ["..."],
  "recent_transcript": "last ~2 minutes as 'Maya: ...' / 'Dev: ...' lines",
  "current_topic": "half marathon training",
  "used_topics": ["where you're from", "half marathon training"],
  "used_item_ids": ["s1_i3"],
  "featured_count": {"1": 2, "2": 1},
  "off_limits": {"1": ["work"], "2": []},
  "allowed_formats": ["question", "fun_fact", "callback", "collab"]
}
```

System instruction:

```text
You are Breadcrumbs, a warm, witty conversation companion sitting between two people
who are getting to know each other in person. You speak rarely. When you do, you say one
short thing that gets them talking to EACH OTHER again, then you get out of the way.

kind = "steer": stay on current_topic. Ask a follow-up or share a relevant angle that builds
on something specific one of them just said. Quote or paraphrase their words when possible.
kind = "switch": move to a fresh topic from requested_source. Do not reuse used_topics.

Rules:
- Max 28 words. Spoken aloud, so write for the ear. No emojis, no lists, no em dashes.
- Address at least one person by name. End by handing the turn to someone.
- Only use profile items provided. Never mention off-limits topics. Never invent facts about them.
- Never embarrass, flirt in networking mode, or go sexual in any mode. Humor is never at someone's expense.
- Respect current_depth_max.
- For "outside" source without a verified fact provided, ask an interesting open question instead of stating a fact.
- Return JSON matching the schema. "why" explains your choice in one sentence for the debug panel.
```

Output: `Crumb` (Section 12). **Server-side validation** after generation: word count, names present, no removed item IDs, no off-limits terms (simple keyword check), no em dash characters (replace with commas). On validation failure, regenerate once, then fall back to the best unused overlap opener or a canned crumb from Appendix B.

### 9.5 Outside facts (grounded)

For `source = "outside"` with `format = "fun_fact"`:

1. Call `gemini-3.8-flash` with the `google_search` tool: *"Find one surprising, verifiable, light fun fact related to {current_topic or event location}. Return the fact in one sentence and nothing else."*
2. Keep the `url_citation` annotation (url, title). If there is no citation, **do not use it as a fact**.
3. Feed the fact + URL into the crumb generator as `verified_fact`, which phrases it and ends with a question.
4. Show the source title subtly on the Table under the crumb.

Structured output combined with built-in tools is in preview for Gemini 3 models, so use this two-step flow by default; you may try a single combined call, but keep the two-step path as the fallback.

### 9.6 Fast classifiers (Flash-Lite)

**Hold classifier** (on lexical hit or ambiguous fragment):

```text
Given the last few turns of an in-person conversation and the fact that silence followed,
decide whether the speaker intentionally paused. kinds:
show = getting something to show (phone, photo, video), think = thinking about an answer,
external = dealing with something outside the conversation, none = not an intentional pause.
Return {"hold": bool, "kind": "...", "expected_seconds": int, "confidence": 0..1}.
```

**Energy and topic tagger** (every 4 turns): returns `{"topic": "3-5 words", "energy": "high|medium|low", "winding_down": bool}`. `energy` feeds component **E** of staleness; `topic` drives steer prefetch and `used_topics`.

### 9.7 Trail generator

At session end, if both opted in: up to 3 threads per person, each `{for_seat, text}` like "Ask Maya how the half marathon goes on Nov 16." Only from things said in the conversation or the allowed profiles. Then the transcript is deleted.

### 9.8 Voice

- Voice: `Kore` (default) or `Puck`; pick by ear during testing and keep one.
- Style annotation: `"warm, playful, curious, like a friendly host speaking softly at a small table"`.
- Inline tags allowed in text for timing, e.g. `<short pause>`; the `text` field is read verbatim, so no stage directions in it.
- Output: unary WAV, 24 kHz mono 16-bit (play directly in the browser).

### 9.9 Responsible AI guardrails (say these out loud in the pitch)

- Only first-party, consented profile data; no scraping; no inference of sensitive traits.
- Off-limits and removed items are filtered **before** prompting and validated **after**.
- Facts must be grounded with a source or they become questions.
- Every crumb's reasoning is visible in X-ray (`why`).
- Either person can pause listening or end at any time; audio is never stored.

---

## 10. Architecture and tech stack

### 10.1 Stack (chosen for fastest reliable execution in about 17 hours)

| Layer | Choice | Why |
|---|---|---|
| Backend | **Python 3.11+, FastAPI, Uvicorn**, `websockets`, `httpx`, `google-genai`, `pydantic` v2, `python-multipart`, `python-dotenv` | Async WebSockets for audio relay and engine; Muse's official samples are Python; Pydantic doubles as Gemini JSON schemas |
| Audio conversion | **ffmpeg** (installed on the Table laptop) | Converts phone recordings (webm/opus or mp4/aac) to the WAV Muse requires |
| Frontend | **React + Vite + TypeScript + Tailwind CSS**, `qrcode.react`, `framer-motion` (orb animation) | Fast to build; one static bundle served by FastAPI, so one origin and one HTTPS tunnel |
| Realtime transport | Browser to FastAPI: WebSocket (binary PCM + JSON events); FastAPI to Muse: WebSocket | API keys stay server-side; never in the browser |
| State | **In-memory** `SessionStore` (dict + asyncio tasks), TTL 2 h | Privacy by design; no database needed |
| HTTPS for phones | **Cloudflare quick tunnel** (`cloudflared tunnel --url http://localhost:8000`) or ngrok | Phones require HTTPS for microphone access |
| Table browser | **Chrome** on the laptop | Reliable `AudioContext({sampleRate: 24000})`, echo cancellation, AudioWorklet |

### 10.2 System diagram

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

### 10.3 Repository layout

```
breadcrumbs/
  README.md                 # setup, run, demo, "what we built vs libraries we used"
  PRD.md
  .env.example              # MODEL_API_KEY=, GEMINI_API_KEY=, PUBLIC_BASE_URL=
  .gitignore                # .env, __pycache__, node_modules, web/dist, *.wav, *.webm
  server/
    main.py                 # FastAPI app: routes, websockets, static files
    config.py               # Section 8.12 parameters + env loading
    models.py               # Pydantic models (Section 12): build this FIRST
    session_store.py        # in-memory sessions, TTL sweeper, wipe
    audio/convert.py        # ffmpeg wrapper → 24 kHz mono s16 WAV
    muse/realtime.py        # MuseRealtimeClient (Section 11.1)
    muse/transcribe.py      # REST transcribe (Section 11.2)
    engine/state.py         # turns, pause timer, calibration map
    engine/staleness.py     # Section 8.5
    engine/hold.py          # Section 8.6 + Appendix A lexicon
    engine/policy.py        # Section 8.7, 8.8
    engine/runner.py        # 10 Hz loop per session
    ai/gemini.py            # client wrapper (async), JSON helper, retries
    ai/prompts.py           # all system instructions (Section 9)
    ai/profiles.py          # extraction + overlaps
    ai/crumbs.py            # generation, validation, cache, prefetch
    ai/facts.py             # grounded outside facts
    ai/tts.py               # Gemini TTS
    ai/trail.py             # trail generation
    sim/replay.py           # simulator: feed scripted turns with timing into the engine
    sim/scripts/*.json      # test conversations (Section 16.1)
    tests/                  # pytest: hold lexicon, staleness, policy via simulator
  web/
    index.html
    vite.config.ts          # proxy /api and /ws (ws: true) to :8000 in dev
    src/main.tsx, src/App.tsx
    src/pages/Home.tsx      # create session, choose mode
    src/pages/Table.tsx     # orb, controls, X-ray, audio capture, TTS playback
    src/pages/Join.tsx      # phone flow: consent → interview → review → waiting → trail
    public/pcm-worklet.js   # AudioWorklet module (must be in public/ so it is served at /pcm-worklet.js)
    src/audio/capture.ts    # mic → worklet → WS; RMS VAD
    src/audio/recorder.ts   # MediaRecorder for profile answers
    src/components/CrumbOrb.tsx, Controls.tsx, XRay.tsx, ConsentForm.tsx, ProfileChips.tsx, TrailCard.tsx
    src/lib/ws.ts, src/lib/api.ts, src/lib/types.ts  # types mirror server/models.py
```

### 10.4 Environment and running locally

```bash
# server
cd server
python -m venv .venv && source .venv/bin/activate
pip install -U fastapi "uvicorn[standard]" websockets httpx google-genai pydantic python-multipart python-dotenv
brew install ffmpeg            # or apt-get install ffmpeg
cp ../.env.example ../.env     # fill MODEL_API_KEY (Meta Model API) and GEMINI_API_KEY
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# web (dev)
cd web && npm install && npm run dev          # http://localhost:5173 (proxies to :8000)

# phones (HTTPS required for mic)
cd web && npm run build                       # FastAPI serves web/dist at /
cloudflared tunnel --url http://localhost:8000
# set PUBLIC_BASE_URL=https://<random>.trycloudflare.com so QR codes point to it
```

Environment variables:

| Var | Used by |
|---|---|
| `MODEL_API_KEY` | Meta Model API (Muse Voice Transcribe; optional Muse Spark fallback) |
| `GEMINI_API_KEY` | `google-genai` client (read automatically by `genai.Client()`) |
| `PUBLIC_BASE_URL` | QR code links for phones |
| `ASR_MODEL` | Default `muse-voice-transcribe-1.0` |

Credits: each HackTX participant can redeem **$50 in Muse API credits** (intake form QR on Meta's track sheet; account at https://dev.meta.ai/). Muse Voice Transcribe costs $0.18 per audio hour, so credits are not a constraint.

---

## 11. API reference for builders

All details below were taken from the official docs on Oct 24, 2026. If anything fails, re-check the linked pages; do not guess field names.

### 11.1 Muse Voice Transcribe: realtime WebSocket (live conversation)

- **URL:** `wss://api.meta.ai/v1/asr/realtime?sessionId=<optional-uuid>`
- **Model:** `muse-voice-transcribe-1.0`
- **Auth:** in the **first JSON frame** (handshake): `"authorization": {"accessToken": "Bearer <MODEL_API_KEY>"}`. The HTTP `Authorization` header is ignored on this endpoint. Send the handshake within 10 s of connecting.
- **Audio:** binary frames of raw PCM, signed 16-bit little-endian, mono. Use `PCM_24KHZ` (engine-native, 48,000 bytes/s). 80 ms frames = 1,920 samples = 3,840 bytes. Send at about real-time speed; never more than 5 s ahead of processing (backlog or below-real-time ingress closes with 1008).
- **Modes** (fixed at handshake): `PUSH_TO_TALK` (default, you define turns), `ENDPOINTING` (model detects turns), **`DIARIZATION`** (model detects turns and labels speakers). **Use `DIARIZATION`.** Notes: labels like `A`, `B` are session-scoped and not identities; diarization does not handle overlapping speech; it is not tuned for low-latency voice commands (fine for us: we act on multi-second pauses).
- **Other handshake fields:** `partialMode` (`CUMULATIVE` default: each partial replaces the previous one; render in place), `emitAudioProgress` (bool), `languageBias` (e.g. `["English"]`, a hint), `keywords` (list of terms to bias recognition; set to both names + distinctive profile terms). Both hints can only be set at session start. Full schema: `RealtimeHandshakeRequest` in the realtime API reference.
- **Handshake ack:** `{"sessionId": "..."}` (the only server frame without `type`).
- **Client control:** `{"type": "endStream"}` closes input; the server flushes results.
- **Server events:**

| `type` | Fields | Notes |
|---|---|---|
| `speechStart` | `turnId`, `audioProcessedMs` | Turn begins |
| `transcript` | `transcript`, `final`, `audioProcessedMs` | Partials carry no `turnId`; cumulative |
| `speaker` | `label`, `audioProcessedMs` | Labels the preceding audio span; exactly one per turn |
| `speechEnd` | `turnId` | Boundary only (no text) |
| `speechComplete` | `turnId`, `transcript`, `audioProcessedMs` | Post-processed final text for the turn; may arrive async |
| `audioProgress` | `audioProcessedMs` | When `emitAudioProgress` is on |
| `error` | `message`, `sessionId` | Sent before a fatal close when possible |

- **Turn lifecycle:** `speechStart` → partials → `speaker` → `speechEnd` → `speechComplete`. Correlate by `turnId`.
- **Close codes:** `1000` normal; `1008` invalid request or pacing violation (fix, don't retry as-is); `1011` internal error or "Max session duration reached" (retry with backoff / open a new session); `1013` rate limited (back off).
- **Limits:** 60-minute max session by default; 256 concurrent streams; 16,000 streams per hour per team.
- **Pricing:** $0.18 per hour of audio.

**Reference client (adapt into `server/muse/realtime.py`):**

```python
import asyncio, json, os, uuid
import websockets

MUSE_URL = "wss://api.meta.ai/v1/asr/realtime?sessionId={sid}"

class MuseRealtimeClient:
    def __init__(self, on_event, keywords: list[str]):
        self.on_event = on_event            # async callback(dict)
        self.keywords = keywords
        self.audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=40)   # ~3.2 s of 80 ms frames

    async def run(self):
        sid = f"bc-{uuid.uuid4()}"
        async with websockets.connect(MUSE_URL.format(sid=sid), open_timeout=30, max_size=None) as ws:
            await ws.send(json.dumps({
                "authorization": {"accessToken": f"Bearer {os.environ['MODEL_API_KEY']}"},
                "audioEncoding": "PCM_24KHZ",
                "model": os.environ.get("ASR_MODEL", "muse-voice-transcribe-1.0"),
                "mode": "DIARIZATION",
                "partialMode": "CUMULATIVE",
                "emitAudioProgress": True,
                "languageBias": ["English"],
                "keywords": self.keywords,
            }))
            ack = json.loads(await ws.recv())
            if "sessionId" not in ack:
                raise RuntimeError(f"Muse handshake failed: {ack}")

            async def sender():
                while True:
                    frame = await self.audio_q.get()
                    if frame is None:
                        await ws.send(json.dumps({"type": "endStream"}))
                        return
                    await ws.send(frame)      # binary PCM, 3,840 bytes per 80 ms

            async def receiver():
                async for msg in ws:
                    if isinstance(msg, bytes):
                        continue
                    await self.on_event(json.loads(msg))

            await asyncio.gather(sender(), receiver())

    def push_audio(self, frame: bytes):
        # Called from the /ws/audio handler. If the queue is full (network stall), drop the
        # OLDEST frame so a burst on reconnect can never push us 5 s ahead (Muse closes with 1008).
        if self.audio_q.full():
            self.audio_q.get_nowait()
        self.audio_q.put_nowait(frame)
```

Reconnect policy: on close `1011`/`1013`, reconnect with backoff (0.5 s, 1 s, 2 s). A new realtime session resets diarization labels, so after a reconnect the Table shows "Re-syncing voices" and re-runs the short calibration prompt (Section 6.5). Automatic crumbs are paused until both seats are mapped again. Keep demo sessions well under 60 minutes.

### 11.2 Muse Voice Transcribe: REST (profile answers)

- **URL:** `POST https://api.meta.ai/v1/asr/transcribe` (optional `?sessionId=`)
- **Auth:** `Authorization: Bearer <MODEL_API_KEY>`
- **Body:** `multipart/form-data` with parts `request` (JSON, same settings as the handshake minus `authorization`, plus `"audioEncoding": "WAV"`) and `audio` (WAV: RIFF, mono, 16-bit PCM, 16 kHz or 24 kHz).
- **Response** (`Accept: application/json`): `{sessionId, transcript, audioDurationMs, turns[]}` (`turns` is empty in `PUSH_TO_TALK`).
- **Limits:** 32 MB body, 10 minutes of audio; `429` on rate limits (not billed).
- **Convert first:** `ffmpeg -i input.webm -ac 1 -ar 24000 -c:a pcm_s16le -map_metadata -1 output.wav`

```python
import json, os, httpx

async def transcribe_wav(wav_bytes: bytes) -> str:
    req = {"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK",
           "languageBias": ["English"]}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.meta.ai/v1/asr/transcribe",
            headers={"Authorization": f"Bearer {os.environ['MODEL_API_KEY']}", "Accept": "application/json"},
            files={
                "request": (None, json.dumps(req), "application/json"),
                "audio": ("answer.wav", wav_bytes, "audio/wav"),
            },
        )
        r.raise_for_status()
        return r.json()["transcript"]
```

### 11.3 Gemini API

- **Install:** `pip install -U google-genai` (JS: `npm install @google/genai`)
- **Key:** `GEMINI_API_KEY` (read by `genai.Client()`)
- **API surface:** the **Interactions API** (`client.interactions.create`). Older `generate_content` tutorials are outdated; do not copy them.
- **Models used:** `gemini-3.8-flash` (generation), `gemini-3.5-flash-lite` (fast classifiers), `gemini-3.8-flash-tts` / `gemini-3.8-flash-lite-tts` (voice).
- **Do not send** `temperature`, `top_p`, `top_k` (ignored by Gemini 3.6+ and 4 models).
- **Async:** use the SDK's async client if available (check `client.aio`); otherwise wrap calls in `asyncio.to_thread(...)`. Never block the engine loop.

#### 11.3.1 Basic call

```python
from google import genai

client = genai.Client()

interaction = client.interactions.create(
    model="gemini-3.8-flash",
    input="Explain how AI works in a few words"
)
print(interaction.output_text)
```

#### 11.3.2 Structured JSON output (profiles, overlaps, crumbs, classifiers)

```python
import json
from google import genai
from models import Crumb          # Pydantic model, Section 12
from prompts import CRUMB_SYSTEM

client = genai.Client()

interaction = client.interactions.create(
    model="gemini-3.8-flash",
    system_instruction=CRUMB_SYSTEM,
    input=json.dumps(payload),
    response_format={
        "type": "text",
        "mime_type": "application/json",
        "schema": Crumb.model_json_schema(),
    },
    generation_config={"thinking_level": "low", "max_output_tokens": 400},
)
crumb = Crumb.model_validate_json(interaction.output_text)
```

#### 11.3.3 Grounding with Google Search (outside facts)

```python
interaction = client.interactions.create(
    model="gemini-3.8-flash",
    input=f"Find one surprising, verifiable, light fun fact related to {topic}. "
          "Return the fact in one sentence and nothing else.",
    tools=[{"type": "google_search"}],
)
fact_text = interaction.output_text
# Citations: each model_output text block can carry annotations of type "url_citation"
# with url, title, start_index, end_index. Read them from the interaction's output blocks
# (check the exact attribute names against the Grounding docs) and keep url + title.
```

Billing note: Gemini 3+ models share 5,000 free grounded search requests per month, then $14 per 1,000 queries. Only call grounding for `outside` crumbs.

#### 11.3.4 Text-to-speech (crumb voice)

```python
import base64
from google import genai

client = genai.Client()

interaction = client.interactions.create(
    model="gemini-3.8-flash-tts",
    input=[{
        "type": "user_input",
        "content": [{
            "type": "text",
            "text": crumb.text,
            "annotations": [{
                "type": "speech_metadata",
                "style": "warm, playful, curious, like a friendly host speaking softly at a small table",
            }],
        }],
    }],
    response_format={"type": "audio"},
    generation_config={
        "speech_config": [
            {"voice": "Kore"},
        ]
    },
)
wav_bytes = base64.b64decode(interaction.output_audio.data)   # WAV, 24 kHz mono 16-bit
```

Streaming TTS is available (`stream=True`, `step.delta` events with `audio` deltas as raw 24 kHz PCM), but because crumbs are prefetched, unary WAV is simpler and recommended.

### 11.4 Optional: Muse Spark fallback (Meta Model API, OpenAI-compatible)

Use only if Gemini is rate limited. Base URL `https://api.meta.ai/v1`, bearer `MODEL_API_KEY`, model `muse-spark-1.3`. The API is drop-in compatible with the OpenAI SDK (Responses and Chat Completions). Keep the same prompts; parse JSON from the text output with the same Pydantic models.

---

## 12. Data models and event protocol

Build `server/models.py` first; mirror it in `web/src/lib/types.ts`. Every workstream depends on it.

```python
from typing import Literal
from pydantic import BaseModel, Field

Mode = Literal["social", "networking", "speed_dating"]
Seat = Literal[1, 2]
Phase = Literal["lobby", "profiles", "calibrating", "live", "ended"]

class Consent(BaseModel):
    typed_name: str
    agreed: bool
    at: float

class ProfileItem(BaseModel):
    id: str                                   # e.g. "s1_i3"
    kind: Literal["interest", "story", "goal", "fun_fact", "value", "preference", "offer", "seeking"]
    text: str = Field(max_length=100)
    hook: str = Field(max_length=140)
    depth: int = Field(ge=1, le=4)

class Profile(BaseModel):
    seat: Seat
    display_name: str
    items: list[ProfileItem]
    vibe: str = Field(default="", max_length=80)   # e.g. "curious, dry humor"
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
    kind: Literal["opener", "steer", "switch", "manual", "wildcard"]
    format: Literal["question", "fun_fact", "would_you_rather", "callback", "collab"]
    text: str                                  # <= 28 words, spoken
    addressed_to: Literal["both", "seat1", "seat2"]
    source: Literal["overlap", "profile_seat1", "profile_seat2", "conversation", "outside"]
    source_item_ids: list[str] = []
    topic: str
    depth: int = Field(ge=1, le=4)
    why: str                                   # one sentence for X-ray
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
```

### 12.1 WebSocket channels

**`/ws/audio/{sid}`** (Table → server): binary frames only, PCM16 LE mono 24 kHz, 80 ms each.

**`/ws/events/{sid}?role=table|seat1|seat2`** (bidirectional JSON):

Client → server:

| `type` | Payload | From |
|---|---|---|
| `vad` | `{active: bool, rms: float}` at 10 Hz | Table |
| `control` | `{action: "spark" \| "skip" \| "hold" \| "resume" \| "pause_listening" \| "resume_listening" \| "end" \| "xray"}` | Table |
| `tts_state` | `{crumb_id, playing: bool}` | Table |

Server → clients:

| `type` | Payload | To |
|---|---|---|
| `session` | `{phase, mode, seats: {1: {joined, consented, ready, name}, 2: {...}}}` | All |
| `partial` | `{text}` | Table |
| `turn` | `{turn_id, seat, text, words, dropped_as_bot: bool}` | Table |
| `engine` | `{silence_s, t_long, t_exc, staleness, parts: {L,R,G,Q,E}, hold: {kind, until} \| null, cooldown_s, next: "none" \| "steer" \| "switch", cached: {steer, switch}}` at 4 Hz | Table |
| `crumb` | `{crumb: Crumb, audio_b64: str}` | Table |
| `crumb_interrupted` | `{crumb_id}` | Table |
| `calibration` | `{step: "seat1" \| "seat2" \| "done", label_map}` | Table |
| `trail_ready` | `{}` (then each phone GETs its own trail) | Phones |
| `error` | `{message}` | All |

---

## 13. Backend REST endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/sessions` | `{mode}` | `{session_id, join_urls: {1, 2}}` |
| GET | `/api/sessions/{sid}` | | session summary (no transcript) |
| POST | `/api/sessions/{sid}/seats/{seat}/consent` | `{typed_name, agreed: true}` | `{ok}` |
| GET | `/api/interview?mode=&quick=` | | `{questions: [{id, text}]}` |
| POST | `/api/sessions/{sid}/seats/{seat}/answers` | multipart: `question_id`, `audio` (webm/mp4) | `{transcript}` (ffmpeg → Muse REST) |
| POST | `/api/sessions/{sid}/seats/{seat}/profile` | | `Profile` (Gemini extraction) |
| PATCH | `/api/sessions/{sid}/seats/{seat}/profile` | `{removed_item_ids, off_limits}` | `Profile` |
| POST | `/api/sessions/{sid}/seats/{seat}/ready` | | `{ok}`; when both ready → overlaps + `calibrating` |
| POST | `/api/sessions/{sid}/end` | | `{ok}` → phones prompted for opt-in |
| POST | `/api/sessions/{sid}/seats/{seat}/trail-optin` | `{share: bool, contact: str \| null}` | `{ok}` |
| GET | `/api/sessions/{sid}/seats/{seat}/trail` | | `{matched: bool, partner_contact, trail: TrailItem[]}` |
| DELETE | `/api/sessions/{sid}` | | `{ok}` (wipe) |

Server rule: consent is checked on every audio-related endpoint and on `/ws/audio`; reject if either seat lacks consent.

---

## 14. Frontend specification

### 14.1 Table audio capture

```ts
// capture.ts
const ctx = new AudioContext({ sampleRate: 24000 });          // Chrome resamples the mic for us
await ctx.audioWorklet.addModule("/pcm-worklet.js");
const stream = await navigator.mediaDevices.getUserMedia({
  audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
});
const src = ctx.createMediaStreamSource(stream);
const node = new AudioWorkletNode(ctx, "pcm-worklet");
src.connect(node);
node.port.onmessage = (e) => {
  if (e.data.type === "frame") audioWs.send(e.data.pcm);       // ArrayBuffer, 3,840 bytes
  if (e.data.type === "rms") vad.update(e.data.rms);           // every 100 ms
};
```

```js
// pcm-worklet.js: Float32 → Int16, 80 ms frames (1,920 samples at 24 kHz), RMS every 100 ms
class PcmWorklet extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(1920); this.n = 0; this.sumSq = 0; this.cnt = 0; }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      this.sumSq += s * s; this.cnt++;
      if (this.n === 1920) { this.port.postMessage({ type: "frame", pcm: this.buf.slice().buffer }); this.n = 0; }
      if (this.cnt === 2400) { this.port.postMessage({ type: "rms", rms: Math.sqrt(this.sumSq / this.cnt) }); this.sumSq = 0; this.cnt = 0; }
    }
    return true;
  }
}
registerProcessor("pcm-worklet", PcmWorklet);
```

**Local VAD:** track a noise floor (rolling 10th percentile of RMS over 10 s). `active = rms > max(0.01, 3 * noiseFloor)` for at least 150 ms; becomes inactive after 400 ms below. Send `{type: "vad"}` at 10 Hz. (P2: swap in a Silero-based browser VAD if the hall is noisy.)

**TTS playback:** decode `audio_b64` to a Blob (`audio/wav`), play through an `<audio>` element or WebAudio. Send `tts_state` on start and end. On local VAD active (after the first 300 ms) or a `speechStart`-driven server message, fade out over 250 ms and send `tts_state {playing: false}`.

### 14.2 Phone recording

Use `MediaRecorder` with the browser's default container (webm/opus on Chrome/Android, mp4/aac on iOS Safari). Upload the blob; the server converts with ffmpeg. Show a 20-second countdown ring and a waveform from an `AnalyserNode` so it feels alive.

### 14.3 Visual design

- **Mood:** warm, cozy, a little magical (fairy-tale theme), not clinical. Dark warm background (`#17120E`), amber orb (`#F5A524`), crumb highlight (`#FFD27A`), holding blue (`#5B8DEF`), paused gray (`#6B6B6B`), off-white text (`#F6EFE6`).
- **Orb:** large centered circle with a soft radial glow; breathing animation at about 0.2 Hz while listening; scale and brightness pulse on a crumb.
- **Crumb text:** 40 to 56 px, centered below the orb, fades in word by word in sync with the voice (approximate by duration).
- **The trail of crumbs:** a row of small glowing dots along the bottom edge; one dot appears per crumb dropped (a visual history and a nod to the name).
- **Controls:** bottom corners, large touch targets, labels always visible.
- **X-ray:** a right-side drawer with monospace readouts and small sparkline charts for silence vs thresholds and staleness over time.
- **Accessibility:** crumbs are always captioned; high contrast; no information carried by color alone (orb states also show a text label).

---

## 15. Privacy, consent, safety, and rules compliance

### 15.1 Data lifecycle

| Data | Where | Lifetime |
|---|---|---|
| Raw audio (live) | Streamed Table → server → Muse; never written to disk | Not stored |
| Profile answer recordings | Server temp dir during ffmpeg conversion | Deleted immediately after transcription |
| Transcripts, profiles, crumbs | Server memory | Until trail shown, session deleted, or 2 h TTL |
| Consent records | Server memory | Session lifetime |
| Metrics for the pitch (counts, latencies, survey numbers) | Aggregated, anonymous | Kept (no names, no text) |

- Check the Meta Model API dashboard for a zero-data-retention option for Muse (the pricing page says ZDR is priced the same as Standard).
- Review Gemini API data-use terms for your tier; unpaid tiers may allow Google to use content to improve products. Prefer a billed key for demos with real people.

### 15.2 Meta Official Rules checklist

- [ ] **Express written consent** before collecting anyone's personal information (consent screen with typed name + checkbox). Only teammates appear in the demo video.
- [ ] **Public GitHub repo**, with no API keys (use `.env`, listed in `.gitignore`) and no recordings or transcripts committed.
- [ ] README states what the team built during the event vs third-party libraries used.
- [ ] No Jubilee clips, logos, or "The Button" name anywhere (IP rule). Verbal "inspired by" is fine.
- [ ] Content follows Meta Community Standards (no sexual content even in speed dating mode).
- [ ] One project submitted to the Meta track.
- [ ] Demo video 2 to 3 minutes; short written description of what was built and the role of AI.

### 15.3 Safety behaviors

- If either person says something indicating distress or harm, Breadcrumbs does not respond to it with a crumb; it pauses automatic crumbs for the session. (The hold classifier's `external` path covers this; add a small lexicon.)
- Off-limits topics are never raised, even as "outside" topics.
- Speed dating mode prompts never reference bodies, appearance, or sexual topics.

---

## 16. Testing and evaluation

### 16.1 Simulator (build early; lets the engine be tested without microphones)

`sim/replay.py` feeds scripted turns into the engine on a fake clock, emitting the same events Muse would (`speechStart`, `speaker`, `speechEnd`, `speechComplete`) and VAD frames, then records the engine's decisions.

```json
{
  "name": "show_hold_then_resume",
  "mode": "social",
  "turns": [
    {"seat": 1, "text": "I did this crazy climb last summer.", "start": 0.0, "end": 2.1},
    {"seat": 2, "text": "No way, where?", "start": 2.8, "end": 3.6},
    {"seat": 1, "text": "Wait, let me show you, I have a picture.", "start": 4.2, "end": 6.0},
    {"seat": 1, "text": "Okay here, this is the top.", "start": 31.0, "end": 33.0}
  ],
  "expect": [{"between": [6.0, 31.0], "action": "none"}]
}
```

Required scripts (each a pytest case):

| Script | Scenario | Expectation |
|---|---|---|
| `normal_pauses` | 3 minutes of lively turns with pauses up to 3 s | No automatic crumbs |
| `warming_only` | Lively, then a 4.5 s pause, then speech | No crumb (warming band only) |
| `long_pause_lively` | Lively, then a 5.5 s pause, then speech | One **steer** at 5.0 s |
| `stale_decay` | Replies shrink to 1 to 3 words, gaps grow, then a 5.5 s pause | One **switch** at 5.0 s |
| `exceptional_pause` | A 7 s pause starting 20 s after a previous crumb (regular cooldown blocks the 5 s steer) | One **switch** at 6.5 s |
| `unanswered_steer` | Steer at 5 s, then nobody speaks for 20 s | One **switch** 6.5 s after the steer ends, then nothing more (max 2 unanswered) |
| `show_hold_then_resume` | "let me show you" + 25 s silence | **No crumb** during the hold |
| `show_hold_timeout` | "let me pull it up" + 80 s silence | No crumb before 60 s; then normal thresholds from 0 |
| `think_hold` | "hmm, good question" + 8 s silence | No crumb (thresholds doubled to 10 s / 13 s) |
| `wait_really_false_positive` | "Wait, really? That's wild, how did..." continues talking | **No hold** |
| `barge_in` | Speech starts 1 s into a crumb | `crumb_interrupted` |
| `self_hearing` | Muse turn overlapping TTS playback | Dropped as bot speech |
| `cooldown` | Two 5.5 s pauses 15 s apart with one short exchange between | Only one automatic crumb |

Unit tests: hold lexicon positives and negatives (Appendix A), staleness components on fixed inputs, threshold clamping, Crumb validator (word count, off-limits, removed items, em dash replacement).

### 16.2 Live tuning (overnight)

Run 4 to 6 pairs of willing hackers for 5 minutes each (with the consent screen). Watch the X-ray. Tune `T_LONG` and `T_EXCEPTIONAL` clamps and `STALE_SWITCH` first. Record **only aggregate metrics**, never text.

### 16.3 Metrics to report in the pitch (honestly, with small n)

| Metric | Definition | Target |
|---|---|---|
| Crumb success rate | Share of crumbs followed by 3 or more human turns within 60 s | 70% or more |
| False interruptions | Crumbs fired during speech or an intentional hold | 0 |
| Silence-to-voice latency | Threshold crossed to crumb audio starting (p50 / p95) | 0.7 s / 3 s |
| Connection rating | After the chat, each person rates "How connected do you feel to your partner?" 1 to 7 | Report the mean |
| Would use again | Yes / no | Report the share |

---

## 17. Build plan

### 17.1 Timeline (from about 6:30 PM Saturday)

| By | Milestone | Must be true |
|---|---|---|
| **8:00 PM** | M0: plumbing | Muse key works; Table mic streams to Muse in `DIARIZATION`; live diarized turns print in X-ray; Gemini structured call returns a `Crumb`; HTTPS tunnel works on a phone |
| **11:30 PM** | M1: soft submission | Phone flow (consent → 1 to 3 answers → profile → review) works; calibration maps seats; manual **Drop a crumb** speaks a Gemini crumb via TTS. Soft-submit on Devpost with title, tagline, screenshots |
| **3:00 AM** | M2: the brain | Pause timer, adaptive thresholds, hold detection, policy, cooldowns, prefetch; all simulator tests pass; opening crumb from overlaps |
| **7:00 AM** | M3: polish + modes | Staleness with energy tags; grounded outside facts; modes; trail with mutual opt-in; orb animations; X-ray polish; live tuning with real pairs |
| **10:00 AM** | M4: ship | Demo video recorded; README; Devpost description; repo public and scrubbed |
| **11:30 AM** | Submit | All tracks selected (Meta, MLH Gemini, Most Entertaining) |

**Cut lines if behind:** at 2 AM, drop modes (ship Social only) and the trail. At 6 AM, drop grounded facts (use questions for `outside`). Never drop: consent, hold detection, barge-in, X-ray.

### 17.2 Workstreams (one per teammate or coding agent)

| Workstream | Owns | Depends on |
|---|---|---|
| **A. Audio + Muse** | `pcm-worklet.js`, `capture.ts`, VAD, `/ws/audio`, `muse/realtime.py`, `muse/transcribe.py`, `audio/convert.py`, calibration mapping | `models.py` |
| **B. Engine** | `engine/*`, `config.py`, simulator + tests | `models.py`; can run fully on the simulator before audio exists |
| **C. AI services** | `ai/*` prompts, profiles, overlaps, crumbs + validator, facts, TTS, trail, prefetch cache | `models.py` |
| **D. Frontend + demo** | Home, Table (orb, controls, X-ray), Join flow, Trail; demo video and Devpost text | `types.ts`, WS protocol (Section 12.1) |

Contracts first: whoever starts writes `server/models.py`, `web/src/lib/types.ts`, and a stub server that emits fake `engine` and `crumb` events, so the frontend can be built against it immediately.

### 17.3 Instructions for coding agents

- Read Sections 8, 11 and 12 before writing code. Do not invent API fields; use Section 11 exactly and check the linked docs when unsure.
- Keep the engine pure and deterministic (inject the clock) so the simulator can test it.
- Never block the asyncio loop with sync network calls.
- Log every engine decision with its inputs (feeds X-ray and debugging), but never log transcript text to disk.
- Prefer small, working increments: get one end-to-end path working before adding features.

---

## 18. Demo plan

### 18.1 Live judging (about 2 minutes at the table)

1. **Hook (10 s):** "Once upon a time, two strangers sat down at a table, and the conversation died after 'what's your major.' Breadcrumbs makes sure it doesn't."
2. **Setup (30 s):** both judges scan QR codes, consent, and do the Quick profile (one question). Show the profile chips and the X button: "you control what it knows."
3. **Calibration + opener (20 s):** each judge says hi; the orb drops the overlap opener about the two of them. (This is the wow moment.)
4. **Hold (20 s):** a teammate seated with a judge says "wait, let me show you something" and scrolls a phone. The orb turns blue: Holding. Nothing interrupts.
5. **Steer and switch (30 s):** everyone goes quiet. At 5 s a steer lands on the current topic; if nobody answers it, about 6.5 s later a context switch arrives with a grounded fun fact (source shown).
6. **X-ray (10 s):** open the panel: pause timer vs thresholds, staleness parts, the reasoning for the last crumb.
7. **Close (10 s):** "Every AI icebreaker helps you start a conversation. Breadcrumbs keeps it going, and knows when to stay quiet." Show the trail on a phone.

Demo hygiene: a quieter corner if possible, the laptop between the two speakers, volume at about 60%, a backup run recorded on video in case the hall is too loud.

### 18.2 Demo video (2 to 3 minutes, following Meta's suggested structure)

| Segment | Time | Content |
|---|---|---|
| Problem and how it brings people closer | ~30 s | Small talk stalls; the research on deeper conversations; one in two adults lonely; Breadcrumbs gets strangers past small talk in person |
| Working demo | ~90 s | Two teammates: setup, opener, a natural conversation, a hold ("let me pull this up"), a steer, a switch with a fact, the trail |
| How AI is integrated | ~30 s | Muse diarized live transcription → engine (pauses, staleness, holds) → Gemini crumbs, grounding, voice; X-ray footage |
| Tools, harnesses, models used to build | ~30 s | Muse Voice Transcribe, Gemini 3.8 Flash / Flash-Lite / Flash TTS, Google Search grounding, FastAPI, React; plus the AI coding tools the team used and how they helped |

---

## 19. Submission checklist (Devpost, by 11:30 AM Sunday)

- [ ] Title: **Breadcrumbs**; tagline: "Never lose the thread."
- [ ] Tracks (max 3 besides Best Overall/Novice): **Meta**, **[MLH] Best Use of Gemini API**, **Most Entertaining**
- [ ] Demo video, 2 to 3 minutes (Section 18.2)
- [ ] Public GitHub repo link; README with setup, architecture diagram, "what we built vs libraries"
- [ ] Written description: what it does, the role of AI, privacy design (Appendix C draft)
- [ ] Screenshots: orb listening, crumb, holding, X-ray, phone profile, trail
- [ ] At least one team member present for judging 2 to 5 PM in Welch
- [ ] Sign up for Best Pitch (Discord) for 5:30 PM

---

## 20. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Loud judging hall hurts transcription and diarization | High | Sit close to the laptop; quieter corner; `keywords` with names; backup video; local VAD still drives timing |
| Diarization mislabels or overlapping speech | Medium | Calibration step; X-ray shows labels; engine metrics tolerate a few mislabeled turns; overlapping speech is rare in two-person talk |
| Breadcrumbs hears its own voice | Medium | Echo cancellation + bot-speaking interval filter (Section 8.11) |
| Gemini latency at the moment of a pause | Medium | Prefetch cache with pre-synthesized audio (Section 8.10) |
| Hallucinated "facts" | Medium | Grounding required; no citation means it becomes a question |
| Phone mic blocked (no HTTPS) | High if forgotten | Cloudflare tunnel + `PUBLIC_BASE_URL` |
| Muse session drops | Low | Reconnect with backoff; re-calibrate |
| Feels intrusive ("is it always listening?") | Medium | Consent, Pause listening, visible listening state, no storage, X-ray transparency; say this in the pitch before judges ask |
| Rate limits or credits | Low | $50 Muse credits cover hundreds of hours; Muse Spark fallback for text |
| Time | High | Cut lines in Section 17.1; simulator lets engine work start immediately |

---

## 21. Open questions for the team

1. **Wildcard drops (F22):** keep the original Jubilee-style random interjection as an optional toggle for entertainment, or leave it out?
2. **Speaker for the demo:** laptop speakers or a small Bluetooth speaker? (Bluetooth adds latency and can confuse echo cancellation; test both.)
3. **Voice:** `Kore` or `Puck`? Decide by ear in the first hour.
4. **Who on the team wants the Round 2 trip?** Each person who wants it must be in Meta's recruiting pipeline and pass Meta's online coding assessment.

---

## Appendix A: Hold lexicon (`engine/hold.py`)

Match case-insensitively against the **last 8 words** of the latest turn, only when silence follows.

```python
HOLD_PATTERNS = {
    "show": [
        r"\blet me (show|pull (it|this|that) up|find (it|this|that)|grab (it|my phone)|look (it|this) up)\b",
        r"\b(look|check) (at )?this( out)?\b",
        r"\b(hold on|hang on|one sec(ond)?|just a sec(ond)?|give me a (sec(ond)?|minute))\b",
        r"\bwhere (is|was) (it|that)\b",
        r"\bi('m| am) (looking|trying to find)\b",
        r"\bwait\W*$",                                   # "wait" as the last word of the turn
    ],
    "think": [
        r"\blet me think\b",
        r"\b(hmm+|umm+)\W*$",
        r"\b(good|great|tough|hard) question\b",
        r"\bhow do i (put|say) (this|it)\b",
        r"\bthat'?s a (tough|hard|good) one\b",
    ],
    "external": [
        r"\b(sorry,? )?i (have|need|gotta|got) to take this\b",
        r"\bexcuse me\b",
        r"\b(be right back|brb)\b",
    ],
}
NEGATIVE_PATTERNS = [
    r"\bwait,? (really|what|seriously|no way)\b.*\w+\s+\w+\s+\w+",   # surprise followed by more speech
    r"\bcan'?t wait\b",
]
```

Test positives: "Wait, let me show you." / "hold on, where is it" / "Hmm, good question." / "Let me pull it up real quick." / "sorry I have to take this."
Test negatives: "Wait, really? That's insane, how did you even get in?" / "I can't wait for the trip." / "Show me sometime!" (followed by more speech).

## Appendix B: Canned fallback crumbs

Use only when generation fails validation twice. Fill `{name1}` / `{name2}`.

- Social: "{name1}, what's something you've changed your mind about recently?" / "{name2}, what's the best thing that happened to you this month?"
- Networking: "{name2}, what's a problem you'd love help with right now? {name1}, any ideas?" / "{name1}, what's something you're great at that most people don't know?"
- Speed dating: "Would you rather have a spontaneous weekend trip or a perfectly planned one? {name1} first." / "{name2}, what's a small thing that instantly makes your day better?"
- Any: "{name1}, tell {name2} about the last thing that made you laugh really hard."

## Appendix C: Devpost description draft (no em dashes)

> **Breadcrumbs** helps two people who just met get past small talk, in person. Each person records a 60-second voice intro on their phone and approves a short profile. A laptop on the table listens with Muse Voice Transcribe, which labels who is speaking, and Breadcrumbs stays quiet while the conversation flows. When a pause runs long or replies start getting shorter, it drops a "crumb": a question about something you both care about, a callback to something you said, or a grounded fun fact, written by Gemini and spoken in a warm voice. It knows when not to talk, too. Say "wait, let me show you this" and it holds. At the end, if you both want to, you swap contacts and get a trail of threads to pick up next time. No audio is stored, transcripts are wiped, and Breadcrumbs only ever mentions what you chose to share.
>
> Research by Kardas, Kumar and Epley found that people expect deep conversations with strangers to be more awkward than they really are. Breadcrumbs takes that risk off your shoulders: the crumb asked, not you.

Pitch lines:
- "Every AI icebreaker helps you start a conversation. Breadcrumbs keeps it going, and knows when to stay quiet."
- "Most breadcrumbing leaves you hanging. Ours leads you somewhere."
- "Success for Breadcrumbs is how little it talks."

## Appendix D: References

- Meta-Sponsored Track Round 1 Official Rules (HackTX 2026) and Meta track sheet
- Muse Voice Transcribe docs: https://dev.meta.ai/docs/speech-to-text ; realtime reference: https://dev.meta.ai/docs/api-reference/voice/realtime ; transcribe reference: https://dev.meta.ai/docs/api-reference/voice/transcribe ; pricing and limits: https://dev.meta.ai/docs/pricing-rate-limits ; Meta Model API overview: https://dev.meta.ai/docs/overview
- Gemini API: quickstart https://ai.google.dev/gemini-api/docs/quickstart ; models https://ai.google.dev/gemini-api/docs/models ; structured output https://ai.google.dev/gemini-api/docs/structured-output ; text generation https://ai.google.dev/gemini-api/docs/text-generation ; grounding https://ai.google.dev/gemini-api/docs/google-search ; TTS https://ai.google.dev/gemini-api/docs/speech-generation
- Kardas, M., Kumar, A., & Epley, N. "Overly Shallow? Miscalibrated Expectations Create a Barrier to Deeper Conversation." *Journal of Personality and Social Psychology* (APA press release: https://www.apa.org/news/press/releases/2021/09/deep-conversations-strangers)
- U.S. Surgeon General's Advisory on the Epidemic of Loneliness and Isolation (2023)

