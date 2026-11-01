import test from "node:test";
import assert from "node:assert/strict";
import { agentFigures, datesBetween, dayTotals, validateRange } from "./callUpdatesCalc.js";

const day = {
  date: "2026-10-01",
  agents: {
    a: { incoming: 6, outgoing: 18, wa_answered: 5, wa_missed: 2, open_leads: 7, successful_ge: 13, paid_pc: 3, dropped: 3, vm: 3, initial_emails: 0 },
    b: { incoming: 1, outgoing: 15, wa_answered: 0, wa_missed: 0, open_leads: 4, successful_ge: 7, paid_pc: 2, dropped: 0, vm: 3 },
  },
};

test("per-agent totals are derived from the cells", () => {
  const a = agentFigures(day.agents.a);
  assert.equal(a.totalCalls, 24);
  assert.equal(a.totalWhatsApp, 7);
  assert.equal(a.callsAndWhatsApp, 31);
  assert.equal(a.caseStatusTotal, 26);
  assert.equal(agentFigures().totalCalls, 0);
});

test("day totals and the comparison follow manual edits", () => {
  const ids = ["a", "b"];
  let totals = dayTotals(day, ids);
  assert.equal(totals.totalCalls, 40);
  assert.equal(totals.totalWhatsApp, 7);
  assert.equal(totals.callsAndWhatsApp, 47);
  assert.equal(totals.caseStatusTotal, 26 + 13);
  assert.equal(totals.matches, false);
  assert.equal(totals.column("vm"), 6);
  // Edit WhatsApp numbers so A + B equals Case Status (39): 40 + 0 -> need WA = -1, so adjust cases instead.
  const edited = { ...day, agents: { ...day.agents, a: { ...day.agents.a, open_leads: 15, wa_answered: 0, wa_missed: 0 } } };
  totals = dayTotals(edited, ids);
  assert.equal(totals.callsAndWhatsApp, 40);
  assert.equal(totals.caseStatusTotal, 34 + 13);
  assert.equal(totals.matches, false);
  const matching = { ...edited, agents: { ...edited.agents, a: { ...edited.agents.a, open_leads: 8 } } };
  assert.equal(dayTotals(matching, ids).caseStatusTotal, 27 + 13);
  assert.equal(dayTotals(matching, ids).matches, true);
});

test("one date per day in the range and range validation", () => {
  assert.deepEqual(datesBetween("2026-10-01", "2026-10-05"), ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]);
  assert.equal(validateRange("2026-10-05", "2026-10-01"), "From Date must be on or before To Date.");
  assert.equal(validateRange("", "2026-10-01"), "Choose both a From Date and a To Date.");
  assert.equal(validateRange("2026-01-01", "2026-12-31"), "Choose a range of 92 days or fewer.");
  assert.equal(validateRange("2026-10-01", "2026-10-31"), "");
});

test("each lifecycle category lists its Case Status Change values with their own counts", async () => {
  const { LIFECYCLE_GROUPS, dayTotals, groupFigures } = await import("./callUpdatesCalc.js");
  const [open, inTransition, lost, dropped] = LIFECYCLE_GROUPS;
  const values = {
    status_counts: { Open: 3, "Client Side Delay": 2, Blocker: 1, Dropped: 4, "Fee details shared & FU": 5, "Wrong Number Dialed": 1 },
    lost_counts: { "PC link shared & Lost": 2, "Fee details shared & FU & Lost": 1 },
  };
  assert.deepEqual(groupFigures(open, values).lines.map((l) => [l.label, l.count]), [["Open", 3], ["Client Side Delay", 2], ["Blocker", 1], ["Same Client Context", 0]]);
  assert.equal(groupFigures(open, values).total, 6);
  assert.equal(groupFigures(inTransition, values).lines.find((l) => l.label === "Fee details shared & FU").count, 5);   // the added In-Transition value
  assert.deepEqual(groupFigures(lost, values).lines.map((l) => l.label), ["Fee details shared & FU & Lost", "PC link shared & Lost"]);
  assert.equal(groupFigures(lost, values).total, 3);
  assert.equal(groupFigures(dropped, values).total, 7);                                  // Client Side Delay 2 + Dropped 4 + Wrong Number Dialed 1
  assert.equal(groupFigures(open, {}).total, 0);                                         // a day with no data is zero, never an error
  const day = { agents: { a: values, b: { status_counts: { Open: 1 } } } };
  assert.equal(dayTotals(day, ["a", "b"]).groupColumn("open"), 7);                       // the Total column sums the agents' category totals
});
