import test from "node:test";
import assert from "node:assert/strict";

import { callFocusFromConfirmation, focusRangeFor, shiftIsoDate } from "./callFocus.js";

test("collects every updated call that has a unique call id, without duplicates", () => {
  const result = { updated: [
    { call_reference: "AF#1", new_status: "Dropped" },
    { call_reference: "AF#2", call_id: " 11111111-aaaa ", call_date: "2026-10-09" },
    { call_reference: "AF#3", call_id: "22222222-bbbb", call_date: "2026-10-11" },
    { call_reference: "AF#3 again", call_id: "22222222-bbbb" },
  ] };
  assert.deepEqual(callFocusFromConfirmation(result), { calls: [
    { callId: "11111111-aaaa", callReference: "AF#2", callDate: "2026-10-09" },
    { callId: "22222222-bbbb", callReference: "AF#3", callDate: "2026-10-11" },
  ] });
});

test("nothing to open when nothing was updated or the server sent no id", () => {
  assert.equal(callFocusFromConfirmation(null), null);
  assert.equal(callFocusFromConfirmation({ state: "already_confirmed", updated: [] }), null);
  assert.equal(callFocusFromConfirmation({ updated: [{ call_reference: "AF#1" }] }), null);
});

test("date window is the call's day plus one either side, across month and year ends", () => {
  assert.deepEqual(focusRangeFor("2026-10-09"), { start: "2026-10-08", end: "2026-10-10" });
  assert.deepEqual(focusRangeFor("2026-03-01"), { start: "2026-02-28", end: "2026-03-02" });
  assert.deepEqual(focusRangeFor("2026-12-31"), { start: "2026-12-30", end: "2027-01-01" });
  assert.equal(shiftIsoDate("2028-03-01", -1), "2028-02-29");
});

test("several calls: window runs from a day before the earliest to a day after the latest, ignoring unknown dates", () => {
  assert.deepEqual(focusRangeFor(["2026-10-11", "", "2026-10-02", "2026-10-09"]), { start: "2026-10-01", end: "2026-10-12" });
});

test("unknown or malformed dates give no window", () => {
  assert.equal(focusRangeFor(""), null);
  assert.equal(focusRangeFor("10/09/2026"), null);
});
