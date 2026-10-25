// The Table's VAD frame for the server (src/lib/vadMessage.ts). Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test 'src/components/table/__tests__/*.test.mjs'
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { vadMessage } from "../../../lib/vadMessage.ts";

describe("vadMessage", () => {
  it("has the old shape without a reading", () => {
    assert.deepEqual(vadMessage(true, 0.05), { type: "vad", active: true, rms: 0.05 });
  });

  it("adds level_db (0.1 dB) and quiet_ms (integer ms) from the reading", () => {
    const msg = vadMessage(false, 0.002, { levelDb: -31.26, quietMs: 512.6 });
    assert.deepEqual(msg, { type: "vad", active: false, rms: 0.002, level_db: -31.3, quiet_ms: 513 });
  });

  it("caps quiet_ms at 60 s and never goes below 0", () => {
    assert.equal(vadMessage(false, 0, { levelDb: -60, quietMs: 1e9 }).quiet_ms, 60000);
    assert.equal(vadMessage(false, 0, { levelDb: -60, quietMs: -5 }).quiet_ms, 0);
  });

  it("leaves out non-finite fields and adds no other keys", () => {
    const msg = vadMessage(true, 0.1, { levelDb: -Infinity, quietMs: Number.NaN });
    assert.deepEqual(Object.keys(msg).sort(), ["active", "rms", "type"]);
    const full = vadMessage(true, 0.1, { levelDb: -20, quietMs: 0, voiced: true, snrDb: 12 });
    assert.deepEqual(Object.keys(full).sort(), ["active", "level_db", "quiet_ms", "rms", "type"]);
  });
});
