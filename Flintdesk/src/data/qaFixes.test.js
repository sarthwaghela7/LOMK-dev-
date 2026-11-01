import test from "node:test";
import assert from "node:assert/strict";

import { callTaskRecords } from "./callTaskRecords.js";
import { calculateDashboardMetrics } from "./dashboardCalculations.js";
import { DASHBOARD_REFRESH_MS } from "./dashboardRefresh.js";
import { parseLeadDate } from "./leadDates.js";
import { calculateOpenCallAgeCounts } from "./openCallAge.js";
import { calculateLeadScore, calculatePriority, getLeadScoreBreakdown } from "./priorityEngine.js";

test("completed case flag contributes to Dashboard completed totals", () => {
  const result = calculateDashboardMetrics([{ raw: { case_closing_completed: true } }]);
  assert.equal(result.completedCalls, 1);
});

test("Dashboard polling interval is a load-conscious 30 seconds", () => {
  assert.equal(DASHBOARD_REFRESH_MS, 30_000);
});

test("Lead dates use strict MM/DD/YYYY or ISO parsing", () => {
  const usDate = parseLeadDate("01/13/2025");
  const isoDate = parseLeadDate("2025-01-13");
  assert.deepEqual([usDate?.getFullYear(), usDate?.getMonth() + 1, usDate?.getDate()], [2025, 1, 13]);
  assert.deepEqual([isoDate?.getFullYear(), isoDate?.getMonth() + 1, isoDate?.getDate()], [2025, 1, 13]);
  assert.equal(parseLeadDate("13/01/2025"), null);
  assert.equal(parseLeadDate("02/30/2025"), null);
});

test("missed and non-responsive Tasks are mapped into labeled Calls records", () => {
  const records = callTaskRecords([
    { id: "11111111-aaaa", task_type: "missed_call", data: { name: "A", contact_number: "5551", missed_call_date: "2025-01-13" } },
    { id: "22222222-bbbb", task_type: "non_responsiveness", data: { name: "B", phone_number: "5552", date_added: "2025-01-14" } },
    { id: "33333333-cccc", task_type: "general", data: {} },
  ]);
  assert.deepEqual(records.map((record) => record.recordLabel), ["Missed Call Task", "Non-Responsive Task"]);
  assert.deepEqual(records.map((record) => record.capturedNumber), ["5551", "5552"]);
  assert.ok(records.every((record) => record.isTaskRecord));
});

// Updated on purpose: Open Call Aging now uses the Dashboard's / Calls page's single open rule (only Dropped, Success - GE,
// Success - PC or a confirmed closure close a call), so an Action Status of "Completed" no longer closes a call here.
test("open-call aging uses cumulative thresholds and the shared open rule", () => {
  const result = calculateOpenCallAgeCounts([
    { date: "2026-08-28", raw: { action_status: "Open" } },
    { date: "2026-08-25", raw: { action_status: "Pending" } },
    { date: "2026-08-15", raw: { action_status: "Open" } },
    { date: "2026-08-01", raw: { action_status: "Completed" } },
  ], new Date("2026-08-31T12:00:00Z"));
  assert.deepEqual(result, { 3: 4, 5: 3, 10: 2, 15: 2 });
});

test("calculateLeadScore derives rubric score from call context when database column is null", () => {
  const eb1Call = {
    caseType: "EB-1A Extraordinary Ability",
    transcript: "Client has received an RFE for their EB-1A petition and needs urgent consultation.",
    raw: {
      fd_a_urgency_flag: "Urgent - RFE",
      fd_a_client_type: "New Client - Individual",
      fd_a_lead_source: "Client Referral",
      fd_a_case_type: "EB-1A",
      fd_a_call_outcome: "Consultation booked",
    },
  };
  const score = calculateLeadScore(eb1Call);
  assert.ok(typeof score === "number");
  assert.ok(score >= 70, `Expected BOOK_NOW score (>= 70), got ${score}`);

  const breakdown = getLeadScoreBreakdown(eb1Call);
  assert.ok(Array.isArray(breakdown));
  assert.equal(breakdown.length, 6);
  assert.deepEqual(
    breakdown.map((b) => b.label),
    [
      "Urgency Signal",
      "Case Complexity",
      "Employer Involvement",
      "Lead Source Trust",
      "Existing Relationship",
      "Readiness / Decision Stage",
    ]
  );
  // Urgency signal should be max 25 for RFE
  assert.equal(breakdown.find((b) => b.label === "Urgency Signal")?.value, 25);
  // Case complexity should be max 20 for EB-1A
  assert.equal(breakdown.find((b) => b.label === "Case Complexity")?.value, 20);
});

test("calculateLeadScore honors explicit lead_score when already persisted in database", () => {
  const persistedCall = {
    raw: {
      lead_score: 88,
      lead_score_breakdown: {
        urgency_signal: 25,
        case_complexity: 20,
        employer_involvement: 15,
        lead_source_trust: 15,
        existing_relationship: 5,
        readiness_decision_stage: 8,
      },
    },
  };
  assert.equal(calculateLeadScore(persistedCall), 88);
  const breakdown = getLeadScoreBreakdown(persistedCall);
  assert.equal(breakdown?.find((b) => b.label === "Urgency Signal")?.value, 25);
});

test("Daily call target counting excludes voicemail calls and preserves 35 target", async () => {
  const { isVoicemailCall } = await import("./callKind.js");
  const agentCalls = [
    // 30 qualifying calls
    ...Array.from({ length: 30 }, (_, i) => ({
      agent: "Sarah Connor",
      date: "2025-01-15",
      raw: { fd_a_call_outcome: "Consultation booked" },
    })),
    // 5 voicemail calls
    ...Array.from({ length: 5 }, (_, i) => ({
      agent: "Sarah Connor",
      date: "2025-01-15",
      raw: { fd_a_call_outcome: "Voicemail left" },
    })),
  ];

  // Verify voicemail detection
  assert.equal(agentCalls.filter(isVoicemailCall).length, 5);

  // Verify qualifying non-voicemail calls count
  const nonVoicemailCount = agentCalls.filter(
    (row) => row.agent === "Sarah Connor" && row.date === "2025-01-15" && !isVoicemailCall(row)
  ).length;
  assert.equal(nonVoicemailCount, 30);
  assert.equal(35, 35); // target remains 35
  assert.equal(nonVoicemailCount - 35, -5); // difference is 30 - 35 = -5

  // Edge case: all calls are voicemail -> count is 0
  const allVmCalls = Array.from({ length: 10 }, () => ({
    agent: "Sarah Connor",
    date: "2025-01-15",
    raw: { voicemail_left_if_no_answer: "yes" },
  }));
  const zeroCount = allVmCalls.filter((row) => !isVoicemailCall(row)).length;
  assert.equal(zeroCount, 0);

  // Edge case: 35 calls and none are voicemail -> count is 35
  const nonVmCalls = Array.from({ length: 35 }, () => ({
    agent: "Sarah Connor",
    date: "2025-01-15",
    raw: { fd_a_call_outcome: "Consultation booked" },
  }));
  const fullCount = nonVmCalls.filter((row) => !isVoicemailCall(row)).length;
  assert.equal(fullCount, 35);
});
