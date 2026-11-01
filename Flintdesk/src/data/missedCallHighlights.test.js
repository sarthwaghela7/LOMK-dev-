import test from "node:test";
import assert from "node:assert/strict";
import {
  filterMissedCallsByStatus,
  isAttendedMissedCall,
  repeatedClientNumbers,
} from "./missedCallHighlights.js";

const row = (id, number, extra = {}) => ({ id, data: { contact_number: number, ...extra } });
const rows = [
  row("a", "1234567890"),                                   // white
  row("b", "+1 (123) 456-7890", { callback_matched: true }), // green, same client as a
  row("c", "9876543210"),                                   // white, unique
  row("d", "5551234567", { callback: { agent: "X" } }),     // green, unique
  row("e", "Anonymous"),
  row("f", ""),
];

test("green state is a matched callback in either view's shape", () => {
  assert.equal(isAttendedMissedCall(rows[1]), true);
  assert.equal(isAttendedMissedCall(rows[3]), true);
  assert.equal(isAttendedMissedCall(rows[0]), false);
});

test("repeated client numbers use shared normalisation and ignore unusable numbers", () => {
  const repeated = repeatedClientNumbers(rows);
  assert.deepEqual([...repeated], ["1234567890"]);
});

test("Pending shows only non-green, Completed only green", () => {
  const repeated = repeatedClientNumbers(rows);
  assert.deepEqual(filterMissedCallsByStatus(rows, "pending", repeated).map((r) => r.id), ["a", "c", "e", "f"]);
  assert.deepEqual(filterMissedCallsByStatus(rows, "completed", repeated).map((r) => r.id), ["b", "d"]);
});

test("Same Client Number shows every repeated row regardless of green/white", () => {
  const repeated = repeatedClientNumbers(rows);
  assert.deepEqual(filterMissedCallsByStatus(rows, "same_client", repeated).map((r) => r.id), ["a", "b"]);
  assert.equal(filterMissedCallsByStatus(rows, "", repeated).length, rows.length);
});
