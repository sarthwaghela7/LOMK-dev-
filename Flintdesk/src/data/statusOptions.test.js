import test from "node:test";
import assert from "node:assert/strict";

import { CASE_STATUS_OPTIONS, caseStatusKey, isLostStatus } from "./statusOptions.js";

test("the Case Status Change dropdown has exactly the thirteen values, in order, without duplicates", () => {
  assert.deepEqual(CASE_STATUS_OPTIONS, [
    "Open", "Client Side Delay", "Blocker", "Dropped", "Same Client Context", "Wrong Number Dialed",
    "Initial email sent (Fee Details shared)", "Initial Email Follow Up", "Fee details shared & FU",
    "PC link shared", "PC Follow Up", "Success PC", "Success GE",
  ]);
  assert.equal(new Set(CASE_STATUS_OPTIONS).size, 13);
  assert.ok(!CASE_STATUS_OPTIONS.some((option) => /lost|transition/i.test(option)), "lifecycle stages are not dropdown values");
});

test("legacy spellings compare equal to the new wording", () => {
  assert.equal(caseStatusKey(" Success - PC "), caseStatusKey("Success PC"));
  assert.equal(caseStatusKey("initial email followup"), caseStatusKey("Initial Email Follow Up"));
  assert.equal(caseStatusKey("Same client context"), caseStatusKey("Same Client Context"));
  assert.notEqual(caseStatusKey("Success PC"), caseStatusKey("Success GE"));
});

test("a Lost status is recognised in both the new and the legacy form", () => {
  assert.equal(isLostStatus("PC link shared & Lost"), true);
  assert.equal(isLostStatus("PC shared and lost"), true);
  assert.equal(isLostStatus("PC link shared"), false);
});
