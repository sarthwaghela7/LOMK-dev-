import test from "node:test";
import assert from "node:assert/strict";
import { centralCalendarDay, enquiryOpenDuration } from "./enquiryOpenDuration.js";

test("Central midnight is independent of browser timezone", () => {
  assert.equal(enquiryOpenDuration({ registeredAt: "2026-09-10T23:30:00Z" }, new Date("2026-09-11T04:30:00Z")), "0 days");
  assert.equal(enquiryOpenDuration({ registeredAt: "2026-09-10T23:30:00Z" }, new Date("2026-09-11T05:30:00Z")), "1 days");
});
test("DST days count calendar boundaries, not elapsed 24-hour intervals", () => {
  assert.equal(centralCalendarDay("2026-03-09T05:00:00Z") - centralCalendarDay("2026-03-08T06:00:00Z"), 1);
  assert.equal(centralCalendarDay("2026-11-02T06:00:00Z") - centralCalendarDay("2026-11-01T05:00:00Z"), 1);
});
test("business dates and closed enquiries retain calendar meaning", () => {
  assert.equal(enquiryOpenDuration({ date: "09/01/2026", raw: { action_status: "Closed", action_date: "2026-09-03" } }), "2 days");
  assert.equal(centralCalendarDay("2026-09-01T00:00:00"), centralCalendarDay("2026-09-01"));
  assert.equal(enquiryOpenDuration({ date: "invalid" }), "-");
  assert.equal(enquiryOpenDuration({ date: "2026-02-30" }), "-");
});
