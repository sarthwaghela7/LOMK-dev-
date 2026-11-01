import test from "node:test";
import assert from "node:assert/strict";

import { TEAM_LEAD_COLUMNS, canSubmitRow, isSubmitted, normalizeAmount, submitLabel } from "./followUps.js";

test("the Team Lead Fills columns are the eight requested ones, in order", () => {
  assert.deepEqual(TEAM_LEAD_COLUMNS.map((c) => c.label), [
    "Business Status", "Paid Amount", "Retainer Follow Up Required?", "Retainer Owner",
    "Retainer Action Plan", "Retainer Action Status", "Retainer Status", "Retainer Verification"]);
  assert.deepEqual(TEAM_LEAD_COLUMNS.find((c) => c.key === "business_status").options, ["PC Only", "PC + Retainer"]);
  assert.deepEqual(TEAM_LEAD_COLUMNS.find((c) => c.key === "retainer_action_status").options, ["Pending", "Completed", "Dropped"]);
  assert.deepEqual(TEAM_LEAD_COLUMNS.find((c) => c.key === "retainer_status").options, ["Success", "Lost"]);
});

test("amounts are normalised or rejected", () => {
  assert.equal(normalizeAmount("1,250.5"), "1250.50");
  assert.equal(normalizeAmount("$12"), "12.00");
  assert.equal(normalizeAmount(""), "");
  for (const bad of ["abc", "-5", "1.234", "1e5", "999999999"]) assert.equal(normalizeAmount(bad), null, bad);
});

test("Submit needs an owner and turns into Submitted", () => {
  assert.equal(canSubmitRow({}), false);
  assert.equal(canSubmitRow({ retainer_owner_id: "a" }), true);
  assert.equal(canSubmitRow({ retainer_owner_id: "a", submitted: true }), false);
  assert.equal(isSubmitted({ submitted_at: "2026-10-25T10:00:00Z" }), true);
  assert.equal(submitLabel({ submitted: true }, false), "Submitted");
  assert.equal(submitLabel({}, true), "Submitting...");
  assert.equal(submitLabel({}, false), "Submit");
});

import { CALL_DETAIL_LABELS, CALL_GROUP_LABELS, FOLLOW_UP_STAGE_GROUPS, isSuccessPcStatus, stageCellText, stageSource, successPcRows } from "./followUps.js";
import { STAGE1_COLUMNS, STAGE2_COLUMNS, STAGE3_COLUMNS, STAGE4_COLUMNS } from "./callStageColumns.js";

test("Follow Ups repeats Stage 1 to Stage 4 of the Calls table, in order, from the same definitions", () => {
  assert.deepEqual(FOLLOW_UP_STAGE_GROUPS.map((group) => group.columns), [STAGE1_COLUMNS, STAGE2_COLUMNS, STAGE3_COLUMNS, STAGE4_COLUMNS]);
  const keys = FOLLOW_UP_STAGE_GROUPS.flatMap((group) => group.columns.map((column) => column.key));
  assert.equal(keys.length, 21);
  assert.equal(new Set(keys).size, keys.length);
  assert.ok(keys.includes("fd_m_case_status_change") && keys.includes("fd_m_google_review") && keys.includes("fd_a_client_name"));
  assert.deepEqual(CALL_DETAIL_LABELS, ["Call_ID", "Client ID", "Lead ID", "Client Phone Number", "Agent", "Ext Number", "Direction", "Call Type", "Duration", "Manual Data Score"]);
  assert.deepEqual(CALL_GROUP_LABELS, ["Copy / Repeated Logs", "#", "Date", "Enquiry Open Duration", "Timestamp", "Transcript", "Audio"]);
});

test("only Success PC counts, in either spelling", () => {
  assert.equal(isSuccessPcStatus("Success PC"), true);
  assert.equal(isSuccessPcStatus("  success - pc "), true);
  for (const other of ["Success GE", "Open", "PC Follow Up", "", null, undefined]) assert.equal(isSuccessPcStatus(other), false);
});

test("rows whose loaded status is not Success PC are hidden; unknown stage data keeps the server's row", () => {
  const rows = [{ call_id: "a" }, { call_id: "b" }, { call_id: "c" }];
  const shown = successPcRows(rows, { a: { fd_m_case_status_change: "Success PC" }, b: { fd_m_case_status_change: "Open" } });
  assert.deepEqual(shown.map((row) => row.call_id), ["a", "c"]);
});

test("stage cells show dates as MM-DD-YYYY, follow-up dates on one line, and nothing for empty values", () => {
  assert.equal(stageCellText({ fd_m_action_due_date: "2026-10-23T00:00:00" }, "fd_m_action_due_date"), "10-23-2026");
  assert.equal(stageCellText({ fd_m_follow_up_dates: "10-24-2026\n 10-26-2026 \n" }, "fd_m_follow_up_dates"), "10-24-2026, 10-26-2026");
  assert.equal(stageCellText({ fd_a_case_type: "  H-1B " }, "fd_a_case_type"), "H-1B");
  for (const value of [null, undefined, "", "   "]) assert.equal(stageCellText({ x: value }, "x"), "");
  assert.equal(stageCellText(undefined, "x"), "");
});

test("the stage source prefers the Calls-page record and falls back to what the Follow Ups row knows", () => {
  const row = { client_name: "Row Client", case_type: "Row Type", call_summary: "Row summary" };
  assert.equal(stageSource(row, undefined, undefined).fd_a_client_name, "Row Client");
  const merged = stageSource(row, { fd_a_client_name: "Analysis Client" }, { fd_a_call_summary: "Full summary" });
  assert.equal(merged.fd_a_client_name, "Analysis Client");
  assert.equal(merged.fd_a_call_summary, "Full summary");
  assert.equal(merged.fd_a_case_type, "Row Type");
});

test("the refresh signature changes when a call changes owner or submission, and not with row order", async () => {
  const { followUpSignature } = await import("./followUps.js");
  const a = { call_id: "1", retainer_owner_id: "A", submitted_at: "t", retainer_action_status: "Pending" };
  const b = { call_id: "2", retainer_owner_id: "B", submitted_at: "", retainer_action_status: "" };
  assert.equal(followUpSignature([a, b]), followUpSignature([b, a]));
  assert.notEqual(followUpSignature([a, b]), followUpSignature([{ ...a, retainer_owner_id: "B" }, b]));
  assert.notEqual(followUpSignature([a, b]), followUpSignature([a, { ...b, submitted_at: "t2" }]));
});
