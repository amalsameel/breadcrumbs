// Unit tests for the Table's pure helpers. Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test src/components/table/__tests__/
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  deriveOrb,
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
