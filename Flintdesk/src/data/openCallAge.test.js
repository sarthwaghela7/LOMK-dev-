import test from "node:test";
import assert from "node:assert/strict";

import { calculateOpenCallAgeCounts, countOpenCalls, isOpenForAging } from "./openCallAge.js";

const NOON_UTC = new Date("2026-10-15T12:00:00Z");       // 07:00 on 15 Oct in Chicago: both zones agree on the date
const daysAgo = (n) => { const d = new Date(Date.UTC(2026, 9, 15 - n)); return d.toISOString().slice(0, 10); };
let seq = 0;
const call = (age, status = "", raw = {}) => ({ callId: `c${++seq}`, date: daysAgo(age), raw: { fd_m_case_status_change: status, call_recording_date_normalized: daysAgo(age), ...raw } });
const counts = (calls, today = NOON_UTC) => calculateOpenCallAgeCounts(calls, today);
const totals = (calls, today = NOON_UTC) => countOpenCalls(calls, today);

test("calls below, exactly at and above each threshold land in the right cumulative buckets", () => {
  const result = counts([call(2), call(3), call(4), call(5), call(9), call(10), call(14), call(15), call(40)]);
  assert.deepEqual([result[3], result[5], result[10], result[15]], [8, 6, 4, 2]);
  assert.equal(totals([call(2), call(3), call(4), call(5), call(9), call(10), call(14), call(15), call(40)]).total, 9);
});

test("the buckets are nested and are not distinct counts (they overlap)", () => {
  const result = counts(Array.from({ length: 30 }, (_, i) => call(i)));
  assert.ok(result[3] >= result[5] && result[5] >= result[10] && result[10] >= result[15]);
  assert.ok(result[3] + result[5] + result[10] + result[15] > totals(Array.from({ length: 30 }, (_, i) => call(i))).total);   // summing them double counts
});

test("blank and the five open-list statuses count; every other status (incl. Wrong Number Dialed) does not", () => {
  for (const status of ["", "Open", "Client Side Delay", "Blocker", "Dropped", "Same client context", " DROPPED "]) assert.equal(isOpenForAging(call(5, status)), true, status);
  for (const status of ["Success - GE", "Success PC", "PC Follow Up", "initial email followup", "Wrong Number Dialed", "PC link shared & Lost"]) assert.equal(isOpenForAging(call(5, status)), false, status);
  assert.equal(isOpenForAging(call(5, "Open", { closure_inherited: { closure_status: "Dropped" } })), false);
});

test("the Mark Completed flag and the Action Status never decide it (same rule as the Calls page)", () => {
  assert.equal(isOpenForAging(call(5, "Blocker", { case_closing_completed: true })), true);
  assert.equal(isOpenForAging({ ...call(5, ""), manualActionStatus: "Completed" }), true);
  assert.equal(isOpenForAging(call(5, "", { action_status: "Completed" })), true);      // the Open column treats it the same way
});

test("a call that moves from open to closed leaves every bucket", () => {
  const open = call(12, "Open");
  assert.equal(counts([open])[10], 1);
  assert.deepEqual([3, 5, 10, 15].map((n) => counts([{ ...open, raw: { ...open.raw, fd_m_case_status_change: "Success PC" } }])[n]), [0, 0, 0, 0]);
});

test("a call closed through related-call confirmation (inherited closure) stops being counted", () => {
  const rows = [call(12), call(12), call(12)];
  assert.equal(counts(rows)[10], 3);
  rows[1].raw.closure_inherited = { closing_agent_name: "Priya", closure_status: "Success - GE" };
  rows[2].raw.fd_m_case_status_change = "Success - GE";                                // confirmed log: status set by the confirmation
  assert.equal(counts(rows)[10], 1);
});

test("duplicate records of one call are counted once", () => {
  const one = call(12);
  assert.equal(totals([one, { ...one }, { ...one, raw: { ...one.raw } }]).total, 1);
  assert.equal(totals([call(12), call(12)]).total, 2);                                // different calls stay separate
});

test("missing or invalid dates are never guessed: excluded from every bucket and reported", () => {
  const rows = [call(12), { callId: "x1", date: "", raw: {} }, { callId: "x2", date: "not a date", raw: {} }, { callId: "x3", date: "2026-02-31", raw: {} }];
  const result = counts(rows);
  assert.deepEqual([result[3], result[10]], [1, 1]);
  assert.deepEqual(totals(rows), { total: 1, missingDate: 3 });
});

test("age is measured in Chicago calendar days: evening in Chicago is already the next UTC day", () => {
  const call10 = call(10);                                                            // dated 5 Oct
  const lateEveningChicago = new Date("2026-10-16T02:30:00Z");                         // 21:30 on 15 Oct in Chicago, 16 Oct in UTC
  assert.equal(counts([call10], lateEveningChicago)[10], 1);                           // still 10 days (UTC arithmetic would say 11)
  const call11 = call(11);
  assert.equal(counts([call11], lateEveningChicago)[10], 1);
  assert.equal(calculateOpenCallAgeCounts([{ callId: "b", date: "2026-10-05", raw: {} }], new Date("2026-10-15T04:59:00Z"))[10], 0);   // 23:59 on 14 Oct in Chicago: 9 days
  assert.equal(calculateOpenCallAgeCounts([{ callId: "b", date: "2026-10-05", raw: {} }], new Date("2026-10-15T05:01:00Z"))[10], 1);   // 00:01 on 15 Oct in Chicago: 10 days
});

test("future-dated and empty input never go negative or throw", () => {
  assert.deepEqual(counts([]), { 3: 0, 5: 0, 10: 0, 15: 0 });                       // the card shape is exactly the four buckets
  assert.equal(totals([call(-3)]).total, 1);
  assert.equal(counts([call(-3)])[3], 0);
});
