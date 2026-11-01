// Follow Ups tab: the Team Lead Fills columns, their allowed values, and small pure helpers. The server enforces the same
// values (AutoFlint_Backend/services/follow_ups.py); this is only what the page offers.
import { STAGE1_COLUMNS, STAGE2_COLUMNS, STAGE3_COLUMNS, STAGE4_COLUMNS } from "./callStageColumns.js";

export const BUSINESS_STATUSES = ["PC Only", "PC + Retainer"];
export const YES_NO = ["Yes", "No"];
export const ACTION_STATUSES = ["Pending", "Completed", "Dropped"];
export const RETAINER_STATUSES = ["Success", "Lost"];

// kind: select (fixed values) | owner (the agents) | amount | text (opens a popup). `key` is the API field name.
export const TEAM_LEAD_COLUMNS = [
  { key: "business_status", label: "Business Status", kind: "select", options: BUSINESS_STATUSES },
  { key: "paid_amount", label: "Paid Amount", kind: "amount" },
  { key: "retainer_follow_up_required", label: "Retainer Follow Up Required?", kind: "select", options: YES_NO },
  { key: "retainer_owner_id", label: "Retainer Owner", kind: "owner" },
  { key: "retainer_action_plan", label: "Retainer Action Plan", kind: "text" },
  { key: "retainer_action_status", label: "Retainer Action Status", kind: "select", options: ACTION_STATUSES },
  { key: "retainer_status", label: "Retainer Status", kind: "select", options: RETAINER_STATUSES },
  { key: "retainer_verification", label: "Retainer Verification", kind: "text" },
];

// "1250.5" / "1,250.50" / "$12" -> "1250.50"; "" -> ""; anything not a non-negative amount -> null (not valid).
export function normalizeAmount(value) {
  const text = String(value ?? "").replace(/[$,\s]/g, "");
  if (text === "") return "";
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null;
  const number = Number(text);
  return Number.isFinite(number) && number <= 99999999.99 ? number.toFixed(2) : null;
}

export const isSubmitted = (row) => Boolean(row?.submitted || row?.submitted_at);
export const canSubmitRow = (row) => Boolean(row?.retainer_owner_id) && !isSubmitted(row);
export const submitLabel = (row, busy) => (busy ? "Submitting..." : isSubmitted(row) ? "Submitted" : "Submit");

// The Call Records columns the Follow Ups table repeats, Stage 1 to Stage 4 in the Calls page's own order and from its own
// definitions (data/callStageColumns.js), so the two tables can never disagree.
export const FOLLOW_UP_STAGE_GROUPS = [
  { key: "stage1", label: "Stage 1 \u00b7 AI Intake", groupClass: "group-stage1", headerClass: "stage1-column-header", columns: STAGE1_COLUMNS },
  { key: "stage2", label: "Stage 2 \u00b7 Action Tracking", groupClass: "group-stage2", headerClass: "stage2-column-header", columns: STAGE2_COLUMNS },
  { key: "stage3", label: "Stage 3 \u00b7 Case Status", groupClass: "group-stage3", headerClass: "stage3-column-header", columns: STAGE3_COLUMNS },
  { key: "stage4", label: "Stage 4 \u00b7 Google Review", groupClass: "group-stage4", headerClass: "stage4-column-header", columns: STAGE4_COLUMNS },
];
// The identifying columns that lead each row (what the old table showed before the stages).
// The Call details group of the Calls table, in its order.
export const CALL_DETAIL_LABELS = ["Call_ID", "Client ID", "Lead ID", "Client Phone Number", "Agent", "Ext Number", "Direction", "Call Type", "Duration", "Manual Data Score"];
// The frozen CALL columns of the Calls table, in its order (the first one is the Copy / Repeated Logs cell).
export const CALL_GROUP_LABELS = ["Copy / Repeated Logs", "#", "Date", "Enquiry Open Duration", "Timestamp", "Transcript", "Audio"];

// Follow Ups lists only calls whose Case Status Change is Success PC (the server enforces it; this is the same rule on the page).
export const isSuccessPcStatus = (value) => ["success pc", "success - pc"].includes(String(value ?? "").trim().replace(/\s+/g, " ").toLowerCase());

const DATE_KEYS = new Set(["fd_m_action_due_date", "fd_m_date_entry"]);
/** The text one Stage 1-4 cell shows for a call ("" when there is nothing), formatted like the Calls table. */
export function stageCellText(source, key) {
  const raw = source?.[key];
  if (raw === null || raw === undefined) return "";
  const text = String(raw).trim();
  if (!text) return "";
  if (DATE_KEYS.has(key) && /^\d{4}-\d{2}-\d{2}/.test(text)) {
    const [year, month, day] = text.slice(0, 10).split("-");
    return `${month}-${day}-${year}`;
  }
  if (key === "fd_m_follow_up_dates") return text.split("\n").map((entry) => entry.trim()).filter(Boolean).join(", ");
  return text;
}

/** The Stage 1-4 values of a Follow Ups row: the Calls-page analysis record, its summary text, and what the Follow Ups row itself knows. */
export function stageSource(row, analysis, summary) {
  return {
    fd_a_client_name: row?.client_name, fd_a_case_type: row?.case_type, fd_a_call_summary: row?.call_summary,
    ...(analysis || {}), ...(summary || {}),
  };
}

/** Rows to show: every row, except one whose loaded Stage 3 status is known and is not Success PC. */
export const successPcRows = (rows, analysisById) => rows.filter((row) => {
  const analysis = analysisById[String(row.call_id)];
  return !analysis || isSuccessPcStatus(analysis.fd_m_case_status_change);
});

// What a background refresh compares: which calls are listed and who owns / has submitted / finished each. Equal signatures mean the
// page already shows the server's picture, so nothing is reloaded.
export const followUpSignature = (items) => (items || [])
  .map((row) => [row.call_id, row.retainer_owner_id || "", row.submitted_at || "", row.retainer_action_status || ""].join("|"))
  .sort().join("\n");
