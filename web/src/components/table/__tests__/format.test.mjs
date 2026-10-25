// Unit tests for the Table's pure helpers. Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test src/components/table/__tests__/
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  CAPTION_ENTER_S,
  CAPTION_EXIT_S,
  CAPTION_MIN_TOTAL_MS,
  CAPTION_RECAP_MS,
  captionHideAt,
  captionLingerMs,
  captionStage,
  captionStaggerS,
  deriveOrb,
  lobbyStep,
  noiseSuspected,
  estimateSpeechS,
  formatClock,
  isInsecureUrl,
  isLocalUrl,
  isResyncStep,
  micOnMessage,
  seatName,
  sourceCaption,
  speechServiceProblem,
  staleThreshold,
  wavDurationS,
  wordsOf,
} from "../format.ts";
import { approach, backingSize, hexToRgb, MODE_LOOKS, PALETTES, resolveLook, rippleProgress } from "../orbLook.ts";

/** A real 24 kHz mono 16-bit WAV of `seconds` of silence, base64 encoded. */
function wavB64(seconds, sampleRate = 24000) {
  const dataBytes = Math.round(seconds * sampleRate) * 2;
  const buf = new ArrayBuffer(44 + dataBytes);
  const v = new DataView(buf);
  const ascii = (off, s) => [...s].forEach((c, i) => v.setUint8(off + i, c.charCodeAt(0)));
  ascii(0, "RIFF");
  v.setUint32(4, 36 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  ascii(36, "data");
  v.setUint32(40, dataBytes, true);
  return Buffer.from(buf).toString("base64");
}

function engine(overrides = {}) {
  return {
    phase: "live",
    silence_s: 0,
    band: "normal",
    t_normal: 3,
    t_long: 5,
    t_exc: 6.5,
    staleness: 0,
    stale: false,
    parts: { L: 0, R: 0, G: 0, Q: 0, E: 0 },
    hold: null,
    cooldown_s: 0,
    next: "none",
    cached: { steer: null, switch: null },
    listening_paused: false,
    auto_paused: false,
    turn_active: false,
    vad_active: false,
    tts_playing: false,
    pending_delivery: false,
    crumbs_this_silence: 0,
    quiet_hint: false,
    depth_max: 1,
    topic: null,
    energy: null,
    label_map: {},
    round_remaining_s: null,
    crumbs_last_10min: 0,
    last_decision: null,
    metrics: {
      crumbs: 0,
      interrupted: 0,
      answered: 0,
      success_rate: null,
      false_interruptions: 0,
      latency_ms_p50: null,
      latency_ms_p95: null,
    },
    ...overrides,
  };
}

const crumb = (source, extra = {}) => ({
  id: "c_1",
  kind: "steer",
  format: "question",
  text: "Maya, what made you sign up?",
  addressed_to: "seat1",
  source,
  source_item_ids: [],
  topic: "running",
  depth: 1,
  why: "test",
  citation_url: null,
  citation_title: null,
  ...extra,
});

describe("wavDurationS", () => {
  it("reads the duration from the WAV header", () => {
    assert.ok(Math.abs(wavDurationS(wavB64(2.5)) - 2.5) < 0.01);
    assert.ok(Math.abs(wavDurationS(wavB64(1, 16000)) - 1) < 0.01);
  });

  it("returns null for empty or non-WAV payloads", () => {
    assert.equal(wavDurationS(""), null);
    assert.equal(wavDurationS(Buffer.from("not a wav file at all, just some text here....").toString("base64")), null);
    assert.equal(wavDurationS("%%%not-base64%%%"), null);
  });
});

describe("estimateSpeechS", () => {
  it("prefers the WAV duration", () => {
    assert.ok(Math.abs(estimateSpeechS(wavB64(4), "one two") - 4) < 0.01);
  });

  it("falls back to a words-per-second estimate and clamps", () => {
    const thirteen = "a b c d e f g h i j k l m";
    assert.equal(estimateSpeechS("", thirteen), 5); // 13 words / 2.6 wps
    assert.equal(estimateSpeechS("", "hi"), 1.5); // minimum
    assert.equal(estimateSpeechS("", "w ".repeat(200)), 16); // maximum
  });
});

describe("captions and names", () => {
  const names = { 1: "Maya", 2: "Dev" };

  it("maps every crumb source to its subtle caption", () => {
    assert.equal(sourceCaption(crumb("overlap"), names), "from something you both shared");
    assert.equal(sourceCaption(crumb("profile_seat1"), names), "from Maya's profile");
    assert.equal(sourceCaption(crumb("profile_seat2"), names), "from Dev's profile");
    assert.equal(sourceCaption(crumb("conversation"), names), "from your conversation");
    assert.equal(sourceCaption(crumb("outside"), names), "outside the box");
  });

  it("a quick table (no profiles) never claims a profile", () => {
    assert.equal(sourceCaption(crumb("profile_seat1"), names, true), "from something Maya said");
    assert.equal(sourceCaption(crumb("profile_seat2"), names, true), "from something Dev said");
    assert.equal(sourceCaption(crumb("overlap"), names, true), "from something you both said");
    assert.equal(sourceCaption(crumb("conversation"), names, true), "from your conversation");
  });

  it("the Just talk icebreaker reads as a starter, not 'outside the box'", () => {
    assert.equal(sourceCaption(crumb("outside", { kind: "opener" }), names, true), "to get you started");
    assert.equal(sourceCaption(crumb("outside", { kind: "opener" }), names), "outside the box");
    assert.equal(sourceCaption(crumb("outside"), names, true), "outside the box");
  });

  it("falls back to seat numbers when a name is missing", () => {
    const session = { seats: { 1: { name: "  " }, 2: { name: "Dev" } } };
    assert.equal(seatName(session, 1), "Seat 1");
    assert.equal(seatName(session, 2), "Dev");
    assert.equal(seatName(null, 2), "Seat 2");
  });

  it("splits words and formats clocks", () => {
    assert.deepEqual(wordsOf("  two   words "), ["two", "words"]);
    assert.equal(formatClock(272), "4:32");
    assert.equal(formatClock(-3), "0:00");
    assert.equal(formatClock(59.6), "1:00");
  });

  it("detects join URLs phones cannot use for the mic", () => {
    assert.equal(isLocalUrl("http://localhost:8000/join/abc/1"), true);
    assert.equal(isLocalUrl("http://127.0.0.1:5173/join/abc/1"), true);
    assert.equal(isLocalUrl("https://x.trycloudflare.com/join/abc/1"), false);
    assert.equal(isInsecureUrl("http://192.168.1.5:8000/join/abc/1"), true);
    assert.equal(isInsecureUrl("https://x.trycloudflare.com/join/abc/1"), false);
    assert.equal(isLocalUrl("not a url"), false);
  });
});

describe("deriveOrb", () => {
  it("is idle until the first engine snapshot", () => {
    assert.equal(deriveOrb(null, false).mode, "idle");
  });

  it("listens by default and warms past the normal band", () => {
    assert.deepEqual(deriveOrb(engine(), false), { mode: "listening", label: "Listening", detail: null });
    assert.equal(deriveOrb(engine({ band: "warming", silence_s: 3.4 }), false).mode, "warming");
    assert.equal(deriveOrb(engine({ pending_delivery: true }), false).label, "Finding a crumb");
    // A long band while someone is mid-turn is not a pause.
    assert.equal(deriveOrb(engine({ band: "long", turn_active: true }), false).mode, "listening");
  });

  it("shows holds with kind and remaining seconds", () => {
    const view = deriveOrb(engine({ hold: { kind: "show", remaining_s: 41.2, source: "lexical" } }), false);
    assert.deepEqual(view, { mode: "holding", label: "Holding (show)", detail: "42 s" });
  });

  it("orders states: paused > speaking > safety > hold > warming", () => {
    const all = engine({
      listening_paused: true,
      auto_paused: true,
      hold: { kind: "think", remaining_s: 5, source: "llm" },
      band: "long",
    });
    assert.equal(deriveOrb(all, true).mode, "paused");
    assert.equal(deriveOrb({ ...all, listening_paused: false }, true).mode, "crumb");
    assert.equal(deriveOrb({ ...all, listening_paused: false }, false).mode, "safety");
    assert.equal(deriveOrb({ ...all, listening_paused: false, auto_paused: false }, false).mode, "holding");
  });

  it("always carries a text label (no color-only state)", () => {
    for (const e of [engine(), engine({ listening_paused: true }), engine({ auto_paused: true })]) {
      assert.ok(deriveOrb(e, false).label.length > 0);
    }
  });
});

describe("voice re-sync during live", () => {
  it("a calibration prompt while live is a re-sync", () => {
    assert.equal(isResyncStep("seat1"), true);
    assert.equal(isResyncStep("seat2"), true);
    assert.equal(isResyncStep("done"), false);
    assert.equal(isResyncStep(null), false);
  });

  it("the orb says Re-syncing voices instead of Listening", () => {
    const orb = deriveOrb(engine(), false, true);
    assert.equal(orb.label, "Re-syncing voices");
    assert.notEqual(orb.mode, "listening");
    assert.equal(deriveOrb(engine(), false).label, "Listening");
    assert.equal(deriveOrb(engine({ listening_paused: true }), false, true).mode, "paused");
  });
});

describe("micOnMessage (PRD 7 F2)", () => {
  it("never claims live transcription before both seats consent", () => {
    for (const phase of ["lobby", "profiles", "calibrating", "live"]) {
      assert.doesNotMatch(micOnMessage(phase, false), /transcribed live/);
    }
  });

  it("claims it only once audio is actually transcribed (calibrating or live)", () => {
    assert.doesNotMatch(micOnMessage("profiles", true), /transcribed live/);
    assert.match(micOnMessage("calibrating", true), /transcribed live and never stored/);
    assert.match(micOnMessage("live", true), /transcribed live and never stored/);
  });
});

describe("staleThreshold (X-ray follows live tuning)", () => {
  it("uses the live STALE_SWITCH", () => {
    assert.equal(staleThreshold({ STALE_SWITCH: 0.4 }), 0.4);
    assert.equal(staleThreshold({ STALE_SWITCH: 0.85, T_LONG_S: 5 }), 0.85);
  });

  it("falls back to 0.6", () => {
    assert.equal(staleThreshold({}), 0.6);
    assert.equal(staleThreshold({ STALE_SWITCH: "x" }), 0.6);
    assert.equal(staleThreshold({ STALE_SWITCH: Number.NaN }), 0.6);
  });
});

describe("speechServiceProblem (calibration must not stall silently)", () => {
  it("explains a failing speech connection instead of hanging", () => {
    assert.match(speechServiceProblem("reconnecting", "SSLCertVerificationError"), /Can't reach the speech service.*SSLCertVerificationError/);
    assert.match(speechServiceProblem("error", "Muse rejected the connection (HTTP 401)"), /HTTP 401/);
    assert.match(speechServiceProblem("closed"), /Start listening/);
  });
  it("stays quiet while the connection is healthy", () => {
    for (const status of ["connecting", "connected", "resyncing", "mock", null, undefined]) {
      assert.equal(speechServiceProblem(status), null);
    }
  });
});

describe("caption timing (issue 2: whole line at once, then time to read)", () => {
  const line = (status, extra = {}) => ({
    text: "Maya, what made you sign up for the half marathon?",
    durationS: 3,
    status,
    receivedAt: 1_000,
    startedAt: null,
    endedAt: null,
    ...extra,
  });

  it("reading time is at least 4 s, else 0.35 s per word plus 2 s", () => {
    assert.equal(captionLingerMs("hi"), 4_000);
    assert.equal(captionLingerMs("one two three four five"), 4_000); // 1.75 + 2 = 3.75 s
    assert.equal(captionLingerMs("w ".repeat(10)), 5_500);
    assert.equal(captionLingerMs("w ".repeat(20)), 9_000);
  });

  it("the entrance is quick: the last word starts within ~0.12 s and lands in under 0.45 s", () => {
    assert.ok(CAPTION_ENTER_S >= 0.25 && CAPTION_ENTER_S <= 0.35);
    assert.equal(captionStaggerS(1), 0);
    assert.equal(captionStaggerS(4), 0.02);
    for (const n of [2, 6, 12, 30, 80]) {
      const stagger = captionStaggerS(n);
      assert.ok(stagger <= 0.02, `stagger for ${n} words`);
      assert.ok((n - 1) * stagger + CAPTION_ENTER_S <= 0.45 + 1e-9, `total for ${n} words`);
    }
  });

  it("stays up while pending (chime, decode) and while playing, whatever the clock says", () => {
    assert.equal(captionHideAt(line("pending"), 1_000), null);
    assert.equal(captionHideAt(line("pending"), 8_500), null);
    assert.equal(captionHideAt(line("playing", { startedAt: 1_500 }), 120_000), null);
    assert.equal(captionStage(line("playing", { startedAt: 1_500 }), 120_000), "full");
  });

  it("hides a reading time after the voice ends, and never before 6 s in total", () => {
    const words = 10;
    const done = line("done", { startedAt: 1_500, endedAt: 4_500 });
    assert.equal(captionHideAt(done, 5_000), 4_500 + Math.max(4_000, words * 350 + 2_000));
    const blip = line("done", { text: "ok", startedAt: 1_100, endedAt: 1_400 });
    assert.equal(captionHideAt(blip, 2_000), 1_000 + CAPTION_MIN_TOTAL_MS);
  });

  it("an interrupted line keeps the same reading time from the moment it was cut", () => {
    const cut = line("interrupted", { startedAt: 1_500, endedAt: 2_000 });
    assert.equal(captionHideAt(cut, 2_100), Math.max(2_000 + 5_500, 1_000 + CAPTION_MIN_TOTAL_MS));
  });

  it("a line whose audio never plays falls back to its expected length", () => {
    const stuck = line("pending");
    assert.equal(captionHideAt(stuck, 9_500), Math.max(1_000 + 3_000 + 5_500, 1_000 + CAPTION_MIN_TOTAL_MS));
  });

  it("a skipped line leaves the moment it is skipped: no reading time, no 6 s minimum, no recap", () => {
    const playing = line("playing", { startedAt: 1_500, skippedAt: 2_000 });
    assert.equal(captionHideAt(playing, 2_000), 2_000);
    assert.equal(captionStage(playing, 1_999), "full");
    assert.equal(captionStage(playing, 2_000), "gone");
    // skipped while still in its chime, or after it finished and was lingering
    assert.equal(captionStage(line("interrupted", { endedAt: 1_200, skippedAt: 1_200 }), 1_200), "gone");
    assert.equal(captionStage(line("done", { startedAt: 1_500, endedAt: 4_500, skippedAt: 5_000 }), 5_000), "gone");
    // a barge-in (not a skip) still gets its full reading time
    assert.equal(captionStage(line("interrupted", { startedAt: 1_500, endedAt: 2_000, skippedAt: null }), 2_100), "full");
  });

  it("a leaving caption is a short crossfade, never something a new crumb waits on", () => {
    assert.ok(CAPTION_EXIT_S > 0 && CAPTION_EXIT_S <= 0.25);
  });

  it("moves full -> recap -> gone", () => {
    const done = line("done", { startedAt: 1_500, endedAt: 4_500 });
    const hideAt = captionHideAt(done, 5_000);
    assert.equal(captionStage(done, hideAt - 1), "full");
    assert.equal(captionStage(done, hideAt), "recap");
    assert.equal(captionStage(done, hideAt + CAPTION_RECAP_MS - 1), "recap");
    assert.equal(captionStage(done, hideAt + CAPTION_RECAP_MS), "gone");
  });
});

describe("background noise (issue 4)", () => {
  it("reads the optional engine flag and says so under the orb", () => {
    assert.equal(noiseSuspected(null), false);
    assert.equal(noiseSuspected(engine()), false);
    assert.equal(noiseSuspected(engine({ noise_suspected: true })), true);
    assert.deepEqual(deriveOrb(engine({ noise_suspected: true }), false), {
      mode: "listening",
      label: "Listening",
      detail: "Background noise ignored",
    });
    // a real state still wins over the noise note
    assert.equal(deriveOrb(engine({ noise_suspected: true, pending_delivery: true }), false).label, "Finding a crumb");
  });
});

describe("lobbyStep", () => {
  const seat = (extra = {}) => ({ joined: false, consented: false, ready: false, name: null, has_profile: false, answered: 0, opted_in: null, ...extra });
  const session = (a, b) => ({ seats: { 1: seat(a), 2: seat(b) } });

  it("walks scan -> consent and profile -> say hi", () => {
    assert.equal(lobbyStep(session({}, {})), 1);
    assert.equal(lobbyStep(session({ joined: true }, {})), 1);
    assert.equal(lobbyStep(session({ joined: true }, { joined: true })), 2);
    assert.equal(lobbyStep(session({ joined: true, ready: true }, { joined: true })), 2);
    assert.equal(lobbyStep(session({ joined: true, ready: true }, { joined: true, ready: true })), 3);
  });
});

describe("orb looks (CrumbOrb contract)", () => {
  it("maps every state to its palette", () => {
    assert.equal(MODE_LOOKS.listening.palette, "ember");
    assert.equal(MODE_LOOKS.warming.palette, "ember");
    assert.equal(MODE_LOOKS.crumb.palette, "ember");
    assert.equal(MODE_LOOKS.idle.palette, "ember");
    assert.equal(MODE_LOOKS.holding.palette, "lilac");
    assert.equal(MODE_LOOKS.paused.palette, "sage");
    assert.equal(MODE_LOOKS.safety.palette, "cocoa");
  });

  it("listening breathes at 0.2 Hz; warming is livelier; a crumb is brightest with the strongest tip", () => {
    assert.equal(MODE_LOOKS.listening.breathHz, 0.2);
    assert.ok(MODE_LOOKS.warming.speed > MODE_LOOKS.listening.speed);
    assert.ok(MODE_LOOKS.warming.saturation > MODE_LOOKS.listening.saturation);
    assert.ok(MODE_LOOKS.crumb.brightness >= MODE_LOOKS.warming.brightness);
    for (const look of Object.values(MODE_LOOKS)) {
      assert.ok(look.glint > 0.5, "the white tip is never switched off");
      assert.ok(MODE_LOOKS.crumb.glint >= look.glint);
    }
    assert.ok(MODE_LOOKS.idle.speed < MODE_LOOKS.listening.speed);
  });

  it("a palette override changes only the colors", () => {
    const look = resolveLook("holding", "citrus");
    assert.equal(look.palette, "citrus");
    assert.equal(look.breathHz, MODE_LOOKS.holding.breathHz);
    assert.equal(resolveLook("listening").palette, "ember");
  });

  it("every palette has five valid colors and none of them is white (the tip owns white)", () => {
    for (const [name, colors] of Object.entries(PALETTES)) {
      for (const hex of Object.values(colors)) {
        assert.match(hex, /^#[0-9A-F]{6}$/i, `${name} ${hex}`);
        const [r, g, b] = hexToRgb(hex);
        assert.ok(r >= 0 && r <= 1 && g >= 0 && g <= 1 && b >= 0 && b <= 1);
        assert.ok(r + g + b < 2.99, `${name} ${hex} is pure white`);
      }
    }
    assert.deepEqual(hexToRgb("#FFFFFF"), [1, 1, 1]);
    assert.deepEqual(hexToRgb("#000"), [0, 0, 0]);
  });

  it("canvas size is DPR aware and capped", () => {
    assert.equal(backingSize(200, 1), 200);
    assert.equal(backingSize(200, 2), 400);
    assert.equal(backingSize(200, 3), 400); // DPR above 2 is not worth the pixels
    assert.equal(backingSize(400, 2), 560);
    assert.equal(backingSize(0, 2), 0);
    assert.equal(backingSize(100, Number.NaN), 100);
  });

  it("ripple and easing helpers", () => {
    assert.equal(rippleProgress(null, 100), null);
    assert.equal(rippleProgress(1_000, 1_000), 0);
    assert.equal(rippleProgress(1_000, 1_750, 1_500), 0.5);
    assert.equal(rippleProgress(1_000, 2_600, 1_500), null);
    assert.equal(approach(0, 0.5), 0);
    assert.equal(approach(1, 0), 1);
    assert.ok(Math.abs(approach(0.5, 0.5) - (1 - Math.exp(-1))) < 1e-12);
  });
});

describe("Table setups (source)", () => {
  it("a quick table skips the lobby QR codes and the calibration screen", async () => {
    const { readFile } = await import("node:fs/promises");
    const table = await readFile(new URL("../../../pages/Table.tsx", import.meta.url), "utf8");
    const quick = table.slice(table.indexOf("if (quick) {"), table.indexOf("switch (view.session.phase)", table.indexOf("if (quick) {") + 20));
    assert.match(quick, /<QuickConsent onStart=\{actions\.startQuick\} \/>/);
    assert.match(quick, /case "calibrating":\s*return <QuickStarting/);
    assert.match(quick, /case "ended":\s*return <QuickTrail[^>]*onDone=\{actions\.wipe\}/);
    assert.ok(!quick.includes("<Calibration") && !quick.includes("<Lobby"));
  });

  it("the quick consent card says exactly what happens", async () => {
    const { readFile } = await import("node:fs/promises");
    const src = await readFile(new URL("../QuickSetup.tsx", import.meta.url), "utf8");
    assert.match(src, /Breadcrumbs listens and transcribes your conversation live\. Nothing is stored\./);
    assert.match(src, /We're both in/);
    assert.match(src, /\(optional\)/);
  });
});
