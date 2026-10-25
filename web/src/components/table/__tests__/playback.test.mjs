// Unit tests for crumb playback sequencing (chime inside the bot interval, VAD grace, End/Skip during
// the chime). Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test 'src/components/table/__tests__/*.test.mjs'
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  BARGE_IN_GRACE_MS,
  ECHO_HOLD_AFTER_MS,
  ECHO_MARGIN_DB,
  PERSON_SUSTAIN_MS,
  PlaybackGate,
  speakLine,
} from "../playback.ts";

/** A player whose chime and voice are driven by the test. */
function fakePlayer() {
  const log = [];
  let releaseChime;
  let current = null;
  const player = {
    log,
    get playing() {
      return current !== null;
    },
    playChime() {
      log.push("chime");
      return new Promise((r) => (releaseChime = r));
    },
    play(id, _b64, _text, cb) {
      log.push(`play:${id}`);
      return new Promise((resolve) => {
        current = { id, cb, resolve };
      });
    },
    fadeOut(ms) {
      log.push(`fade:${ms}`);
    },
    endChime() {
      releaseChime();
    },
    startVoice() {
      current.cb.onStart();
    },
    endVoice(interrupted = false) {
      const c = current;
      current = null;
      c.cb.onEnd(interrupted);
      c.resolve();
    },
  };
  return player;
}

function harness() {
  const player = fakePlayer();
  const gate = new PlaybackGate();
  const sent = [];
  const lines = [];
  const cancelled = new Set();
  let clock = 0;
  const deps = {
    player,
    gate,
    send: (e) => sent.push(e),
    started: (id, at) => lines.push(["started", id, at]),
    ended: (id, at, interrupted) => lines.push(["ended", id, interrupted]),
    cancelled: (id) => cancelled.has(id) || cancelled.has("*"),
    now: () => clock,
  };
  return {
    player,
    gate,
    sent,
    lines,
    cancelled,
    deps,
    set now(v) {
      clock = v;
    },
    get now() {
      return clock;
    },
  };
}

const tick = () => new Promise((r) => setImmediate(r));

describe("chime is part of playback (PRD 8.11.3)", () => {
  it("tts_state playing=true goes out when the chime starts, not after it", async () => {
    const h = harness();
    const done = speakLine(h.deps, "c_1", "", "Maya, what made you sign up?", true);
    await tick();
    assert.deepEqual(h.player.log, ["chime"]);
    assert.deepEqual(h.sent, [{ type: "tts_state", crumb_id: "c_1", playing: true }]);
    assert.equal(h.gate.currentId, "c_1");

    h.player.endChime();
    await tick();
    h.now = 740;
    h.player.startVoice();
    assert.equal(h.sent.length, 1, "no second playing=true when the voice starts");
    h.player.endVoice();
    await done;
    assert.deepEqual(h.sent.at(-1), { type: "tts_state", crumb_id: "c_1", playing: false });
    assert.equal(h.gate.currentId, null);
  });

  it("system lines have no chime: tts_state opens when the voice starts", async () => {
    const h = harness();
    const done = speakLine(h.deps, "sys_cal1", "", "Seat 1, say hi.", false);
    await tick();
    assert.deepEqual(h.sent, []);
    h.player.startVoice();
    assert.deepEqual(h.sent, [{ type: "tts_state", crumb_id: "sys_cal1", playing: true }]);
    h.player.endVoice();
    await done;
  });
});

const on = (levelDb, voiced = true) => ({ active: true, levelDb, voiced });
const off = (levelDb = -70) => ({ active: false, levelDb, voiced: false });

/** Deterministic +/- jitter so the echo looks like speech rather than a constant. */
function jitter(i, db) {
  return db * Math.sin(i * 1.7) * Math.cos(i * 0.37);
}

/** A crumb line: chime from t0 for chimeMs, voice from then on. Returns the voice start time. */
function startLine(g, id, t0, chimeMs = 600) {
  g.claim(id);
  g.begin(id);
  if (chimeMs === 0) {
    g.voiceStarted(id, t0);
    return t0;
  }
  return t0 + chimeMs;
}

describe("VAD gate: chime grace", () => {
  it("a VAD onset during the chime and the first 300 ms is held back and does not fade", () => {
    const g = new PlaybackGate();
    startLine(g, "c_1", 0);
    assert.deepEqual(g.observe(on(-30), 200), { send: false, fade: false }, "chime echo never reaches the engine");
    assert.deepEqual(g.observe(on(-30), 600), { send: false, fade: false });
    g.voiceStarted("c_1", 740);
    assert.deepEqual(g.observe(on(-30), 740 + BARGE_IN_GRACE_MS - 10), { send: false, fade: false }, "first 300 ms");
  });

  it("releases always pass, and speech already under way is not hidden", () => {
    const g = new PlaybackGate();
    assert.equal(g.observe(on(-30), 0).send, true); // someone talking before the crumb
    g.begin("c_1");
    assert.equal(g.observe(on(-30), 100).send, true, "pre-existing speech keeps flowing (overtaken crumb)");
    assert.equal(g.observe(off(), 200).send, true);
    assert.equal(g.observe(on(-30), 300).send, false, "a fresh onset inside the chime is held");
    assert.equal(g.observe(off(), 400).send, true);
  });

  it("with nothing playing every reading passes and nothing fades", () => {
    const g = new PlaybackGate();
    assert.deepEqual(g.observe(on(-30), 0), { send: true, fade: false });
    assert.deepEqual(g.observe(off(), 100), { send: true, fade: false });
  });
});

describe("VAD gate: our own echo never stops the line (round 2 cutoff fix)", () => {
  for (const echoDb of [-55, -45, -35, -25, -15]) {
    it(`steady echo at ${echoDb} dBFS through chime, voice and tail: never sent, never fades`, () => {
      const g = new PlaybackGate();
      const voiceAt = startLine(g, "c_1", 0);
      let t = 0;
      let i = 0;
      // Chime: a little quieter than the voice.
      for (; t < voiceAt; t += 100, i++) {
        const v = g.observe(on(echoDb - 4 + jitter(i, 2)), t);
        assert.deepEqual(v, { send: false, fade: false }, `chime @${t}`);
      }
      g.voiceStarted("c_1", voiceAt);
      // Voice: 6 s of speech-like echo (syllables up to +/- 4 dB, voiced, VAD on the whole time).
      for (; t < voiceAt + 6000; t += 100, i++) {
        const v = g.observe(on(echoDb + jitter(i, 4)), t);
        assert.deepEqual(v, { send: false, fade: false }, `voice @${t}`);
      }
      g.end("c_1", t);
      // Tail: the VAD's own release keeps it active a little after the audio stopped.
      for (const dt of [100, 200, 300, 400, 500]) {
        assert.equal(g.observe(on(echoDb - 10), t + dt).send, false, `tail +${dt}`);
      }
      assert.equal(g.observe(off(), t + 600).send, true);
    });
  }

  it("a second line uses what the first one taught (warm margin)", () => {
    const g = new PlaybackGate();
    let t = startLine(g, "c_1", 0);
    g.voiceStarted("c_1", t);
    for (let i = 0; i < 30; i++, t += 100) g.observe(on(-30 + jitter(i, 3)), t);
    g.end("c_1", t);
    g.observe(off(), t + 1000);
    assert.ok(g.echo.frames >= 20);
    // Line 2, no chime (system line): the very first loud syllables of its echo do not fade it.
    const v0 = startLine(g, "sys_cal2", t + 5000, 0);
    for (let k = 0; k < 20; k++) {
      assert.deepEqual(g.observe(on(-30 + jitter(k, 4)), v0 + k * 100), { send: false, fade: false });
    }
  });

  it("cold start: the first line's louder syllables do not fade it", () => {
    const g = new PlaybackGate();
    const v0 = startLine(g, "sys_cal1", 0, 0); // no chime, nothing learned yet
    const levels = [-40, -38, -37, -33, -32, -31, -34, -30, -36, -32];
    levels.forEach((db, k) => assert.deepEqual(g.observe(on(db), v0 + k * 100), { send: false, fade: false }, `@${k}`));
  });
});

describe("VAD gate: real people still interrupt (PRD 6.7, F12)", () => {
  for (const echoDb of [-50, -35, -20]) {
    it(`a voice ${ECHO_MARGIN_DB + 6} dB over a ${echoDb} dBFS echo fades the line within 250 ms`, () => {
      const g = new PlaybackGate();
      let t = startLine(g, "c_1", 0);
      g.voiceStarted("c_1", t);
      for (let i = 0; i < 20; i++, t += 100) assert.equal(g.observe(on(echoDb + jitter(i, 3)), t).fade, false);
      const onset = t;
      let fadedAt = null;
      let sentAt = null;
      for (let i = 0; i < 6; i++, t += 100) {
        const v = g.observe(on(echoDb + ECHO_MARGIN_DB + 6 + jitter(i, 2)), t);
        if (v.fade && fadedAt === null) fadedAt = t;
        if (v.send && sentAt === null) sentAt = t;
      }
      assert.ok(fadedAt !== null, "faded");
      assert.ok(fadedAt - onset <= 250, `fade ${fadedAt - onset} ms after the voice started`);
      assert.ok(fadedAt - onset >= PERSON_SUSTAIN_MS - 100, "not on a single loud frame");
      assert.equal(sentAt, fadedAt, "the server hears the person at the same moment (barge-in)");
    });
  }

  it("a single loud frame (a laugh, a bump) is not a person", () => {
    const g = new PlaybackGate();
    let t = startLine(g, "c_1", 0);
    g.voiceStarted("c_1", t);
    for (let i = 0; i < 20; i++, t += 100) g.observe(on(-35 + jitter(i, 3)), t);
    assert.equal(g.observe(on(-15), t).fade, false);
    assert.equal(g.observe(on(-35), t + 100).fade, false);
    assert.equal(g.observe(on(-35), t + 200).fade, false);
  });

  it("loud but not voiced (a click, a knock) never fades", () => {
    const g = new PlaybackGate();
    let t = startLine(g, "c_1", 0);
    g.voiceStarted("c_1", t);
    for (let i = 0; i < 20; i++, t += 100) g.observe(on(-35 + jitter(i, 3)), t);
    for (let i = 0; i < 5; i++, t += 100) assert.deepEqual(g.observe(on(-10, false), t), { send: false, fade: false });
  });

  it("a much louder line than the last does not cut itself off (volume up, other voice path)", () => {
    const g = new PlaybackGate();
    let t = startLine(g, "c_1", 0);
    g.voiceStarted("c_1", t);
    for (let i = 0; i < 40; i++, t += 100) g.observe(on(-45 + jitter(i, 3)), t);
    g.end("c_1", t);
    g.observe(off(), t + 100);
    t = startLine(g, "c_2", t + 4000);
    g.voiceStarted("c_2", t);
    for (let i = 0; i < 60; i++, t += 100) {
      assert.deepEqual(g.observe(on(-30 + jitter(i, 4)), t), { send: false, fade: false }, `@${i}`);
    }
  });

  it("frames of a person are not learned as echo", () => {
    const g = new PlaybackGate();
    let t = startLine(g, "c_1", 0);
    g.voiceStarted("c_1", t);
    for (let i = 0; i < 30; i++, t += 100) g.observe(on(-40 + jitter(i, 3)), t);
    const before = g.echo.level;
    for (let i = 0; i < 10; i++, t += 100) g.observe(on(-20), t);
    assert.equal(g.echo.level, before);
  });

  it("someone talking at echo level who keeps going after the line is passed on", () => {
    const g = new PlaybackGate();
    let t = startLine(g, "c_1", 0);
    g.voiceStarted("c_1", t);
    for (let i = 0; i < 20; i++, t += 100) g.observe(on(-30 + jitter(i, 3)), t);
    g.end("c_1", t);
    const end = t;
    let passedAt = null;
    for (t = end + 100; t <= end + 1500; t += 100) {
      const v = g.observe(on(-31), t);
      assert.equal(v.fade, false);
      if (v.send && passedAt === null) passedAt = t;
    }
    assert.ok(passedAt !== null && passedAt - end >= ECHO_HOLD_AFTER_MS && passedAt - end <= ECHO_HOLD_AFTER_MS + 100);
  });

  it("an onset after the echo tail is a person straight away", () => {
    const g = new PlaybackGate();
    let t = startLine(g, "c_1", 0);
    g.voiceStarted("c_1", t);
    for (let i = 0; i < 10; i++, t += 100) g.observe(on(-30), t);
    g.end("c_1", t);
    g.observe(off(), t + 100);
    assert.equal(g.observe(on(-40), t + 600).send, true);
  });
});

/** The Vad's raw reading while it is still in its 200 ms attack: voiced and loud, not active yet. */
const rising = (levelDb) => ({ active: false, levelDb, voiced: true });

describe("VAD gate: the Vad's attack counts towards a person (review fix, PRD F12)", () => {
  /** A line whose echo (VAD idle, good echo cancellation) sits at echoDb; returns the time after it. */
  function quietEchoLine(g, echoDb) {
    let t = startLine(g, "c_1", 0);
    for (let k = 0; t < 600; t += 100, k++) g.observe(off(echoDb - 2 + jitter(k, 1)), t);
    g.voiceStarted("c_1", t);
    for (let i = 0; i < 40; i++, t += 100) g.observe(off(echoDb + jitter(i, 2)), t);
    return t;
  }

  for (const attackFrames of [1, 2]) {
    it(`a person over a quiet echo fades the line one frame after the VAD turns on (${attackFrames} attack frame(s))`, () => {
      const g = new PlaybackGate();
      let t = quietEchoLine(g, -48);
      const onset = t;
      let fadedAt = null;
      for (let i = 0; i < 8 && fadedAt === null; i++, t += 100) {
        const sample = i < attackFrames ? rising(-30 + jitter(i, 1)) : on(-30 + jitter(i, 1));
        const v = g.observe(sample, t);
        if (v.fade) fadedAt = t;
        if (i < attackFrames) assert.equal(v.fade, false, "never before the VAD is active");
      }
      assert.ok(fadedAt !== null, "faded");
      assert.equal(fadedAt - onset, attackFrames * 100, `fade ${fadedAt - onset} ms after the voice started`);
    });
  }

  it("the attack frames of a person are not learned as echo", () => {
    const g = new PlaybackGate();
    let t = quietEchoLine(g, -48);
    const before = g.echo.level;
    g.observe(rising(-28), t);
    g.observe(rising(-27), t + 100);
    for (let i = 2; i < 6; i++) g.observe(on(-28), t + i * 100);
    assert.equal(g.echo.level, before);
  });

  it("loud attack frames followed by an echo-level stretch do not fade (an echo swell)", () => {
    const g = new PlaybackGate();
    let t = startLine(g, "c_1", 0);
    g.voiceStarted("c_1", t);
    for (let i = 0; i < 30; i++, t += 100) g.observe(on(-35 + jitter(i, 3)), t);
    g.observe(off(-60), t);
    t += 100;
    g.observe(rising(-20), t);
    g.observe(rising(-20), t + 100);
    for (let i = 2; i < 10; i++) {
      assert.deepEqual(g.observe(on(-35 + jitter(i, 3)), t + i * 100), { send: false, fade: false }, `@${i}`);
    }
  });

  it("a loud voiced run the VAD never takes up is learned as echo, not withheld forever", () => {
    const g = new PlaybackGate();
    let t = quietEchoLine(g, -48);
    const frames = g.echo.frames;
    for (let i = 0; i < 12; i++, t += 100) assert.deepEqual(g.observe(rising(-30), t), { send: true, fade: false });
    assert.ok(g.echo.frames >= Math.min(80, frames + 10), "all but the newest attack window were learned");
    g.observe(off(-48), t);
    assert.ok(g.echo.level !== null && g.echo.level > -40, "the swell raised the echo estimate");
  });
});

describe("cancelled during the chime", () => {
  it("End during the chime: the crumb never starts and the interval closes", async () => {
    const h = harness();
    const done = speakLine(h.deps, "c_1", "", "text", true);
    await tick();
    h.cancelled.add("*"); // the session ended (stoppedRef)
    h.player.endChime();
    await done;
    assert.ok(!h.player.log.includes("play:c_1"), "nothing plays after End");
    assert.deepEqual(h.sent, [
      { type: "tts_state", crumb_id: "c_1", playing: true },
      { type: "tts_state", crumb_id: "c_1", playing: false },
    ]);
    assert.deepEqual(h.lines, [["ended", "c_1", true]]);
  });

  it("Skip / crumb_interrupted during the chime: the skipped crumb does not start", async () => {
    const h = harness();
    const done = speakLine(h.deps, "c_1", "", "text", true);
    await tick();
    h.cancelled.add(h.gate.currentId); // what the Skip action does with the line in its chime
    h.player.endChime();
    await done;
    assert.ok(!h.player.log.includes("play:c_1"));
  });

  it("a newer crumb arriving during the chime supersedes the older one", async () => {
    const h = harness();
    const first = speakLine(h.deps, "c_1", "", "one", true);
    await tick();
    const second = speakLine(h.deps, "c_2", "", "two", false);
    await tick();
    h.player.endChime();
    await first;
    assert.ok(!h.player.log.includes("play:c_1"));
    assert.ok(h.player.log.includes("play:c_2"));
    h.player.startVoice();
    h.player.endVoice();
    await second;
  });

  it("interrupted while the audio decodes: faded the moment it starts", async () => {
    const h = harness();
    const done = speakLine(h.deps, "c_1", "", "text", true);
    await tick();
    h.player.endChime();
    await tick();
    h.cancelled.add("c_1");
    h.player.startVoice();
    assert.ok(h.player.log.includes("fade:250"));
    h.player.endVoice(true);
    await done;
  });
});
