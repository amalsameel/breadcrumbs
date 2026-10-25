// Unit tests for mic-loss detection on the Table capture (a lost mic must be reported so the live
// screen can offer Start listening again). Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test 'src/components/table/__tests__/*.test.mjs'
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { watchCaptureLoss } from "../../../audio/capture.ts";

class FakeCtx extends EventTarget {
  state = "running";
  set(state) {
    this.state = state;
    this.dispatchEvent(new Event("statechange"));
  }
}

describe("watchCaptureLoss", () => {
  it("reports a mic track that ends on its own, once", () => {
    const track = new EventTarget();
    const ctx = new FakeCtx();
    let lost = 0;
    watchCaptureLoss([track], ctx, () => lost++);
    track.dispatchEvent(new Event("ended"));
    track.dispatchEvent(new Event("ended"));
    ctx.set("closed");
    assert.equal(lost, 1);
  });

  it("reports the capture context closing, but not a suspend", () => {
    const ctx = new FakeCtx();
    let lost = 0;
    watchCaptureLoss([], ctx, () => lost++);
    ctx.set("suspended");
    assert.equal(lost, 0);
    ctx.set("closed");
    assert.equal(lost, 1);
  });

  it("a deliberate stop unsubscribes first", () => {
    const track = new EventTarget();
    const ctx = new FakeCtx();
    let lost = 0;
    const off = watchCaptureLoss([track], ctx, () => lost++);
    off();
    track.dispatchEvent(new Event("ended"));
    ctx.set("closed");
    assert.equal(lost, 0);
  });
});
