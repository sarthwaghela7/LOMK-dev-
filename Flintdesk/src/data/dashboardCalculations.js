// Dashboard calculations are centralized so every KPI, table and chart uses the
// same selected-date dataset and the same business rules. This file intentionally
// holds the derived metrics, while the Dashboard page stays focused on rendering.

import { calculateLeadScore } from "./priorityEngine.js";

// Paid lead sources — kept in sync with leadSource.js LEAD_SOURCE_GROUPS "Paid Leads" group.
// Matching is done here (case-insensitive keyword check) so the KPI card always
// agrees with the LEADS table columns already shown in the Dashboard.

const EMPTY_VALUES = new Set(["", "n/a", "na", "none", "null", "undefined", "unknown"]);

const CALL_OUTCOME_CATEGORIES = [
  "Connected - Resolved",
  "Connected - Follow up needed",
  "Connected - Redirected",
  "Voicemail Left",
  "Voicemail Full (Not Available)",
  "Call Back Scheduled",
];

const CALL_DIRECTION_CATEGORIES = [
  "Direct Inbound",
  "Direct Outbound",
  "Brevo Outbound",
  "Brevo Follow Up",
  "Missed Call Outbound",
];

const CASE_STATUS_CATEGORIES = [
  "Open",
  "Initial email sent.",
  "Success - PC",
  "Success - GE",
  "PC Follow Up",
  "PC shared and lost",
  "Fee details shared & FU",
  "Client Side delay",
  "Moved to next date",
  "Blocker",
  "Dropped",
  "Same client context",
  "initial email followup",
];

const URGENCY_FLAG_CATEGORIES = [
  "None",
  "Urgent - RFE",
  "Urgent - NOID",
  "Grace Period",
  "Visa Expiry",
  "Court Date",
  "Filing Deadline",
  "Client Distressed",
];

const CLIENT_TYPE_CATEGORIES = [
  "New Client - Employer",
  "Existing client - Organization",
  "Existing client - Individual",
  "New Client - Individual",
  "Voicemail Call",
];

// Stage 4 review field (fd_m_google_review), same categories used in the
// Calls table's Stage 4 column. "Submitted" is what counts as a review
// actually received from the client; the others are in-progress/not-applicable.
const GOOGLE_REVIEW_CATEGORIES = ["Pending", "Requested", "Submitted", "Not Applicable"];

function normalizeText(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim().replace(/\s+/g, " ");
}

function normalizeKey(value) {
  return normalizeText(value).toLowerCase();
}

function isMeaningful(value) {
  const text = normalizeKey(value);
  if (!text) return false;
  return !EMPTY_VALUES.has(text);
}

function getFirstMeaningfulValue(row, fields) {
  for (const field of fields) {
    const value = row?.[field] ?? row?.raw?.[field];
    if (isMeaningful(value)) return value;
  }
  return "";
}

function safeNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(String(value).replace(/%/g, ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function durationToSeconds(value) {
  if (value === null || value === undefined || value === "") return 0;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const text = String(value).trim();
  if (/^\d+(?::\d{1,2}){1,2}$/.test(text)) {
    const parts = text.split(":").map(Number);
    return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
  }
  const numeric = Number(text.replace(/[^\d.]/g, ""));
  return Number.isFinite(numeric) ? numeric : 0;
}

export function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.round(Number(totalSeconds) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours ? `${hours}h ${minutes}m` : `${minutes}m ${remainder}s`;
}

function buildFixedBreakdown(rows, categories, fieldNames, normalizer) {
  const counts = new Map(categories.map((category) => [category, 0]));

  rows.forEach((row) => {
    const rawValue = getFirstMeaningfulValue(row, fieldNames);
    const mappedLabel = normalizer(rawValue);
    if (!mappedLabel) return;
    if (!counts.has(mappedLabel)) return;
    counts.set(mappedLabel, (counts.get(mappedLabel) || 0) + 1);
  });

  const total = [...counts.values()].reduce((sum, count) => sum + count, 0);
  const items = categories.map((label) => {
    const count = counts.get(label) || 0;
    return {
      label,
      count,
      percentage: total === 0 ? 0 : (count / total) * 100,
    };
  });

  return { total, items };
}

function normalizeCallOutcome(value) {
  const normalized = normalizeKey(value);
  if (!normalized) return "";

  if (normalized.includes("resolved") && normalized.includes("connected")) return "Connected - Resolved";
  if ((normalized.includes("follow") && normalized.includes("needed")) || (normalized.includes("follow") && normalized.includes("connected"))) return "Connected - Follow up needed";
  if (normalized.includes("redirected")) return "Connected - Redirected";
  if (normalized.includes("voicemail full") || normalized.includes("not available")) return "Voicemail Full (Not Available)";
  if (normalized.includes("voicemail") || normalized.includes("voicemail left")) return "Voicemail Left";
  if (normalized.includes("call back") || normalized.includes("scheduled")) return "Call Back Scheduled";

  return "";
}

function normalizeCallDirection(value) {
  const normalized = normalizeKey(value);
  if (!normalized) return "";

  if (normalized === "inbound" || normalized.includes("direct inbound")) return "Direct Inbound";
  if (normalized === "outbound" || normalized.includes("direct outbound")) return "Direct Outbound";
  if (normalized.includes("brevo outbound")) return "Brevo Outbound";
  if (normalized.includes("brevo follow up") || normalized.includes("brevo follow-up")) return "Brevo Follow Up";
  if (normalized.includes("missed") && normalized.includes("outbound")) return "Missed Call Outbound";
  if (normalized.includes("missed call")) return "Missed Call Outbound";

  return "";
}

export function isInboundCall(row) {
  return normalizeCallDirection(getFirstMeaningfulValue(row, ["call_direction", "call_type", "direction"])) === "Direct Inbound";
}

export function isOutboundCall(row) {
  const direction = normalizeCallDirection(getFirstMeaningfulValue(row, ["call_direction", "call_type", "direction"]));
  return ["Direct Outbound", "Brevo Outbound", "Brevo Follow Up", "Missed Call Outbound"].includes(direction);
}

export function isPaidLead(row) {
  const opportunity = normalizeKey(getFirstMeaningfulValue(row, ["business_opportunity"]));
  const status = normalizeCaseStatus(getFirstMeaningfulValue(row, ["fd_m_case_status_change", "action_status", "case_status"]));
  return opportunity === "yes" && status === "Success - PC";
}

function normalizedIdentifier(value) {
  return normalizeText(value).toLowerCase();
}

/**
 * Return one dashboard call for each unique client represented by Leads Page S1.
 *
 * The canonical S1 population comes from GET /api/flintdesk/leads: the backend
 * selects call_analysis_results rows whose fd_m_leads JSON is non-null and links
 * an S1 record to a call through fd_m_leads.matched_call_id. client_id is the
 * permanent client-lifecycle identifier on calls. Rows without a client_id are
 * excluded because they cannot be safely deduplicated as leads.
 */
export function getUniqueLeadsFromS1Calls(rows = [], s1Leads = [], predicate = null) {
  const safeRows = Array.isArray(rows) ? rows.filter(Boolean) : [];
  const safeLeads = Array.isArray(s1Leads) ? s1Leads.filter(Boolean) : [];
  const s1CallIds = new Set();

  safeLeads.forEach((lead) => {
    const matchedCallId = lead?.record?.matched_call_id || lead?.call_details?.call_id;
    const normalized = normalizedIdentifier(matchedCallId);
    if (normalized) s1CallIds.add(normalized);
  });

  const uniqueClients = new Map();
  safeRows.forEach((row) => {
    const callId = normalizedIdentifier(row?.callId ?? row?.call_id ?? row?.raw?.call_id);
    if (!callId || !s1CallIds.has(callId)) return;
    if (predicate && !predicate(row)) return;

    const clientId = normalizedIdentifier(row?.clientId ?? row?.client_id ?? row?.raw?.client_id);
    if (!clientId || uniqueClients.has(clientId)) return;
    uniqueClients.set(clientId, row);
  });

  return [...uniqueClients.values()];
}

function normalizeCaseStatus(value) {
  const normalized = normalizeKey(value);
  if (!normalized) return "";

  const aliases = {
    "Open": ["open"],
    "Initial email sent.": ["initial email sent.", "initial email sent"],
    "Success - PC": ["success - pc", "success pc", "pc success"],
    "Success - GE": ["success - ge", "success ge", "ge success"],
    "PC Follow Up": ["pc follow up", "pc follow-up"],
    "PC shared and lost": ["pc shared and lost"],
    "Fee details shared & FU": ["fee details shared & fu", "fee details shared and fu", "fee details shared & fu"],
    "Client Side delay": ["client side delay", "client-side delay"],
    // The Calls page's actual stored value is "Move to next date" -- matching
    // only "moved to next date" here never matched anything (see the same
    // fix already made to Dashboard.jsx's CASE_STATUS_KEYS).
    "Moved to next date": ["moved to next date", "move to next date"],
    "Blocker": ["blocker"],
    "Dropped": ["dropped"],
    "Same client context": ["same client context"],
    "initial email followup": ["initial email followup"],
  };

  const match = Object.entries(aliases).find(([, list]) => list.includes(normalized));
  return match ? match[0] : "";
}

function normalizeUrgency(value) {
  const normalized = normalizeKey(value);
  if (!normalized) return "";

  const aliases = {
    "None": ["none", "no urgency", "n/a"],
    "Urgent - RFE": ["urgent - rfe", "rfe", "urgent rfe"],
    "Urgent - NOID": ["urgent - noid", "noid", "urgent noid"],
    "Grace Period": ["grace period", "grace-period"],
    "Visa Expiry": ["visa expiry", "visa-expiry"],
    "Court Date": ["court date", "court-date"],
    "Filing Deadline": ["filing deadline", "filing-deadline"],
    "Client Distressed": ["client distressed", "client-distressed"],
  };

  const match = Object.entries(aliases).find(([, list]) => list.includes(normalized));
  return match ? match[0] : "";
}

function normalizeClientType(value) {
  const normalized = normalizeKey(value);
  if (!normalized) return "";

  const aliases = {
    "New Client - Employer": ["new client - employer", "new client employer"],
    "Existing client - Organization": ["existing client - organization", "existing client organization"],
    "Existing client - Individual": ["existing client - individual", "existing client individual"],
    "New Client - Individual": ["new client - individual", "new client individual"],
    "Voicemail Call": ["voicemail call", "voicemail"],
  };

  const match = Object.entries(aliases).find(([, list]) => list.includes(normalized));
  return match ? match[0] : "";
}

function normalizeGoogleReview(value) {
  const normalized = normalizeKey(value);
  if (!normalized) return "";

  const aliases = {
    "Pending": ["pending"],
    "Requested": ["requested"],
    "Submitted": ["submitted"],
    "Not Applicable": ["not applicable", "n/a", "na"],
  };

  const match = Object.entries(aliases).find(([, list]) => list.includes(normalized));
  return match ? match[0] : "";
}

function calculateCallCompleteness(row) {
  // Completeness is based on the core FlintDesk fields that provide the call
  // metadata and review context. The same fields are used for every row so the
  // average remains consistent across the full selected date range.
  const fieldGroups = [
    ["fd_a_client_name", "client_name"],
    ["fd_a_case_type", "case_type"],
    ["call_type"],
    ["call_duration"],
    ["front_desk_person_name", "fd_a_case_worker_assigned", "agent"],
    ["fd_a_client_type", "client_type"],
    ["fd_a_urgency_flag", "urgency"],
    ["fd_a_call_outcome", "call_outcome", "disposition"],
    ["fd_m_case_status_change", "action_status", "case_status"],
    ["fd_m_self_action_item"],
    ["fd_m_action_due_date"],
    ["fd_m_manual_action_status"],
    ["fd_m_attachment"],
    ["fd_m_google_review"],
  ];
  const filled = fieldGroups.filter((fields) => getFirstMeaningfulValue(row, fields)).length;
  return fieldGroups.length ? (filled / fieldGroups.length) * 100 : 0;
}

function calculateLegacyDataQualityScore(row) {
  // Return one consistent 0–5 rating. Backend scores may be stored either as
  // a five-point rating or as a 0–100 percentage; the fallback is derived
  // from completeness and structured-field coverage.
  const rawScore = null;

  const numericScore = safeNumber(rawScore);
  if (numericScore !== null) {
    // All four raw score aliases are canonical 0-10 scores. Normalize to the
    // dashboard's 0-5 star scale without changing its existing clamping.
    return Math.min(5, Math.max(0, numericScore / 2));
  }

  const completeness = calculateCallCompleteness(row);
  const structuredFields = [
    "fd_a_client_type",
    "fd_a_urgency_flag",
    "fd_a_call_outcome",
    "fd_m_case_status_change",
    "fd_m_manual_action_status",
  ].filter((field) => isMeaningful(row?.[field])).length;
  const structuredCoverage = (structuredFields / 5) * 100;

  return Math.min(5, Math.max(0, ((completeness * 0.7) + (structuredCoverage * 0.3)) / 20));
}

function calculateDataQualityScore(row) {
  // DB-backed completeness score: populated canonical fields / 14 fields × 5.
  // No LLM score alias or heuristic fallback is mixed into this metric.
  return Math.min(5, Math.max(0, calculateCallCompleteness(row) / 20));
}

function averageOf(rows, callback) {
  const values = rows
    .map((row) => callback(row))
    .filter((value) => Number.isFinite(value));

  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function calculateDashboardMetrics(rows = [], startDate = null, endDate = null, s1Leads = []) {
  const safeRows = Array.isArray(rows) ? rows.filter(Boolean) : [];

  // Calculate dayCount for daily averaging
  let dayCount = 1;
  if (startDate && endDate) {
    const start = new Date(`${startDate}T00:00:00`);
    const end = new Date(`${endDate}T00:00:00`);
    if (!Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime()) && start <= end) {
      dayCount = Math.max(1, Math.round((end - start) / 86400000) + 1);
    }
  } else {
    const dates = new Set();
    safeRows.forEach((row) => {
      const rawDate = row?.raw?.call_recording_date_normalized || row?.raw?.call_recording_date || row?.raw?.call_date || row?.date || row?.call_recording_date;
      if (rawDate) {
        const dateStr = String(rawDate).slice(0, 10);
        if (/^\d{4}-\d{2}-\d{2}/.test(dateStr)) {
          dates.add(dateStr);
        }
      }
    });
    dayCount = Math.max(1, dates.size);
  }

  const totalCalls = safeRows.length;
  // Leads are unique clients, not raw calls. Both directions are eligible, but
  // only calls linked to the canonical Leads Page S1 population are included.
  const uniqueS1Leads = getUniqueLeadsFromS1Calls(safeRows, s1Leads);
  const leads = uniqueS1Leads.length;
  // Paid Leads counts unique clients (by client_id), not raw calls, restricted
  // to Leads Page S1 traffic, inbound+outbound.
  const paidLeads = getUniqueLeadsFromS1Calls(safeRows, s1Leads, isPaidLead).length;
  const outboundCalls = safeRows.filter(isOutboundCall).length;
  const leadsPerDay = Number((leads / dayCount).toFixed(1));
  // Per the requested business rule, Leads Called uses both inbound and
  // outbound S1 calls and the same range-level client deduplication as Leads
  // Per Day. The two KPIs therefore intentionally measure the same population.
  const leadsCalledPerDay = Number((leads / dayCount).toFixed(1));
  const completedCalls = safeRows.filter((row) => {
    if (getFirstMeaningfulValue(row, ["case_closing_completed"]) === true) return true;
    const value = normalizeKey(getFirstMeaningfulValue(row, [
      "action_status",
      "status",
      "fd_m_manual_action_status",
      "fd_m_case_status_change",
    ]));
    return ["success - pc", "success - ge", "completed", "resolved", "closed"].includes(value);
  }).length;

  const resolvedLeads = safeRows.filter((row) => {
    const status = normalizeCaseStatus(getFirstMeaningfulValue(row, ["fd_m_case_status_change", "action_status", "case_status"]));
    return isInboundCall(row) && ["Success - PC", "Success - GE"].includes(status);
  }).length;
  const closedLoopRate = leads ? Number(((resolvedLeads / leads) * 100).toFixed(1)) : 0;

  const urgentCalls = safeRows.filter((row) => {
    const value = normalizeKey(getFirstMeaningfulValue(row, [
      "fd_a_urgency_flag",
      "urgency",
    ]));
    if (!value || ["none", "unknown"].includes(value)) return false;
    if (value.includes("urgent")) return true;
    return [
      "grace period",
      "visa expiry",
      "court date",
      "filing deadline",
      "client distressed",
    ].includes(value);
  }).length;

  const averageCompletenessScore = averageOf(safeRows, calculateCallCompleteness);
  const averageDataQualityScore = averageOf(safeRows, calculateDataQualityScore);
  const averageLeadScore = averageOf(
    safeRows,
    (row) =>
      safeNumber(getFirstMeaningfulValue(row, ["lead_score"])) ??
      calculateLeadScore(row),
  );
  const averageBusinessOpportunityScore = averageOf(safeRows, (row) => safeNumber(getFirstMeaningfulValue(row, ["bos_score"])));
  const totalDurationSeconds = safeRows.reduce(
    (total, row) => total + durationToSeconds(row?.billsec ?? row?.raw?.billsec),
    0,
  );

  return {
    totalCalls,
    leads,
    paidLeads,
    outboundCalls,
    resolvedLeads,
    completedCalls,
    urgentCalls,
    leadsPerDay,
    leadsCalledPerDay,
    closedLoopRate,
    averageCompletenessScore,
    averageDataQualityScore,
    averageLeadScore,
    averageBusinessOpportunityScore,
    totalDurationSeconds,
    callOutcomeBreakdown: buildFixedBreakdown(
      safeRows,
      CALL_OUTCOME_CATEGORIES,
      ["fd_a_call_outcome", "call_outcome", "disposition"],
      normalizeCallOutcome,
    ),
    caseStatusBreakdown: buildFixedBreakdown(
      safeRows,
      CASE_STATUS_CATEGORIES,
      ["fd_m_case_status_change", "action_status", "case_status"],
      normalizeCaseStatus,
    ),
    urgencyFlagBreakdown: buildFixedBreakdown(
      safeRows,
      URGENCY_FLAG_CATEGORIES,
      ["fd_a_urgency_flag", "urgency"],
      normalizeUrgency,
    ),
    callDirectionBreakdown: buildFixedBreakdown(
      safeRows,
      CALL_DIRECTION_CATEGORIES,
      ["call_direction", "call_type", "direction"],
      normalizeCallDirection,
    ),
    clientTypeBreakdown: buildFixedBreakdown(
      safeRows,
      CLIENT_TYPE_CATEGORIES,
      ["fd_a_client_type", "client_type"],
      normalizeClientType,
    ),
    googleReviewBreakdown: buildFixedBreakdown(
      safeRows,
      GOOGLE_REVIEW_CATEGORIES,
      ["fd_m_google_review", "google_review"],
      normalizeGoogleReview,
    ),
  };
}

export function formatPercentage(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

// Sample performance figures shown only when the selected range has fewer than
// two distinct agents, since a real per-agent comparison needs multiple agents.
export const MOCK_AGENT_PERFORMANCE = [
  { label: "Ayesha Khan", count: 42, performance: 88 },
  { label: "Rohan Mehta", count: 37, performance: 76 },
  { label: "Priya Nair", count: 29, performance: 91 },
  { label: "Daniel Cruz", count: 24, performance: 68 },
];

export function getAgentNames(rows = []) {
  const safeRows = Array.isArray(rows) ? rows : [];
  const names = new Set();

  safeRows.forEach((row) => {
    const value = getFirstMeaningfulValue(row, ["front_desk_person_name", "fd_a_case_worker_assigned", "agent", "agent_name"]);
    if (value) names.add(normalizeText(value));
  });

  return [...names].sort((a, b) => a.localeCompare(b));
}

export function buildCallVolumeSeries(rows = []) {
  const safeRows = Array.isArray(rows) ? rows.filter(Boolean) : [];
  const counts = new Map();

  safeRows.forEach((row) => {
    const rawDate = getFirstMeaningfulValue(row, [
      "call_recording_date_normalized",
      "call_recording_date",
      "call_date",
    ]);
    const label = rawDate ? String(rawDate).slice(0, 10) : "Unknown";
    counts.set(label, (counts.get(label) || 0) + 1);
  });

  return [...counts.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([label, count]) => ({ label, count }));
}

export function buildAgentPerformance(rows = []) {
  const safeRows = Array.isArray(rows) ? rows.filter(Boolean) : [];
  const totals = new Map();
  const successes = new Map();

  safeRows.forEach((row) => {
    const agent = getFirstMeaningfulValue(row, ["front_desk_person_name", "fd_a_case_worker_assigned", "agent", "agent_name"]) || "Unassigned";
    totals.set(agent, (totals.get(agent) || 0) + 1);

    const statusRaw = getFirstMeaningfulValue(row, ["fd_m_case_status_change", "action_status", "case_status"]);
    const status = normalizeCaseStatus(statusRaw);
    if (status === "Success - PC" || status === "Success - GE") {
      successes.set(agent, (successes.get(agent) || 0) + 1);
    }
  });

  return [...totals.entries()]
    .map(([label, count]) => ({
      label,
      count,
      performance: count ? Math.round(((successes.get(label) || 0) / count) * 100) : 0,
    }))
    .sort((a, b) => b.count - a.count);
}
