import test from "node:test";
import assert from "node:assert/strict";

import { isFromAnEarlierDay, looksLikeDateRange } from "./persistentTableState.js";

test("a saved date range is recognised by its two ISO dates", () => {
  assert.equal(looksLikeDateRange({ start: "2026-10-23", end: "2026-10-23" }), true);
  assert.equal(looksLikeDateRange({ start: "2026-10-23", end: "2026-10-24", agent: "all" }), true);
  for (const other of [null, undefined, "", 5, [], {}, { start: "2026-10-23" }, { start: "10/23/2026", end: "10/23/2026" }, { filter: "all" }]) assert.equal(looksLikeDateRange(other), false);
});

test("a range saved on an earlier business day (Central Time) is stale; today's, or an unstamped one, is not", () => {
  const noon = new Date("2026-10-26T17:00:00Z");                    // Monday 12:00 in Chicago
  assert.equal(isFromAnEarlierDay("2026-10-26", noon), false);
  assert.equal(isFromAnEarlierDay("2026-10-25", noon), true);
  assert.equal(isFromAnEarlierDay("2026-10-23", noon), true);
  assert.equal(isFromAnEarlierDay(null, noon), false);
  assert.equal(isFromAnEarlierDay("", noon), false);
  const justAfterMidnightUtc = new Date("2026-10-27T03:30:00Z");    // still Monday 22:30 in Chicago
  assert.equal(isFromAnEarlierDay("2026-10-26", justAfterMidnightUtc), false);
  assert.equal(isFromAnEarlierDay("2026-10-26", new Date("2026-10-27T06:00:00Z")), true);   // Tuesday 01:00 in Chicago
});
