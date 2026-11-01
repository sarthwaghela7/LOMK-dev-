import test from "node:test";
import assert from "node:assert/strict";

import { caseStatusText, matchesCaseStatus } from "./leadCaseStatus.js";

test("a lead with no chosen case status shows N/A", () => {
  for (const empty of [undefined, null, "", "   "]) assert.equal(caseStatusText(empty), "N/A");
  assert.equal(caseStatusText(" Success - PC "), "Success - PC");
});

test("the filter matches the exact status regardless of spacing and case, and never hides anything when empty", () => {
  assert.equal(matchesCaseStatus("Success - PC", "Success - PC"), true);
  assert.equal(matchesCaseStatus("  success - pc ", "Success - PC"), true);
  assert.equal(matchesCaseStatus("Success - GE", "Success - PC"), false);
  assert.equal(matchesCaseStatus("", "Success - PC"), false);          // N/A rows are not "Success - PC"
  assert.equal(matchesCaseStatus(undefined, "Open"), false);
  assert.equal(matchesCaseStatus("Blocker", ""), true);
  assert.equal(matchesCaseStatus("", ""), true);
});
