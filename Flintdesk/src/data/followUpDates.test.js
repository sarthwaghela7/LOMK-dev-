import test from "node:test";
import assert from "node:assert/strict";

import { cleanFollowUpDates, formatFollowUpDate } from "./followUpDates.js";

test("formats a calendar date as MM/DD/YYYY without any timezone shift", () => {
  const previous = process.env.TZ;
  for (const zone of ["UTC", "America/Chicago", "Pacific/Auckland"]) {
    process.env.TZ = zone;
    assert.equal(formatFollowUpDate("2026-10-12"), "10/12/2026");
    assert.equal(formatFollowUpDate("2026-01-01"), "01/01/2026");
  }
  process.env.TZ = previous;
});

test("rejects anything that is not YYYY-MM-DD", () => {
  for (const bad of ["", null, undefined, "10-12-2026", "2026-10-12T00:00:00Z", "N/A", "2026-1-2"]) {
    assert.equal(formatFollowUpDate(bad), "");
  }
});

test("cleans, de-duplicates and sorts chronologically", () => {
  assert.deepEqual(
    cleanFollowUpDates(["2026-10-12", "N/A", "2026-10-03", "2026-10-12", "2028-01-01", null]),
    ["2026-10-03", "2026-10-12", "2028-01-01"],
  );
  assert.deepEqual(cleanFollowUpDates(undefined), []);
});
