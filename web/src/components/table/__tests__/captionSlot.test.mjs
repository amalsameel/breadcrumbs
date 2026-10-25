// Regression guard for issue 2: a new crumb's caption must start its entrance the moment it arrives.
// framer-motion's AnimatePresence mode="wait" holds the entering child until the leaving one has
// finished its exit (0.4 to 0.5 s with the old exits), so the caption slots must not use it.
// Run from web/:
//   node --import ./src/components/table/__tests__/register.mjs --test src/components/table/__tests__/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { CAPTION_SLOT, CAPTION_SLOT_ITEM } from "../format.ts";

const source = (name) => readFileSync(fileURLToPath(new URL(`../${name}`, import.meta.url)), "utf8");
// Drop comments so a note that mentions mode="wait" does not count as using it.
const code = (name) => source(name).replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

describe("caption slots never make a new crumb wait (issue 2)", () => {
  for (const file of ["LiveStage.tsx", "Calibration.tsx"]) {
    it(`${file}: no AnimatePresence mode="wait", and the slot overlays leaving and arriving lines`, () => {
      const src = code(file);
      assert.ok(src.includes("<AnimatePresence"), "still animates the caption slot");
      assert.doesNotMatch(src, /<AnimatePresence[^>]*mode=["{]\s*["']?wait/);
      assert.match(src, /\$\{CAPTION_SLOT\}/, "slot uses the one-cell grid");
      assert.match(src, /CAPTION_SLOT_ITEM/, "slot children share the grid cell");
    });
  }

  it("the slot is one grid cell that every child is placed into", () => {
    assert.match(CAPTION_SLOT, /\bgrid\b/);
    assert.match(CAPTION_SLOT, /\bgrid-cols-1\b/);
    assert.match(CAPTION_SLOT_ITEM, /\bcol-start-1\b/);
    assert.match(CAPTION_SLOT_ITEM, /\brow-start-1\b/);
  });

  it("CrumbCaption's exit uses the short shared exit time", () => {
    const src = code("CrumbCaption.tsx");
    assert.match(src, /duration:\s*CAPTION_EXIT_S/);
    assert.doesNotMatch(src, /duration:\s*0\.4/);
  });
});
