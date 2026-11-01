import { calculateDashboardMetrics, buildCallVolumeSeries, buildAgentPerformance } from "./dashboardCalculations.js";
import { ALIAS_EXTENSION_FOR_LEAD_NUMBER, callMatchesLeadNumber, getLeadNumberExtensions, getAllLeadExtensions, getOutboundLeadNumberOptions } from "./leadNumberFilter.js";
import { calculatePriority } from "./priorityEngine.js";
import { CLIENT_TYPE_OPTIONS, URGENCY_FLAG_OPTIONS, LEAD_SOURCE_DROPDOWN_OPTIONS } from "./callFieldOptions.js";
import { calculateOpenCallAgeCounts } from "./openCallAge.js";
import { isVoicemailCall, countsAsDashboardVoicemail } from "./callKind.js";
import { displayClientType } from "./displayLabels.js";
import { CASE_STATUS_OPTIONS, LEGACY_CASE_STATUS_ALIASES } from "./statusOptions.js";
import { formatDisplayDate } from "./displayDate.js";
const getLocalDateString = (date = new Date()) => date.toLocaleDateString("en-CA");
const CALL_LOG_STATUS_COLUMNS = [
  ["vm", "VM"],
  ["open", "Open"],
  ["initialEmail", "Initial Email"],
  ["successPC", "Success - PC"],
  ["successGE", "Success - GE"],
  ["pcFollowUp", "PC Follow Up"],
  ["pcSharedLost", "PC shared and lost"],
  ["feeDetails", "Fee details shared & FU"],
  ["clientDelay", "Client Side delay"],
  ["movedNextDate", "Moved to next date"],
  ["blocker", "Blocker"],
  ["dropped", "Dropped"],
  ["sameClientContext", "Same client context"],
  ["initialEmailFollowup", "initial email followup"],
];

const WEBSITE_PAID_ADS_COLUMNS = [
  ["received", "# of Leads Received"],
  ["reachedOut", "Leads Reached out"],
  ["vm", "VM"],
  ["open", "Open"],
  ["initialEmail", "Initial Email"],
  ["successPC", "Success - PC"],
  ["successGE", "Success - GE"],
  ["pcFollowUp", "PC Follow Up"],
  ["pcSharedLost", "PC shared and lost"],
  ["feeDetails", "Fee details shared & FU"],
  ["clientDelay", "Client Side delay"],
  ["movedNextDate", "Moved to next date"],
  ["blocker", "Blocker"],
  ["dropped", "Dropped"],
  ["sameClientContext", "Same client context"],
  ["initialEmailFollowup", "initial email followup"],
];

const LEAD_REACHED_OUT_FIELDS = [
  "agent_name",
  "call_direction",
  "call_duration",
  "case_type",
  "case_worker_assigned",
  "urgency_flag",
  "redirect_escalation_to",
  "call_outcome",
  "call_summary",
  "enquiry_question",
];

function getLeadDate(item) {
  const value =
    item?.record?.date_received || item?.call_details?.date_received;
  return /^\d{4}-\d{2}-\d{2}/.test(String(value || ""))
    ? String(value).slice(0, 10)
    : "";
}

// Shared with buildCallLogStatusRows below -- both tables bucket the same
// Calls page Stage 3 "Case Status Change" values (src/data/statusOptions.js
// CASE_STATUS_OPTIONS) into the same set of Dashboard columns, so the
// lookup lives in one place instead of two copies drifting apart.
const CASE_STATUS_KEYS = {
  "initial email sent.": "initialEmail",
  "success - pc": "successPC",
  "success - ge": "successGE",
  "pc follow up": "pcFollowUp",
  "pc shared and lost": "pcSharedLost",
  "fee details shared & fu": "feeDetails",
  "client side delay": "clientDelay",
  // CASE_STATUS_OPTIONS' actual value is "Move to next date" -- matching on
  // "moved to next date" here never matched anything.
  "move to next date": "movedNextDate",
  blocker: "blocker",
  dropped: "dropped",
  "same client context": "sameClientContext",
  "initial email followup": "initialEmailFollowup",
};

// Exact Case Status Change dropdown label for a lower-cased value (the Call Log Status columns are these labels).
// A status still stored in its pre-change spelling counts in the column of its new wording.
const CASE_STATUS_LABEL_BY_LOWER = {
  ...Object.fromEntries(Object.entries(LEGACY_CASE_STATUS_ALIASES).filter(([, label]) => CASE_STATUS_OPTIONS.includes(label))),
  ...Object.fromEntries(CASE_STATUS_OPTIONS.map((label) => [label.toLowerCase(), label])),
};

function buildWebsitePaidAdsRows(leads, startDate, endDate) {
  const dates = buildCallLogStatusRows([], startDate, endDate).map(
    (row) => row.date,
  );
  return dates.map((date) => {
    const dayLeads = (Array.isArray(leads) ? leads : []).filter(
      (item) => getLeadDate(item) === date,
    );
    const result = {
      date,
      received: dayLeads.length,
      reachedOut: 0,
      vm: 0,
      open: 0,
      initialEmail: 0,
      successPC: 0,
      successGE: 0,
      pcFollowUp: 0,
      pcSharedLost: 0,
      feeDetails: 0,
      clientDelay: 0,
      movedNextDate: 0,
      blocker: 0,
      dropped: 0,
      sameClientContext: 0,
      initialEmailFollowup: 0,
      qualityTotal: 0,
      qualityCount: 0,
      duration: 0,
    };
    dayLeads.forEach((item) => {
      const record = item.record || {};
      if (
        LEAD_REACHED_OUT_FIELDS.some((key) => String(record[key] ?? "").trim())
      )
        result.reachedOut += 1;
      const outcome = String(record.call_outcome || "")
        .trim()
        .toLowerCase();
      // The lead's own case_type/s2_status are AI-extracted topics and a
      // Lead-native workflow status -- neither is the Open/Success-PC/etc.
      // vocabulary this table tracks. That instead lives on the OUTBOUND
      // call agents placed to this lead (Calls page Stage 3 "Case Status
      // Change"), matched server-side by phone+date -- see
      // routes/flintdesk/leads.py's _outbound_calls_by_lead.
      const status = String(record.outbound_call_status || "")
        .trim()
        .toLowerCase();
      if (outcome === "vm" || outcome.includes("voicemail")) result.vm += 1;
      if (status === "open") result.open += 1;
      const statusKey = CASE_STATUS_KEYS[status];
      if (statusKey) result[statusKey] += 1;
      if (String(record.blocker_comment || "").trim()) result.blocker += 1;
      const quality = Number(
        record.data_quality_score ?? record.overall_data_quality_score,
      );
      if (Number.isFinite(quality)) {
        result.qualityTotal += quality;
        result.qualityCount += 1;
      }
      result.duration += getNumericDuration(record.call_duration);
    });
    result.avgQuality = result.qualityCount
      ? Number((result.qualityTotal / result.qualityCount).toFixed(1))
      : null;
    return result;
  });
}

const CALL_DIRECTION_TABLE_COLUMNS = [
  ["Direct Inbound", "Direct Inbound"],
  ["Direct Outbound", "Direct Outbound"],
  ["Brevo Outbound", "Leads Outbound"],
  ["Missed Call Outbound", "Missed Call Outbound"],
  ["WA Inbound", "WA Inbound"],
  ["WA Outbound", "WA Outbound"],
];

const URGENCY_TABLE_COLUMNS = URGENCY_FLAG_OPTIONS.map((value) => [
  value,
  value,
]);

const CLIENT_TYPE_TABLE_COLUMNS = CLIENT_TYPE_OPTIONS.map((value) => [
  value,
  displayClientType(value),
]);

const LEADS_TABLE_COLUMNS = LEAD_SOURCE_DROPDOWN_OPTIONS.map((value) => [
  value,
  value,
]);

const CASE_TYPE_ENQUIRY_TYPES = ["H1B", "H4EAD", "F1", "I14A"];

function getCallDate(row) {
  const value =
    row?.date ||
    row?.raw?.call_recording_date_normalized ||
    row?.raw?.call_recording_date;
  return /^\d{4}-\d{2}-\d{2}/.test(String(value || ""))
    ? String(value).slice(0, 10)
    : "";
}

// Open Leads = every call whose Case Status Change is NOT Success - PC,
// Success - GE or Dropped, including calls with no status at all.
const OPEN_LEAD_EXCLUDED_STATUSES = new Set([
  "success - pc",
  "success - ge",
  "dropped",
]);

// A case is open unless its Case Status Change is Success - PC, Success - GE or
// Dropped; a call with no status selected is open too. Same rule as the
// Open column of the Call Log Status table (OPEN_LEAD_EXCLUDED_STATUSES).
function isOpenCaseRow(row) {
  const status = String(row?.raw?.fd_m_case_status_change || row?.raw?.action_status || "").trim().toLowerCase();
  return !OPEN_LEAD_EXCLUDED_STATUSES.has(status) && !row?.raw?.closure_inherited;
}

function buildCallLogStatusRows(rows, startDate, endDate) {
  const dates = [];
  const start = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  if (
    !Number.isNaN(start.getTime()) &&
    !Number.isNaN(end.getTime()) &&
    start <= end
  ) {
    for (
      const cursor = new Date(start);
      cursor <= end;
      cursor.setDate(cursor.getDate() + 1)
    )
      dates.push(getLocalDateString(cursor));
  }
  rows.forEach((row) => {
    const rowDate = getCallDate(row);
    const withinRange =
      (!startDate || rowDate >= startDate) && (!endDate || rowDate <= endDate);
    if (rowDate && withinRange && !dates.includes(rowDate)) dates.push(rowDate);
  });
  dates.sort();
  return dates.map((date) => {
    const dayRows = rows.filter((row) => getCallDate(row) === date);
    const result = { date, total: dayRows.length, avgQuality: 0, duration: 0 };
    CALL_LOG_STATUS_COLUMNS.forEach(([key]) => {
      result[key] = 0;
    });
    // Per Case Status Change dropdown label (exact option text): each column counts the calls whose saved status is that
    // option, "Open" included (a call with no status chosen is not in any column). `open` below is the dashboard's broader
    // open rule, kept for other readers.
    result.statusCounts = Object.fromEntries(CASE_STATUS_OPTIONS.map((label) => [label, 0]));
    result.noStatus = 0;      // calls with no Case Status Change chosen yet
    result.otherStatus = 0;   // calls whose stored status is not one of the dropdown options
    result.lostCounts = {};   // "<In-Transition status> & Lost" (set by the Lost processor, not a dropdown option) -> calls
    dayRows.forEach((row) => {
      // The persisted Case Status Change only (the field the dropdown writes). The separate Action Status
      // field is a different thing and is never used to work out a case status.
      const status = String(row?.raw?.fd_m_case_status_change || "").trim().toLowerCase();
      if (countsAsDashboardVoicemail(row)) result.vm += 1;
      // A log that confirmed another agent's closure of the same matter (closure_inherited) is no longer open.
      const inherited = Boolean(row?.raw?.closure_inherited);
      if (!OPEN_LEAD_EXCLUDED_STATUSES.has(status) && !inherited) result.open += 1;
      const statusKey = CASE_STATUS_KEYS[status];
      if (statusKey) result[statusKey] += 1;
      const statusLabel = CASE_STATUS_LABEL_BY_LOWER[status];
      if (statusLabel) result.statusCounts[statusLabel] += 1;
      if (!status) { if (!inherited) result.noStatus += 1; } else if (!statusLabel) result.otherStatus += 1;
      if (status.endsWith(" & lost")) {
        const lostLabel = String(row?.raw?.fd_m_case_status_change || "").trim();
        result.lostCounts[lostLabel] = (result.lostCounts[lostLabel] || 0) + 1;
      }
      result.avgQuality += calculateDashboardMetrics([
        row,
      ]).averageDataQualityScore;
      const parts = String(row?.duration || "")
        .split(":")
        .map(Number);
      result.duration +=
        parts.length === 3
          ? parts[0] * 3600 + parts[1] * 60 + parts[2]
          : Number(row?.duration) || 0;
    });
    result.avgQuality = dayRows.length
      ? Number((result.avgQuality / dayRows.length).toFixed(1))
      : 0;
    return result;
  });
}

const DAILY_CALL_TARGET = 35;

// Reuses the same per-day bucketing as buildCallLogStatusRows so the Daily
// Call Target table stays aligned with the Call Log Status date range.
function buildDailyCallTargetRows(rows, agentNames, startDate, endDate) {
  const dates = buildCallLogStatusRows([], startDate, endDate).map(
    (row) => row.date,
  );
  const result = [];
  dates.forEach((date) => {
    agentNames.forEach((agentName) => {
      const callsMade = rows.filter(
        (row) =>
          (row.agent || "").trim() === agentName &&
          getCallDate(row) === date &&
          // Only count a call once it's actually documented, not merely
          // attributed to the agent (by extension for outbound, or by
          // self-identification for inbound). A summary can't exist without
          // a transcript already having been generated first, so checking
          // it alone is sufficient to require both.
          Boolean(row.raw?.fd_a_call_summary) &&
          // Keep inbound and outbound normal calls, but voicemail is not a
          // completed normal call toward the 35-call daily target.
          !isVoicemailCall(row),
      ).length;
      const difference = callsMade - DAILY_CALL_TARGET;
      result.push({
        agent: agentName,
        date,
        callsMade,
        target: DAILY_CALL_TARGET,
        difference,
      });
    });
  });
  return result;
}

function normalizeEnquiryCaseType(value) {
  const normalized = String(value || "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
  if (normalized.includes("H4EAD")) return "H4EAD";
  if (normalized.includes("H1B")) return "H1B";
  if (normalized === "F1" || normalized.startsWith("F1VISA")) return "F1";
  if (["I14A", "I140", "FORMI140"].some((type) => normalized.includes(type)))
    return "I14A";
  return "";
}

function getEnquiryQuestion(row) {
  return String(
    row?.raw?.fd_a_enquiry_question ||
      row?.raw?.enquiry_question ||
      row?.raw?.enquiry_details ||
      "",
  ).trim();
}

function buildCaseTypeEnquiryRows(rows, startDate, endDate) {
  const start = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  if (
    Number.isNaN(start.getTime()) ||
    Number.isNaN(end.getTime()) ||
    start > end
  )
    return [];

  const buckets = [];
  for (const cursor = new Date(start); cursor <= end; ) {
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const week = Math.floor((cursor.getDate() - 1) / 7) + 1;
    const naturalStart = new Date(year, month, (week - 1) * 7 + 1);
    const naturalEnd = new Date(
      year,
      month,
      Math.min(week * 7, new Date(year, month + 1, 0).getDate()),
    );
    const bucketStart = naturalStart < start ? new Date(start) : naturalStart;
    const bucketEnd = naturalEnd > end ? new Date(end) : naturalEnd;
    buckets.push({
      key: `${year}-${month}-${week}`,
      label: `${formatDisplayDate(getLocalDateString(cursor))} – Week ${week}`,
      range: `${formatDisplayDate(getLocalDateString(bucketStart))} – ${formatDisplayDate(getLocalDateString(bucketEnd))}`,
      start: getLocalDateString(bucketStart),
      end: getLocalDateString(bucketEnd),
    });
    cursor.setTime(naturalEnd.getTime());
    cursor.setDate(cursor.getDate() + 1);
  }

  return buckets.map((bucket) => ({
    ...bucket,
    types: CASE_TYPE_ENQUIRY_TYPES.map((type) => {
      const questions = rows
        .filter((row) => {
          const date = getCallDate(row);
          return (
            date >= bucket.start &&
            date <= bucket.end &&
            normalizeEnquiryCaseType(
              row.caseType || row.raw?.fd_a_case_type || row.raw?.case_type,
            ) === type
          );
        })
        .map(getEnquiryQuestion)
        .filter(Boolean);
      return { type, questions: [...new Set(questions)] };
    }),
  }));
}

function buildCallLoopClosingSummary(rows, startDate, endDate) {
  const finishedStatuses = new Map([
    ["success pc", "successPC"],
    ["success ge", "successGE"],
    ["dropped", "dropped"],
  ]);
  const summary = {
    startDate,
    endDate,
    successPC: 0,
    successGE: 0,
    dropped: 0,
    closedCases: 0,
    averageDays: 0,
  };
  const closingDays = [];
  rows.forEach((row) => {
    const status = String(
      row.raw?.fd_m_case_status_change || row.raw?.action_status || "",
    )
      .trim()
      .toLowerCase()
      .replace(/\s*-\s*/g, " ");
    const statusKey = finishedStatuses.get(status);
    if (!statusKey) return;
    summary[statusKey] += 1;
    summary.closedCases += 1;
    const opened = toDateOnly(
      row.raw?.registered_at ||
        row.raw?.created_at ||
        row.raw?.call_date ||
        row.registeredAt ||
        row.date,
    );
    const closed = toDateOnly(
      row.raw?.fd_m_date_entry ||
        row.raw?.action_date ||
        row.raw?.case_closing_completed_at,
    );
    if (!opened || !closed) return;
    const openedDate = new Date(`${opened}T00:00:00`);
    const closedDate = new Date(`${closed}T00:00:00`);
    if (
      !Number.isNaN(openedDate.getTime()) &&
      !Number.isNaN(closedDate.getTime()) &&
      closedDate >= openedDate
    )
      closingDays.push(Math.round((closedDate - openedDate) / 86400000));
  });
  summary.averageDays = closingDays.length
    ? Number(
        (
          closingDays.reduce((total, days) => total + days, 0) /
          closingDays.length
        ).toFixed(1),
      )
    : 0;
  return summary;
}

function normalizeTableValue(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/whatsapp/g, "wa")
    .replace(/leads/g, "brevo")
    .replace(/[-_]/g, " ")
    .replace(/\s+/g, " ");
}

function buildDateCategoryRows(rows, startDate, endDate, columns, getValue) {
  const dates = buildCallLogStatusRows(rows, startDate, endDate).map(
    (row) => row.date,
  );
  return dates.map((date) => {
    const dayRows = rows.filter((row) => getCallDate(row) === date);
    const counts = Object.fromEntries(columns.map(([key]) => [key, 0]));
    dayRows.forEach((row) => {
      const value = getValue(row);
      const match = columns.find(
        ([key]) => normalizeTableValue(key) === normalizeTableValue(value),
      );
      if (match) counts[match[0]] += 1;
    });
    return { date, counts };
  });
}

function getLeadTableValue(row) {
  return row.raw?.fd_a_lead_source || row.raw?.lead_source || "";
}

function getCallDirectionTableValue(row) {
  const direction = String(row.direction || "")
    .trim()
    .toLowerCase();
  if (direction === "outbound") return "Direct Outbound";
  if (direction === "inbound") return "Direct Inbound";
  return "";
}

const PERFORMANCE_COLUMNS = [
  ["enquiries", "# of client enquiries"],
  ["successPC", "# of Success PC"],
  ["successGE", "# of Success GE"],
  ["quality", "Call Quality Score"],
  ["openCases", "Number of open cases"],
  ["dataQuality", "Data Quality Score (/5)"],
  ["business", "Business Clocked"],
  ["reviews", "Client Reviews"],
  ["averageOpenDays", "Avg Case Open Duration"],
  ["averageClosingDays", "Call Loop Closing Duration"],
];

const RANKING_COLUMNS = [
  ...PERFORMANCE_COLUMNS,
  ["averageOpenDays", "Avg Open Case Duration"],
  ["averageClosingDays", "Avg Case Closing Duration"],
];

function toDateOnly(value) {
  const text = String(value || "");
  return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : "";
}

function average(values) {
  const valid = values.filter((value) => Number.isFinite(value));
  return valid.length
    ? valid.reduce((sum, value) => sum + value, 0) / valid.length
    : 0;
}

function getNumericDuration(value) {
  const text = String(value || "");
  const parts = text.split(":").map(Number);
  if (parts.length === 3 && parts.every(Number.isFinite))
    return parts[0] * 3600 + parts[1] * 60 + parts[2];
  const numeric = Number(text.replace(/[^\d.]/g, ""));
  return Number.isFinite(numeric) ? numeric : 0;
}

function getOpenDays(row) {
  const registered = toDateOnly(
    row.raw?.registered_at ||
      row.raw?.created_at ||
      row.raw?.call_date ||
      row.date,
  );
  if (!registered) return 0;
  const status = String(
    row.raw?.fd_m_case_status_change || row.raw?.action_status || "",
  ).toLowerCase();
  const changed = toDateOnly(row.raw?.fd_m_date_entry || row.raw?.action_date);
  const end =
    changed && !["open", "pc follow up"].includes(status)
      ? changed
      : getLocalDateString();
  const startDate = new Date(`${registered}T00:00:00`);
  const endDate = new Date(`${end}T00:00:00`);
  return Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime())
    ? 0
    : Math.max(0, Math.round((endDate - startDate) / 86400000));
}

function getAverageOpenCaseDuration(rows) {
  return Number(
    average(
      rows
        .filter(
          (row) =>
            String(
              row.raw?.fd_m_case_status_change || row.raw?.action_status || "",
            ).toLowerCase() === "open",
        )
        .map(getOpenDays),
    ).toFixed(1),
  );
}

function getPerformanceAgent(row) {
  return (
    String(
      row.agent ||
        row.raw?.front_desk_person_name ||
        row.raw?.fd_a_case_worker_assigned ||
        "Unassigned",
    ).trim() || "Unassigned"
  );
}

function buildPerformanceRows(rows, startDate, endDate) {
  const dates = buildCallLogStatusRows(rows, startDate, endDate).map(
    (row) => row.date,
  );
  return dates.map((date) => {
    const dayRows = rows.filter((row) => getCallDate(row) === date);
    const statuses = dayRows.map((row) =>
      String(
        row.raw?.fd_m_case_status_change || row.raw?.action_status || "",
      ).toLowerCase(),
    );
    const result = {
      date,
      enquiries: dayRows.length,
      successPC: statuses.filter((status) => status === "success - pc").length,
      successGE: statuses.filter((status) => status === "success - ge").length,
      quality: Math.round(
        average(
          dayRows
            .map((row) => Number(row.raw?.overall_call_quality_score))
            .filter(Number.isFinite),
        ),
      ),
      closingDuration: average(
        dayRows.map((row) => getNumericDuration(row.duration)),
      ),
      // A count of open cases (the label says "Number of open cases"); it used to
      // be the average open days, which is a different metric.
      openCases: dayRows.filter(isOpenCaseRow).length,
      dataQuality: Number(
        average(
          dayRows.map(
            (row) => calculateDashboardMetrics([row]).averageDataQualityScore,
          ),
        ).toFixed(1),
      ),
      // The persisted Business Opportunity Score gates a call's existing
      // mapped fee; do not turn the score itself into an invented price.
      business: dayRows.reduce((total, row) => {
        const business = calculatePriority(row).business;
        return business.score === null || business.value === null
          ? total
          : total + business.value;
      }, 0),
      reviews: dayRows.filter(
        (row) =>
          String(row.raw?.fd_m_google_review || "").toLowerCase() ===
          "submitted",
      ).length,
      averageOpenDays: getAverageOpenCaseDuration(dayRows),
      averageClosingDays: buildCallLoopClosingSummary(dayRows).averageDays,
    };
    result.openCases = Number(result.openCases.toFixed(1));
    return result;
  });
}

function buildAgentRankingRows(rows, agentDirectory = []) {
  const grouped = new Map();
  const agentsById = new Map(
    agentDirectory
      .map((agent) => [String(agent.id || "").trim(), agent])
      .filter(([id, agent]) => id && agent?.name),
  );
  const agentsByName = new Map(
    agentDirectory
      .map((agent) => [
        String(agent.name || "")
          .trim()
          .toLowerCase(),
        agent,
      ])
      .filter(([name, agent]) => name && agent?.id),
  );
  agentDirectory.forEach((agent) => grouped.set(agent.id, { agent, rows: [] }));
  rows.forEach((row) => {
    const agent =
      agentsById.get(String(row.agentId || "").trim()) ||
      agentsByName.get(getPerformanceAgent(row).toLowerCase());
    if (agent) grouped.get(agent.id).rows.push(row);
  });
  return [...grouped.values()]
    .map(({ agent, rows: agentRows }) => {
      const performanceRows = buildPerformanceRows(
        agentRows,
        agentRows.map(getCallDate).filter(Boolean).sort()[0] || getLocalDateString(),
        getLocalDateString(),
      ).filter(
        (row) =>
          row.enquiries ||
          row.successPC ||
          row.successGE ||
          row.quality ||
          row.dataQuality,
      );
      const totalCalls = agentRows.length;
      const successCount = agentRows.filter((row) =>
        ["success - pc", "success - ge"].includes(
          String(
            row.raw?.fd_m_case_status_change || row.raw?.action_status || "",
          ).toLowerCase(),
        ),
      ).length;
      const reviews = agentRows.filter(
        (row) =>
          String(row.raw?.fd_m_google_review || "").toLowerCase() ===
          "submitted",
      ).length;
      const business = agentRows.filter(
        (row) =>
          String(row.raw?.business_opportunity || "").toLowerCase() === "yes",
      ).length;
      const quality = Math.round(
        average(
          agentRows
            .map((row) => Number(row.raw?.overall_call_quality_score))
            .filter(Number.isFinite),
        ),
      );
      const dataQuality = Number(
        average(
          agentRows.map(
            (row) => calculateDashboardMetrics([row]).averageDataQualityScore,
          ),
        ).toFixed(1),
      );
      // The established weighted score is a percentage. Convert the completed
      // weighted result to a 0–10 normalized score for display and ranking.
      const weightedPercent =
        quality * 0.25 +
        (dataQuality / 5) * 100 * 0.25 +
        (successCount / Math.max(1, totalCalls)) * 100 * 0.25 +
        (reviews / Math.max(1, totalCalls)) * 100 * 0.15 +
        (business / Math.max(1, totalCalls)) * 100 * 0.1;
      const performanceScore = Number(
        (Math.max(0, Math.min(100, weightedPercent)) / 10).toFixed(1),
      );
      return {
        agent: agent.name,
        extensions: (agent.extensions || []).join(", ") || "-",
        enquiries: performanceRows.reduce((sum, row) => sum + row.enquiries, 0),
        successPC: agentRows.filter(
          (row) =>
            String(
              row.raw?.fd_m_case_status_change || row.raw?.action_status || "",
            ).toLowerCase() === "success - pc",
        ).length,
        successGE: agentRows.filter(
          (row) =>
            String(
              row.raw?.fd_m_case_status_change || row.raw?.action_status || "",
            ).toLowerCase() === "success - ge",
        ).length,
        quality,
        closingDuration: average(
          agentRows.map((row) => getNumericDuration(row.duration)),
        ),
        openCases: agentRows.filter(isOpenCaseRow).length,
        averageClosingDays: buildCallLoopClosingSummary(agentRows).averageDays,
        averageOpenDays: getAverageOpenCaseDuration(agentRows),
        dataQuality,
        business,
        reviews,
        performanceScore,
        totalCalls,
      };
    })
    .sort((a, b) => b.performanceScore - a.performanceScore);
}

export { buildDailyCallTargetRows, buildCaseTypeEnquiryRows };
export function computeDashboardSummary({ calls = [], dailyTargetCalls = [], leads = [], fdAgents = [], syncFilters = [],
    dateFrom, dateTo, selectedAgent = "all", selectedNumber = "", selectedLeadNumber = "" }) {
  const draftNumber = selectedNumber;
  const derive = (fn) => fn();
  const performanceCalls = calls.map(row => ({
    agent: row.agent || row.raw?.front_desk_person_name || row.raw?.fd_a_case_worker_assigned || "-",
    date: row.date || row.raw?.call_recording_date_normalized || row.raw?.call_recording_date || "",
    duration: row.duration || row.raw?.call_duration || "-", raw: row.raw || row,
  }));
  const agentOptions = derive(
    () => fdAgents.map((agent) => agent.name),
    [fdAgents],
  );
  const numberOptions = derive(() => {
    return getOutboundLeadNumberOptions(syncFilters, calls);
  }, [calls, syncFilters]);
  const extensionOptions = derive(() => {
    return draftNumber
      ? getLeadNumberExtensions(draftNumber)
      : getAllLeadExtensions();
  }, [draftNumber]);

  const filteredCalls = derive(() => {
    return calls.filter((row) => {
      // Use camelCase field names from getCallsList() transformation
      const agent = row.agent || "";
      const extension = String(row.extension || "");

      // For lead number filtering, normalize both values to handle phone number formatting variations
      const matchesLeadNumber = callMatchesLeadNumber(
        row,
        selectedNumber,
        calls,
        syncFilters,
      );

      return (
        (selectedAgent === "all" || String(agent).trim() === selectedAgent) &&
        matchesLeadNumber &&
        (!selectedLeadNumber ||
          extension === selectedLeadNumber ||
          extension === ALIAS_EXTENSION_FOR_LEAD_NUMBER[selectedLeadNumber])
      );
    });
  }, [calls, selectedAgent, selectedNumber, selectedLeadNumber, syncFilters]);

  // Daily Call Target's own source: same lead-number/extension/agent
  // narrowing as filteredCalls, but built from the Calls Records page's
  // unscoped dataset (dailyTargetCalls) instead of the Dashboard's
  // is_dashboard_call-restricted one, so its counts match that page.
  const filteredDailyTargetCalls = derive(() => {
    return dailyTargetCalls.filter((row) => {
      const agent = row.agent || "";
      const extension = String(row.extension || "");
      const matchesLeadNumber = callMatchesLeadNumber(
        row,
        selectedNumber,
        dailyTargetCalls,
        syncFilters,
      );
      return (
        (selectedAgent === "all" || String(agent).trim() === selectedAgent) &&
        matchesLeadNumber &&
        (!selectedLeadNumber ||
          extension === selectedLeadNumber ||
          extension === ALIAS_EXTENSION_FOR_LEAD_NUMBER[selectedLeadNumber])
      );
    });
  }, [dailyTargetCalls, selectedAgent, selectedNumber, selectedLeadNumber, syncFilters]);

  const filteredLeads = derive(() => {
    const permittedExtensions = selectedNumber
      ? new Set(getLeadNumberExtensions(selectedNumber))
      : null;
    return leads.filter((item) => {
      const record = item.record || {};
      const extension = String(
        record.extension || item.call_details?.extension || "",
      );
      const agent = String(record.owner || record.agent_name || "").trim();
      return (
        (selectedAgent === "all" || agent === selectedAgent) &&
        (!permittedExtensions || permittedExtensions.has(extension)) &&
        (!selectedLeadNumber || extension === selectedLeadNumber)
      );
    });
  }, [leads, selectedAgent, selectedNumber, selectedLeadNumber]);
  const metrics = derive(
    () =>
      calculateDashboardMetrics(filteredCalls, dateFrom, dateTo, filteredLeads),
    [filteredCalls, dateFrom, dateTo, filteredLeads],
  );
  // Total Calls/Normal Calls/Voice Mails must count the same population as
  // Daily Call Target (filteredDailyTargetCalls, the Calls Records page's
  // unscoped dataset), not the is_dashboard_call-restricted filteredCalls
  // every other Dashboard table uses -- otherwise these 3 cards silently
  // undercount relative to Daily Call Target for the exact same agent/date.
  // Unlike filteredCalls, filteredDailyTargetCalls can include synthetic
  // task-placeholder rows (isTaskRecord -- see callTaskRecords.js) for a
  // Missed/Non-Responsive task with no matched call yet; those aren't real
  // calls and must stay excluded here, same as filteredCalls always did.
  const callKindCounts = derive(
    () =>
      filteredDailyTargetCalls
        .filter((call) => !call.isTaskRecord)
        .reduce(
          (counts, call) => {
            counts.total += 1;
            if (countsAsDashboardVoicemail(call)) counts.voicemail += 1;
            // Same match as the Calls page's Call Direction filter, limited
            // to calls that have an agent name.
            const agentName = String(call.agent || "").trim();
            if (agentName && agentName !== "-") {
              const direction = String(call.direction || "").trim().toLowerCase();
              if (direction === "inbound") counts.inbound += 1;
              else if (direction === "outbound") counts.outbound += 1;
            }
            return counts;
          },
          { total: 0, voicemail: 0, inbound: 0, outbound: 0 },
        ),
    [filteredDailyTargetCalls],
  );
  const priorityMetrics = derive(
    () =>
      filteredCalls.reduce(
        (totals, call) => {
          const priority = calculatePriority(call);
          totals.bosTotal += priority.business.value || 0;
          if (priority.business.value !== null) totals.bosCount += 1;
          if (priority.leadScore !== null) totals[priority.leadBand] += 1;
          if (priority.urgencyOverride) totals.urgency += 1;
          totals[priority.businessTier] += 1;
          return totals;
        },
        {
          BOOK_NOW: 0,
          bosTotal: 0,
          bosCount: 0,
          GATHER_MORE_INFO: 0,
          NURTURE: 0,
          urgency: 0,
          HIGH: 0,
          HIGH_MID: 0,
          STANDARD: 0,
          N_A: 0,
        },
      ),
    [filteredCalls],
  );
  const openCallAges = derive(
    () => calculateOpenCallAgeCounts(filteredCalls),
    [filteredCalls],
  );
  const leadPriorityMetrics = derive(
    () =>
      filteredLeads.reduce(
        (totals, lead) => {
          const score =
            calculatePriority(lead).leadScore ??
            (Number.isFinite(Number(lead.lead_score))
              ? Number(lead.lead_score)
              : null);
          const bos = calculatePriority(lead).business.value;
          if (Number.isFinite(score)) {
            totals[
              score >= 70
                ? "BOOK_NOW"
                : score >= 40
                  ? "GATHER_MORE_INFO"
                  : "NURTURE"
            ] += 1;
            totals.leadTotal += score;
            totals.leadCount += 1;
          }
          if (Number.isFinite(bos)) {
            totals.bosTotal += bos;
            totals.bosCount += 1;
          }
          return totals;
        },
        {
          BOOK_NOW: 0,
          GATHER_MORE_INFO: 0,
          NURTURE: 0,
          leadTotal: 0,
          leadCount: 0,
          bosTotal: 0,
          bosCount: 0,
        },
      ),
    [filteredLeads],
  );
  // Call Log Status must count the same calls as the Total Calls / Voice Mails
  // cards and Daily Call Target (the Calls Records page's unscoped dataset),
  // not the is_dashboard_call-restricted filteredCalls. Using different
  // populations made this table disagree with those cards for the same
  // agent and date. Task placeholder rows are not real calls, so they stay out.
  const callLogStatusRows = derive(
    () =>
      buildCallLogStatusRows(
        filteredDailyTargetCalls.filter((call) => !call.isTaskRecord),
        dateFrom,
        dateTo,
      ),
    [filteredDailyTargetCalls, dateFrom, dateTo],
  );
  const dailyCallTargetRows = derive(
    () =>
      buildDailyCallTargetRows(
        filteredDailyTargetCalls,
        selectedAgent === "all" ? agentOptions : [selectedAgent],
        dateFrom,
        dateTo,
      ),
    [filteredDailyTargetCalls, agentOptions, selectedAgent, dateFrom, dateTo],
  );
  const websitePaidAdsRows = derive(
    () => buildWebsitePaidAdsRows(filteredLeads, dateFrom, dateTo),
    [filteredLeads, dateFrom, dateTo],
  );
  const callDirectionRows = derive(
    () =>
      buildDateCategoryRows(
        filteredCalls,
        dateFrom,
        dateTo,
        CALL_DIRECTION_TABLE_COLUMNS,
        getCallDirectionTableValue,
      ),
    [filteredCalls, dateFrom, dateTo],
  );
  const urgencyRows = derive(
    () =>
      buildDateCategoryRows(
        filteredCalls,
        dateFrom,
        dateTo,
        URGENCY_TABLE_COLUMNS,
        (row) => row.raw?.fd_a_urgency_flag || row.raw?.urgency,
      ),
    [filteredCalls, dateFrom, dateTo],
  );
  const clientTypeRows = derive(
    () =>
      buildDateCategoryRows(
        filteredCalls,
        dateFrom,
        dateTo,
        CLIENT_TYPE_TABLE_COLUMNS,
        (row) => row.raw?.fd_a_client_type || row.raw?.client_type,
      ),
    [filteredCalls, dateFrom, dateTo],
  );
  const leadsRows = derive(
    () =>
      buildDateCategoryRows(
        filteredCalls,
        dateFrom,
        dateTo,
        LEADS_TABLE_COLUMNS,
        getLeadTableValue,
      ),
    [filteredCalls, dateFrom, dateTo],
  );
  const caseTypeEnquiryRows = derive(
    () => buildCaseTypeEnquiryRows(filteredCalls, dateFrom, dateTo),
    [filteredCalls, dateFrom, dateTo],
  );
  const performanceRows = derive(() => {
    const rows =
      selectedAgent === "all"
        ? performanceCalls
        : performanceCalls.filter(
            (row) => String(row.agent || "").trim() === selectedAgent,
          );
    return buildPerformanceRows(rows, dateFrom, dateTo);
  }, [performanceCalls, selectedAgent, dateFrom, dateTo]);
  const callLoopClosing = derive(
    () => buildCallLoopClosingSummary(filteredCalls, dateFrom, dateTo),
    [filteredCalls, dateFrom, dateTo],
  );
  const rankingSource = derive(() => {
    const normalizedRows = performanceCalls.map((row) => ({
      ...row,
      agent: row.agent || row.raw?.front_desk_person_name || "",
      agentId: row.raw?.front_desk_agent_id || "",
      extension: row.raw?.extension || "-",
    }));
    if (selectedAgent === "all") return normalizedRows;
    const selected = selectedAgent.trim().toLowerCase();
    return normalizedRows.filter(
      (row) => getPerformanceAgent(row).toLowerCase() === selected,
    );
  }, [selectedAgent, performanceCalls]);
  const rankingRows = derive(
    () => buildAgentRankingRows(rankingSource, fdAgents),
    [rankingSource, fdAgents],
  );
  const callVolumeSeries = derive(
    () => buildCallVolumeSeries(filteredCalls),
    [filteredCalls],
  );
  const realAgentPerformance = derive(
    () => buildAgentPerformance(filteredCalls),
    [filteredCalls],
  );
  const agentPerformance = realAgentPerformance;
  const usingMockAgentPerformance = false;


  return { metrics, callKindCounts, priorityMetrics, openCallAges, leadPriorityMetrics, callLogStatusRows, dailyCallTargetRows, websitePaidAdsRows, callDirectionRows, urgencyRows, clientTypeRows, leadsRows, caseTypeEnquiryRows, performanceRows, callLoopClosing, rankingRows, callVolumeSeries, agentPerformance, agentOptions, numberOptions, extensionOptions, fdAgents, syncFilters };
}
