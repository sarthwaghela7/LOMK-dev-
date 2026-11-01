import { usePersistentTableState, tableStorageKey, markSavedToday, savedOnAnEarlierDay } from "../data/persistentTableState.js";
import { enquiryOpenDuration } from "../data/enquiryOpenDuration.js";
import { caseStatusKey, isLostStatus } from "../data/statusOptions.js";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  getCallsList,
  getCallAudioBlobUrl,
  getDashboardFrontDeskAgents,
  getSyncFilters,
  updateCallField,
  updateManualFields,
} from "../data/api.js";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { audioDownloadName } from "../components/CallMediaModals.jsx";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { AttachmentCell } from "../components/AttachmentCell.jsx";
import { getDefaultDateRange, isValidDateRange } from "../data/filterDates.js";
import {
  ALIAS_EXTENSION_FOR_LEAD_NUMBER,
  callMatchesLeadNumber,
  displayExtensionOption,
  getLeadNumberExtensions,
  getOutboundLeadNumberOptions,
} from "../data/leadNumberFilter.js";
import { LEAD_SOURCE_OPTIONS } from "../data/leadSource.js";
import {
  calculatePriority,
  rankCallsByPriority,
} from "../data/priorityEngine.js";
import {
  BusinessOpportunityCell,
  LeadScoreCell,
} from "../components/PriorityScoreCell.jsx";
import { displayClientType } from "../data/displayLabels.js";
import { formatDisplayDate } from "../data/displayDate.js";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";

const normalizePhoneSearch = (value) => {
  const digits = String(value || "").replace(/\D/g, "");
  return digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
};

const ATTACHMENT_FIELD_KEYS = new Set([
  "fd_m_attachment",
  "fd_m_stage4_attachment",
]);

async function fetchAudioBlobUrl(callId) {
  return getCallAudioBlobUrl(callId);
}

// Stage columns for Case Closing
const STAGE1_COLUMNS = [
  { key: "fd_a_client_name", label: "Client Name" },
  {
    key: "fd_a_client_type",
    label: "Client Type",
    options: [
      "New Client - Individual",
      "New Client - Organization",
      "Existing client - Individual",
      "Existing client - Organization",
      "Voicemail Call",
      "Spam / Wrong Call",
    ],
  },
  {
    key: "fd_a_lead_source",
    label: "Lead Source",
    options: LEAD_SOURCE_OPTIONS,
  },
  { key: "fd_a_email_address", label: "Email Address" },
  {
    key: "fd_a_urgency_flag",
    label: "Urgency Flag",
    options: [
      "None",
      "Urgent - RFE",
      "Urgent - NOID",
      "Grace Period",
      "Visa Expiry",
      "Court Date",
      "Filing Deadline",
      "Client Distressed",
    ],
  },
  { key: "fd_a_case_type", label: "Case Type" },
  { key: "fd_a_case_worker_assigned", label: "Case Worker Assigned" },
  {
    key: "fd_a_call_outcome",
    label: "Call Outcome",
    options: [
      "Connected - Resolved",
      "Connected - Follow up needed",
      "Connected - Redirected",
      "Voicemail Left",
      "Voicemail Full (Not Available)",
      "Call Back Scheduled",
    ],
  },
  { key: "fd_a_call_summary", label: "Call Summary" },
];

const STAGE2_COLUMNS = [
  { key: "fd_m_self_action_item", label: "Self Action Item" },
  { key: "fd_m_action_due_date", label: "Action Due Date" },
  {
    key: "fd_m_manual_action_status",
    label: "Action Status",
    options: ["Open", "In Progress", "Completed", "Pending", "Not Required"],
  },
  {
    key: "fd_m_dependency",
    label: "Dependency",
    options: ["None", "Client", "Attorney", "Internal", "Other"],
  },
  { key: "fd_m_follow_up_dates", label: "Follow Up Dates" },
  { key: "fd_m_follow_up_attempts", label: "# of Follow Up Attempts" },
  { key: "fd_m_blocker_comment", label: "Blocker Comment" },
];

// Stage 3 and Stage 4 columns for Case Closing
const STAGE3_COLUMNS = [
  { key: "fd_m_case_status_change", label: "Case Status Change" },
  { key: "fd_m_date_entry", label: "Date Entry" },
  { key: "fd_m_verification_comment", label: "Verification Comment" },
  { key: "fd_m_attachment", label: "Attachment" },
];

const STAGE4_COLUMNS = [
  { key: "fd_m_stage4_attachment", label: "Stage 4 Attachment" },
  { key: "fd_m_google_review", label: "Google Review" },
];

const COLUMN_FILTERS = [
  {
    key: "fd_a_client_type",
    label: "Client Type",
    options: STAGE1_COLUMNS.find((field) => field.key === "fd_a_client_type")
      .options,
  },
  {
    key: "fd_a_lead_source",
    label: "Lead Source",
    options: STAGE1_COLUMNS.find((field) => field.key === "fd_a_lead_source")
      .options,
  },
  {
    key: "fd_a_urgency_flag",
    label: "Urgency Flag",
    options: STAGE1_COLUMNS.find((field) => field.key === "fd_a_urgency_flag")
      .options,
  },
];

const CASE_CLOSING_FILTER_STORAGE_KEY = "flintdesk_case_closing_filters";
const CASE_CLOSING_EXPORT_COLUMNS = [
  { key: "id", label: "Call ID" },
  { key: "clientId", label: "Client ID" },
  { key: "date", label: "Date" },
  { key: "enquiryOpenDuration", label: "Enquiry Open Duration" },
  { key: "timestamp", label: "Timestamp" },
  { key: "agent", label: "Agent" },
  { key: "direction", label: "Direction" },
  { key: "duration", label: "Duration" },
  ...STAGE1_COLUMNS,
  ...STAGE2_COLUMNS,
  ...STAGE3_COLUMNS,
  ...STAGE4_COLUMNS,
  { key: "case_closing_completed", label: "Completion Status" },
];

function readPersistedFilters() {
  const { start, end } = getDefaultDateRange();
  const defaults = {
    start,
    end,
    agent: "all",
    capturedNumber: "",
    extension: "",
    clientNumber: "",
    clientId: "",
    callId: "",
    columnFilters: Object.fromEntries(
      COLUMN_FILTERS.map((filter) => [filter.key, ""]),
    ),
  };

  try {
    const saved = JSON.parse(
      sessionStorage.getItem(tableStorageKey(CASE_CLOSING_FILTER_STORAGE_KEY)) || "null",
    );
    if (savedOnAnEarlierDay(tableStorageKey(CASE_CLOSING_FILTER_STORAGE_KEY))) return defaults;   // an earlier day's range resets to the default day
    return {
      ...defaults,
      ...saved,
      columnFilters: { ...defaults.columnFilters, ...saved?.columnFilters },
    };
  } catch {
    return defaults;
  }
}

const cellValue = (call, key) => {
  const value = call?.raw?.[key] ?? call?.[key];
  return value === null || value === undefined || value === "" ? "-" : value;
};

const displayDirection = (value) =>
  String(value || "-").replace(/brevo outbound/gi, "Leads Outbound");

// A case reaches the closing workspace only after it has a terminal outcome.
// Completed rows remain visible as an auditable closing history.
const CASE_CLOSING_STATUSES = new Set([
  "success pc",
  "success ge",
  "dropped",
]);
const isClosingRelevant = (call) => {
  const status = caseStatusKey(call.raw?.fd_m_case_status_change);
  return (
    Boolean(call.raw?.case_closing_completed) ||
    CASE_CLOSING_STATUSES.has(status) ||
    isLostStatus(status)
  );
};



export default function CaseClosing({ isTeamLead = false, onNavigate }) {
  const [initialFilters] = useState(readPersistedFilters);
  const [range, setRange] = useState({
    start: initialFilters.start,
    end: initialFilters.end,
  });
  const [customDraft, setCustomDraft] = useState({
    start: initialFilters.start,
    end: initialFilters.end,
  });
  const [agentFilter, setAgentFilter] = useState(initialFilters.agent);
  const [capturedNumberFilter, setCapturedNumberFilter] = useState(
    initialFilters.capturedNumber,
  );
  const [extensionFilter, setExtensionFilter] = useState(
    initialFilters.extension,
  );
  const [clientNumberFilter, setClientNumberFilter] = useState(
    initialFilters.clientNumber,
  );
  const [callIdFilter, setCallIdFilter] = useState(initialFilters.callId);
  const [clientIdFilter, setClientIdFilter] = useState(initialFilters.clientId);
  const [draftAgentFilter, setDraftAgentFilter] = useState(
    initialFilters.agent,
  );
  const [draftCapturedNumberFilter, setDraftCapturedNumberFilter] = useState(
    initialFilters.capturedNumber,
  );
  const [draftExtensionFilter, setDraftExtensionFilter] = useState(
    initialFilters.extension,
  );
  const [draftClientNumberFilter, setDraftClientNumberFilter] = useState(
    initialFilters.clientNumber,
  );
  const [draftCallIdFilter, setDraftCallIdFilter] = useState(
    initialFilters.callId,
  );
  const [draftClientIdFilter, setDraftClientIdFilter] = useState(
    initialFilters.clientId,
  );
  const [columnFilters, setColumnFilters] = useState(
    initialFilters.columnFilters,
  );
  useEffect(() => {
    try {
      const key = tableStorageKey(CASE_CLOSING_FILTER_STORAGE_KEY);
      const saved = JSON.parse(sessionStorage.getItem(key) || "{}");
      sessionStorage.setItem(key, JSON.stringify({ ...saved, columnFilters }));
      markSavedToday(key);
    } catch { /* Storage may be disabled. */ }
  }, [columnFilters]);
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [transcriptModalCall, setTranscriptModalCall] = useState(null);
  const [audioModalCall, setAudioModalCall] = useState(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [audioError, setAudioError] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [cellModal, setCellModal] = useState(null);
  const [cellDraft, setCellDraft] = useState("");
  const [cellError, setCellError] = useState("");
  const [cellSaving, setCellSaving] = useState(false);
  const [stageInputDrafts, setStageInputDrafts] = useState({});
  const [fdAgents, setFdAgents] = useState([]);
  const [syncFilters, setSyncFilters] = useState([]);
  const [viewByPriority, setViewByPriority] = usePersistentTableState("CaseClosing.viewByPriority", false);
  const [markingCompleted, setMarkingCompleted] = useState({});

  useEffect(() => {
    Promise.all([getDashboardFrontDeskAgents(), getSyncFilters()])
      .then(([agents, filters]) => {
        const agentNames = agents.map((agent) => agent.name).sort();
        setFdAgents(agentNames);
        setSyncFilters(filters);
      })
      .catch(() => {
        setFdAgents([]);
        setSyncFilters([]);
      });
  }, []);

  const openCellModal = (label, key, value, rowId, callId) => {
    const initial =
      value === "-" || value === undefined || value === null
        ? ""
        : String(value);
    setCellError("");
    setCellDraft(initial);
    setCellModal({ label, key, value: initial, rowId, callId });
  };

  const closeCellModal = () => {
    setCellModal(null);
    setCellDraft("");
    setCellError("");
  };

  const saveCellModal = async () => {
    if (!cellModal) return;
    setCellSaving(true);
    setCellError("");
    try {
      const saved = await updateCallField(cellModal.callId, cellModal.key, cellDraft);
      // Editing a completion-relevant field (Stage 1's FD_AI_FIELDS) clears
      // case_closing_completed as part of this same update on the backend --
      // apply the authoritative state it returns so the Mark Completed
      // button here stays in sync without a separate request or refetch.
      const completion = {
        case_closing_completed: saved.case_closing_completed,
        case_closing_completed_at: saved.case_closing_completed_at,
      };
      setCalls((current) =>
        current.map((call) => {
          if (call.id !== cellModal.rowId) return call;
          if (call.raw && cellModal.key in call.raw) {
            return {
              ...call,
              raw: { ...call.raw, [cellModal.key]: cellDraft, ...completion },
            };
          }
          return {
            ...call,
            [cellModal.key]: cellDraft,
            ...(call.raw ? { raw: { ...call.raw, ...completion } } : {}),
          };
        }),
      );
      closeCellModal();
    } catch (err) {
      setCellError(err.message || "Failed to save changes.");
    } finally {
      setCellSaving(false);
    }
  };

  const closeAudioModal = () => {
    setAudioModalCall(null);
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl("");
    setAudioError("");
    setAudioLoading(false);
  };

  const downloadAudio = () => {
    if (!audioUrl || !audioModalCall) return;
    const link = document.createElement("a");
    link.href = audioUrl;
    link.download = audioDownloadName(audioModalCall);
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const openAudioModal = async (call) => {
    setAudioModalCall(call);
    setAudioUrl("");
    setAudioError("");
    setAudioLoading(true);
    try {
      const blobUrl = await fetchAudioBlobUrl(call.callId);
      setAudioUrl(blobUrl);
    } catch (err) {
      setAudioError(err.message || "Unable to load audio.");
    } finally {
      setAudioLoading(false);
    }
  };

  const capturedNumberOptions = useMemo(() => {
    return getOutboundLeadNumberOptions(syncFilters);
  }, [syncFilters]);

  const extensionOptions = useMemo(
    () => getLeadNumberExtensions(draftCapturedNumberFilter),
    [draftCapturedNumberFilter],
  );

  const draftLeadMode =
    !isTeamLead && Boolean(draftCapturedNumberFilter || draftExtensionFilter);
  const draftAgentMode =
    !isTeamLead && !draftLeadMode && draftAgentFilter !== "all";

  const applyFilters = () => {
    if (!isValidDateRange(customDraft)) {
      setError("Date From must be on or before Date To.");
      return;
    }
    const applied = {
      start: customDraft.start,
      end: customDraft.end,
      agent: isTeamLead
        ? draftAgentFilter
        : draftAgentMode
          ? draftAgentFilter
          : "all",
      capturedNumber: isTeamLead
        ? draftCapturedNumberFilter
        : draftLeadMode
          ? draftCapturedNumberFilter
          : "",
      extension: isTeamLead
        ? draftExtensionFilter
        : draftLeadMode
          ? draftExtensionFilter
          : "",
      clientNumber: draftClientNumberFilter,
      callId: draftCallIdFilter,
      clientId: draftClientIdFilter.trim(),
      columnFilters,
    };
    setRange({ start: applied.start, end: applied.end });
    setAgentFilter(applied.agent);
    setCapturedNumberFilter(applied.capturedNumber);
    setExtensionFilter(applied.extension);
    setClientNumberFilter(applied.clientNumber);
    setCallIdFilter(applied.callId);
    setClientIdFilter(applied.clientId);
    setColumnFilters(applied.columnFilters);
    try {
      sessionStorage.setItem(tableStorageKey(CASE_CLOSING_FILTER_STORAGE_KEY), JSON.stringify(applied));
      markSavedToday(tableStorageKey(CASE_CLOSING_FILTER_STORAGE_KEY));
    } catch { /* Storage may be disabled. */ }
  };

  const refreshCalls = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getCallsList(
      range.start,
      range.end,
      capturedNumberFilter,
      extensionFilter,
      clientIdFilter,
    )
      .then((data) => {
        if (!cancelled) setCalls(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Failed to load cases.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    range.end,
    range.start,
    capturedNumberFilter,
    extensionFilter,
    clientIdFilter,
  ]);

  useEffect(() => {
    return refreshCalls();
  }, [refreshCalls]);

  const filtered = useMemo(
    () =>
      calls.filter((call) => {
        const matchesDateRange =
          call.date >= range.start && call.date <= range.end;
        const agent = String(call.agent || "").toLowerCase();
        const extension = String(call.extension || call.raw?.extension || "")
          .trim()
          .toLowerCase();
        const clientNumber = normalizePhoneSearch(
          call.clientNumber ||
            call.raw?.client_phone_number ||
            call.raw?.phone_number ||
            "",
        ).toLowerCase();
        const matchesAgent =
          agentFilter === "all" || agent === agentFilter.toLowerCase();
        const matchesCapturedNumber = callMatchesLeadNumber(
          call,
          capturedNumberFilter,
          calls,
          syncFilters,
        );
        const trimmedExtensionFilter = extensionFilter.trim();
        const aliasExtensionFilter = ALIAS_EXTENSION_FOR_LEAD_NUMBER[trimmedExtensionFilter];
        const matchesExtension =
          !trimmedExtensionFilter ||
          extension === trimmedExtensionFilter.toLowerCase() ||
          (Boolean(aliasExtensionFilter) && extension === aliasExtensionFilter.toLowerCase());
        const matchesClientNumber =
          !clientNumberFilter.trim() ||
          clientNumber.includes(normalizePhoneSearch(clientNumberFilter));
        const matchesCallId =
          !callIdFilter.trim() ||
          String(call.id || call.callId || "")
            .toLowerCase()
            .includes(callIdFilter.trim().toLowerCase());
        const matchesClientId =
          !clientIdFilter.trim() ||
          String(call.clientId || "").toLowerCase() ===
            clientIdFilter.trim().toLowerCase();
        const matchesColumnFilters = COLUMN_FILTERS.every((filter) => {
          const selected = columnFilters[filter.key];
          if (!selected) return true;
          return String(call.raw?.[filter.key] ?? "").trim() === selected;
        });

        return (
          isClosingRelevant(call) &&
          matchesDateRange &&
          matchesAgent &&
          matchesCapturedNumber &&
          matchesExtension &&
          matchesClientNumber &&
          matchesClientId &&
          matchesCallId &&
          matchesColumnFilters
        );
      }),
    [
      calls,
      range,
      agentFilter,
      capturedNumberFilter,
      extensionFilter,
      clientNumberFilter,
      clientIdFilter,
      callIdFilter,
      columnFilters,
      syncFilters,
    ],
  );

  const displayedCalls = useMemo(() => {
    return viewByPriority ? rankCallsByPriority(filtered) : filtered;
  }, [filtered, viewByPriority]);

  const agentOptions = fdAgents;

  const saveStageValue = async (call, field, value) => {
    try {
      if (!call.callId) throw new Error("Missing call identifier.");
      // This writes to the same analysis record used by Call Records, so
      // manual values remain shared between both pages. Dropdown-based Stage 1
      // fields (e.g. Urgency Flag, Call Outcome) are completion-relevant, so
      // the response's case_closing_completed reflects whether this save just
      // invalidated a prior completion -- apply it here to keep the button in
      // sync without a separate request or refetch.
      const saved = await updateCallField(call.callId, field.key, value);
      setCalls((current) =>
        current.map((item) => {
          if (item.id !== call.id) return item;
          const raw = {
            ...item.raw,
            [field.key]: value,
            case_closing_completed: saved.case_closing_completed,
            case_closing_completed_at: saved.case_closing_completed_at,
          };
          return { ...item, raw };
        }),
      );
    } catch (saveError) {
      setError(saveError.message || "Failed to save stage value.");
    }
  };

  const updateAttachmentValue = (call, fieldKey, newValue) => {
    setCalls((current) =>
      current.map((item) =>
        item.id === call.id
          ? { ...item, raw: { ...item.raw, [fieldKey]: newValue } }
          : item,
      ),
    );
  };

  const renderStageCell = (call, field) => {
    if (ATTACHMENT_FIELD_KEYS.has(field.key)) {
      return (
        <AttachmentCell
          callId={call.callId}
          target={field.key}
          value={call.raw?.[field.key]}
          onChange={(newValue) =>
            updateAttachmentValue(call, field.key, newValue)
          }
        />
      );
    }
    const value = cellValue(call, field.key);
    // The client ID popup is the source of truth for a client's name once set.
    if (field.key === "fd_a_client_name") {
      const manualName = call?.raw?.fd_m_client_name ?? "";
      return (
        <span
          className="calls-cell-clickable"
          title={manualName || value}
          onClick={() =>
            openCellModal(field.label, "fd_m_client_name", manualName || value, call.id, call.callId)
          }
        >
          {manualName || value}
        </span>
      );
    }
    if (
      !field.options ||
      (field.key.startsWith("fd_a_") &&
        !COLUMN_FILTERS.some((filter) => filter.key === field.key))
    ) {
      return (
        <span
          className="calls-cell-clickable"
          title={value}
          onClick={() =>
            openCellModal(field.label, field.key, value, call.id, call.callId)
          }
        >
          {value}
        </span>
      );
    }

    const options =
      value !== "-" && !field.options.includes(value)
        ? [value, ...field.options]
        : field.options;
    const draftKey = `${call.id}:${field.key}`;
    const inputValue =
      stageInputDrafts[draftKey] ?? (value === "-" ? "" : String(value));
    return (
      <div className="calls-stage-editor">
        <select
          className="calls-stage-select"
          value={value !== "-" && options.includes(value) ? value : ""}
          onChange={(event) => {
            const selectedValue = event.target.value;
            setStageInputDrafts((current) => ({
              ...current,
              [draftKey]: selectedValue,
            }));
            saveStageValue(call, field, selectedValue);
          }}
          title={`Choose ${field.label}`}
        >
          <option value="">Choose</option>
          {options.map((option) => (
            <option key={option} value={option}>
              {field.key === "fd_a_client_type"
                ? displayClientType(option)
                : option}
            </option>
          ))}
        </select>
        <input
          className="calls-stage-input"
          value={inputValue}
          placeholder="Type custom"
          aria-label={`Type ${field.label}`}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.currentTarget.blur();
            }
          }}
          onChange={(event) =>
            setStageInputDrafts((current) => ({
              ...current,
              [draftKey]: event.target.value,
            }))
          }
          onBlur={(event) => {
            const typedValue = event.currentTarget.value.trim();
            if (typedValue !== (value === "-" ? "" : String(value)))
              saveStageValue(call, field, typedValue);
          }}
        />
      </div>
    );
  };

  const markAsCompleted = async (call) => {
    setMarkingCompleted((prev) => ({ ...prev, [call.id]: true }));
    try {
      // TODO: Implement backend endpoint to mark case as completed
      // For now, we'll use the existing updateManualFields with a completion flag
      await updateManualFields(call.analysisId, {
        case_closing_completed: true,
      });

      setCalls((current) =>
        current.map((item) => {
          if (item.id !== call.id) return item;
          return {
            ...item,
            raw: { ...item.raw, case_closing_completed: true },
          };
        }),
      );
    } catch (err) {
      setError(err.message || "Failed to mark case as completed.");
    } finally {
      setMarkingCompleted((prev) => ({ ...prev, [call.id]: false }));
    }
  };

  const exportCases = () => {
    const escape = (value) =>
      String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    const valueFor = (call, key) => {
      if (key === "enquiryOpenDuration") return enquiryOpenDuration(call);
      if (key === "direction") return displayDirection(call.direction);
      if (key === "case_closing_completed")
        return call.raw?.case_closing_completed ? "Completed" : "Open";
      const value = call.raw?.[key] ?? call[key];
      return value === null || value === undefined || value === "" ? "" : value;
    };
    const stageStyle = (key) =>
      STAGE1_COLUMNS.some((field) => field.key === key)
        ? "background:#eef8fd;color:#0369a1;"
        : STAGE2_COLUMNS.some((field) => field.key === key)
          ? "background:#fff9eb;color:#92400e;"
          : STAGE3_COLUMNS.some((field) => field.key === key)
            ? "background:#f5f0ff;color:#6d28d9;"
            : STAGE4_COLUMNS.some((field) => field.key === key)
              ? "background:#effaf2;color:#166534;"
              : "background:#f3f4f6;color:#4f5968;";
    const html = `<table border="1" style="border-collapse:collapse;font-family:Arial,sans-serif"><thead><tr>${CASE_CLOSING_EXPORT_COLUMNS.map((column) => `<th style="${stageStyle(column.key)}padding:8px">${escape(column.label)}</th>`).join("")}</tr></thead><tbody>${displayedCalls.map((call) => `<tr>${CASE_CLOSING_EXPORT_COLUMNS.map((column) => `<td style="padding:6px">${escape(valueFor(call, column.key))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    const blob = new Blob(
      [`<html><head><meta charset="utf-8"></head><body>${html}</body></html>`],
      { type: "application/vnd.ms-excel" },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `FlintDesk_Case_Closing_${range.start}_to_${range.end}.xls`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="process-page">
      <div className="calls-header-block">
        <div>
          <h1>Case Closing</h1>
          <p>Review and complete cases across all stages.</p>
        </div>
        <button
          type="button"
          className="primary-button"
          onClick={() => onNavigate?.("process-calls")}
        >
          <i className="fa-solid fa-arrow-left" /> Back to calls page
        </button>
      </div>

      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector value={customDraft} onChange={setCustomDraft} />

        <div className="calls-filter-group calls-agent-filter">
          <span className="calls-toolbar-label">Agent</span>
          <div className="date-input calls-toolbar-select">
            <select
              value={draftAgentFilter}
              onChange={(event) => {
                setDraftAgentFilter(event.target.value);
                if (event.target.value !== "all") setDraftExtensionFilter("");
                if (!isTeamLead && event.target.value !== "all")
                  setDraftCapturedNumberFilter("");
              }}
              disabled={
                Boolean(draftExtensionFilter) && draftAgentFilter === "all"
              }
            >
              <option value="all">All Agents</option>
              {agentOptions.map((agent) => (
                <option key={agent} value={agent}>
                  {agent}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="calls-filter-group calls-inline-filter calls-dropdown-filter">
          <span className="calls-toolbar-label">Lead number</span>
          <select
            className="calls-filter-select"
            value={draftCapturedNumberFilter}
            onChange={(event) => {
              setDraftCapturedNumberFilter(event.target.value);
              if (event.target.value && !isTeamLead) setDraftAgentFilter("all");
              setDraftExtensionFilter("");
            }}
            disabled={!isTeamLead && draftAgentMode}
          >
            <option value="">All Numbers</option>
            {capturedNumberOptions.map((number) => (
              <option key={number} value={number}>
                {number}
              </option>
            ))}
          </select>
        </div>

        <div className="calls-filter-group calls-inline-filter calls-dropdown-filter">
          <span className="calls-toolbar-label">Extension</span>
          <select
            className="calls-filter-select"
            value={draftExtensionFilter}
            onChange={(event) => {
              setDraftExtensionFilter(event.target.value);
              if (event.target.value) setDraftAgentFilter("all");
            }}
            disabled={draftAgentFilter !== "all" && !draftExtensionFilter}
          >
            <option value="">All Extensions</option>
            {extensionOptions.map((ext) => (
              <option key={ext} value={ext}>
                {displayExtensionOption(ext)}
              </option>
            ))}
          </select>
        </div>

        <div className="calls-filter-group calls-inline-filter calls-text-filter">
          <span className="calls-toolbar-label">Client #</span>
          <input
            className="calls-filter-input"
            type="text"
            placeholder="Search client #"
            value={draftClientNumberFilter}
            onChange={(event) => setDraftClientNumberFilter(event.target.value)}
          />
        </div>

        <div className="calls-filter-group calls-inline-filter calls-text-filter">
          <span className="calls-toolbar-label">Client ID</span>
          <input
            className="calls-filter-input"
            type="text"
            placeholder="CL-000123"
            value={draftClientIdFilter}
            onChange={(event) => setDraftClientIdFilter(event.target.value)}
          />
        </div>

        <div className="calls-filter-group calls-inline-filter calls-text-filter">
          <span className="calls-toolbar-label">Call ID</span>
          <input
            className="calls-filter-input"
            type="text"
            placeholder="Search call ID"
            value={draftCallIdFilter}
            onChange={(event) => setDraftCallIdFilter(event.target.value)}
          />
        </div>

        <div className="calls-filter-group calls-priority-toggle">
          <label className="calls-toggle-label">
            <input
              type="checkbox"
              checked={viewByPriority}
              onChange={(event) => setViewByPriority(event.target.checked)}
            />
            <span>View by Priority</span>
          </label>
        </div>
      </ScrollableFilterBelt>
      <div className="filter-belt-actions">
        <button type="button" className="primary-button calls-toolbar-apply" onClick={applyFilters}>
          Apply
        </button>
        <button
          type="button"
          className="table-action calls-export-button"
          onClick={exportCases}
        >
          <i className="fa-solid fa-file-excel" /> Export to Excel
        </button>
      </div>
      </StickyFilterRegion>

      <section className="history-table-card">
        <h2>Case Records</h2>
        <p className="result-count">
          {loading ? "Loading..." : `${displayedCalls.length} cases`}
        </p>
        {error && <p className="muted">{error}</p>}
        {loading ? (
          <div className="calls-table-loading">
            <BufferingLoader text="Fetching case records" size="md" />
          </div>
        ) : (
          <TableScrollWrap className="calls-table-wrap">
            <table className="calls-table calls-table-expanded">
              <thead>
                <tr className="calls-table-group-row">
                  <th colSpan={6}>Call</th>
                  <th colSpan={5}>Call details</th>
                  {viewByPriority && <th colSpan={2}>Priority</th>}
                  <th colSpan={STAGE1_COLUMNS.length} className="group-stage1">
                    Stage 1 &middot; AI Intake
                  </th>
                  <th colSpan={STAGE2_COLUMNS.length} className="group-stage2">
                    Stage 2 &middot; Action Tracking
                  </th>
                  <th colSpan={STAGE3_COLUMNS.length} className="group-stage3">
                    Stage 3 &middot; Case Status
                  </th>
                  <th colSpan={STAGE4_COLUMNS.length} className="group-stage4">
                    Stage 4 &middot; Review
                  </th>
                  <th>Completion</th>
                </tr>
                <tr>
                  <th>#</th>
                  <th>Date</th>
                  <th>Enquiry Open Duration</th>
                  <th>Timestamp</th>
                  <th>Transcript</th>
                  <th className="calls-audio-header">Audio</th>
                  <th>Call_ID</th>
                  <th>Client ID</th>
                  <th>Agent</th>
                  <th>Direction</th>
                  <th>Duration</th>
                  {viewByPriority && (
                    <>
                      <th>Lead Score</th>
                      <th>Business Opportunity</th>
                    </>
                  )}
                  {STAGE1_COLUMNS.map((field) => (
                    <th key={field.key} className="stage1-column-header">
                      {field.label}
                    </th>
                  ))}
                  {STAGE2_COLUMNS.map((field) => (
                    <th key={field.key} className="stage2-column-header">
                      {field.label}
                    </th>
                  ))}
                  {STAGE3_COLUMNS.map((field) => (
                    <th key={field.key} className="stage3-column-header">
                      {field.label}
                    </th>
                  ))}
                  {STAGE4_COLUMNS.map((field) => (
                    <th key={field.key} className="stage4-column-header">
                      {field.label}
                    </th>
                  ))}
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {displayedCalls.map((call, index) => {
                  const tier = calculatePriority(call).leadBand;
                  const startsNoScoreGroup =
                    viewByPriority &&
                    tier === "N_A" &&
                    (index === 0 ||
                      calculatePriority(displayedCalls[index - 1]).leadBand !==
                        "N_A");

                  return (
                    <Fragment key={call.id}>
                      {startsNoScoreGroup && (
                        <tr className="calls-no-score-group">
                          <td
                            colSpan={
                              12 +
                              (viewByPriority ? 2 : 0) +
                              STAGE1_COLUMNS.length +
                              STAGE2_COLUMNS.length +
                              STAGE3_COLUMNS.length +
                              STAGE4_COLUMNS.length
                            }
                          >
                            N/A — No Score
                          </td>
                        </tr>
                      )}
                      <tr
                        data-priority-tier={viewByPriority ? tier : undefined}
                      >
                        <td>{index + 1}</td>
                        <td>
                          <span
                            className="calls-cell-clickable"
                            onClick={() =>
                              openCellModal(
                                "Date",
                                "date",
                                call.date,
                                call.id,
                                call.callId,
                              )
                            }
                          >
                            {formatDisplayDate(call.date)}
                          </span>
                        </td>
                        <td>
                          <span
                            className="calls-cell-clickable"
                            title="Days since call registration until case status change"
                            onClick={() =>
                              openCellModal(
                                "Enquiry Open Duration",
                                "enquiryOpenDuration",
                                enquiryOpenDuration(call),
                                call.id,
                                call.callId,
                              )
                            }
                          >
                            {enquiryOpenDuration(call)}
                          </span>
                        </td>
                        <td>
                          <span
                            className="calls-cell-clickable"
                            title={call.timestamp}
                          >
                            {call.timestamp || "-"}
                          </span>
                        </td>
                        <td>
                          <button
                            type="button"
                            className="icon-action-btn"
                            onClick={() => setTranscriptModalCall(call)}
                            disabled={!call.transcript}
                            title="View transcript"
                          >
                            <i className="fa-regular fa-file-lines" />
                          </button>
                        </td>
                        <td>
                          <button
                            type="button"
                            className="icon-action-btn"
                            onClick={() => openAudioModal(call)}
                            disabled={!call.hasAudio}
                            title="Play audio"
                          >
                            <i className="fa-solid fa-play" />
                          </button>
                        </td>
                        <td>
                          <span
                            className="calls-cell-clickable"
                            onClick={() =>
                              openCellModal(
                                "Call ID",
                                "id",
                                call.id,
                                call.id,
                                call.callId,
                              )
                            }
                          >
                            {call.call_reference || call.id}
                          </span>
                        </td>
                        <td>{call.clientId || "-"}</td>
                        <td>
                          <span
                            className="calls-cell-clickable"
                            title={call.agent}
                            onClick={() =>
                              openCellModal(
                                "Agent",
                                "agent",
                                call.agent,
                                call.id,
                                call.callId,
                              )
                            }
                          >
                            {call.agent || "-"}
                          </span>
                        </td>
                        <td>
                          <span
                            className="calls-cell-clickable"
                            title={displayDirection(call.direction)}
                            onClick={() =>
                              openCellModal(
                                "Direction",
                                "direction",
                                call.direction,
                                call.id,
                                call.callId,
                              )
                            }
                          >
                            {displayDirection(call.direction)}
                          </span>
                        </td>
                        <td>
                          <span
                            className="calls-cell-clickable"
                            title={call.duration}
                            onClick={() =>
                              openCellModal(
                                "Duration",
                                "duration",
                                call.duration,
                                call.id,
                                call.callId,
                              )
                            }
                          >
                            {call.duration || "-"}
                          </span>
                        </td>
                        {viewByPriority && (
                          <>
                            <td>
                              <LeadScoreCell item={call} />
                            </td>
                            <td>
                              <BusinessOpportunityCell item={call} />
                            </td>
                          </>
                        )}
                        {STAGE1_COLUMNS.map((field) => (
                          <td key={field.key} className="calls-cell-wide">
                            {renderStageCell(call, field)}
                          </td>
                        ))}
                        {STAGE2_COLUMNS.map((field) => (
                          <td key={field.key} className="calls-cell-wide">
                            {renderStageCell(call, field)}
                          </td>
                        ))}
                        {STAGE3_COLUMNS.map((field) => (
                          <td key={field.key} className="calls-cell-wide">
                            {renderStageCell(call, field)}
                          </td>
                        ))}
                        {STAGE4_COLUMNS.map((field) => (
                          <td key={field.key} className="calls-cell-wide">
                            {renderStageCell(call, field)}
                          </td>
                        ))}
                        <td className="calls-cell-wide">
                          <button
                            type="button"
                            className={`primary-button ${call.raw?.case_closing_completed ? "completed" : ""}`}
                            disabled={
                              markingCompleted[call.id] ||
                              call.raw?.case_closing_completed
                            }
                            onClick={() => markAsCompleted(call)}
                            title={
                              call.raw?.case_closing_completed
                                ? "Case marked as completed"
                                : "Mark this case as completed"
                            }
                          >
                            {markingCompleted[call.id]
                              ? "Saving..."
                              : call.raw?.case_closing_completed
                                ? "Saved"
                                : "Mark completed"}
                          </button>
                        </td>
                      </tr>
                    </Fragment>
                  );
                })}
                {!displayedCalls.length && (
                  <tr>
                    <td
                      colSpan={
                        12 +
                        (viewByPriority ? 2 : 0) +
                        STAGE1_COLUMNS.length +
                        STAGE2_COLUMNS.length +
                        STAGE3_COLUMNS.length +
                        STAGE4_COLUMNS.length
                      }
                      className="muted"
                    >
                      No cases in this date range.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableScrollWrap>
        )}
      </section>

      {cellModal && (
        <div className="call-modal-overlay" onClick={closeCellModal}>
          <div
            className="call-modal-card call-modal-medium"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="call-modal-header">
              <div>
                <p className="call-modal-kicker">{cellModal.label}</p>
                <h2>Edit</h2>
              </div>
              <button
                type="button"
                className="call-modal-close"
                onClick={closeCellModal}
              >
                Close
              </button>
            </div>
            <div className="call-modal-content">
              {cellError && <p className="cell-modal-error">{cellError}</p>}
              <textarea
                className="cell-modal-textarea"
                value={cellDraft}
                onChange={(event) => setCellDraft(event.target.value)}
                disabled={cellSaving}
                autoFocus
              />
            </div>
            <div className="call-modal-footer">
              <button
                type="button"
                className="table-action"
                onClick={closeCellModal}
                disabled={cellSaving}
              >
                Cancel
              </button>
              <button
                type="button"
                className="table-action primary"
                onClick={saveCellModal}
                disabled={cellSaving}
              >
                {cellSaving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

      {transcriptModalCall && (
        <div
          className="call-modal-overlay"
          onClick={() => setTranscriptModalCall(null)}
        >
          <div
            className="call-modal-card"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="call-modal-header">
              <div>
                <p className="call-modal-kicker">Call Transcript</p>
                <h2>{transcriptModalCall.call_reference || transcriptModalCall.id}</h2>
              </div>
              <button
                type="button"
                className="call-modal-close"
                onClick={() => setTranscriptModalCall(null)}
              >
                Close
              </button>
            </div>
            <div className="call-modal-content">
              <p>
                {transcriptModalCall.transcript || "No transcript available."}
              </p>
            </div>
          </div>
        </div>
      )}

      {audioModalCall && (
        <div className="call-modal-overlay" onClick={closeAudioModal}>
          <div
            className="call-modal-card call-modal-compact"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="call-modal-header">
              <div>
                <p className="call-modal-kicker">Call Audio</p>
                <h2>{audioModalCall.call_reference || audioModalCall.id}</h2>
              </div>
              <div className="call-modal-header-actions">
                {audioUrl && (
                  <button
                    type="button"
                    className="table-action"
                    onClick={downloadAudio}
                  >
                    Download
                  </button>
                )}
                <button
                  type="button"
                  className="call-modal-close"
                  onClick={closeAudioModal}
                >
                  Close
                </button>
              </div>
            </div>
            <div className="call-modal-audio-body">
              {audioLoading && <p className="muted">Loading audio...</p>}
              {audioError && <p className="muted">{audioError}</p>}
              {audioUrl && (
                <audio
                  className="call-modal-player"
                  controls
                  autoPlay
                  onEnded={closeAudioModal}
                >
                  <source src={audioUrl} type="audio/mpeg" />
                  Your browser does not support the audio element.
                </audio>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
