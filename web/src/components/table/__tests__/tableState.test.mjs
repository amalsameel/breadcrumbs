// Unit tests for the Table state machine. Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test src/components/table/__tests__/
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { initialState, isQuick, isSpeaking, quickStage, recapCrumb, tableReducer, visibleCrumb } from "../tableState.ts";

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

  it("shows the crumb in full the moment it arrives, while it plays, and for a reading time after", () => {
    assert.equal(visibleCrumb([], 0), null);
    // no waiting for the chime or the first word: the whole line is up on arrival
    assert.ok(visibleCrumb([record("pending")], 0));
    assert.ok(visibleCrumb([record("playing", { startedAt: 500 })], 60_000));
    // one word: 4 s of reading time after the voice ends
    const done = [record("done", { startedAt: 500, endedAt: 10_000 })];
    assert.ok(visibleCrumb(done, 13_900));
    assert.equal(visibleCrumb(done, 14_100), null);
  });

  it("then shrinks into a small last-crumb line, then leaves the orb alone", () => {
    const done = [record("done", { startedAt: 500, endedAt: 10_000 })];
    assert.equal(recapCrumb(done, 13_900), null);
    assert.ok(recapCrumb(done, 14_100));
    assert.ok(recapCrumb(done, 43_900));
    assert.equal(recapCrumb(done, 44_100), null);
    assert.equal(visibleCrumb(done, 44_100), null);
  });

  it("keeps an interrupted crumb readable for its full reading time", () => {
    const ten = "one two three four five six seven eight nine ten";
    const cut = [record("interrupted", { text: ten, startedAt: 500, endedAt: 1_000 })];
    // 10 words: 0.35 s per word + 2 s = 5.5 s after the cut, never yanked away at once
    assert.ok(visibleCrumb(cut, 6_400));
    assert.equal(visibleCrumb(cut, 6_600), null);
  });

  it("never shows a caption for less than 6 s in total", () => {
    const quick = [record("done", { startedAt: 100, endedAt: 600 })];
    assert.ok(visibleCrumb(quick, 5_900));
    assert.equal(visibleCrumb(quick, 6_100), null);
  });

  it("a crumb whose audio never starts still gets its reading time", () => {
    const stuck = [record("pending")];
    assert.ok(visibleCrumb(stuck, 7_900)); // still waiting for playback
    // 3 s of expected speech + 4 s reading = 7 s, but the 8 s wait for playback wins
    assert.ok(visibleCrumb(stuck, 6_500));
    assert.equal(visibleCrumb(stuck, 8_100), null);
    assert.ok(recapCrumb(stuck, 8_100));
  });

  it("Skip removes the crumb at once, with no lingering caption and no recap", () => {
    let s = server(initialState, crumbEvent("c_1"), 0);
    s = tableReducer(s, { type: "line_started", id: "c_1", at: 500 });
    s = tableReducer(s, { type: "line_skipped", id: "c_1", at: 900 });
    assert.equal(s.crumbs[0].status, "interrupted");
    assert.equal(s.crumbs[0].endedAt, 900);
    assert.equal(s.crumbs[0].skippedAt, 900);
    assert.equal(isSpeaking(s.crumbs[0], 900), false);
    assert.ok(visibleCrumb(s.crumbs, 899));
    assert.equal(visibleCrumb(s.crumbs, 900), null);
    assert.equal(recapCrumb(s.crumbs, 900), null);
    assert.equal(recapCrumb(s.crumbs, 20_000), null);
    // playback then reports the faded line as interrupted: it stays skipped
    s = tableReducer(s, { type: "line_ended", id: "c_1", at: 1_100, interrupted: true });
    assert.equal(s.crumbs[0].skippedAt, 900);
    assert.equal(visibleCrumb(s.crumbs, 1_100), null);
    assert.equal(recapCrumb(s.crumbs, 1_100), null);
  });

  it("Skip on a crumb still in its chime, or on one that already finished", () => {
    let s = server(initialState, crumbEvent("c_1"), 0);
    s = tableReducer(s, { type: "line_skipped", id: "c_1", at: 300 });
    assert.equal(s.crumbs[0].status, "interrupted");
    assert.equal(visibleCrumb(s.crumbs, 300), null);

    let d = server(initialState, crumbEvent("c_2"), 0);
    d = tableReducer(d, { type: "line_started", id: "c_2", at: 200 });
    d = tableReducer(d, { type: "line_ended", id: "c_2", at: 3_000, interrupted: false });
    assert.ok(visibleCrumb(d.crumbs, 4_000)); // lingering for its reading time
    d = tableReducer(d, { type: "line_skipped", id: "c_2", at: 4_000 });
    assert.equal(d.crumbs[0].status, "done"); // a finished crumb is never rewritten
    assert.equal(d.crumbs[0].endedAt, 3_000);
    assert.equal(visibleCrumb(d.crumbs, 4_000), null);
    assert.equal(recapCrumb(d.crumbs, 4_000), null);
  });

  it("a barge-in or server interruption is not a skip and keeps its reading time", () => {
    let s = server(initialState, crumbEvent("c_1"), 0);
    s = tableReducer(s, { type: "line_started", id: "c_1", at: 500 });
    s = server(s, { type: "crumb_interrupted", crumb_id: "c_1" }, 900);
    assert.equal(s.crumbs[0].skippedAt, null);
    assert.ok(visibleCrumb(s.crumbs, 4_000));
    // skipping an unknown id or a second time changes nothing
    const again = tableReducer(tableReducer(s, { type: "line_skipped", id: "c_1", at: 5_000 }), {
      type: "line_skipped",
      id: "c_1",
      at: 6_000,
    });
    assert.equal(again.crumbs[0].skippedAt, 5_000);
    assert.equal(tableReducer(s, { type: "line_skipped", id: "nope", at: 5_000 }).crumbs[0].skippedAt, null);
  });

  it("Skip also clears a spoken calibration prompt", () => {
    let s = server(initialState, { type: "system_say", id: "sys_1", text: "Say hi", audio_b64: "" }, 0);
    assert.equal(s.systemLine.skippedAt, null);
    s = tableReducer(s, { type: "line_skipped", id: "sys_1", at: 400 });
    assert.equal(s.systemLine.skippedAt, 400);
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

// ---- Quick setup ("Just talk"): consent on the laptop -> live -> trail on the laptop ----------------

describe("quick setup flow", () => {
  const quickSession = (phase, over = {}) => ({ ...session(phase), setup: "quick", ...over });
  const memory = { 1: ["climbing"], 2: ["moved from Austin"], shared: ["coffee"], threads: ["the Lisbon trip"] };
  const trail = { items: [{ for_seat: 1, text: "Ask Dev how the Lisbon trip went." }] };

  it("isQuick is true only for setup quick (older servers without setup run the profiles flow)", () => {
    assert.equal(isQuick(quickSession("lobby")), true);
    assert.equal(isQuick({ ...session("lobby"), setup: "profiles" }), false);
    assert.equal(isQuick(session("lobby")), false);
    assert.equal(isQuick(null), false);
  });

  it("walks consent -> starting -> live -> trail_pending -> trail -> wiped", () => {
    let s = initialState;
    assert.equal(quickStage(s), "loading");
    s = server(s, { type: "session", session: quickSession("lobby") });
    assert.equal(quickStage(s), "consent");
    // POST /start: both seats consent from the laptop; calibrating until the Table's audio arrives.
    s = server(s, { type: "session", session: quickSession("calibrating") });
    assert.equal(quickStage(s), "starting");
    s = server(s, { type: "session", session: quickSession("live") });
    assert.equal(quickStage(s), "live");
    s = server(s, { type: "memory", memory });
    assert.deepEqual(s.memory, memory);
    s = server(s, { type: "session", session: quickSession("ended") });
    assert.equal(quickStage(s), "trail_pending");
    s = server(s, { type: "table_trail", trail });
    assert.equal(quickStage(s), "trail");
    assert.deepEqual(s.tableTrail, trail);
    s = tableReducer(s, { type: "wiped" });
    assert.equal(quickStage(s), "wiped");
  });

  it("a wipe (Done or the server's 10 minute timer) forgets the memory and the trail", () => {
    let s = server(initialState, { type: "session", session: quickSession("ended") });
    s = server(s, { type: "memory", memory });
    s = server(s, { type: "table_trail", trail });
    s = server(s, { type: "wiped" });
    assert.equal(s.memory, null);
    assert.equal(s.tableTrail, null);
    assert.equal(s.session, null);
    assert.equal(s.wiped, true);
  });

  it("a later memory event replaces the earlier one", () => {
    let s = server(initialState, { type: "memory", memory });
    const next = { ...memory, threads: [] };
    s = server(s, { type: "memory", memory: next });
    assert.deepEqual(s.memory.threads, []);
  });
});
