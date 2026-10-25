// Unit tests for crumb playback sequencing (chime inside the bot interval, VAD grace, End/Skip during
// the chime). Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test 'src/components/table/__tests__/*.test.mjs'
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { BARGE_IN_GRACE_MS, PlaybackGate, speakLine } from "../playback.ts";

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

describe("VAD gate", () => {
  it("a VAD onset during the chime is held back from the server and does not fade", () => {
    const g = new PlaybackGate();
    g.claim("c_1");
    g.begin("c_1");
    assert.equal(g.forward(true, 200), false, "chime echo must not reach the engine as human speech");
    assert.equal(g.shouldFade(true, 200), false);
    assert.equal(g.forward(true, 600), false);
    g.voiceStarted("c_1", 740);
    assert.equal(g.forward(true, 740 + BARGE_IN_GRACE_MS - 10), false, "first 300 ms of the voice");
    assert.equal(g.shouldFade(true, 740 + BARGE_IN_GRACE_MS - 10), false);
    // Still talking after the grace: a real barge-in reaches the server and fades locally.
    assert.equal(g.forward(true, 740 + BARGE_IN_GRACE_MS + 10), true);
    assert.equal(g.shouldFade(true, 740 + BARGE_IN_GRACE_MS + 10), true);
  });

  it("releases always pass, and speech already under way is not hidden", () => {
    const g = new PlaybackGate();
    assert.equal(g.forward(true, 0), true); // someone talking before the crumb
    g.begin("c_1");
    assert.equal(g.forward(true, 100), true, "pre-existing speech keeps flowing (overtaken crumb)");
    assert.equal(g.forward(false, 200), true);
    assert.equal(g.forward(true, 300), false, "a fresh onset inside the chime is held");
    assert.equal(g.forward(false, 400), true);
  });

  it("with nothing playing every reading passes", () => {
    const g = new PlaybackGate();
    assert.equal(g.forward(true, 0), true);
    assert.equal(g.shouldFade(true, 0), false);
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
