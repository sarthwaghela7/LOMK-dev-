import test from "node:test";
import assert from "node:assert/strict";

import { LIFECYCLE_STAGES, lifecycleCards, nextLifecycleSelection, validLifecycleStage } from "./caseLifecycle.js";

test("the five lifecycle buttons / cards exist in the required order", () => {
  assert.deepEqual(LIFECYCLE_STAGES.map((s) => s.label), [
    "Open Cases", "In-Transition Cases", "Lost Cases", "Dropped Cases", "Success Cases",
  ]);
  assert.deepEqual(LIFECYCLE_STAGES.map((s) => s.key), ["open", "in_transition", "lost", "dropped", "success"]);
});

test("the filter is single choice: another stage replaces it, the same stage clears it", () => {
  assert.equal(nextLifecycleSelection("", "lost"), "lost");
  assert.equal(nextLifecycleSelection("lost", "success"), "success");
  assert.equal(nextLifecycleSelection("success", "success"), "");
});

test("an unknown restored value means no lifecycle filter", () => {
  assert.equal(validLifecycleStage("in_transition"), "in_transition");
  for (const bad of ["", null, undefined, "Lost", "bogus", "open,lost"]) assert.equal(validLifecycleStage(bad), "");
});

test("cards show the backend counts in order, and 0 for anything missing", () => {
  const cards = lifecycleCards({ counts: { open: 2, in_transition: 2, lost: 0, dropped: 0, success: 1 }, total: 5 });
  assert.deepEqual(cards.map((c) => c.value), [2, 2, 0, 0, 1]);
  assert.deepEqual(lifecycleCards(null).map((c) => c.value), [0, 0, 0, 0, 0]);
  assert.deepEqual(lifecycleCards({ counts: { open: "x" } }).map((c) => c.value), [0, 0, 0, 0, 0]);
});
