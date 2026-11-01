import { buildCaseTypeEnquiryRows, computeDashboardSummary } from "../src/data/dashboardSummary.js";
import { _buildCallRecordsWithTasks, buildDashboardCallRecords } from "../src/data/callRecordProjection.js";

let input = "";
for await (const chunk of process.stdin) {
  input += chunk;
  if (input.length > 32 * 1024 * 1024) throw new Error("Dashboard input exceeds limit");
}
const payload = JSON.parse(input);
const calls = buildDashboardCallRecords(payload.dashboardAnalysis, payload.dashboardCalls);
// Case Types + EQ page: only these rows are needed, so skip the full summary.
if (payload.mode === "case_types_eq") {
  process.stdout.write(JSON.stringify({ caseTypeEnquiryRows: buildCaseTypeEnquiryRows(calls, payload.dateFrom, payload.dateTo) }));
  process.exit(0);
}
const dailyTargetCalls = _buildCallRecordsWithTasks(payload.analysis, payload.calls, payload.tasks, payload.leads);
const result = computeDashboardSummary({ ...payload, calls, dailyTargetCalls });
process.stdout.write(JSON.stringify({ ...result, dependencySummary: payload.dependencySummary,
  taskSummary: payload.taskSummary, timeDurations: payload.timeDurations }));
