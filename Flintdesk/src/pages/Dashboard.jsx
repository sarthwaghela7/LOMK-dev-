import { usePersistentTableState } from "../data/persistentTableState.js";
import { useExplicitPageRefresh } from "../data/useExplicitPageRefresh.js";
import { useActivePageRefresh } from "../data/useActivePageRefresh.js";
import { computeDashboardSummary } from "../data/dashboardSummary.js";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  getDashboardSummary,
  getOpenCallAging,
  getCaseLifecycle,
} from "../data/api";
import { lifecycleCards } from "../data/caseLifecycle.js";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { getDefaultDateRange, isValidDateRange } from "../data/filterDates.js";
import {
  calculateDashboardMetrics,
  formatPercentage,
  formatDuration,
  buildCallVolumeSeries,
  buildAgentPerformance,
} from "../data/dashboardCalculations";
import {
  ALIAS_EXTENSION_FOR_LEAD_NUMBER,
  callMatchesLeadNumber,
  displayExtensionOption,
  getAllLeadExtensions,
  getLeadNumberExtensions,
  getOutboundLeadNumberOptions,
} from "../data/leadNumberFilter.js";
import { calculatePriority } from "../data/priorityEngine.js";
import {
  CLIENT_TYPE_OPTIONS,
  URGENCY_FLAG_OPTIONS,
  LEAD_SOURCE_DROPDOWN_OPTIONS,
} from "../data/callFieldOptions.js";
import { calculateOpenCallAgeCounts } from "../data/openCallAge.js";
import { agingCardValue, agingContextNote, agingContextRows, resolveAgingCards } from "../data/openCallAgingContext.js";
import { LeadScoreMatrixModal } from "../components/LeadScoreMatrixModal.jsx";
import { displayClientType } from "../data/displayLabels.js";
import { isVoicemailCall, countsAsDashboardVoicemail } from "../data/callKind.js";
import { formatDisplayDate } from "../data/displayDate.js";
import { CASE_STATUS_OPTIONS } from "../data/statusOptions.js";
import { CollapsiblePanel } from "../components/CollapsiblePanel.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";

function getLocalDateString(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

const formatUsd = (value) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value) || 0);

function getMonthStartEnd() {
  return getDefaultDateRange();
}

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
    dayRows.forEach((row) => {
      const status = String(
        row?.raw?.fd_m_case_status_change || row?.raw?.action_status || "",
      )
        .trim()
        .toLowerCase();
      if (countsAsDashboardVoicemail(row)) result.vm += 1;
      // A log that confirmed another agent's closure of the same matter (closure_inherited) is no longer open.
      if (!OPEN_LEAD_EXCLUDED_STATUSES.has(status) && !row?.raw?.closure_inherited) result.open += 1;
      const statusKey = CASE_STATUS_KEYS[status];
      if (statusKey) result[statusKey] += 1;
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

export default function Dashboard({ isTeamLead = false, user, isActive = true }) {
  const { start: monthStart, end: monthEnd } = getMonthStartEnd();
  const [dateFrom, setDateFrom] = usePersistentTableState("Dashboard.dateFrom", () => monthStart);
  const [dateTo, setDateTo] = usePersistentTableState("Dashboard.dateTo", () => monthEnd);
  const [draftDateRange, setDraftDateRange] = usePersistentTableState("Dashboard.draftDateRange", () => ({ start: monthStart, end: monthEnd }));
  const [selectedAgent, setSelectedAgent] = usePersistentTableState("Dashboard.selectedAgent", "all");
  const [draftAgent, setDraftAgent] = usePersistentTableState("Dashboard.draftAgent", "all");
  const [selectedNumber, setSelectedNumber] = usePersistentTableState("Dashboard.selectedNumber", "");
  const [draftNumber, setDraftNumber] = usePersistentTableState("Dashboard.draftNumber", "");
  const [selectedLeadNumber, setSelectedLeadNumber] = usePersistentTableState("Dashboard.selectedLeadNumber", "");
  const [draftLeadNumber, setDraftLeadNumber] = usePersistentTableState("Dashboard.draftLeadNumber", "");
  const [summary, setSummary] = useState(null);
  // The Open Call Aging cards load on their own ({ key, data }): they never hold up the rest of the Dashboard.
  const [aging, setAging] = useState(null);
  // The five Case Lifecycle Stages cards load on their own as well: { key, data } (data null = unavailable).
  const [lifecycle, setLifecycle] = useState(null);
  const agingKey = JSON.stringify([dateFrom, dateTo, selectedAgent, selectedNumber, selectedLeadNumber]);
  const [fetchRevision, setFetchRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [apiError, setApiError] = useState("");
  const [isLeadScoreMatrixOpen, setIsLeadScoreMatrixOpen] = useState(false);
  const refreshDashboard = useCallback(async ({ silent, isCurrent }) => {
    if (!silent) setLoading(true);
    // Started together with the summary but NOT awaited by it: a slow aging read only delays its own four cards.
    const key = JSON.stringify([dateFrom, dateTo, selectedAgent, selectedNumber, selectedLeadNumber]);
    getOpenCallAging(dateFrom, dateTo, selectedAgent, selectedNumber, selectedLeadNumber)
      .then((data) => ({ key, data }), () => ({ key, data: null }))
      .then((answer) => { if (isCurrent()) setAging(answer); });
    getCaseLifecycle(dateFrom, dateTo, selectedAgent, selectedNumber, selectedLeadNumber)
      .then((data) => ({ key, data }), () => ({ key, data: null }))
      .then((answer) => { if (isCurrent()) setLifecycle(answer); });
    try {
      const result = await getDashboardSummary(dateFrom, dateTo, selectedAgent, selectedNumber, selectedLeadNumber);
      if (isCurrent()) { setSummary(result); setApiError(""); }
    } catch (error) {
      if (isCurrent()) setApiError(error.message || "Dashboard summary unavailable.");
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [dateFrom, dateTo, selectedAgent, selectedNumber, selectedLeadNumber]);
  useExplicitPageRefresh(refreshDashboard, fetchRevision);
  // Keep the numbers current without a manual reload: re-read when this page is shown again, every minute or two
  // while it is open, and right after any status (or other record) change made in the app, even from another tab.
  // Quiet (no spinner); a request made for older filters is ignored.
  useActivePageRefresh(isActive, refreshDashboard);
  useEffect(() => {
    if (!isLeadScoreMatrixOpen) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previousOverflow; };
  }, [isLeadScoreMatrixOpen]);
  const emptySummary = useMemo(() => computeDashboardSummary({ dateFrom, dateTo }), [dateFrom, dateTo]);
  const { metrics, callKindCounts, priorityMetrics, openCallAges: summaryOpenCallAges, leadPriorityMetrics, callLogStatusRows, dailyCallTargetRows, callDirectionRows, urgencyRows, clientTypeRows, leadsRows, caseTypeEnquiryRows, performanceRows, callLoopClosing, rankingRows, callVolumeSeries, agentPerformance, agentOptions, numberOptions, fdAgents, syncFilters } = summary || emptySummary;
  const agingCards = resolveAgingCards(aging, agingKey, summaryOpenCallAges);
  const openCallAges = agingCards.ages || summaryOpenCallAges;      // PDF export: whatever is known right now
  const openCallAgeContext = agingCards.context;
  // Sarth and Satish are intentionally left off the ranking display (their
  // accounts and records are untouched); matched on the agent's first name.
  const visibleRankingRows = rankingRows.filter(
    (row) => !RANKING_HIDDEN_AGENTS.has(String(row.agent || "").trim().split(/\s+/)[0].toLowerCase()),
  );
  const extensionOptions = draftNumber ? getLeadNumberExtensions(draftNumber) : getAllLeadExtensions();
  const operationalCallTable = summary?.operationalCallTable || EMPTY_OPERATIONAL_TABLE;
  const closeCallAging = summary?.closeCallAging;
  const clientConversionDuration = summary?.clientConversionDuration;
  const dependencySummary = summary?.dependencySummary || { total: 0, pending: 0, approved: 0, rejected: 0 };
  const taskSummary = summary?.taskSummary || { missed_calls: [], non_responsive_calls: [] };
  const timeDurations = summary?.timeDurations || { totalCallDurationSeconds: 0, voicemailCallDurationSeconds: 0, qualifyingCallCount: 0 };
  const timeDurationsLoading = loading;
  const timeDurationsError = apiError;
  const leadsError = apiError;

  const exportDashboardPdf = async () => {
    // Open the preview tab immediately, then draw a structured A4 report from
    // the same calculated values shown by the Dashboard.
    const previewWindow = window.open("about:blank", "_blank");
    if (!previewWindow) {
      window.alert("Please allow pop-ups to preview the PDF.");
      return;
    }

    const { jsPDF } = await import("jspdf");
    const pdf = new jsPDF({
      orientation: "portrait",
      unit: "mm",
      format: "a4",
    });
    const margin = 10;
    const pageWidth = 210 - margin * 2;
    const pageHeight = 297 - margin * 2;
    let cursorY = margin;
    const ensureSpace = (height) => {
      if (cursorY + height <= pageHeight - margin) return;
      pdf.addPage("a4", "portrait");
      cursorY = margin;
    };
    const drawBreakdown = (title, breakdown) => {
      const rows = breakdown.items || [];
      const rowHeight = 6;
      const cardHeight = 14 + Math.max(1, rows.length + 1) * rowHeight;
      ensureSpace(cardHeight + 8);
      pdf.setFillColor(248, 250, 252);
      pdf.setDrawColor(226, 230, 237);
      pdf.roundedRect(margin, cursorY, pageWidth, cardHeight, 2, 2, "FD");
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(10);
      pdf.setTextColor(31, 36, 48);
      pdf.text(title, margin + 4, cursorY + 7);
      pdf.setFontSize(8);
      pdf.setTextColor(100, 116, 139);
      pdf.text(
        `${breakdown.total || 0} total`,
        pageWidth + margin - 4,
        cursorY + 7,
        { align: "right" },
      );
      let rowY = cursorY + 14;
      rows.forEach((item) => {
        pdf.setFont("helvetica", "normal");
        pdf.setFontSize(8.5);
        pdf.setTextColor(55, 65, 81);
        pdf.text(String(item.label || "-"), margin + 4, rowY);
        pdf.setTextColor(31, 36, 48);
        pdf.text(String(item.count ?? 0), pageWidth + margin - 28, rowY, {
          align: "right",
        });
        pdf.setTextColor(100, 116, 139);
        pdf.text(
          formatPercentage(item.percentage),
          pageWidth + margin - 4,
          rowY,
          { align: "right" },
        );
        rowY += rowHeight;
      });
      pdf.setDrawColor(226, 230, 237);
      pdf.line(margin + 4, rowY - 3, pageWidth + margin - 4, rowY - 3);
      pdf.setFont("helvetica", "bold");
      pdf.setTextColor(31, 36, 48);
      pdf.text("Total", margin + 4, rowY + 1);
      pdf.text(
        String(breakdown.total || 0),
        pageWidth + margin - 28,
        rowY + 1,
        { align: "right" },
      );
      pdf.text(
        formatPercentage(breakdown.total ? 100 : 0),
        pageWidth + margin - 4,
        rowY + 1,
        { align: "right" },
      );
      cursorY += cardHeight + 8;
    };
    const drawTable = (title, columns, rows) => {
      ensureSpace(18);
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(11);
      pdf.setTextColor(31, 36, 48);
      pdf.text(title, margin, cursorY);
      cursorY += 5;
      const widths = columns.map(() => pageWidth / columns.length);
      const drawRow = (values, header = false) => {
        const lines = values.map((value, index) =>
          pdf.splitTextToSize(String(value ?? ""), widths[index] - 3),
        );
        const rowHeight = Math.max(5, ...lines.map((item) => item.length * 3 + 2));
        if (cursorY + rowHeight > pageHeight - margin) {
          pdf.addPage("a4", "portrait");
          cursorY = margin;
          if (!header) drawRow(columns, true);
        }
        let x = margin;
        lines.forEach((value, index) => {
          pdf.setFillColor(
            header ? 238 : 255,
            header ? 246 : 255,
            header ? 251 : 255,
          );
          pdf.setDrawColor(226, 230, 237);
          pdf.rect(x, cursorY, widths[index], rowHeight, "FD");
          pdf.setFont("helvetica", header ? "bold" : "normal");
          pdf.setFontSize(6.5);
          pdf.setTextColor(31, 36, 48);
          pdf.text(value, x + 1.5, cursorY + 3.2);
          x += widths[index];
        });
        cursorY += rowHeight;
      };
      drawRow(columns, true);
      rows.forEach((row) => drawRow(row));
      cursorY += 7;
    };

    pdf.setFont("helvetica", "bold");
    pdf.setFontSize(22);
    pdf.setTextColor(31, 36, 48);
    pdf.text("FlintDesk Dashboard", margin, cursorY + 8);
    cursorY += 15;
    pdf.setFont("helvetica", "normal");
    pdf.setFontSize(9);
    pdf.setTextColor(100, 116, 139);
    pdf.text(`Date: ${dateFrom} to ${dateTo}`, margin, cursorY);
    cursorY += 4.5;
    pdf.text(
      `Agent: ${selectedAgent === "all" ? "All Agents" : selectedAgent} | Lead number: ${selectedNumber || "All Numbers"} | Extension: ${selectedLeadNumber || "All Extensions"}`,
      margin,
      cursorY,
    );
    cursorY += 7;
    pdf.setDrawColor(219, 227, 236);
    pdf.line(margin, cursorY, pageWidth + margin, cursorY);
    cursorY += 10;

    ensureSpace(54);
    pdf.setFont("helvetica", "bold");
    pdf.setFontSize(13);
    pdf.setTextColor(31, 36, 48);
    pdf.text("Key Metrics", margin, cursorY);
    cursorY += 7;
    const metricWidth = (pageWidth - 12) / 3;
    [
      ["Total Calls", callKindCounts.total],
      ["Normal Calls", callKindCounts.total - callKindCounts.voicemail],
      ["Voice Mails", callKindCounts.voicemail],
      ["Avg Lead Score", `${metrics.averageLeadScore.toFixed(1)} / 100`],
      [
        "Avg Business Opportunity Value",
        formatUsd(
          (priorityMetrics.bosTotal || 0) / (priorityMetrics.bosCount || 1),
        ),
      ],
    ].forEach(([label, value], index) => {
      const x = margin + (index % 3) * (metricWidth + 6);
      const y = cursorY + Math.floor(index / 3) * 29;
      pdf.setDrawColor(226, 230, 237);
      pdf.roundedRect(x, y, metricWidth, 25, 2, 2, "S");
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(8);
      pdf.setTextColor(100, 116, 139);
      pdf.text(pdf.splitTextToSize(label, metricWidth - 8), x + 4, y + 6);
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(18);
      pdf.setTextColor(31, 36, 48);
      pdf.text(String(value), x + 4, y + 19);
    });
    cursorY += 62;
    pdf.setFont("helvetica", "italic");
    pdf.setFontSize(7.5);
    pdf.setTextColor(100, 116, 139);
    pdf.text(
      "Total Calls includes calls for the extensions configured in the Settings page and includes both processed and not processed calls.",
      margin,
      cursorY,
    );
    cursorY += 9;

    ensureSpace(38);
    pdf.setFont("helvetica", "bold");
    pdf.setFontSize(13);
    pdf.setTextColor(31, 36, 48);
    pdf.text("Open Call Aging", margin, cursorY);
    cursorY += 7;
    const agingMetricWidth = (pageWidth - 18) / 4;
    [3, 5, 10, 15].forEach((days, index) => {
      const x = margin + index * (agingMetricWidth + 6);
      pdf.setDrawColor(226, 230, 237);
      pdf.roundedRect(x, cursorY, agingMetricWidth, 25, 2, 2, "S");
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(8);
      pdf.setTextColor(100, 116, 139);
      pdf.text(`Open ${days}+ Days`, x + 4, cursorY + 7);
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(18);
      pdf.setTextColor(31, 36, 48);
      pdf.text(String(openCallAges[days]), x + 4, cursorY + 18);
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(6.5);
      pdf.setTextColor(100, 116, 139);
      pdf.text("Unresolved calls, cumulative", x + 4, cursorY + 22);
    });
    cursorY += 34;

    [
      ["Close Call Aging", CLOSE_AGING_BUCKETS, closeCallAging],
      ["Client Conversion Duration", [3, 5, 10, 15].map((m) => [m, `${m} mins +`]), clientConversionDuration],
    ].forEach(([title, items, values]) => {
      ensureSpace(38);
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(13);
      pdf.setTextColor(31, 36, 48);
      pdf.text(title, margin, cursorY);
      cursorY += 7;
      const cardWidth = (pageWidth - 6 * (items.length - 1)) / items.length;
      items.forEach(([key, label], index) => {
        const x = margin + index * (cardWidth + 6);
        pdf.setDrawColor(226, 230, 237);
        pdf.roundedRect(x, cursorY, cardWidth, 25, 2, 2, "S");
        pdf.setFont("helvetica", "normal");
        pdf.setFontSize(8);
        pdf.setTextColor(100, 116, 139);
        pdf.text(label, x + 4, cursorY + 7);
        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(18);
        pdf.setTextColor(31, 36, 48);
        pdf.text(String(values?.error ? "—" : (values?.[key] ?? "—")), x + 4, cursorY + 18);
      });
      cursorY += 34;
    });

    [
      [
        "Call Intelligence",
        "Outcomes, direction, and daily volume",
        [
          ["Call Outcome", metrics.callOutcomeBreakdown],
          ["Call Direction", metrics.callDirectionBreakdown],
          [
            "Call Volume",
            {
              total: callVolumeSeries.length,
              items: callVolumeSeries.map((item) => ({
                label: item.label,
                count: item.count,
                percentage: 0,
              })),
            },
          ],
        ],
      ],
      [
        "Case Intelligence",
        "Status movement and urgency signals",
        [
          ["Case Status", metrics.caseStatusBreakdown],
          ["Urgency Flag", metrics.urgencyFlagBreakdown],
          ["Google Review Status", metrics.googleReviewBreakdown],
        ],
      ],
      [
        "Client and Agent Intelligence",
        "Client mix and agent performance",
        [
          ["Client Type", metrics.clientTypeBreakdown],
          [
            "Agent Performance",
            {
              total: agentPerformance.length,
              items: agentPerformance.map((item) => ({
                label: item.label,
                count: `${item.performance}%`,
                percentage: item.performance,
              })),
            },
          ],
        ],
      ],
    ].forEach(([title, subtitle, breakdowns]) => {
      ensureSpace(18);
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(13);
      pdf.setTextColor(31, 36, 48);
      pdf.text(title, margin, cursorY);
      cursorY += 5;
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(8.5);
      pdf.setTextColor(100, 116, 139);
      pdf.text(subtitle, margin, cursorY);
      cursorY += 7;
      breakdowns.forEach(([breakdownTitle, breakdown]) =>
        drawBreakdown(breakdownTitle, breakdown),
      );
    });

    drawTable(
      "Call Log Status",
      ["Date", "Calls"],
      callLogStatusRows.map((row) => [
        formatDisplayDate(row.date),
        row.total,
      ]),
    );
    drawTable(
      "Call Duration",
      ["Metric", "Value", "Description"],
      [
        ["Total Call Duration", formatDuration(timeDurations.totalCallDurationSeconds), "Qualifying calls for the selected filters"],
        ["Voicemail Call Duration", formatDuration(timeDurations.voicemailCallDurationSeconds), "Calls identified as voicemail"],
      ],
    );
    drawTable(
      "Dependency Tracking",
      ["Total", "Pending", "Approved", "Rejected"],
      [[dependencySummary.total || 0, dependencySummary.pending || 0, dependencySummary.approved || 0, dependencySummary.rejected || 0]],
    );
    drawTable(
      "Frontdesk Team Performance Ranking",
      ["Agent", "Enquiries", "Success PC", "Success GE", "Avg Open Case Duration", "Avg Case Closing Duration", "Score"],
      visibleRankingRows.map((row) => [
        row.agent,
        row.enquiries,
        row.successPC,
        row.successGE,
        `${row.averageOpenDays.toFixed(1)} days`,
        `${row.averageClosingDays.toFixed(1)} days`,
        `${row.performanceScore.toFixed(1)}/10`,
      ]),
    );
    drawTable(
      "Daily Call Target",
      ["Date", "Agent", "Call Target", "Difference"],
      dailyCallTargetRows.map((row) => [
        formatDisplayDate(row.date),
        row.agent,
        `${row.callsMade}/${row.target}`,
        row.difference > 0 ? `+${row.difference}` : row.difference,
      ]),
    );
    drawTable(
      "Call Assignment & Callback Tracking",
      ["Category", "No. of Calls Assigned", "No. of Calls Called Back"],
      OPERATIONAL_TABLE_BLOCKS.map(([block, label]) => [
        label,
        operationalCallTable[block]?.assigned ?? "—",
        operationalCallTable[block]?.calledBack ?? "—",
      ]),
    );
    const drawCategoryTable = (title, columns, rows) =>
      drawTable(
        title,
        ["Date", ...columns.map(([, label]) => label)],
        rows.map((row) => [
          formatDisplayDate(row.date),
          ...columns.map(([key]) => row.counts[key]),
        ]),
      );
    drawTable(
      "Missed Calls",
      ["Missed Call Date", "Missed Call Added Date", "# of Missed Calls", "# Called Back"],
      (taskSummary.missed_calls || []).map((row) => [
        formatDisplayDate(row.date),
        formatDisplayDate(row.added_date),
        row.count,
        row.called_back,
      ]),
    );
    drawTable(
      "Non Responsive Calls",
      ["Non Responsive Call Added Date", "# of Non Responsive Calls", "# Called Back"],
      (taskSummary.non_responsive_calls || []).map((row) => [
        formatDisplayDate(row.date),
        row.count,
        row.called_back,
      ]),
    );
    drawCategoryTable("Call Direction", CALL_DIRECTION_TABLE_COLUMNS, callDirectionRows);
    drawCategoryTable("Urgency Flag", URGENCY_TABLE_COLUMNS, urgencyRows);
    drawCategoryTable("Client Type", CLIENT_TYPE_TABLE_COLUMNS, clientTypeRows);
    drawCategoryTable("Leads", LEADS_TABLE_COLUMNS, leadsRows);
    drawTable(
      "Performance Analysis",
      ["Date", ...PERFORMANCE_COLUMNS.map(([, label]) => label)],
      performanceRows.map((row) => [
        formatDisplayDate(row.date),
        ...PERFORMANCE_COLUMNS.map(([key]) =>
          key === "closingDuration" ? formatDuration(row[key]) : row[key],
        ),
      ]),
    );
    drawTable(
      "Call Loop Closing Duration",
      ["Date Range", "Avg Loop Closing Duration", "Closed Cases", "Success PC", "Success GE", "Dropped"],
      [[
        `${formatDisplayDate(callLoopClosing.startDate)} – ${formatDisplayDate(callLoopClosing.endDate)}`,
        `${callLoopClosing.averageDays.toFixed(1)} days`,
        callLoopClosing.closedCases,
        callLoopClosing.successPC,
        callLoopClosing.successGE,
        callLoopClosing.dropped,
      ]],
    );
    drawTable(
      "Case Type View with Enquiry Questions",
      ["Month & Date", "Visa Type", "Enquiry Question"],
      caseTypeEnquiryRows.flatMap((week) =>
        week.types.map((item) => [
          `${week.label} (${week.range})`,
          item.type,
          item.questions.join("\n"),
        ]),
      ),
    );

    const pdfUrl = URL.createObjectURL(pdf.output("blob"));
    const filename = `FlintDesk_Dashboard_${dateFrom}_to_${dateTo}_Agent-${selectedAgent === "all" ? "All-Agents" : selectedAgent.replace(/[^a-zA-Z0-9_-]/g, "-")}.pdf`;
    // Keep the PDF in the new tab while exposing an explicit download link;
    // the download attribute prevents the browser from naming the blob URL
    // "unknown".
    previewWindow.document.open();
    previewWindow.document.write(
      `<!doctype html><html><head><title>${filename}</title><style>html,body{height:100%;margin:0;font-family:Arial,sans-serif;background:#eef2f7}header{height:52px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;background:#fff;border-bottom:1px solid #dbe3ec;box-sizing:border-box}strong{font-size:14px;color:#1f2430}a{display:inline-flex;align-items:center;height:32px;padding:0 14px;border-radius:7px;background:#38bdf8;color:#fff;text-decoration:none;font-size:13px;font-weight:600}iframe{display:block;width:100%;height:calc(100% - 52px);border:0;background:#fff}</style></head><body><header><strong>${filename}</strong><a href="${pdfUrl}" download="${filename}">Download PDF</a></header><iframe title="Dashboard PDF preview" src="${pdfUrl}"></iframe></body></html>`,
    );
    previewWindow.document.close();
    window.setTimeout(() => URL.revokeObjectURL(pdfUrl), 60000);
  };

  const successPC = metrics.caseStatusBreakdown.items.find(
    (item) => item.label === "Success - PC",
  ) || { count: 0, percentage: 0 };
  const successGE = metrics.caseStatusBreakdown.items.find(
    (item) => item.label === "Success - GE",
  ) || { count: 0, percentage: 0 };
  const reviewsReceived = metrics.googleReviewBreakdown.items.find(
    (item) => item.label === "Submitted",
  ) || { count: 0, percentage: 0 };

  return (
    <section className={`dashboard-v2${loading ? (summary ? " dashboard-v2--loading" : " dashboard-v2--loading dashboard-v2--first-load") : ""}`} aria-busy={loading}>
      <header className="dashboard-v2-header">
        <div className="dashboard-v2-heading">
          <h1>Dashboard</h1>
          <p>Operational overview for the selected period</p>
        </div>
        <DashboardControlBar
          onFetch={() => setFetchRevision((value) => value + 1)}
          draftDateRange={draftDateRange}
          setDraftDateRange={setDraftDateRange}
          setDateFrom={setDateFrom}
          setDateTo={setDateTo}
          setSelectedAgent={setSelectedAgent}
          draftAgent={draftAgent}
          setDraftAgent={setDraftAgent}
          agentOptions={agentOptions}
          draftNumber={draftNumber}
          setDraftNumber={setDraftNumber}
          draftLeadNumber={draftLeadNumber}
          setDraftLeadNumber={setDraftLeadNumber}
          extensionOptions={extensionOptions}
          setSelectedNumber={setSelectedNumber}
          setSelectedLeadNumber={setSelectedLeadNumber}
          numberOptions={numberOptions}
          loading={loading}
          onExportPdf={exportDashboardPdf}
        />
      </header>

      {apiError && (
        <p className="api-status" role="alert">
          <strong aria-label="warning">! </strong>
          {apiError}
        </p>
      )}

      <section
        className="dashboard-v2-primary-row"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}
      >
        <PrimaryKpiCard
          icon="fa-phone"
          label="Total Calls"
          value={callKindCounts.total}
          detail="Selected date range"
          extra={
            <div className="primary-kpi-direction-split">
              <span>Inbound: <b>{callKindCounts.inbound}</b></span>
              <span>Outbound: <b>{callKindCounts.outbound}</b></span>
            </div>
          }
        />
        <PrimaryKpiCard
          icon="fa-phone-volume"
          label="Normal Calls"
          value={callKindCounts.total - callKindCounts.voicemail}
          detail="Total Calls minus Voicemail Calls"
        />
        <PrimaryKpiCard
          icon="fa-voicemail"
          label="Voice Mails"
          value={callKindCounts.voicemail}
          detail="Identified by AI call analysis"
        />
        <DualScoreKpiCard
          icon="fa-chart-line"
          label="Avg Lead Score"
          callsValue={`${metrics.averageLeadScore.toFixed(1)} / 100`}
          leadsValue={`${(leadPriorityMetrics.leadCount ? leadPriorityMetrics.leadTotal / leadPriorityMetrics.leadCount : 0).toFixed(1)} / 100`}
          callsBreakdown={priorityMetrics}
          leadsBreakdown={leadPriorityMetrics}
          onIconClick={() => setIsLeadScoreMatrixOpen(true)}
        />
        <DualScoreKpiCard
          icon="fa-briefcase"
          label="Avg Business Opportunity Value"
          callsValue={formatUsd(
            (priorityMetrics.bosTotal || 0) / (priorityMetrics.bosCount || 1),
          )}
          leadsValue={formatUsd(
            leadPriorityMetrics.bosCount
              ? leadPriorityMetrics.bosTotal / leadPriorityMetrics.bosCount
              : 0,
          )}
          showBreakdown={false}
        />
      </section>
      <CollapsiblePanel id="dashboard-call-kpi-note" className="dashboard-call-kpi-note" title="What Total Calls includes">
        Total Calls includes calls for the extensions configured in the Settings page and includes both processed and not processed calls.
      </CollapsiblePanel>

      <DashboardSection title="Case Lifecycle Stages">
        {/* Five lifecycle stages: every call record counted once (not by client or context). */}
        <section
          className="dashboard-v2-primary-row dashboard-lifecycle-row"
          aria-label="Case lifecycle stages"
          style={{
            gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
          }}
        >
          {lifecycleCards(lifecycle?.key === agingKey ? lifecycle.data : null).map(({ key, label, value }) => (
            <PrimaryKpiCard
              key={key}
              icon={LIFECYCLE_CARD_ICONS[key]}
              tone={key}
              label={label}
              value={lifecycle?.key !== agingKey ? "…" : lifecycle.data ? value : "—"}
              detail="Individual call records"
            />
          ))}
        </section>
        {/* The four existing Open Call Aging cards, unchanged, grouped in one rectangular container. */}
        <div className="dashboard-aging-group" role="group" aria-label="Open call aging">
          <section
            className="dashboard-v2-primary-row dashboard-open-aging-row"
            style={{
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            }}
          >
            {[3, 5, 10, 15].map((days) => (
              <PrimaryKpiCard
                key={days}
                icon="fa-hourglass-half"
                label={`Open ${days}+ Days`}
                value={agingCardValue(agingCards, days)}
                detail="Unresolved calls, cumulative"
                extra={<OpenCallAgingContext rows={agingContextRows(openCallAgeContext, selectedAgent, days)} />}
              />
            ))}
          </section>
          {agingContextNote(openCallAgeContext, selectedAgent) && (
            <p className="open-aging-context-note">{agingContextNote(openCallAgeContext, selectedAgent)}</p>
          )}
        </div>
      </DashboardSection>

      <DashboardSection title="Close Call Aging">
        <section
          className="dashboard-v2-primary-row"
          style={{
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          }}
        >
          {CLOSE_AGING_BUCKETS.map(([key, label]) => (
            <PrimaryKpiCard
              key={key}
              icon="fa-hourglass-end"
              label={label}
              value={closeCallAging?.error ? "—" : (closeCallAging?.[key] ?? "—")}
              detail={closeCallAging && !closeCallAging.error
                ? `Calls closed in this time, of ${closeCallAging.eligible} closed`
                : "Success GE / Success PC / Dropped"}
            />
          ))}
        </section>
      </DashboardSection>

      <DashboardSection title="Client Conversion Duration">
        <section
          className="dashboard-v2-primary-row"
          style={{
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          }}
        >
          {[3, 5, 10, 15].map((minutes) => (
            <PrimaryKpiCard
              key={minutes}
              icon="fa-stopwatch"
              label={`${minutes} mins +`}
              value={clientConversionDuration?.error ? "—" : (clientConversionDuration?.[minutes] ?? "—")}
              detail={clientConversionDuration && !clientConversionDuration.error
                ? `${clientConversionDuration.eligible} calls measured, voicemail excluded, ${clientConversionDuration.invalidDuration} invalid`
                : "Calls of this length or longer, voicemail excluded"}
            />
          ))}
        </section>
      </DashboardSection>

      <DashboardSection title="Leads Management Tracking">
        <section className="dashboard-v2-grid dashboard-v2-grid-3">
          <PrimaryKpiCard
            icon="fa-calendar-days"
            label="Leads Per Day"
            value={metrics.leadsPerDay.toFixed(1)}
            detail={`Total: ${metrics.leads} inbound leads`}
          />
          <PrimaryKpiCard
            icon="fa-phone-volume"
            label="Leads Called Per Day"
            value={metrics.leadsCalledPerDay.toFixed(1)}
            detail={`Total outbound: ${metrics.outboundCalls}`}
          />
          <PrimaryKpiCard
            icon="fa-circle-check"
            label="Closed Loop Rate"
            value={`${metrics.closedLoopRate.toFixed(1)}%`}
            detail={`${metrics.resolvedLeads} of ${metrics.leads} inbound leads resolved`}
          />
        </section>
      </DashboardSection>

      <DashboardSection title="Lead Scoring and Business Opportunity">
        <section
          className="dashboard-v2-primary-row"
          style={{
            gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
          }}
        >
          <PrimaryKpiCard
            icon="fa-bolt"
            label="BOOK NOW"
            value={priorityMetrics.BOOK_NOW}
            detail="Lead Score 70–100"
          />
          <PrimaryKpiCard
            icon="fa-circle-info"
            label="GATHER MORE INFO"
            value={priorityMetrics.GATHER_MORE_INFO}
            detail="Lead Score 40–69"
          />
          <PrimaryKpiCard
            icon="fa-seedling"
            label="NURTURE"
            value={priorityMetrics.NURTURE}
            detail="Lead Score 0–39"
          />
          <PrimaryKpiCard
            icon="fa-triangle-exclamation"
            label="Urgency Override"
            value={priorityMetrics.urgency}
            detail="Same-day consultation signal"
          />
          <PrimaryKpiCard
            icon="fa-arrow-trend-up"
            label="BOS HIGH"
            value={priorityMetrics.HIGH}
            detail="BOS 8–10"
          />
          <PrimaryKpiCard
            icon="fa-arrow-up"
            label="BOS HIGH (Mid)"
            value={priorityMetrics.HIGH_MID}
            detail="BOS 7–7.9"
          />
          <PrimaryKpiCard
            icon="fa-minus"
            label="BOS STANDARD"
            value={priorityMetrics.STANDARD}
            detail="BOS 4–6.9"
          />
          <PrimaryKpiCard
            icon="fa-circle-question"
            label="BOS N/A"
            value={priorityMetrics.N_A}
            detail="Unclassified or unavailable"
          />
        </section>
      </DashboardSection>

      <DailyCallTargetTable rows={dailyCallTargetRows} />
      <section
        className="time-duration-kpi-grid"
        aria-label="Call duration metrics"
      >
        <TimeDurationKpiCard
          icon="fa-phone-volume"
          title="Total Call Duration"
          description="Total duration of normal calls; voicemail duration is excluded."
          seconds={timeDurations.totalCallDurationSeconds}
          loading={timeDurationsLoading}
          error={timeDurationsError}
          callCount={timeDurations.qualifyingCallCount}
        />
        <TimeDurationKpiCard
          icon="fa-voicemail"
          title="Voicemail Call Duration"
          description="Total duration of calls identified as voicemail."
          seconds={timeDurations.voicemailCallDurationSeconds}
          loading={timeDurationsLoading}
          error={timeDurationsError}
          callCount={timeDurations.qualifyingCallCount}
        />
      </section>
      <CallLogStatusTable rows={callLogStatusRows} />
      <OperationalCallTable data={operationalCallTable} loading={loading} />

      <section className="dashboard-v2-grid dashboard-v2-grid-2 dashboard-category-tables">
        <TaskSummaryTable
          title="MISSED CALLS"
          columns={[
            ["date", "Missed Call Date", true],
            ["added_date", "Missed Call Added Date", true],
            ["count", "# of Missed Call"],
            ["called_back", "# of Called back"],
          ]}
          rows={taskSummary.missed_calls || []}
        />
        <TaskSummaryTable
          title="NON RESPONSIVE CALLS"
          columns={[
            ["date", "Non Responsive Call Added Date", true],
            ["count", "# of Non Responsive Calls"],
            ["called_back", "# of Called back"],
          ]}
          rows={taskSummary.non_responsive_calls || []}
        />
      </section>

      <DashboardSection title="Dependency Tracking">
        <section
          className="dashboard-v2-primary-row"
          style={{
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          }}
        >
          <PrimaryKpiCard
            icon="fa-link"
            label="Total Dependencies"
            value={dependencySummary.total || 0}
            detail="Selected date range"
          />
          <PrimaryKpiCard
            icon="fa-clock"
            label="Pending Dependencies"
            value={dependencySummary.pending || 0}
            detail="Awaiting review"
          />
          <PrimaryKpiCard
            icon="fa-circle-check"
            label="Approved Dependencies"
            value={dependencySummary.approved || 0}
            detail="Review completed"
          />
        </section>
      </DashboardSection>

      <section className="dashboard-v2-grid dashboard-v2-grid-3 dashboard-review-kpis">
        <BreakdownCard
          title="Google Review Status"
          total={metrics.googleReviewBreakdown.total}
          items={metrics.googleReviewBreakdown.items}
        />
        <ComparisonBarCard
          title="Success PC vs Reviews Received"
          leftLabel="Success - PC"
          leftValue={successPC.count}
          rightLabel="Reviews Received"
          rightValue={reviewsReceived.count}
        />
        <ComparisonBarCard
          title="Success GE vs Reviews Received"
          leftLabel="Success - GE"
          leftValue={successGE.count}
          rightLabel="Reviews Received"
          rightValue={reviewsReceived.count}
        />
      </section>

      <section className="dashboard-v2-grid dashboard-v2-grid-2 dashboard-category-tables">
        <PerformanceAnalysisTable rows={performanceRows} />
        <CallLoopClosingTable summary={callLoopClosing} />
      </section>

      <section className="dashboard-v2-grid dashboard-v2-grid-2 dashboard-category-tables">
        <DateCategoryTable
          title="CALL DIRECTION"
          columns={CALL_DIRECTION_TABLE_COLUMNS}
          rows={callDirectionRows}
          highlight="Leads Outbound"
        />
        <DateCategoryTable
          title="URGENCY FLAG"
          columns={URGENCY_TABLE_COLUMNS}
          rows={urgencyRows}
        />
      </section>

      <section className="dashboard-v2-grid dashboard-v2-grid-2 dashboard-category-tables">
        <DateCategoryTable
          title="CLIENT TYPE"
          columns={CLIENT_TYPE_TABLE_COLUMNS}
          rows={clientTypeRows}
        />
        <DateCategoryTable
          title="LEADS"
          columns={LEADS_TABLE_COLUMNS}
          rows={leadsRows}
          highlight="Others"
        />
      </section>

      <section className="dashboard-v2-grid dashboard-category-tables">
        <RankingTable rows={visibleRankingRows} />
      </section>

      {isLeadScoreMatrixOpen && (
        <LeadScoreMatrixModal onClose={() => setIsLeadScoreMatrixOpen(false)} />
      )}
    </section>
  );
}

const LIFECYCLE_CARD_ICONS = {
  open: "fa-folder-open", in_transition: "fa-arrow-right-arrow-left", lost: "fa-circle-xmark", dropped: "fa-ban", success: "fa-circle-check",
};

function DashboardSection({ title, subtitle, badge, children }) {
  if (
    [
      "Leads Management Tracking",
      "Lead Scoring and Business Opportunity",
    ].includes(title)
  )
    return null;
  return (
    <section className="dashboard-v2-section">
      <div className="dashboard-v2-section-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {badge && <span className="dashboard-v2-section-badge">{badge}</span>}
      </div>
      {children}
    </section>
  );
}

// Three small figures under an Open Call Aging card while one agent is selected (see data/openCallAgingContext.js).
function OpenCallAgingContext({ rows }) {
  if (!rows) return null;
  return (
    <dl className="open-aging-context">
      {rows.map((row) => (
        <div key={row.key}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function PrimaryKpiCard({ icon, label, value, detail, stars, extra, tone }) {
  return (
    <article className={`primary-kpi-card${tone ? ` lifecycle-card lifecycle-${tone}` : ""}`}>
      <div className="primary-kpi-card-top">
        <span className="primary-kpi-label">{label}</span>
        <span className="primary-kpi-icon">
          <i className={`fa-solid ${icon}`} />
        </span>
      </div>
      <strong className="primary-kpi-value">{value}</strong>
      <div className="primary-kpi-foot">
        {stars !== undefined && (
          <div
            className="primary-kpi-stars"
            aria-label={`${stars.toFixed(2)} out of 5 stars`}
          >
            {Array.from({ length: 5 }, (_, index) => (
              <span
                key={index}
                className={index < Math.round(stars) ? "filled" : ""}
              >
                ★
              </span>
            ))}
          </div>
        )}
        <small>{detail}</small>
      </div>
      {extra}
    </article>
  );
}

function DashboardControlBar({
  onFetch,
  draftDateRange,
  setDraftDateRange,
  setDateFrom,
  setDateTo,
  setSelectedAgent,
  draftAgent,
  setDraftAgent,
  draftNumber,
  setDraftNumber,
  draftLeadNumber,
  setDraftLeadNumber,
  setSelectedNumber,
  setSelectedLeadNumber,
  numberOptions,
  extensionOptions,
  agentOptions,
  loading,
  onExportPdf,
}) {
  const [activePreset, setActivePreset] = useState(null);
  const applyDashboardFilters = () => {
    if (!isValidDateRange(draftDateRange)) return;
    setDateFrom(draftDateRange.start);
    setDateTo(draftDateRange.end);
    setSelectedAgent(draftAgent);
    setSelectedNumber(draftNumber);
    setSelectedLeadNumber(draftLeadNumber);
    onFetch();
  };
  const setPreset = (monthly) => {
    const end = new Date();
    const start = new Date(end);
    if (monthly) start.setDate(1);
    else start.setDate(end.getDate() - 6);
    const nextStart = getLocalDateString(start);
    const nextEnd = getLocalDateString(end);
    setDraftDateRange({ start: nextStart, end: nextEnd });
    setActivePreset(monthly ? "monthly" : "weekly");
  };
  const isWeekly = activePreset === "weekly";
  const isMonthly = activePreset === "monthly";
  const hasAgentFilter = draftAgent !== "all";
  const hasLeadNumberFilter = Boolean(draftNumber);
  const hasExtensionFilter = Boolean(draftLeadNumber);

  return (
    <>
      <StickyFilterRegion>
      <ScrollableFilterBelt className="dashboard-control-bar">
        <DateRangeSelector
          value={draftDateRange}
          onChange={(nextRange) => {
            setDraftDateRange(nextRange);
            setActivePreset(null);
          }}
          compact
        />

        <div
          className="calls-toolbar dashboard-range-presets"
          aria-label="Date range presets"
        >
          <button
            type="button"
            className={isWeekly ? "active" : ""}
            aria-pressed={isWeekly}
            onClick={() => setPreset(false)}
          >
            Weekly
          </button>
          <button
            type="button"
            className={isMonthly ? "active" : ""}
            aria-pressed={isMonthly}
            onClick={() => setPreset(true)}
          >
            Monthly
          </button>
        </div>

        <div className="calls-filter-group">
          <span className="calls-toolbar-label">Agent</span>
          <div className="date-input calls-toolbar-select">
            <select
              value={draftAgent}
              disabled={hasLeadNumberFilter || hasExtensionFilter}
              onChange={(event) => {
                const agent = event.target.value;
                setDraftAgent(agent);
                if (agent !== "all") {
                  setDraftNumber("");
                  setDraftLeadNumber("");
                }
              }}
            >
              <option value="all">All Agents</option>
              {agentOptions.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="calls-filter-group">
          <span className="calls-toolbar-label">Lead number</span>
          <select
            className="calls-filter-select"
            value={draftNumber}
            onChange={(event) => {
              const number = event.target.value;
              setDraftNumber(number);
              if (number) {
                setDraftAgent("all");
                setDraftLeadNumber("");
              }
            }}
            disabled={
              hasAgentFilter || (hasExtensionFilter && !hasLeadNumberFilter)
            }
          >
            <option value="">All Numbers</option>
            {numberOptions.map((number) => (
              <option key={number} value={number}>
                {number}
              </option>
            ))}
          </select>
        </div>

        <div className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Extension</span>
          <select
            className="calls-filter-select"
            value={draftLeadNumber}
            onChange={(event) => {
              const extension = event.target.value;
              if (extension) setDraftAgent("all");
              setDraftLeadNumber(extension);
            }}
            disabled={hasAgentFilter}
          >
            <option value="">All Extensions</option>
            {extensionOptions.map((extension) => (
              <option key={extension} value={extension}>
                {displayExtensionOption(extension)}
              </option>
            ))}
          </select>
        </div>
      </ScrollableFilterBelt>
      <div className="filter-belt-actions">
        <button
          type="button"
          className="primary-button calls-toolbar-apply"
          onClick={applyDashboardFilters}
          disabled={loading || !isValidDateRange(draftDateRange)}
          aria-busy={loading}
        >
          {loading ? <><span className="dashboard-apply-spinner" aria-hidden="true" />Loading…</> : "Apply"}
        </button>
        {/* Apply is disabled for the whole load so the filters on screen always match the numbers that arrive. */}
        {loading && <span className="dashboard-loading-note" role="status" aria-live="polite">Loading dashboard…</span>}
        <button
          type="button"
          className="table-action dashboard-export-button"
          onClick={onExportPdf}
          title="Export the current Dashboard to PDF"
        >
          <i className="fa-solid fa-file-pdf" /> Export to PDF
        </button>
      </div>
      </StickyFilterRegion>
    </>
  );
}

function DailyCallTargetTable({ rows }) {
  const [showAll, setShowAll] = useState(false);
  const groupedRows = rows.reduce((groups, row) => {
    const existing = groups[groups.length - 1];
    if (existing?.date === row.date) existing.rows.push(row);
    else groups.push({ date: row.date, rows: [row] });
    return groups;
  }, []);
  const visibleGroups = showAll ? groupedRows : groupedRows.slice(0, 2);

  return (
    <section className="call-log-status-section">
      <div className="call-log-status-heading">
        <h2>Daily Call Target</h2>
      </div>
      <div className="date-category-table-card daily-call-target-card">
        <table className="date-category-table daily-call-target-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Agent</th>
              <th>Call Target</th>
              <th>Difference</th>
            </tr>
          </thead>
          <tbody>
            {visibleGroups.length ? visibleGroups.map((group) =>
              group.rows.map((row, index) => (
                <tr key={`${row.agent}-${row.date}`}>
                  {index === 0 && (
                    <td rowSpan={group.rows.length}>
                      {formatDisplayDate(group.date)}
                    </td>
                  )}
                  <td className="daily-call-target-agent">{row.agent}</td>
                  <td>
                    {row.callsMade}/{row.target}
                  </td>
                  <td>
                    {row.difference > 0 ? `+${row.difference}` : row.difference}
                  </td>
                </tr>
              )),
            ) : (
              <tr>
                <td colSpan="4" className="dashboard-empty-state">
                  No front-desk agent target data is available for these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {groupedRows.length > 2 && (
        <button
          type="button"
          className="table-action dashboard-show-more-button"
          onClick={() => setShowAll((expanded) => !expanded)}
        >
          {showAll
            ? "Show Less"
            : `Show More (${groupedRows.length - 2} more dates)`}
        </button>
      )}
    </section>
  );
}

// Result keys older servers used per status (kept so a newer page still works against an older server).
const LEGACY_STATUS_KEYS = {
  Open: "open", "Initial email sent (Fee Details shared)": "initialEmail", "Success PC": "successPC", "Success GE": "successGE",
  "PC Follow Up": "pcFollowUp", "Fee details shared & FU": "feeDetails",
  "Client Side Delay": "clientDelay", Blocker: "blocker", Dropped: "dropped",
  "Same Client Context": "sameClientContext", "Initial Email Follow Up": "initialEmailFollowup",
};
// Calls with this Case Status Change (one dropdown option) on that day.
const statusCount = (row, label) => (row.statusCounts ? row.statusCounts[label] ?? 0 : row[LEGACY_STATUS_KEYS[label]] ?? 0);

// One column per Case Status Change dropdown option (statusOptions.js is the single list), filled from each call's
// saved Case Status Change: every column, Open included, counts the calls set to exactly that value.
function CallLogStatusTable({ rows }) {
  const totals = rows.reduce(
    (result, row) => {
      result.total += row.total;
      result.noStatus += row.noStatus || 0;
      result.otherStatus += row.otherStatus || 0;
      CASE_STATUS_OPTIONS.forEach((label) => {
        result.status[label] += statusCount(row, label);
      });
      return result;
    },
    {
      total: 0,
      noStatus: 0,
      otherStatus: 0,
      status: Object.fromEntries(CASE_STATUS_OPTIONS.map((label) => [label, 0])),
    },
  );

  return (
    <section className="call-log-status-section">
      <div className="call-log-status-heading">
        <h2>CALL LOG STATUS</h2>
        <span>{totals.total} calls in selected range</span>
        {(totals.noStatus > 0 || totals.otherStatus > 0) && (
          <span className="call-log-status-note">
            {totals.noStatus} with no Case Status Change yet
            {totals.otherStatus > 0 ? ` · ${totals.otherStatus} with a status that is not in the list` : ""}
          </span>
        )}
      </div>
      <div className="call-log-status-wrap">
        <table className="call-log-status-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Total # of calls</th>
              {CASE_STATUS_OPTIONS.map((label) => (
                <th key={label} title={label === "Open" ? "Calls whose Case Status Change is set to Open" : undefined}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.date}>
                <td>{formatDisplayDate(row.date)}</td>
                <td>{row.total}</td>
                {CASE_STATUS_OPTIONS.map((label) => (
                  <td key={label}>{statusCount(row, label)}</td>
                ))}
              </tr>
            ))}
            <tr className="call-log-status-total">
              <th>TOTAL</th>
              <th>{totals.total}</th>
              {CASE_STATUS_OPTIONS.map((label) => (
                <th key={label}>{totals.status[label]}</th>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

function DualScoreKpiCard({
  icon,
  label,
  callsValue,
  leadsValue,
  callsBreakdown,
  leadsBreakdown,
  showBreakdown = true,
  onIconClick,
}) {
  const [view, setView] = useState("calls");
  const breakdown = view === "calls" ? callsBreakdown : leadsBreakdown;
  return (
    <article className="primary-kpi-card dual-score-kpi">
      <div className="primary-kpi-card-top">
        <span className="primary-kpi-label">{label}</span>
        {onIconClick ? (
          <button
            type="button"
            className="primary-kpi-icon primary-kpi-icon-button"
            onClick={onIconClick}
            aria-label="Open lead score matrix"
            data-tooltip="Click to see the lead score matrix"
          >
            <i className={`fa-solid ${icon}`} />
          </button>
        ) : (
          <span className="primary-kpi-icon">
            <i className={`fa-solid ${icon}`} />
          </span>
        )}
      </div>
      <div
        className="kpi-view-toggle"
        role="group"
        aria-label={`${label} context`}
      >
        <button
          className={view === "calls" ? "active" : ""}
          onClick={() => setView("calls")}
        >
          Calls
        </button>
        <button
          className={view === "leads" ? "active" : ""}
          onClick={() => setView("leads")}
        >
          Leads
        </button>
      </div>
      <strong className="primary-kpi-value">
        {view === "calls" ? callsValue : leadsValue}
      </strong>
      {showBreakdown && (
        <div className="kpi-score-breakdown">
          <span>
            Book now <b>{breakdown.BOOK_NOW || 0}</b>
          </span>
          <span>
            Gather more info <b>{breakdown.GATHER_MORE_INFO || 0}</b>
          </span>
          <span>
            Nurture <b>{breakdown.NURTURE || 0}</b>
          </span>
        </div>
      )}
    </article>
  );
}

const CLOSE_AGING_BUCKETS = [
  ["under3", "Closed in under 3 days"],
  ["d3to4", "Closed in 3-4 days"],
  ["d5to9", "Closed in 5-9 days"],
  ["d10to14", "Closed in 10-14 days"],
  ["d15plus", "Closed in 15+ days"],
];
const OPERATIONAL_TABLE_BLOCKS = [
  ["leads", "Leads"],
  ["missedCalls", "Missed Calls"],
  ["nonResponsiveCalls", "Non Responsive Calls"],
  ["smLeads", "SM Leads"],
];
const EMPTY_OPERATIONAL_TABLE = {};

function OperationalCallTable({ data = EMPTY_OPERATIONAL_TABLE, loading }) {
  const show = (block, key) => {
    if (!data || data[block]?.error) return "—";
    const value = data[block]?.[key];
    return value === undefined ? (loading ? "…" : "—") : value;
  };
  return (
    <section className="call-log-status-section website-paid-ads-section">
      <div className="call-log-status-heading website-paid-ads-heading">
        <h2>Call Assignment &amp; Callback Tracking</h2>
        <span>Assigned = owner chosen and Submit saved; Called Back = green rows</span>
      </div>
      <div className="ops-call-grid">
        {OPERATIONAL_TABLE_BLOCKS.map(([block, label]) => (
          <div className="ops-call-block" key={block}>
            <h3 className="ops-call-block-title">{label}</h3>
            <dl className="ops-call-metrics">
              <div>
                <dt>No. of Calls Assigned</dt>
                <dd>{show(block, "assigned")}</dd>
              </div>
              <div>
                <dt>No. of Calls Called Back</dt>
                <dd>{show(block, "calledBack")}</dd>
              </div>
            </dl>
          </div>
        ))}
      </div>
    </section>
  );
}

function DateCategoryTable({ title, columns, rows, highlight }) {
  const totals = Object.fromEntries(columns.map(([key]) => [key, 0]));
  rows.forEach((row) =>
    columns.forEach(([key]) => {
      totals[key] += row.counts[key];
    }),
  );

  return (
    <article className="date-category-table-card">
      <table className="date-category-table">
        <thead>
          <tr className="date-category-title-row">
            <th colSpan={columns.length + 1}>{title}</th>
          </tr>
          <tr>
            <th>Date</th>
            {columns.map(([key, label]) => (
              <th
                key={key}
                className={highlight === label ? "table-column-highlight" : ""}
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.date}>
              <td>{formatDisplayDate(row.date)}</td>
              {columns.map(([key]) => (
                <td key={key}>{row.counts[key]}</td>
              ))}
            </tr>
          ))}
          <tr className="date-category-total-row">
            <th>TOTAL</th>
            {columns.map(([key]) => (
              <th key={key}>{totals[key]}</th>
            ))}
          </tr>
        </tbody>
      </table>
    </article>
  );
}

function TaskSummaryTable({ title, columns, rows }) {
  const countColumns = columns.filter(([, , isDate]) => !isDate);
  const totals = Object.fromEntries(
    countColumns.map(([key]) => [
      key,
      rows.reduce((sum, row) => sum + (Number(row[key]) || 0), 0),
    ]),
  );
  const formatDate = (value) => (value ? formatDisplayDate(value) : "—");

  return (
    <article className="date-category-table-card">
      <table className="date-category-table">
        <thead>
          <tr className="date-category-title-row">
            <th colSpan={columns.length}>{title}</th>
          </tr>
          <tr>
            {columns.map(([key, label]) => (
              <th key={key}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.date}>
              {columns.map(([key, , isDate]) => (
                <td key={key}>{isDate ? formatDate(row[key]) : row[key]}</td>
              ))}
            </tr>
          ))}
          <tr className="date-category-total-row">
            <th>TOTAL</th>
            {columns.slice(1).map(([key, , isDate]) => (
              <th key={key}>{isDate ? "" : totals[key]}</th>
            ))}
          </tr>
        </tbody>
      </table>
    </article>
  );
}

function formatClockDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function TimeDurationKpiCard({
  icon,
  title,
  description,
  seconds,
  loading,
  error,
  callCount,
}) {
  const noData = !loading && !error && !callCount;
  return (
    <article className={`time-duration-kpi-card ${error ? "error" : ""}`}>
      <div className="time-duration-kpi-top">
        <div>
          <span>{title}</span>
          <p>{description}</p>
        </div>
        <i className={`fa-solid ${icon}`} />
      </div>
      {loading ? (
        <div
          className="time-duration-skeleton"
          aria-label={`Loading ${title}`}
        />
      ) : (
        <strong>{formatClockDuration(seconds)}</strong>
      )}
      <small>
        {error ||
          (noData
            ? "No call duration data for the selected filters."
            : "Selected date range and agent filter")}
      </small>
    </article>
  );
}

function PerformanceAnalysisTable({ rows }) {
  const averageKeys = new Set([
    "quality",
    "closingDuration",
    "openCases",
    "dataQuality",
    "averageOpenDays",
    "averageClosingDays",
  ]);
  const weightedAverage = (key) => {
    const weighted = rows.reduce(
      (sum, row) => sum + (Number(row[key]) || 0) * row.enquiries,
      0,
    );
    const weight = rows.reduce((sum, row) => sum + row.enquiries, 0);
    return weight ? Number((weighted / weight).toFixed(1)) : 0;
  };
  const totals = Object.fromEntries(
    PERFORMANCE_COLUMNS.map(([key]) => [
      key,
      averageKeys.has(key)
        ? weightedAverage(key)
        : rows.reduce((sum, row) => sum + (Number(row[key]) || 0), 0),
    ]),
  );

  const displayValue = (key, value) =>
    key === "closingDuration" ? formatDuration(value)
      : key === "business" ? formatUsd(value)
      : key === "averageOpenDays" || key === "averageClosingDays" ? `${Number(value || 0).toFixed(1)} days`
      : value;

  return (
    <article className="date-category-table-card performance-table-card">
      <table className="date-category-table performance-table">
        <thead>
          <tr className="date-category-title-row">
            <th colSpan={PERFORMANCE_COLUMNS.length + 1}>
              PERFORMANCE ANALYSIS
            </th>
          </tr>
          <tr>
            <th>Date</th>
            {PERFORMANCE_COLUMNS.map(([key, label]) => (
              <th key={key}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.date}>
              <td>{formatDisplayDate(row.date)}</td>
              {PERFORMANCE_COLUMNS.map(([key]) => (
                <td key={key}>{displayValue(key, row[key])}</td>
              ))}
            </tr>
          ))}
          <tr className="date-category-total-row">
            <th>TOTAL</th>
            {PERFORMANCE_COLUMNS.map(([key]) => (
              <th key={key}>{displayValue(key, totals[key])}</th>
            ))}
          </tr>
        </tbody>
      </table>
    </article>
  );
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

function CallLoopClosingTable({ summary }) {
  const displayRange = `${formatDisplayDate(summary.startDate)} – ${formatDisplayDate(summary.endDate)}`;
  return (
    <article className="date-category-table-card call-loop-closing-card">
      <table className="date-category-table call-loop-closing-table">
        <thead>
          <tr className="date-category-title-row">
            <th colSpan="6">Call Loop Closing Duration</th>
          </tr>
          <tr>
            <th>Date Range</th>
            <th>Avg Loop Closing Duration</th>
            <th># of Closed Cases</th>
            <th>Success PC</th>
            <th>Success GE</th>
            <th>Dropped</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>{displayRange}</td>
            <td>
              <strong>{summary.averageDays.toFixed(1)} days</strong>
            </td>
            <td>{summary.closedCases}</td>
            <td>{summary.successPC}</td>
            <td>{summary.successGE}</td>
            <td>{summary.dropped}</td>
          </tr>
        </tbody>
      </table>
      <p className="call-loop-closing-note">
        Average uses finished cases with both a valid opening date and closing
        date.
      </p>
    </article>
  );
}

function RankingTable({ rows }) {
  const displayValue = (key, value) =>
    key === "averageOpenDays" || key === "averageClosingDays"
      ? `${Number(value).toFixed(1)} days`
      : value;
  return (
    <article className="date-category-table-card performance-table-card ranking-table-card">
      <table className="date-category-table performance-table ranking-table">
        <thead>
          <tr className="date-category-title-row">
            <th colSpan={RANKING_COLUMNS.length + 1}>
              FRONTDESK TEAM PERFORMANCE RANKING
            </th>
          </tr>
          <tr>
            <th>Agent</th>
            {RANKING_COLUMNS.map(([key, label]) => (
              <th key={key}>{label.replace("No of ", "# of ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row, index) => (
              <Fragment key={row.agent}>
                <tr>
                  <td>
                    <strong>Rank {index + 1}</strong>
                    <br />
                    {row.agent}
                  </td>
                  {RANKING_COLUMNS.map(([key]) => (
                    <td key={key}>{displayValue(key, row[key])}</td>
                  ))}
                </tr>
                <tr className="date-category-agent-total-row">
                  <th colSpan={RANKING_COLUMNS.length + 1}>
                    {row.agent} TOTAL SCORE: {row.performanceScore.toFixed(1)} /
                    10
                  </th>
                </tr>
              </Fragment>
            ))
          ) : (
            <tr>
              <td colSpan={RANKING_COLUMNS.length + 1}>
                No frontdesk agents available.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </article>
  );
}

function BreakdownCard({ title, total, items }) {
  return (
    <article className="dashboard-breakdown-card">
      <div className="dashboard-breakdown-header">
        <h3>{title}</h3>
        <span>{total} total</span>
      </div>

      <div className="dashboard-breakdown-list">
        {items.length ? (
          <>
            {items.map((item) => (
              <div key={`${title}-${item.label}`} className="metric-row">
                <span className="metric-row-label">{item.label}</span>
                <span className="metric-row-count">{item.count}</span>
                <span className="metric-row-pct">
                  {formatPercentage(item.percentage)}
                </span>
              </div>
            ))}

            <div className="metric-row metric-row-total">
              <span className="metric-row-label">Total</span>
              <span className="metric-row-count">{total}</span>
              <span className="metric-row-pct">
                {formatPercentage(total ? 100 : 0)}
              </span>
            </div>
          </>
        ) : (
          <div className="dashboard-empty-state">No data for this range.</div>
        )}
      </div>
    </article>
  );
}

// Bar chart comparing two counts (Success PC vs Reviews Received). Reuses the
// same .mini-chart primitive as the existing Call Volume card for visual consistency.
function ComparisonBarCard({
  title,
  leftLabel,
  leftValue,
  rightLabel,
  rightValue,
}) {
  const items = [
    { label: leftLabel, count: leftValue },
    { label: rightLabel, count: rightValue },
  ];
  const maxValue = Math.max(1, leftValue, rightValue);

  return (
    <article className="dashboard-breakdown-card">
      <div className="dashboard-breakdown-header">
        <h3>{title}</h3>
        <span>{leftValue + rightValue} total</span>
      </div>
      <div className="mini-chart comparison-mini-chart">
        {items.map((item) => (
          <div key={item.label} className="mini-chart-bar-group">
            <div className="mini-chart-bar-wrap">
              <span
                className="mini-chart-bar"
                style={{ height: `${(item.count / maxValue) * 100}%` }}
              />
            </div>
            <label>
              {item.label}
              <br />
              <strong>{item.count}</strong>
            </label>
          </div>
        ))}
      </div>
    </article>
  );
}

function BarChartCard({
  title,
  subtitle,
  items,
  valueKey = "count",
  valueSuffix = "",
  emptyMessage = "No chart data available.",
  variant,
}) {
  const maxValue = Math.max(
    1,
    ...items.map((item) => item[valueKey] ?? item.count),
  );

  if (variant === "performance") {
    // Sort is display-only (a copy), so it never mutates the source data —
    // safe to swap the mock items for a real API response later.
    const sortedItems = [...items].sort(
      (a, b) => (b[valueKey] ?? b.count) - (a[valueKey] ?? a.count),
    );

    return (
      <article className="dashboard-breakdown-card">
        <div className="dashboard-breakdown-header">
          <h3>{title}</h3>
          <span>{subtitle}</span>
        </div>
        <div className="perf-chart">
          {sortedItems.length ? (
            sortedItems.map((item) => {
              const value = item[valueKey] ?? item.count;
              const pct = Math.max(0, Math.min(100, (value / maxValue) * 100));
              return (
                <div key={`${title}-${item.label}`} className="perf-chart-row">
                  <span className="perf-chart-name" title={item.label}>
                    {item.label}
                  </span>
                  <div className="perf-chart-track">
                    <span
                      className="perf-chart-bar"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="perf-chart-value">
                    {value}
                    {valueSuffix}
                  </span>
                </div>
              );
            })
          ) : (
            <div className="dashboard-empty-state">{emptyMessage}</div>
          )}
        </div>
      </article>
    );
  }

  return (
    <article className="dashboard-breakdown-card">
      <div className="dashboard-breakdown-header">
        <h3>{title}</h3>
        <span>{subtitle}</span>
      </div>

      <div className="mini-chart">
        {items.length ? (
          items.map((item) => {
            const value = item[valueKey] ?? item.count;
            return (
              <div
                key={`${title}-${item.label}`}
                className="mini-chart-bar-group"
              >
                <div className="mini-chart-bar-wrap">
                  <span
                    className="mini-chart-bar"
                    style={{ height: `${(value / maxValue) * 100}%` }}
                  />
                </div>
                <label>
                  {item.label}
                  <br />
                  <strong>
                    {value}
                    {valueSuffix}
                  </strong>
                </label>
              </div>
            );
          })
        ) : (
          <div className="dashboard-empty-state">{emptyMessage}</div>
        )}
      </div>
    </article>
  );
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

const RANKING_HIDDEN_AGENTS = new Set(["sarth", "satish"]);
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
        "2000-01-01",
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
