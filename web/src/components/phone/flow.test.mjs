// Tests for the phone flow logic. Run: cd web && node --test src/components/phone/flow.test.mjs
// Node 24 strips the TypeScript types from flow.ts on import (it only has `import type` dependencies).
import test from "node:test";
import assert from "node:assert/strict";
import {
  NO_PROGRESS,
  addTopic,
  classifyMicError,
  contactHref,
  deriveStep,
  interviewDoneOnLoad,
  interviewStartIndex,
  normalizeTopic,
  optInCopy,
  parseSeat,
  partnerName,
  stepIndex,
  toggleTopic,
  trailForSeat,
  waitingLine,
} from "./flow.ts";

function seatSummary(over = {}) {
  return {
    joined: true,
    consented: false,
    ready: false,
    name: null,
    has_profile: false,
    answered: 0,
    opted_in: null,
    ...over,
  };
}

function summary({ phase = "profiles", mode = "social", s1 = {}, s2 = {} } = {}) {
  return {
    session_id: "abc",
    phase,
    mode,
    quick: false,
    seats: { 1: seatSummary(s1), 2: seatSummary(s2) },
    created_at: 0,
    expires_in_s: 7200,
    dev_tools: true,
    mock: { muse: true, gemini: true },
  };
}

const local = (over = {}) => ({ ...NO_PROGRESS, ...over });
const EM_DASH = String.fromCharCode(0x2014);

test("parseSeat accepts only 1 and 2", () => {
  assert.equal(parseSeat("1"), 1);
  assert.equal(parseSeat("2"), 2);
  assert.equal(parseSeat("3"), null);
  assert.equal(parseSeat(undefined), null);
  assert.equal(parseSeat("01"), null);
});

test("fresh seat starts at consent", () => {
  assert.equal(deriveStep(summary({ phase: "lobby" }), 1, local()), "consent");
});

test("resume: consented seat with no answers goes to the interview", () => {
  const s = summary({ s1: { consented: true, name: "Maya" } });
  assert.equal(deriveStep(s, 1, local()), "interview");
});

test("local consent wins over a stale summary", () => {
  assert.equal(deriveStep(summary(), 1, local({ consented: true })), "interview");
});

test("interview stays open while answered count runs ahead, until the person confirms", () => {
  const s = summary({ s1: { consented: true, answered: 3 } });
  assert.equal(deriveStep(s, 1, local()), "interview");
  assert.equal(deriveStep(s, 1, local({ interviewDone: true })), "building");
});

test("interviewDoneOnLoad only when every question was answered", () => {
  assert.equal(interviewDoneOnLoad(seatSummary({ answered: 2 }), 3), false);
  assert.equal(interviewDoneOnLoad(seatSummary({ answered: 3 }), 3), true);
  assert.equal(interviewDoneOnLoad(seatSummary({ answered: 1 }), 1), true);
  assert.equal(interviewDoneOnLoad(seatSummary({ answered: 0 }), 0), false);
});

test("interviewStartIndex resumes at the first unanswered question, clamped", () => {
  assert.equal(interviewStartIndex(0, 3), 0);
  assert.equal(interviewStartIndex(2, 3), 2);
  assert.equal(interviewStartIndex(5, 3), 2);
  assert.equal(interviewStartIndex(0, 0), 0);
});

test("resume: seat with a profile goes to review, ready goes to waiting", () => {
  assert.equal(deriveStep(summary({ s1: { consented: true, answered: 3, has_profile: true } }), 1, local()), "review");
  assert.equal(
    deriveStep(summary({ s1: { consented: true, has_profile: true, ready: true } }), 1, local()),
    "waiting",
  );
  assert.equal(deriveStep(summary({ s1: { consented: true } }), 1, local({ hasProfile: true, ready: true })), "waiting");
});

test("each seat reads its own summary", () => {
  const s = summary({ s1: { consented: true, has_profile: true }, s2: {} });
  assert.equal(deriveStep(s, 1, local()), "review");
  assert.equal(deriveStep(s, 2, local()), "consent");
});

test("calibrating and live phases show the phones-down screen", () => {
  const ready = { consented: true, has_profile: true, ready: true };
  assert.equal(deriveStep(summary({ phase: "calibrating", s1: ready, s2: ready }), 2, local()), "live");
  assert.equal(deriveStep(summary({ phase: "live", s1: ready, s2: ready }), 1, local()), "live");
});

test("ended: opt-in until answered (server or local), then trail", () => {
  assert.equal(deriveStep(summary({ phase: "ended" }), 1, local()), "optin");
  assert.equal(deriveStep(summary({ phase: "ended", s1: { opted_in: false } }), 1, local()), "trail");
  assert.equal(deriveStep(summary({ phase: "ended", s1: { opted_in: true } }), 1, local()), "trail");
  assert.equal(deriveStep(summary({ phase: "ended" }), 1, local({ optedIn: true })), "trail");
  // The partner's answer never changes this seat's screen.
  assert.equal(deriveStep(summary({ phase: "ended", s2: { opted_in: true } }), 1, local()), "optin");
});

test("stepIndex maps steps onto four milestones", () => {
  assert.deepEqual(
    ["consent", "interview", "building", "review", "waiting", "live"].map(stepIndex),
    [0, 1, 2, 2, 3, 3],
  );
});

test("partnerName and waitingLine", () => {
  const s = summary({ s1: { name: "Maya" }, s2: { name: "Dev" } });
  assert.equal(partnerName(s, 1), "Dev");
  assert.equal(partnerName(s, 2), "Maya");
  assert.equal(waitingLine("Dev", false), "Waiting for Dev...");
  assert.equal(waitingLine(null, false), "Waiting for your partner...");
  assert.match(waitingLine("Dev", true), /both ready/);
});

test("trailForSeat keeps only this seat's items, at most 3", () => {
  const items = [
    { for_seat: 1, text: "a" },
    { for_seat: 2, text: "b" },
    { for_seat: 1, text: "c" },
    { for_seat: 1, text: "d" },
    { for_seat: 1, text: "e" },
  ];
  assert.deepEqual(
    trailForSeat(items, 1).map((i) => i.text),
    ["a", "c", "d"],
  );
  assert.deepEqual(
    trailForSeat(items, 2).map((i) => i.text),
    ["b"],
  );
});

test("off-limits topics are normalized, deduped and toggled", () => {
  assert.equal(normalizeTopic("  My   EX "), "my ex");
  assert.deepEqual(addTopic([], "Work"), ["work"]);
  assert.deepEqual(addTopic(["work"], " WORK "), ["work"]);
  assert.deepEqual(addTopic(["work"], "   "), ["work"]);
  assert.deepEqual(toggleTopic(["work", "health"], "work", false), ["health"]);
  assert.deepEqual(toggleTopic(["health"], "politics", true), ["health", "politics"]);
  assert.equal(normalizeTopic("x".repeat(80)).length, 40);
});

test("contactHref links only clearly safe contacts", () => {
  assert.equal(contactHref("maya@example.com"), "mailto:maya@example.com");
  assert.equal(contactHref("https://linkedin.com/in/maya"), "https://linkedin.com/in/maya");
  assert.equal(contactHref("linkedin.com/in/maya"), "https://linkedin.com/in/maya");
  assert.equal(contactHref("@maya.runs"), "https://instagram.com/maya.runs");
  assert.equal(contactHref("javascript:alert(1)"), null);
  assert.equal(contactHref("call me maybe"), null);
});

test("classifyMicError maps DOMException names", () => {
  assert.equal(classifyMicError({ name: "NotAllowedError" }), "denied");
  assert.equal(classifyMicError({ name: "NotFoundError" }), "no_device");
  assert.equal(classifyMicError({ name: "NotReadableError" }), "busy");
  assert.equal(classifyMicError(new Error("boom")), "unknown");
  assert.equal(classifyMicError("weird"), "unknown");
});

test("opt-in copy follows the mode", () => {
  assert.equal(optInCopy("speed_dating").title, "Match?");
  assert.equal(optInCopy("social").title, "Keep in touch?");
  assert.match(optInCopy("networking").contactLabel, /LinkedIn/);
});

test("no em dash characters in user-facing copy", async () => {
  const { readFile, readdir } = await import("node:fs/promises");
  const { join, dirname } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const here = dirname(fileURLToPath(import.meta.url));
  const src = join(here, "..", "..");
  const files = [
    ...(await readdir(join(src, "components", "phone"))).map((f) => join(src, "components", "phone", f)),
    ...(await readdir(join(src, "components", "ui"))).map((f) => join(src, "components", "ui", f)),
    ...["ConsentForm", "ProfileChips", "TrailCard", "RecordButton", "Waveform"].map((f) =>
      join(src, "components", `${f}.tsx`),
    ),
    join(src, "pages", "Home.tsx"),
    join(src, "pages", "Join.tsx"),
  ];
  for (const f of files) {
    const text = await readFile(f, "utf8");
    assert.ok(!text.includes(EM_DASH), `em dash found in ${f}`);
  }
});
