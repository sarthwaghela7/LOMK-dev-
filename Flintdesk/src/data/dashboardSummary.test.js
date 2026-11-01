import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { buildDailyCallTargetRows, computeDashboardSummary } from "./dashboardSummary.js";
import { isVoicemailCall } from "./callKind.js";
import { _buildCallRecordsWithTasks } from "./callRecordProjection.js";

test("Daily Call Target matches the existing Dashboard function, including task rows", () => {
  const source = fs.readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8");
  const original = source.slice(source.indexOf("const DAILY_CALL_TARGET ="), source.indexOf("function normalizeEnquiryCaseType"));
  const dates = ["2026-09-21", "2026-09-22"];
  const context = { isVoicemailCall,
    getCallDate: (row) => row.date,
    buildCallLogStatusRows: () => dates.map(date => ({ date })),
  };
  vm.createContext(context);
  vm.runInContext(`${original}\nthis.calculate = buildDailyCallTargetRows;`, context);
  const rows = [
    { agent: "Alex", date: dates[0], raw: { fd_a_call_summary: "Documented normal call" } },
    { agent: "Alex", date: dates[0], raw: { fd_a_call_summary: "VM", voicemail_left_if_no_answer: "yes" } },
    { agent: "Alex", date: dates[0], raw: {} },
    { agent: "Alex", date: dates[1], isTaskRecord: true, raw: { fd_a_call_summary: "Task notes" } },
    { agent: "Blair", date: dates[1], raw: { fd_a_call_summary: "Call", fd_a_call_outcome: "Voicemail left" } },
  ];
  const result = buildDailyCallTargetRows(rows, ["Alex", "Blair"], ...dates);
  assert.deepEqual(result, JSON.parse(JSON.stringify(context.calculate(rows, ["Alex", "Blair"], ...dates))));
  assert.deepEqual(result.map(row => row.callsMade), [1, 0, 1, 0]);
  assert.ok(result.every(row => row.target === 35));
});

test("summary keeps the unscoped Daily Target population separate from dashboard KPIs", () => {
  const analysis = [{ call_id: "call-1", front_desk_person_name: "Alex", call_recording_date: "2026-09-22", fd_a_call_summary: "Documented" }];
  const dailyTargetCalls = _buildCallRecordsWithTasks(analysis, [], [], []);
  const summary = computeDashboardSummary({ calls: [], dailyTargetCalls,
    fdAgents: [{ id: "agent-1", name: "Alex", extensions: [] }], dateFrom: "2026-09-22", dateTo: "2026-09-22" });
  assert.equal(summary.dailyCallTargetRows[0].callsMade, 1);
  assert.equal(summary.metrics.totalCalls, 0);
  assert.equal(summary.callKindCounts.total, 1);
  assert.ok(!("calls" in summary));
  assert.ok(!JSON.stringify(summary).includes("Documented"));
});

test("Call Log Status counts the same calls as the Total Calls and Voice Mails cards", () => {
  const day = "2026-09-25";
  const analysis = [
    { call_id: "c1", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "Open" },
    { call_id: "c2", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "Success - PC" },
    { call_id: "c3", front_desk_person_name: "Priyanka", call_recording_date: day, fd_a_call_outcome: "Voicemail left" },
  ];
  // Only c1 is inside the Dashboard-scoped population; all three are on the
  // Calls Records page, which is what the KPI cards count.
  const calls = _buildCallRecordsWithTasks(analysis.slice(0, 1), [], [], []);
  const dailyTargetCalls = _buildCallRecordsWithTasks(analysis, [], [], []);
  const summary = computeDashboardSummary({ calls, dailyTargetCalls,
    fdAgents: [{ id: "a1", name: "Priyanka", extensions: [] }],
    dateFrom: day, dateTo: day, selectedAgent: "Priyanka" });
  const row = summary.callLogStatusRows.find((item) => item.date === day);
  assert.equal(summary.callKindCounts.total, 3);
  assert.equal(summary.callKindCounts.voicemail, 1);
  assert.equal(row.total, summary.callKindCounts.total);
  assert.equal(row.vm, summary.callKindCounts.voicemail);
  // c1 (Open) + c3 (no status) are open; c2 is Success - PC.
  assert.equal(row.open, 2);
  assert.equal(row.successPC, 1);
});

test("Call Log Status counts Same client context and initial email followup dynamically", () => {
  const day = "2026-09-25";
  const analysis = [
    ...Array.from({ length: 3 }, (_, i) => ({ call_id: `s${i}`, front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "Same client context" })),
    ...Array.from({ length: 2 }, (_, i) => ({ call_id: `e${i}`, front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "initial email followup" })),
    { call_id: "p1", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "Success - PC" },
  ];
  const calls = _buildCallRecordsWithTasks(analysis, [], [], []);
  const summary = computeDashboardSummary({ calls, dailyTargetCalls: calls,
    fdAgents: [{ id: "a1", name: "Priyanka", extensions: [] }], dateFrom: day, dateTo: day, selectedAgent: "Priyanka" });
  const row = summary.callLogStatusRows.find((item) => item.date === day);
  assert.equal(row.sameClientContext, 3);
  assert.equal(row.initialEmailFollowup, 2);
  assert.equal(row.successPC, 1);
});

test("Open Leads counts every call that is not Success - PC, Success - GE or Dropped, including blank status", () => {
  const day = "2026-09-26";
  const statuses = ["Open", "", "Success - PC", "Success - GE", "Dropped", "PC Follow Up", "Initial email sent."];
  const analysis = statuses.map((status, i) => ({
    call_id: `o${i}`, front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: status,
  }));
  const calls = _buildCallRecordsWithTasks(analysis, [], [], []);
  const summary = computeDashboardSummary({ calls, dailyTargetCalls: calls,
    fdAgents: [{ id: "a1", name: "Priyanka", extensions: [] }],
    dateFrom: day, dateTo: day, selectedAgent: "Priyanka" });
  const row = summary.callLogStatusRows.find((item) => item.date === day);
  assert.equal(row.open, 4);
});

test("Open cases = every status except Success - PC / Success - GE / Dropped, blank included", () => {
  const statuses = [null, "Open", "PC Follow Up", "Success - GE", "Dropped", "Success - PC", "Initial email sent."];
  const analysis = statuses.map((status, index) => ({
    call_id: `call-${index}`, front_desk_person_name: "Alex", call_recording_date: "2026-10-01",
    fd_a_call_summary: "Documented", fd_m_case_status_change: status,
  }));
  const records = _buildCallRecordsWithTasks(analysis, [], [], []);
  const summary = computeDashboardSummary({ calls: records, dailyTargetCalls: records,
    fdAgents: [{ id: "a1", name: "Alex", extensions: [] }], dateFrom: "2026-10-01", dateTo: "2026-10-01" });
  assert.equal(summary.callLogStatusRows[0].open, 4);
  assert.equal(summary.performanceRows[0].openCases, 4);   // a count, not an average of open days
});

test("Call Log Status fills one count per Case Status Change dropdown option from the saved status only", () => {
  const day = "2026-09-25";
  const analysis = [
    { call_id: "c1", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "Success - PC" },
    { call_id: "c2", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: " dropped " },
    // No Case Status Change chosen: the unrelated Action Status must not become a case status.
    { call_id: "c3", front_desk_person_name: "Priyanka", call_recording_date: day, action_status: "Success - PC" },
    { call_id: "c4", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "Some legacy value" },
    { call_id: "c5", front_desk_person_name: "Meena", call_recording_date: day, fd_m_case_status_change: "Blocker" },
    { call_id: "c6", front_desk_person_name: "Meena", call_recording_date: day, fd_m_case_status_change: " wrong number DIALED " },
  ];
  const dailyTargetCalls = _buildCallRecordsWithTasks(analysis, [], [], []);
  const options = ["Open", "Client Side Delay", "Blocker", "Dropped", "Same Client Context", "Wrong Number Dialed",
    "Initial email sent (Fee Details shared)", "Initial Email Follow Up", "Fee details shared & FU", "PC link shared", "PC Follow Up", "Success PC", "Success GE"];
  const everyone = computeDashboardSummary({ calls: [], dailyTargetCalls, fdAgents: [], dateFrom: day, dateTo: day, selectedAgent: "all" })
    .callLogStatusRows.find((item) => item.date === day);
  assert.deepEqual(Object.keys(everyone.statusCounts), options);
  assert.equal(everyone.statusCounts.Open, 0);                   // nobody is set to Open; blank (c3) and others are not "Open"
  assert.equal(everyone.statusCounts["Success PC"], 1);          // c1 (stored as "Success - PC": counted in the new column), not c3's Action Status
  assert.equal(everyone.statusCounts.Dropped, 1);                // spacing/case ignored
  assert.equal(everyone.statusCounts.Blocker, 1);
  assert.equal(everyone.noStatus, 1);                            // c3
  assert.equal(everyone.otherStatus, 1);                         // c4 is not a dropdown option: reported, not forced into a column
  assert.equal(everyone.statusCounts["Wrong Number Dialed"], 1);   // c6: its own column, counted once, not also under Dropped
  assert.equal(everyone.statusCounts.Dropped, 1);
  assert.equal(everyone.total, 6);
  // Agent filter narrows the same counts.
  const meena = computeDashboardSummary({ calls: [], dailyTargetCalls, fdAgents: [], dateFrom: day, dateTo: day, selectedAgent: "Meena" })
    .callLogStatusRows.find((item) => item.date === day);
  assert.equal(meena.total, 2);
  assert.equal(meena.statusCounts.Blocker, 1);
  assert.equal(meena.statusCounts["Wrong Number Dialed"], 1);
  assert.equal(meena.statusCounts["Success PC"], 0);
});

test("the Open column counts only calls whose Case Status Change is set to Open", () => {
  const day = "2026-09-25";
  const analysis = [
    { call_id: "c1", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "Open" },
    { call_id: "c2", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: " open " },
    { call_id: "c3", front_desk_person_name: "Priyanka", call_recording_date: day },
    { call_id: "c4", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "Blocker" },
    { call_id: "c5", front_desk_person_name: "Priyanka", call_recording_date: day, fd_m_case_status_change: "Success - GE" },
  ];
  const dailyTargetCalls = _buildCallRecordsWithTasks(analysis, [], [], []);
  const row = computeDashboardSummary({ calls: [], dailyTargetCalls, fdAgents: [], dateFrom: day, dateTo: day, selectedAgent: "all" })
    .callLogStatusRows.find((item) => item.date === day);
  assert.equal(row.statusCounts.Open, 2);      // c1 and c2 only
  assert.equal(row.noStatus, 1);               // c3 sits in no status column, only in the total
  assert.equal(row.total, 5);
  assert.equal(row.open, 4);                   // the dashboard's broader open field (c1-c4: not closed) is unchanged
});
