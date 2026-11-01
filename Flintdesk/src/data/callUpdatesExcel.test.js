import test from "node:test";
import assert from "node:assert/strict";

import { callUpdatesExcelFilename, callUpdatesExcelSheet } from "./callUpdatesExcel.js";

const agents = [{ id: "a", name: "Agent A" }, { id: "b", name: "Agent B" }];
const days = [{ date: "2026-10-26", agents: { a: { incoming: 3, outgoing: 5 }, b: { incoming: 1, outgoing: 4 } } }];

test("one sheet: Date / Category / Metric, a column per agent, then Total", () => {
  const sheet = callUpdatesExcelSheet({ days, agents });
  assert.deepEqual(sheet.columns.map((column) => column.label), ["Date", "Category", "Metric", "Agent A", "Agent B", "Total"]);
  assert.ok(sheet.rows.length > 10);
  sheet.rows.forEach((row) => assert.equal(row.length, sheet.columns.length));
});

test("the figures are numbers and equal the on-screen row totals; the date is an ISO day", () => {
  const { rows } = callUpdatesExcelSheet({ days, agents });
  const incoming = rows.find((row) => row[2] === "Incoming Calls");
  assert.deepEqual(incoming, ["2026-10-26", "A. Vitel Calls", "Incoming Calls", 3, 1, 4]);
  rows.forEach((row) => row.slice(3).forEach((value) => assert.equal(typeof value, "number")));
});

test("each day ends with the Calls + WhatsApp total row, and the file name carries the range", () => {
  const { rows } = callUpdatesExcelSheet({ days, agents });
  assert.deepEqual(rows[rows.length - 1].slice(0, 3), ["2026-10-26", "Total (A+B)", "Calls + WhatsApp"]);
  assert.equal(callUpdatesExcelFilename("2026-10-01", "2026-10-31"), "FlintDesk_Call_Updates_2026-10-01_to_2026-10-31.xls");
});
