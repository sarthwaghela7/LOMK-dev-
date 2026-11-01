import test from "node:test";
import assert from "node:assert/strict";

import { businessDateCT, getDefaultDateRange, previousWorkingDay } from "./filterDates.js";

const at = (iso) => new Date(iso);

test("a weekday opens on the day before", () => {
  assert.equal(previousWorkingDay(at("2026-10-23T15:00:00Z")), "2026-10-22");          // Friday 10:00 CT -> Thursday
  assert.deepEqual(getDefaultDateRange(at("2026-10-22T15:00:00Z")), { start: "2026-10-21", end: "2026-10-21" });
});

test("Saturday, Sunday and Monday all show Friday", () => {
  assert.equal(previousWorkingDay(at("2026-10-24T15:00:00Z")), "2026-10-23");          // Saturday
  assert.equal(previousWorkingDay(at("2026-10-25T15:00:00Z")), "2026-10-23");          // Sunday
  assert.equal(previousWorkingDay(at("2026-10-26T15:00:00Z")), "2026-10-23");          // Monday
});

test("the business day follows Central Time, not UTC", () => {
  assert.equal(businessDateCT(at("2026-10-24T03:30:00Z")), "2026-10-23");              // 22:30 Friday in Chicago, already Saturday in UTC
  assert.equal(previousWorkingDay(at("2026-10-24T03:30:00Z")), "2026-10-22");          // still Friday there, so Thursday
  assert.equal(previousWorkingDay(at("2026-10-23T04:30:00Z")), "2026-10-21");          // 23:30 Wednesday in Chicago -> Tuesday
});

test("month and year boundaries", () => {
  assert.equal(previousWorkingDay(at("2026-11-02T18:00:00Z")), "2026-10-30");          // Monday 2 Nov -> Friday 30 Oct
  assert.equal(previousWorkingDay(at("2027-01-01T18:00:00Z")), "2026-12-31");
});
