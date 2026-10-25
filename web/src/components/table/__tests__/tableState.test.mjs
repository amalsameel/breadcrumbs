// Unit tests for the Table state machine. Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test src/components/table/__tests__/
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { initialState, isSpeaking, tableReducer, visibleCrumb } from "../tableState.ts";

const server = (state, event, at = 1000) => tableReducer(state, { type: "server", event, at });

const crumb = (id, text = "Maya, what made you sign up for the half marathon?") => ({
  id,
  kind: "steer",
  format: "question",
  text,
  addressed_to: "seat1",
  source: "profile_seat1",
  source_item_ids: ["s1_i1"],
  topic: "running",
  depth: 1,
  why: "Long pause on a lively topic",
  citation_url: null,
  citation_title: null,
});

const crumbEvent = (id) => ({ type: "crumb", crumb: crumb(id), audio_b64: "", cache_hit: true, latency_ms: 420 });

const turn = (turn_id, text, extra = {}) => ({
  turn_id,
  seat: 1,
  label: "A",
  text,
  words: text.split(" ").length,
  dropped_as_bot: false,
  is_question: false,
  is_backchannel: false,
  hold: null,
  ...extra,
});

const session = (phase = "lobby") => ({
  session_id: "abc",
  phase,
  mode: "social",
  quick: true,
  seats: {
    1: { joined: true, consented: true, ready: false, name: "Maya", has_profile: false, answered: 0, opted_in: null },
    2: { joined: false, consented: false, ready: false, name: null, has_profile: false, answered: 0, opted_in: null },
  },
  created_at: 0,
  expires_in_s: 7200,
  dev_tools: true,
  mock: { muse: true, gemini: true },
});

describe("loading", () => {
  it("a session event makes the table ready", () => {
    const s = server(initialState, { type: "session", session: session("profiles") });
    assert.equal(s.load, "ready");
    assert.equal(s.session.phase, "profiles");
  });

  it("a 404 shows the friendly unknown-session screen", () => {
    const s = tableReducer(initialState, { type: "load_failed", notFound: true, message: "Not found" });
    assert.equal(s.load, "not_found");
  });

  it("a network error does not hide a session the socket already delivered", () => {
    const withSession = server(initialState, { type: "session", session: session() });
    const s = tableReducer(withSession, { type: "load_failed", notFound: false, message: "502" });
    assert.equal(s.load, "ready");
    assert.equal(tableReducer(initialState, { type: "load_failed", notFound: false, message: "502" }).load, "error");
  });

  it("a late REST summary never overwrites a fresher socket summary", () => {
    const live = server(initialState, { type: "session", session: session("live") });
    const s = tableReducer(live, { type: "loaded", session: session("lobby") });
    assert.equal(s.session.phase, "live");
  });
});

describe("crumb playback lifecycle", () => {
  it("records a crumb as pending with an estimated duration", () => {
    const s = server(initialState, crumbEvent("c_1"), 500);
    assert.equal(s.crumbs.length, 1);
    const [c] = s.crumbs;
    assert.equal(c.status, "pending");
    assert.equal(c.receivedAt, 500);
    assert.equal(c.cacheHit, true);
    assert.equal(c.latencyMs, 420);
    assert.ok(c.durationS >= 1.5 && c.durationS <= 16);
  });

  it("moves pending -> playing -> done", () => {
    let s = server(initialState, crumbEvent("c_1"));
    s = tableReducer(s, { type: "line_started", id: "c_1", at: 1200 });
    assert.equal(s.crumbs[0].status, "playing");
    assert.equal(s.crumbs[0].startedAt, 1200);
    s = tableReducer(s, { type: "line_ended", id: "c_1", at: 4000, interrupted: false });
    assert.equal(s.crumbs[0].status, "done");
    assert.equal(s.crumbs[0].endedAt, 4000);
  });

  it("marks barge-in as interrupted, but never rewrites a finished crumb", () => {
    let s = server(initialState, crumbEvent("c_1"));
    s = server(s, crumbEvent("c_2"));
    s = tableReducer(s, { type: "line_ended", id: "c_1", at: 2000, interrupted: false });
    s = server(s, { type: "crumb_interrupted", crumb_id: "c_1" }, 2500);
    s = server(s, { type: "crumb_interrupted", crumb_id: "c_2" }, 2600);
    assert.equal(s.crumbs[0].status, "done");
    assert.equal(s.crumbs[1].status, "interrupted");
    assert.equal(s.crumbs[1].endedAt, 2600);
  });

  it("de-duplicates a re-sent crumb id", () => {
    const s = server(server(initialState, crumbEvent("c_1")), crumbEvent("c_1"));
    assert.equal(s.crumbs.length, 1);
  });

  it("tracks system lines (calibration prompts) separately from crumbs", () => {
    let s = server(initialState, { type: "system_say", id: "sys_1", text: "Seat 1, say hi.", audio_b64: "" });
    s = tableReducer(s, { type: "line_started", id: "sys_1", at: 10 });
    assert.equal(s.systemLine.status, "playing");
    assert.equal(s.crumbs.length, 0);
  });
});

describe("caption selection", () => {
  const record = (status, extra = {}) => ({
    id: "c_1",
    text: "x",
    durationS: 3,
    status,
    receivedAt: 0,
    startedAt: null,
    endedAt: null,
    crumb: crumb("c_1"),
    cacheHit: false,
    latencyMs: 0,
    ...extra,
  });

  it("shows the crumb while it is spoken and lingers after", () => {
    assert.equal(visibleCrumb([], 0), null);
    assert.ok(visibleCrumb([record("playing")], 60_000));
    assert.ok(visibleCrumb([record("done", { endedAt: 10_000 })], 20_000));
    assert.equal(visibleCrumb([record("done", { endedAt: 10_000 })], 30_000), null);
  });

  it("clears interrupted crumbs sooner", () => {
    assert.ok(visibleCrumb([record("interrupted", { endedAt: 10_000 })], 12_000));
    assert.equal(visibleCrumb([record("interrupted", { endedAt: 10_000 })], 15_000), null);
  });

  it("stops treating a crumb that never started as speaking", () => {
    assert.equal(isSpeaking(record("pending"), 1000), true);
    assert.equal(isSpeaking(record("pending"), 9000), false);
    assert.equal(isSpeaking(record("done"), 0), false);
  });
});

describe("engine, transcript and log", () => {
  const snapshot = (silence, staleness) => ({ silence_s: silence, staleness, label_map: { A: 1 } });

  it("keeps 60 s of history for the sparklines", () => {
    let s = initialState;
    for (let t = 0; t <= 80_000; t += 250) s = server(s, { type: "engine", engine: snapshot(t / 1000, 0.1) }, t);
    assert.ok(s.history[0].at >= 20_000);
    assert.equal(s.history.at(-1).at, 80_000);
    assert.deepEqual(s.labelMap, { A: 1 });
  });

  it("upserts turns by turn_id and clears the partial caption", () => {
    let s = server(initialState, { type: "partial", text: "so I was", label: "A" });
    assert.equal(s.partial.text, "so I was");
    s = server(s, { type: "turn", turn: turn(1, "so I was running") });
    s = server(s, { type: "turn", turn: turn(2, "nice") });
    s = server(s, { type: "turn", turn: turn(1, "so I was running a lot") });
    assert.equal(s.partial, null);
    assert.deepEqual(
      s.turns.map((t) => t.turn_id),
      [1, 2],
    );
    assert.equal(s.turns[0].text, "so I was running a lot");
  });

  it("keeps the decision log newest first and bounded", () => {
    let s = initialState;
    for (let i = 0; i < 150; i++) {
      s = server(s, { type: "decision", decision: { at_s: i, action: "steer", reason: `r${i}` } });
    }
    assert.equal(s.decisions.length, 120);
    assert.equal(s.decisions[0].at_s, 149);
  });

  it("merges local tuning changes over server params", () => {
    let s = server(initialState, { type: "params", params: { T_LONG_S: 5, T_EXC_S: 6.5 } });
    s = tableReducer(s, { type: "params_local", changes: { T_LONG_S: 4.5 } });
    assert.deepEqual(s.params, { T_LONG_S: 4.5, T_EXC_S: 6.5 });
  });

  it("stores calibration steps and muse status", () => {
    let s = server(initialState, { type: "calibration", step: "seat2", label_map: { B: 1 } });
    s = server(s, { type: "muse_status", status: "resyncing" });
    assert.equal(s.calibrationStep, "seat2");
    assert.deepEqual(s.labelMap, { B: 1 });
    assert.equal(s.muse.status, "resyncing");
  });

  it("server errors are transient", () => {
    let s = server(initialState, { type: "error", message: "Gemini timed out" });
    assert.equal(s.serverError, "Gemini timed out");
    s = tableReducer(s, { type: "clear_error" });
    assert.equal(s.serverError, null);
  });
});

describe("wipe", () => {
  it("drops every piece of conversation content but keeps app config", () => {
    let s = server(initialState, { type: "session", session: session("ended") });
    s = tableReducer(s, { type: "config", config: { public_base_url: "https://x", dev_tools: true, ffmpeg: true, mock: { muse: true, gemini: true } } });
    s = server(s, crumbEvent("c_1"));
    s = server(s, { type: "turn", turn: turn(1, "private words") });
    s = server(s, { type: "partial", text: "more private words", label: "A" });
    s = server(s, { type: "decision", decision: { at_s: 1, action: "steer", reason: "x" } });
    s = server(s, { type: "wiped" });
    assert.equal(s.wiped, true);
    assert.equal(s.session, null);
    assert.deepEqual(s.turns, []);
    assert.deepEqual(s.crumbs, []);
    assert.deepEqual(s.decisions, []);
    assert.equal(s.partial, null);
    assert.equal(s.config.public_base_url, "https://x");
    assert.equal(JSON.stringify(s).includes("private"), false);
  });

  it("the local Wipe now action does the same", () => {
    const s = tableReducer(server(initialState, crumbEvent("c_1")), { type: "wiped" });
    assert.equal(s.wiped, true);
    assert.deepEqual(s.crumbs, []);
  });
});
