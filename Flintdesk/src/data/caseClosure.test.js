import test from "node:test";
import assert from "node:assert/strict";

import {
  closureBadgeText, describeConfirmation, describeRelatedClosure, isClosureStatus, shouldOfferRelatedClosure, shouldShowInheritedClosure,
} from "./caseClosure.js";
import { calculateOpenCallAgeCounts } from "./openCallAge.js";

const inherited = { closing_agent_name: "Priya Sharma", closure_status: "Success - GE" };

test("only Dropped, Success - GE and Success - PC are closure statuses", () => {
  for (const status of ["Dropped", " success - ge ", "Success - PC"]) assert.equal(isClosureStatus(status), true);
  for (const status of ["", null, "Open", "Same Client Context", "Client Side Delay", "PC Follow Up"]) assert.equal(isClosureStatus(status), false);
  for (const status of ["Success GE", "success pc"]) assert.equal(isClosureStatus(status), true);   // new wording
});

test("badge names the closing agent and the final status", () => {
  assert.equal(closureBadgeText(inherited), "Priya Sharma changed to Success - GE");
  assert.equal(closureBadgeText(null), "");
});

test("the note shows for the confirmed status but never over a different closure the agent set themselves", () => {
  assert.equal(shouldShowInheritedClosure("In Progress", inherited), true);
  assert.equal(shouldShowInheritedClosure("", inherited), true);
  assert.equal(shouldShowInheritedClosure(" success - ge ", inherited), true);   // the status confirmation set
  assert.equal(shouldShowInheritedClosure("Dropped", inherited), false);
  assert.equal(shouldShowInheritedClosure("Open", null), false);
});

test("open call aging no longer counts a log that inherited another agent's closure", () => {
  const today = new Date("2026-10-11T12:00:00Z");
  const calls = [
    { date: "2026-10-01", raw: { fd_m_case_status_change: "In Progress" } },
    { date: "2026-10-01", raw: { fd_m_case_status_change: "In Progress", closure_inherited: inherited } },
    { date: "2026-10-01", raw: { fd_m_case_status_change: "Dropped" } },
  ];
  assert.deepEqual(calculateOpenCallAgeCounts(calls, today), { 3: 1, 5: 1, 10: 1, 15: 0 });
});

test("confirmation message names the closer, the status and each updated record", () => {
  const lines = describeConfirmation({ state: "confirmed", closed_by: "Gokul", closure_status: "Success - PC",
    updated: [{ call_reference: "AF#10850", new_status: "Success - PC" }], skipped: [] });
  assert.deepEqual(lines, ["Gokul closed this matter as Success - PC. You confirmed it.", "AF#10850 is now Success - PC."]);
});

test("skipped records are reported, and an empty result never claims a change", () => {
  const lines = describeConfirmation({ state: "confirmed", updated: [], skipped: [{ call_reference: "AF#1", reason: "Already Dropped." }] }, "Gokul");
  assert.deepEqual(lines, ["Nothing needed to change.", "AF#1 was left as it is: Already Dropped."]);
  assert.deepEqual(describeConfirmation({ state: "already_confirmed" }), ["This closure was already confirmed."]);
});

test("related logs are offered only when a save newly makes the call Dropped / Success - GE / Success - PC", () => {
  assert.equal(shouldOfferRelatedClosure("Open", "Dropped"), true);
  assert.equal(shouldOfferRelatedClosure("", "Success - GE"), true);
  assert.equal(shouldOfferRelatedClosure("Dropped", "Success - PC"), true);
  assert.equal(shouldOfferRelatedClosure("Dropped", "dropped"), false);     // nothing changed
  assert.equal(shouldOfferRelatedClosure("Open", "Blocker"), false);
  assert.equal(shouldOfferRelatedClosure("Success - GE", ""), false);
  assert.equal(shouldOfferRelatedClosure("Open", "Same client context"), false);
});

test("related closure outcome reports exact closed and skipped counts", () => {
  const all = describeRelatedClosure({ state: "confirmed", closure_status: "Dropped",
    updated: [{ call_reference: "AF#1" }, { call_reference: "AF#2" }], skipped: [] });
  assert.deepEqual(all, ["2 related call logs closed as Dropped.", "AF#1, AF#2"]);
  const partial = describeRelatedClosure({ state: "confirmed", closure_status: "Success - PC",
    updated: [{ call_reference: "AF#1" }], skipped: [{ call_reference: "AF#9", reason: "Already Dropped." }] });
  assert.equal(partial[0], "1 related call log closed as Success - PC.");
  assert.ok(partial.includes("AF#9: Already Dropped."));
  assert.ok(partial.some((line) => line.startsWith("1 related call log could not be closed")));
  assert.deepEqual(describeRelatedClosure({ state: "reverted", updated: [], skipped: [] }),
    ["The original call was reopened in the meantime, so nothing was changed."]);
  assert.equal(describeRelatedClosure({ state: "confirmed", updated: [], skipped: [{ call_reference: "AF#3", reason: "x" }] })[0],
    "No related call logs were closed.");
});
