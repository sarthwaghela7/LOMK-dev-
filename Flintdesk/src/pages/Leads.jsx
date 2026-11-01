import { usePersistentTableState } from "../data/persistentTableState.js";
import { useActivePageRefresh } from "../data/useActivePageRefresh.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import {
  deleteLeads,
  getCallAudioBlobUrl,
  getCallContent,
  getFDUsers,
  getLeads,
  importLeads,
  scoreManualData,
  submitLeads,
  updateLeadField,
} from "../data/api.js";
import { getManualScoreBand } from "../data/manualScoreBands.js";
import {
  canApproveDependencies,
  isRestrictedAgent,
} from "../data/accessControl.js";
import { getDefaultDateRange } from "../data/filterDates.js";
import { enquiryOpenDuration, centralCalendarDay } from "../data/enquiryOpenDuration.js";
import { rankCallsByPriority } from "../data/priorityEngine.js";
import {
  BusinessOpportunityCell,
  LeadScoreCell,
} from "../components/PriorityScoreCell.jsx";
import { ClientIdCell, LeadIdCell } from "../components/IdentifierCells.jsx";
import { displayClientType } from "../data/displayLabels.js";
import { CASE_STATUS_OPTIONS } from "../data/statusOptions.js";
import { caseStatusText, matchesCaseStatus } from "../data/leadCaseStatus.js";
import { DataFilterPanel } from "../components/DataFilterPanel.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { formatDisplayDate } from "../data/displayDate.js";
import { leadCallMade, leadCallMadeOnNote } from "../data/leadCallMade.js";
import { parseTable } from "../data/tabularImport.js";
import { AudioModal, TranscriptModal } from "../components/CallMediaModals.jsx";
import { LeadOwnershipNotice } from "../components/LeadOwnershipNotice.jsx";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";
import { getAllLeadExtensions } from "../data/leadNumberFilter.js";
import { LEAD_SOURCE_FILTER_OPTIONS, matchesSourceFilter, selectedSources } from "../data/leadSourceFilter.js";

const CALL = [
  ["client_id", "Client ID"],
  ["call_id", "Call ID"],
  ["date_received", "Date Received"],
  ["source", "Source"],
  ["name", "Name"],
  ["open_duration", "Open Duration"],
  ["transcript", "Transcript"],
  ["audio", "Audio"],
  ["manual_score", "Manual Score"],
];
const S1 = [
  ["date_received", "Entry Date"],
  ["source", "Source"],
  ["name", "Name"],
  ["open_duration", "Open Duration"],
  ["phone", "Phone"],
  ["email", "Email"],
  ["client_type", "Client Type"],
  ["needs", "Needs"],
  ["lead_category", "Lead Category"],
  ["lead_id", "Lead ID"],
  // `client_enquiry` is the persisted S1 notes field.  Keep it immediately
  // beside Lead ID so imported and manually-entered notes share one column.
  ["client_enquiry", "Notes"],
  ["owner", "Owner"],
];
const S2 = [
  ["agent_name", "Agent Name"],
  ["call_direction", "Call Direction"],
  ["call_duration", "Call Duration"],
  ["case_type", "Case Type"],
  ["case_worker_assigned", "Case Worker Assigned"],
  // Read-only: the Case Status Change saved on this lead's call record (Calls page), "N/A" if none chosen.
  ["case_status_change", "Case Status Change"],
  ["urgency_flag", "Urgency Flag"],
  ["redirect_escalation_to", "Redirect/Escalation To"],
  ["call_outcome", "Call Outcome"],
  ["call_summary", "Call Summary"],
  ["enquiry_question", "Enquiry Question"],
  ["s2_status", "Status"],
];
const LEAD_STICKY_FIELDS = ["date_received", "source", "name"];
const FULL_TEXT_LEAD_FIELDS = new Set([
  "needs",
  "call_outcome",
  "call_summary",
  "enquiry_question",
]);
const FIELD_OPTIONS = {
  client_type: [
    "New Client - Individual",
    "New Client - Organization",
    "Existing client - Individual",
    "Existing client - Organization",
    "Voicemail Call",
    "Spam / Wrong Call",
  ],
  call_direction: [
    "Direct Inbound",
    "Direct Outbound",
    "Brevo Outbound",
    "Brevo Follow Up",
    "Missed Call Outbound",
    "WA-Inbound",
    "WA-Outbound",
  ],
  urgency_flag: [
    "None",
    "Urgent - RFE",
    "Urgent - NOID",
    "Grace Period",
    "Visa Expiry",
    "Court Date",
    "Filing Deadline",
    "Client Distressed",
  ],
  case_type: [
    "Open",
    "Success - PC",
    "Success - GE",
    "PC Follow Up",
    "PC shared and lost",
    "Fee details shared & FU",
    "Client Side delay",
    "Move to next date",
    "Blocker",
    "Dropped",
  ],
  source: LEAD_SOURCE_FILTER_OPTIONS,
  lead_category: [
    "EB Employment / DC",
    "Family GC",
    "General Query",
    "RFE / Case Rescue",
    "Student",
    "Visitor / B1,B2",
    "Work Visa",
  ],
  s1_status: ["New", "Moved Over"],
  s2_status: [
    "Contacted",
    "Dropped",
    "Initial email sent",
    "New",
    "Non Responsive",
    "On hold",
    "PC Completed",
  ],
};
const CUSTOM_DROPDOWN_FIELDS = new Set([
  "call_direction",
  "case_type",
  "urgency_flag",
  "s2_status",
]);
const FIXED_OPTION_FIELDS = new Set(["lead_category", "s1_status"]);
const DATE_DISPLAY_FIELDS = new Set(["date_received"]);
// Call Details repeats these S1 columns; they show the lead's own S1 data so
// S1 and S2 never disagree. The call's value is only a fallback.
const LEAD_SHARED_CALL_FIELDS = new Set(["date_received", "source", "name"]);
const callGroupValue = (row, key) => {
  const own = row.record?.[key];
  if (LEAD_SHARED_CALL_FIELDS.has(key) && String(own ?? "").trim())
    return DATE_DISPLAY_FIELDS.has(key) ? formatDisplayDate(own, "") : own;
  return row.call_details?.[key];
};
const LEAD_FILTER_FIELDS = [
  ["source", "Source"],
  ["client_type", "Client Type"],
  ["lead_category", "Lead Category"],
  ["owner", "Owner"],
  ["call_direction", "Call Direction"],
  ["case_type", "Case Type"],
  ["urgency_flag", "Urgency Flag"],
  ["s2_status", "Status"],
  ["action_status", "Action Status"],
  ["dependency", "Dependency"],
  ["case_status", "Case Status"],
];
const IMPORT_FIELDS = [
  ...S1.filter(([key]) => key !== "open_duration"),
  ["client_profile", "Client Profile"],
  ["s1_status", "Status"],
  ["processed", "Processed (mark when complete)"],
  ["flag", "Flag"],
  ["lead_category", "Category"],
  ["lead_id", "Lead ID Assigned"],
  ["extension", "Extension"],
];
const norm = (value) =>
  String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
// Keep the established normalized-header matching as the single import
// mapping source. Imported column order is irrelevant.
const automaticallyMapImportHeaders = (headers) =>
  Object.fromEntries(
    headers.map((header) => {
      const match = IMPORT_FIELDS.find(
        ([, label]) => norm(header) === norm(label),
      );
      return [header, match?.[0] || ""];
    }),
  );
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );


function leadOpenDuration(dateReceived) {
  const received = centralCalendarDay(dateReceived);
  if (!Number.isFinite(received)) return "-";
  const days = Math.max(1, centralCalendarDay(new Date()) - received + 1);
  return `${days} ${days === 1 ? "day" : "days"}`;
}

export default function Leads({ user, sharedAgent = null }) {
  const initial = getDefaultDateRange();
  const [stage, setStage] = usePersistentTableState("Leads.stage", "S1");
  const [draft, setDraft] = usePersistentTableState("Leads.draft", {
    start_date: initial.start,
    end_date: initial.end,
    agent: "",
    lead_number: "",
    extension: "",
    client_number: "",
    call_id: "",
    client_id: "",
    lead_id: "",
  });
  const [filters, setFilters] = usePersistentTableState("Leads.filters", draft);
  const [hasAppliedDateFilter, setHasAppliedDateFilter] = usePersistentTableState("Leads.hasAppliedDateFilter", false);
  // S2 "Case Status Change" filter: chosen in the dropdown, applied (with the other filters) when Apply is pressed.
  const [caseStatusFilter, setCaseStatusFilter] = usePersistentTableState("Leads.caseStatusFilter", { draft: "", applied: "" });
  const [rows, setRows] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [priority, setPriority] = usePersistentTableState("Leads.priority", false);
  const [importOpen, setImportOpen] = useState(false);
  const [paste, setPaste] = useState("");
  const [parsed, setParsed] = useState(null);
  const [selectedFileName, setSelectedFileName] = useState("");
  const [importFeedback, setImportFeedback] = useState("");
  const [importOwnerId, setImportOwnerId] = useState("");
  const [savingCell, setSavingCell] = useState("");
  const [ownerDrafts, setOwnerDrafts] = useState({});
  const [transcriptModalCall, setTranscriptModalCall] = useState(null);
  const [audioModalCall, setAudioModalCall] = useState(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState("");
  const [priorityCursor, setPriorityCursor] = useState(0);
  const [selected, setSelected] = useState(new Set());
  const [editCell, setEditCell] = useState(null);
  const [notesModal, setNotesModal] = useState(null);
  const [leadDetailModal, setLeadDetailModal] = useState(null);
  const [scoreModalRow, setScoreModalRow] = useState(null);
  const [dataFiltersOpen, setDataFiltersOpen] = useState(false);
  const [dataFilters, setDataFilters] = usePersistentTableState("Leads.dataFilters", {});
  const fileInputRef = useRef(null);
  const canManage = canApproveDependencies(user);
  // The Agent filter belongs to "Assigning the Calls" (one selection for every tab); it arrives here as a prop and is applied
  // to this tab's draft and applied filters together, so the list follows it without a second click.
  const sharedAgentName = sharedAgent?.name || "";
  useEffect(() => {
    const withAgent = (current) => (current.agent === sharedAgentName ? current
      : { ...current, agent: sharedAgentName, ...(sharedAgentName ? { extension: "" } : {}) });
    setDraft(withAgent);
    setFilters(withAgent);
  }, [sharedAgentName]);
  const restrictedAgent = isRestrictedAgent(user);
  // Everyone sees every lead. An agent may change exactly one thing: the Lead Category of a lead they own (the server enforces the
  // same); everything else is view-only for them.
  const ownsLead = (row) => !restrictedAgent || (Boolean(user?.id) && String(row.record?.owner_id || "") === String(user.id));

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const query = hasAppliedDateFilter
      ? filters
      : { ...filters, start_date: "", end_date: "" };
    try {
      setRows(await getLeads(query));
    } catch (err) {
      setError(err.message || "Failed to load leads.");
    } finally {
      setLoading(false);
    }
  }, [filters, hasAppliedDateFilter]);
  useEffect(() => {
    load();
  }, [load]);
  // New leads arrive from the Google Sheet sync in the background. Quietly
  // re-fetch on the shared page-refresh timer (no spinner, no page reload) so
  // they appear on their own. Non-silent calls are the initial/filter loads,
  // which load() above already performs.
  const refreshQuietly = useCallback(async ({ silent, isCurrent }) => {
    if (!silent) return;
    const query = hasAppliedDateFilter
      ? filters
      : { ...filters, start_date: "", end_date: "" };
    try {
      const fresh = await getLeads(query);
      if (isCurrent()) setRows(fresh);
    } catch {
      // Keep showing the current rows; the next tick retries.
    }
  }, [filters, hasAppliedDateFilter]);
  useActivePageRefresh(true, refreshQuietly);
  // The agent list feeds the Owner dropdowns and import owner, which only managers use; an agent may not read the user list.
  useEffect(() => {
    if (!canManage) return;
    getFDUsers()
      .then((data) =>
        setAgents(
          (data || []).filter(
            (item) => String(item.role || "agent").toLowerCase() === "agent",
          ),
        ),
      )
      .catch(() => setAgents([]));
  }, [canManage]);

  const activeFieldKeys = useMemo(
    () =>
      new Set((stage === "S1" ? S1 : [...S1, ...S2]).map(([key]) => key)),
    [stage],
  );
  const dataFilterDefinitions = useMemo(
    () =>
      LEAD_FILTER_FIELDS.filter(([key]) => activeFieldKeys.has(key)).map(
        ([key, label]) => ({
          key,
          label,
          multiple: key === "source",
          options: key === "source"
            ? LEAD_SOURCE_FILTER_OPTIONS
            : [
                ...new Set([
                  ...(key === "owner"
                    ? agents.map((agent) => agent.name)
                    : FIELD_OPTIONS[key] || []),
                  ...rows
                    .map((row) => row.record?.[key])
                    .filter((value) => String(value ?? "").trim())
                    .map(String),
                ]),
              ],
        }),
      ),
    [activeFieldKeys, agents, rows],
  );
  const visible = useMemo(() => {
    const matchingRows = rows.filter((row) =>
      dataFilterDefinitions.every(
        (filter) =>
          filter.multiple
            ? matchesSourceFilter(row.record?.[filter.key], dataFilters[filter.key])
            : !dataFilters[filter.key] ||
              String(row.record?.[filter.key] ?? "").trim() ===
                dataFilters[filter.key],
      ),
    );
    // Only on the S2 tab, where the column and its dropdown exist.
    const byStatus = stage === "S2" && caseStatusFilter.applied
      ? matchingRows.filter((row) => matchesCaseStatus(row.case_status_change, caseStatusFilter.applied))
      : matchingRows;
    return priority ? rankCallsByPriority(byStatus) : byStatus;
  }, [dataFilterDefinitions, dataFilters, priority, rows, stage, caseStatusFilter.applied]);
  const activeDataFilterCount = Object.entries(dataFilters).filter(
    ([key, value]) =>
      activeFieldKeys.has(key) &&
      (key === "source" ? selectedSources(value).length > 0 : Boolean(value)),
  ).length;
  const movePriorityCursor = (amount) =>
    setPriorityCursor((current) =>
      Math.min(Math.max(visible.length - 1, 0), current + amount),
    );
  const groups =
    stage === "S1"
      ? [["S1 - Leads Received", S1, "s1"]]
      : [
          ["Call Details", CALL, "call"],
          ["S1 - Leads Received", S1, "s1"],
          ["S2 - Leads Reached Out", S2, "s2"],
        ];
  const toggleSelected = (id) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const save = async (row, key, value, displayValue = value) => {
    const token = `${row.id}:${key}`;
    setSavingCell(token);
    setError("");
    try {
      await updateLeadField(row.id, key, value);
      setRows((current) =>
        current.map((item) =>
          item.id === row.id
            ? {
                ...item,
                record: {
                  ...item.record,
                  [key]: displayValue,
                  ...(key === "owner" ? { owner_id: value } : {}),
                },
              }
            : item,
        ),
      );
      setNotice("Saved");
      setTimeout(() => setNotice(""), 1200);
    } catch (err) {
      setError(err.message || "Save failed.");
    } finally {
      setSavingCell("");
    }
  };

  const optionsFor = (key) =>
    FIXED_OPTION_FIELDS.has(key)
      ? FIELD_OPTIONS[key]
      : [
          ...new Set([
            ...(FIELD_OPTIONS[key] || []),
            ...rows
              .map((row) => row.record?.[key])
              .filter(
                (value) =>
                  value !== null && value !== undefined && String(value).trim(),
              )
              .map(String),
          ]),
        ];

  const updateDraftCell = (row, key, value) =>
    setRows((current) =>
      current.map((item) =>
        item.id === row.id
          ? { ...item, record: { ...item.record, [key]: value } }
          : item,
      ),
    );

  const prepare = (table) => {
    if (table.headerError) {
      setParsed({ ...table, mapping: {}, records: [], validationErrors: [table.headerError] });
      setImportFeedback(table.headerError);
      setError("");
      return;
    }
    const mapping = automaticallyMapImportHeaders(table.headers);
    const records = table.rows.map((cells) =>
      Object.fromEntries(
        table.headers
          .map((header, index) => [mapping[header], cells[index]])
          .filter(([key]) => key),
      ),
    );
    const validationErrors = [];
    if (!records.length)
      validationErrors.push("No records were found to validate.");
    if (!Object.values(mapping).includes("phone"))
      validationErrors.push(
        "A recognized Phone column is required to import leads.",
      );
    records.forEach((record, index) => {
      if (!String(record.phone || "").trim())
        validationErrors.push(`Row ${index + 1}: Phone number is required.`);
      if (!String(record.lead_id || "").trim())
        validationErrors.push(`Row ${index + 1}: Lead ID is required.`);
    });
    setParsed({ ...table, mapping, records, validationErrors });
    setError("");
    setImportFeedback(
      validationErrors.length
        ? validationErrors.join(" ")
        : `Data validated successfully. ${records.length} ${records.length === 1 ? "record is" : "records are"} ready to import.`,
    );
  };
  const onFile = async (file) => {
    if (!file || !/\.(csv|txt)$/i.test(file.name)) {
      setError("Choose a CSV or TXT file.");
      return;
    }
    setSelectedFileName(file.name);
    setPaste("");
    const text = await file.text();
    prepare(
      parseTable(text, file.name.toLowerCase().endsWith(".csv") ? "," : "\t"),
    );
  };
  const removeUploadedFile = () => {
    if (fileInputRef.current) fileInputRef.current.value = "";
    setSelectedFileName("");
    setParsed(null);
    setImportFeedback("");
    setError("");
  };
  const submitImport = async () => {
    const mapping = parsed?.mapping || {};
    if (
      !parsed ||
      parsed.validationErrors?.length ||
      !Object.values(mapping).includes("phone")
    ) {
      setImportFeedback(
        parsed?.validationErrors?.join(" ") ||
          "Validate the imported data before importing.",
      );
      return;
    }
    const records = parsed.records;
    setLoading(true);
    setError("");
    try {
      const result = await importLeads(records, importOwnerId);
      const completed =
        Number(result.inserted || 0) + Number(result.updated || 0);
      setNotice(
        `${completed} lead${completed === 1 ? "" : "s"} imported${result.updated ? ` (${result.updated} updated)` : ""}.`,
      );
      setImportOpen(false);
      setParsed(null);
      setPaste("");
      setSelectedFileName("");
      setImportOwnerId("");
      setImportFeedback("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      await load();
    } catch (err) {
      setError(
        typeof err.message === "string" ? err.message : "Import failed.",
      );
    } finally {
      setLoading(false);
    }
  };
  const [submittingIds, setSubmittingIds] = useState(new Set());
  // Stage 1 Submit updates the affected rows in place instead of going
  // through runAction's full setLoading(true) + reload cycle, which swaps
  // the whole table for a spinner and loses filters/pagination/scroll
  // position. The API call itself only re-confirms the already-set owner
  // and stamps submitted_at -- no other visible field changes as a result,
  // so a local patch is sufficient here.
  const handleSubmit = async (ids) => {
    setSubmittingIds((current) => new Set([...current, ...ids]));
    setError("");
    try {
      const ownerIds = Object.fromEntries(ids.map((id) => [id, ownerDrafts[id]]).filter(([, ownerId]) => ownerId));
      const result = await submitLeads(ids, ownerIds);
      if (result?.submitted !== ids.length) {
        throw new Error("Not every selected lead was submitted. No local changes were applied.");
      }
      setNotice(ids.length > 1 ? `${ids.length} lead(s) assigned.` : "Lead assigned.");
      const submittedAt = new Date().toISOString();
      setRows((current) =>
        current.map((row) => {
          if (!ids.includes(row.id)) return row;
          // The Owner dropdown reads record.owner_id (and the read-only
          // view reads record.owner) -- ownerDrafts is cleared right below,
          // so without patching these here too the row falls back to blank
          // ("Choose owner") until the next full reload, even though the
          // assignment already persisted.
          const newOwnerId = ownerIds[row.id];
          const newOwnerName = newOwnerId
            ? agents.find((agent) => agent.id === newOwnerId)?.name
            : undefined;
          return {
            ...row,
            has_submitted_assignment: true,
            record: {
              ...row.record,
              submitted_at: submittedAt,
              ...(newOwnerId ? { owner_id: newOwnerId } : {}),
              ...(newOwnerName ? { owner: newOwnerName } : {}),
            },
          };
        }),
      );
      setSelected((current) => {
        const next = new Set(current);
        ids.forEach((id) => next.delete(id));
        return next;
      });
      setOwnerDrafts((current) => {
        const next = { ...current };
        ids.forEach((id) => delete next[id]);
        return next;
      });
    } catch (err) {
      setError(err.message || "Action failed.");
    } finally {
      setSubmittingIds((current) => {
        const next = new Set(current);
        ids.forEach((id) => next.delete(id));
        return next;
      });
    }
  };
  const [scoringIds, setScoringIds] = useState(new Set());
  // Reuses the exact Calls-page Manual Data Score endpoint/logic -- a lead's
  // manual fields sync onto the same CallAnalysisResult row once matched to
  // a call (see ANALYSIS_FIELD_MAP in leads.py), so there is nothing
  // lead-specific to score separately.
  const runLeadManualScore = async (row, { silent = false } = {}) => {
    if (!row.analysis_id || scoringIds.has(row.id)) return;
    setScoringIds((current) => new Set([...current, row.id]));
    if (!silent) setError("");
    try {
      const result = await scoreManualData(row.analysis_id);
      setRows((current) =>
        current.map((item) =>
          item.id === row.id
            ? {
                ...item,
                manual_data_score: result.score,
                manual_data_score_reason: result.reason,
              }
            : item,
        ),
      );
    } catch (err) {
      if (!silent) setError(err.message || "Unable to score manual data.");
    } finally {
      setScoringIds((current) => {
        const next = new Set(current);
        next.delete(row.id);
        return next;
      });
    }
  };
  const runAction = async (action, ids, message) => {
    setLoading(true);
    setError("");
    try {
      await action(ids);
      setSelected(new Set());
      setNotice(message);
      await load();
    } catch (err) {
      setError(err.message || "Action failed.");
    } finally {
      setLoading(false);
    }
  };

  const applyFilters = () => {
    const hasRange = Boolean(draft.start_date || draft.end_date);
    setHasAppliedDateFilter(hasRange);
    setFilters({ ...draft });
    setCaseStatusFilter((current) => ({ ...current, applied: current.draft }));
  };

  const exportValue = (row, key, group) => {
    if (group === "call") return callGroupValue(row, key);
    if (key === "open_duration") return leadOpenDuration(row.record?.date_received);
    const value = row.record?.[key];
    // Same display rules as the table cells, so S1/S2 export what is shown.
    if (key === "client_type") return displayClientType(value);
    if (DATE_DISPLAY_FIELDS.has(key)) return formatDisplayDate(value, "");
    return value;
  };

  const exportExcel = () => {
    // Keep each column's group: Call Details and S1 both have "source",
    // "name" and "date_received", and S1 must read the sheet record, not the
    // call's phone number / caller name.
    const columns = groups.flatMap(([, fields, cls]) =>
      fields.map(([key, label]) => [key, label, cls]),
    );
    const header = `<tr>${groups.map(([name, fields, cls]) => `<th colspan="${fields.length}" class="${cls}">${escapeHtml(name)}</th>`).join("")}</tr><tr>${columns.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join("")}</tr>`;
    const body = visible
      .map(
        (row) =>
          `<tr>${columns.map(([key, , cls]) => `<td>${escapeHtml(key === "case_status_change" ? caseStatusText(row.case_status_change) : exportValue(row, key, cls))}</td>`).join("")}</tr>`,
      )
      .join("");
    const blob = new Blob(
      [
        `<html><head><meta charset="utf-8"><style>.call{background:#e6f4ff}.s1{background:#fff6df}.s2{background:#eee7ff}th,td{padding:6px;border:1px solid #999}</style></head><body><table>${header}${body}</table></body></html>`,
      ],
      { type: "application/vnd.ms-excel" },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `FlintDesk_Leads_${filters.start_date}_to_${filters.end_date}.xls`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const cell = (row, key, label, group) => {
    const details = row.call_details || {};
    const record = row.record || {};
    const value = group === "call" ? callGroupValue(row, key) : record[key];
    if (group === "call") {
      if (key === "client_id") return <ClientIdCell clientId={value} />;
      if (key === "call_id")
        return details.call_id ? (
          <span
            title={
              details.identified
                ? "Identified by lead date + phone number, not yet confirmed via Analyse"
                : undefined
            }
            className={details.identified ? "leads-identified-call-id" : undefined}
          >
            {details.call_reference || details.call_id}
          </span>
        ) : (
          "-"
        );
      if (key === "transcript")
        return (
          <button
            className="icon-action-btn"
            disabled={!details.call_id}
            onClick={async () => {
              try {
                const content = await getCallContent(details.call_id);
                setTranscriptModalCall({ id: details.call_reference || details.call_id,
                  transcript: content.transcript || "No transcript available." });
              } catch (err) { setError(err.message || "Failed to load transcript."); }
            }}
            title="View transcript"
          >
            <i className="fa-regular fa-file-lines" />
          </button>
        );
      if (key === "audio")
        return (
          <button
            className="icon-action-btn"
            disabled={!value}
            onClick={async () => {
              setAudioModalCall({ id: details.call_reference || details.call_id, date: details.date_received });
              setAudioUrl(""); setAudioError(""); setAudioLoading(true);
              try {
                const url = await getCallAudioBlobUrl(details.call_id);
                setAudioUrl(url);
              } catch (audioError) {
                setAudioError(audioError.message || "Unable to load audio.");
              } finally {
                setAudioLoading(false);
              }
            }}
            title="Play audio"
          >
            <i className="fa-solid fa-play" />
          </button>
        );
      if (key === "open_duration")
        return enquiryOpenDuration({
          date_received: details.date_received,
          raw: record,
        });
      if (key === "manual_score") {
        // Reuses the exact Calls-page Manual Data Score (same endpoint, same
        // stored score/reason on the linked CallAnalysisResult row).
        if (!row.analysis_id)
          return <span className="muted" title="No matched call yet">—</span>;
        return (
          <div className="manual-score-cell">
            {row.manual_data_score != null && (
              <button
                type="button"
                className="manual-score-value"
                onClick={() => setScoreModalRow(row.id)}
                aria-label="View manual data score and reason"
              >
                {Number(row.manual_data_score).toFixed(1)}
                <small>/10</small>
              </button>
            )}
            <button
              type="button"
              disabled={scoringIds.has(row.id)}
              onClick={() => runLeadManualScore(row)}
            >
              {scoringIds.has(row.id)
                ? "Scoring…"
                : row.manual_data_score != null
                  ? "Re-score"
                  : "Score"}
            </button>
          </div>
        );
      }
      return value || "-";
    }
    // Source is the imported UTM source, shown as received (no dropdown).
    if (key === "source" && group === "s1")
      return (
        <span className="leads-source-text" title={value ?? ""}>
          {value || "—"}
        </span>
      );
    if (key === "open_duration") return leadOpenDuration(record.date_received);
    if (key === "case_status_change" && group === "s2")
      return (
        <span className={`leads-case-status${row.case_status_change ? "" : " leads-case-status-none"}`}
          title="Set on the Calls page; shown here as it is saved there">
          {caseStatusText(row.case_status_change)}
        </span>
      );
    if (key === "lead_id") return <LeadIdCell leadId={value} />;
    // Agents cannot edit S1 data, but they need to be able to read a complete
    // client note instead of the table's truncated value.
    if (key === "client_enquiry" && restrictedAgent)
      return (
        <button
          type="button"
          className="leads-edit-cell leads-notes-viewer"
          onClick={() => setNotesModal({ value: value ?? "" })}
          aria-label="View full notes"
          title="View full notes"
        >
          {value || "—"}
        </button>
      );
    if (FULL_TEXT_LEAD_FIELDS.has(key))
      return (
        <button
          type="button"
          className="leads-edit-cell leads-notes-viewer"
          onClick={() =>
            setLeadDetailModal({ row, key, label, value: value ?? "", group })
          }
          aria-label={`View full ${label}`}
          title={`View full ${label}`}
        >
          {value || "—"}
        </button>
      );
    const disabled = savingCell === `${row.id}:${key}`;
    const options = optionsFor(key);
    if (key === "owner" && group === "s1") {
      // Agents see every lead but never assign one. Show the persisted owner
      // name directly instead of a disabled assignment dropdown, which can
      // otherwise display the placeholder while the agents list is loading.
      if (!canManage)
        return (
          <span className="leads-owner-name" title={record.owner || ""}>
            {record.owner || "—"}
          </span>
        );
      return (
        <select
          className="calls-stage-select"
          aria-label="Choose Owner"
          value={ownerDrafts[row.id] ?? record.owner_id ?? ""}
          disabled={!canManage || disabled}
          onChange={(event) => {
            setOwnerDrafts((current) => ({ ...current, [row.id]: event.target.value }));
          }}
        >
          <option value="">Choose owner</option>
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.name}
            </option>
          ))}
        </select>
      );
    }
    if (FIXED_OPTION_FIELDS.has(key) && group === "s1")
      return (
        <select
          className="calls-stage-select leads-fixed-select"
          aria-label={`Choose ${label}`}
          value={String(value ?? "")}
          // Lead Category is the one field an agent may edit, and only on their own leads.
          disabled={
            (restrictedAgent && (key !== "lead_category" || !ownsLead(row))) ||
            disabled
          }
          onChange={(event) => {
            updateDraftCell(row, key, event.target.value);
            save(row, key, event.target.value);
          }}
        >
          <option value="">Choose</option>
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    if (group === "s2" && CUSTOM_DROPDOWN_FIELDS.has(key)) {
      const s2Disabled = disabled || restrictedAgent;      // Stage 2 is view-only for agents
      return (
        <div className="calls-stage-editor">
          <select
            className="calls-stage-select"
            aria-label={`Choose ${label}`}
            value={options.includes(String(value ?? "")) ? String(value) : ""}
            disabled={s2Disabled}
            onChange={(event) => {
              updateDraftCell(row, key, event.target.value);
              save(row, key, event.target.value);
            }}
          >
            <option value="">Choose</option>
            {options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
          <input
            className="calls-stage-input"
            aria-label={`Type custom ${label}`}
            placeholder="Type custom"
            value={value ?? ""}
            disabled={s2Disabled}
            onChange={(event) => updateDraftCell(row, key, event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            onBlur={(event) => save(row, key, event.target.value)}
          />
        </div>
      );
    }
    return (
      <button
        type="button"
        className="leads-edit-cell"
        disabled={disabled || restrictedAgent}
        onClick={() => setEditCell({ row, key, label, value: value ?? "" })}
      >
        {key === "client_type"
          ? displayClientType(value)
          : DATE_DISPLAY_FIELDS.has(key)
            ? formatDisplayDate(value, "—")
            : value || "—"}
      </button>
    );
  };

  return (
    <section className="process-page leads-page">
      <h1 className="sr-only">Leads</h1>
      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector
          value={{ start: draft.start_date, end: draft.end_date }}
          onChange={(range) =>
            setDraft((current) => ({
              ...current,
              start_date: range.start,
              end_date: range.end,
            }))
          }
        />
        {!restrictedAgent && (
          <Filter label="Lead Number">
            <select
              value={draft.lead_number}
              onChange={(e) =>
                setDraft({ ...draft, lead_number: e.target.value })
              }
            >
              <option value="">Select Number</option>
              <option value="3128781255">3128781255</option>
              <option value="2243374447">2243374447</option>
              <option value="2243939098">2243939098</option>
            </select>
          </Filter>
        )}
        {!restrictedAgent && <Filter label="Extension">
          <select
            value={draft.extension}
            disabled={Boolean(draft.agent) && !draft.extension}
            onChange={(e) =>
              setDraft({
                ...draft,
                extension: e.target.value,
                ...(e.target.value ? { agent: "" } : {}),
              })
            }
          >
            <option value="">All extensions</option>
            {getAllLeadExtensions().map(
              (extension) => (
                <option key={extension} value={extension}>
                  {extension}
                </option>
              ),
            )}
          </select>
        </Filter>}
        <Filter label="Lead ID">
          <input
            value={draft.lead_id}
            placeholder="LD#12345"
            title="Search by Lead ID, for example LD#12345 (part of the number also works)"
            onChange={(e) => setDraft({ ...draft, lead_id: e.target.value })}
            onKeyDown={(e) => { if (e.key === "Enter") applyFilters(); }}
          />
        </Filter>
        <Filter label="Client Number">
          <input
            value={draft.client_number}
            onChange={(e) =>
              setDraft({ ...draft, client_number: e.target.value })
            }
          />
        </Filter>
        <Filter label="Call ID">
          <input
            value={draft.call_id}
            onChange={(e) => setDraft({ ...draft, call_id: e.target.value })}
          />
        </Filter>
        <Filter label="Client ID">
          <input
            value={draft.client_id}
            onChange={(e) => setDraft({ ...draft, client_id: e.target.value })}
          />
        </Filter>
        {stage !== "S1" && (
          <button
            type="button"
            className={`calls-priority-button ${priority ? "active" : ""}`}
            onClick={() => {
              setPriority((active) => !active);
              setPriorityCursor(0);
            }}
            aria-pressed={priority}
          >
            View by Priority
          </button>
        )}
      </ScrollableFilterBelt>
      <div className="filter-belt-actions">
        <div className="leads-tabs">
            {[
              ["S1", "S1 - Leads Received"],
              ["S2", "S2 - Leads Reached Out"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={stage === id ? "active" : ""}
                onClick={() => setStage(id)}
              >
                {label}
              </button>
            ))}
          </div>
        <button
          type="button"
          className={`table-action data-filter-toggle ${dataFiltersOpen ? "active" : ""}`}
          onClick={() => setDataFiltersOpen((open) => !open)}
          aria-expanded={dataFiltersOpen}
        >
          <i className="fa-solid fa-filter" /> Filter Data
          {activeDataFilterCount ? ` (${activeDataFilterCount})` : ""}
        </button>
        {/* Manual lead import disabled: leads sync from the Google Sheet.
        {stage === "S1" && canManage && (
          <button
            className="leads-add-button"
            onClick={() => setImportOpen(true)}
          >
            Add Leads
          </button>
        )}
        */}
        {canManage && selected.size > 0 && (
          <>
            <button
              className="primary-button danger-button"
              onClick={() =>
                runAction(
                  deleteLeads,
                  [...selected],
                  `${selected.size} lead(s) deleted.`,
                )
              }
            >
              Delete selected ({selected.size})
            </button>
            {stage === "S1" && (
              <button
                className="primary-button"
                onClick={() => handleSubmit([...selected])}
              >
                Submit selected ({selected.size})
              </button>
            )}
          </>
        )}
        <button
          className="primary-button calls-toolbar-apply"
          type="button"
          onClick={applyFilters}
        >
          Apply
        </button>
        {stage === "S2" && (
          <label className="leads-status-filter">
            <span className="sr-only">Filter by Case Status Change</span>
            <select
              className="calls-stage-select"
              aria-label="Filter by Case Status Change"
              value={caseStatusFilter.draft}
              onChange={(event) => setCaseStatusFilter((current) => ({ ...current, draft: event.target.value }))}
            >
              <option value="">All case statuses</option>
              {CASE_STATUS_OPTIONS.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </label>
        )}
        {stage === "S2" && (
          <button className="table-action" type="button" onClick={exportExcel}>
            <i className="fa-solid fa-file-excel" /> Export to Excel
          </button>
        )}
      </div>
      </StickyFilterRegion>
      {dataFiltersOpen && (
        <DataFilterPanel
          filters={dataFilterDefinitions}
          values={dataFilters}
          onChange={(key, value) =>
            setDataFilters((current) => ({ ...current, [key]: value }))
          }
          onClear={() => setDataFilters({})}
        />
      )}
      {(priority || notice) && (
        <div className="leads-actions">
          {priority && (
            <>
              <button
                type="button"
                className="table-action"
                onClick={() => movePriorityCursor(1)}
                disabled={!visible.length || priorityCursor >= visible.length - 1}
              >
                Next
              </button>
              <button
                type="button"
                className="table-action"
                onClick={() => movePriorityCursor(3)}
                disabled={!visible.length || priorityCursor >= visible.length - 1}
              >
                Next 3
              </button>
              <span>
                Priority {visible.length ? priorityCursor + 1 : 0} of{" "}
                {visible.length}
              </span>
            </>
          )}
          {notice && <span>{notice}</span>}
        </div>
      )}
      {error && <p className="leads-error">{error}</p>}
      {stage === "S1" && canManage && <LeadOwnershipNotice />}
      <section className="history-table-card">
        <h2>{stage === "S1" ? "Leads Records" : `${stage} records`}</h2>
        <p className="result-count">{visible.length} leads</p>
        {loading ? (
          <div className="calls-table-loading">
            <BufferingLoader text="Fetching leads" size="md" />
          </div>
        ) : (
          <TableScrollWrap className="calls-table-wrap">
            <table
              className={`calls-table leads-table leads-stage-${stage.toLowerCase()}`}
            >
              <thead>
                <tr className="calls-table-group-row">
                  {groups.map(([name, fields, cls]) => (
                    <th
                      key={name}
                      colSpan={fields.length}
                      className={`leads-group-${cls}`}
                    >
                      {name}
                    </th>
                  ))}
                  {priority && <th colSpan={2}>Priority</th>}
                  {stage === "S1" && canManage && <th>Stage 1 Actions</th>}
                  <th>Select rows</th>
                </tr>
                <tr>
                  {groups.flatMap(([, fields, cls]) =>
                    fields.map(([key, label]) => (
                      <th
                        key={`${cls}-${key}`}
                        className={`leads-column-${cls}`}
                        {...(stage === "S1" && LEAD_STICKY_FIELDS.includes(key)
                          ? { "data-lead-sticky-column": LEAD_STICKY_FIELDS.indexOf(key) }
                          : {})}
                      >
                        {label}
                      </th>
                    )),
                  )}
                  {priority && (
                    <>
                      <th>Lead Score</th>
                      <th>Business Opportunity</th>
                    </>
                  )}
                  {stage === "S1" && canManage && <th>Submit</th>}
                  <th>
                    <input
                      type="checkbox"
                      aria-label="Select all rows"
                      checked={
                        visible.length > 0 &&
                        visible.every((row) => selected.has(row.id))
                      }
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? new Set(visible.map((row) => row.id))
                            : new Set(),
                        )
                      }
                    />
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((row, index) => {
                  // A confirmed (non-"identified") call_id means the assigned
                  // call has actually been made and its Stage 2 data is real,
                  // not just a phone+date guess -- see leads.py's
                  // _identified_calls_by_lead vs the explicit Analyse action.
                  // Green once the call was made - the same day or any later day (the backend counts from the assigned date, Central time).
                  const callMade = stage === "S1" && leadCallMade(row);
                  return (
                  <tr
                    key={String(
                      row.id ?? row.record?.lead_id ?? `lead-${index}`,
                    )}
                    className={[
                      priority && index === priorityCursor
                        ? "leads-priority-current"
                        : "",
                      callMade ? "leads-row-call-made" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    {groups.flatMap(([, fields, group]) =>
                      fields.map(([key, label]) => (
                        <td
                          key={`${group}-${key}`}
                          {...(stage === "S1" && LEAD_STICKY_FIELDS.includes(key)
                            ? { "data-lead-sticky-column": LEAD_STICKY_FIELDS.indexOf(key) }
                            : {})}
                        >
                          {cell(row, key, label, group)}
                          {key === "phone" && group === "s1" && leadCallMadeOnNote(row) && (
                            <small className="leads-call-made-on" title="The call was not made on the day the lead was assigned">{leadCallMadeOnNote(row)}</small>
                          )}
                        </td>
                      )),
                    )}
                    {priority && (
                      <>
                        <td>
                          <LeadScoreCell item={row} />
                        </td>
                        <td>
                          <BusinessOpportunityCell item={row} />
                        </td>
                      </>
                    )}
                    {stage === "S1" && canManage && (
                      <td>
                        <div className="leads-row-actions">
                          <button
                            className={`primary-button leads-row-action ${(row.has_submitted_assignment || row.record?.submitted_at) ? "completed-button" : ""}`}
                            disabled={
                              !(ownerDrafts[row.id] || row.record?.owner_id) ||
                              Boolean(row.has_submitted_assignment || row.record?.submitted_at) ||
                              submittingIds.has(row.id)
                            }
                            onClick={() => handleSubmit([row.id])}
                          >
                            {submittingIds.has(row.id)
                              ? "Submitting..."
                              : (row.has_submitted_assignment || row.record?.submitted_at)
                                ? "Submitted"
                                : "Submit"}
                          </button>
                        </div>
                      </td>
                    )}
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Select row ${row.id}`}
                        checked={selected.has(row.id)}
                        onChange={() => toggleSelected(row.id)}
                      />
                    </td>
                  </tr>
                  );
                })}
                {!visible.length && (
                  <tr>
                    <td
                      colSpan={
                        groups.reduce(
                          (sum, [, fields]) => sum + fields.length,
                          priority ? 2 : 0,
                        ) + 2
                      }
                    >
                      No leads found for the permitted extensions.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableScrollWrap>
        )}
      </section>
      {/* Manual lead import modal disabled (leads sync from the Google Sheet).
      {importOpen && (
        <div className="modal-backdrop leads-blur-backdrop">
          <div
            className="modal-card leads-import-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="add-leads-title"
          >
            <div className="leads-import-modal-heading">
              <h3 id="add-leads-title">Add Leads</h3>
              <button
                type="button"
                className="leads-modal-close"
                aria-label="Close Add Leads dialog"
                title="Close"
                onClick={() => setImportOpen(false)}
              >
                <i className="fa-solid fa-xmark" />
              </button>
            </div>
            {error && (
              <p className="leads-error leads-import-error" role="alert">
                {error}
              </p>
            )}
            <label className="leads-file-picker">
              <span>Import CSV/TXT File</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.txt,text/csv,text/plain"
                onChange={(e) => onFile(e.target.files?.[0])}
              />
            </label>
            <label className="leads-file-picker">
              <span>Owner (optional)</span>
              <select
                value={importOwnerId}
                onChange={(event) => setImportOwnerId(event.target.value)}
              >
                <option value="">Leave unassigned</option>
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </select>
            </label>
            {selectedFileName && (
              <div className="leads-selected-file">
                <span>{selectedFileName}</span>
                <button
                  type="button"
                  className="table-action"
                  onClick={removeUploadedFile}
                >
                  Remove file
                </button>
              </div>
            )}
            <p className="leads-import-divider">
              or copy/paste data from Excel
            </p>
            <textarea
              rows="9"
              value={paste}
              onChange={(e) => setPaste(e.target.value)}
              placeholder="Paste header row and data here"
              aria-label="Paste header row and data here"
            />
            <p className="leads-import-hint">
              <em>
                First click “Validate pasted data”, then click “Import validated
                records”.
              </em>
            </p>
            <button
              className="table-action leads-validate-button"
              onClick={() => {
                setSelectedFileName("");
                if (fileInputRef.current) fileInputRef.current.value = "";
                prepare(parseTable(paste, "\t"));
              }}
            >
              Validate pasted data
            </button>
            {importFeedback && (
              <p
                className={`leads-import-feedback ${parsed?.validationErrors?.length ? "is-error" : "is-success"}`}
                role="status"
              >
                {importFeedback}
              </p>
            )}
            <div className="leads-modal-actions">
              <button
                className="table-action"
                onClick={() => setImportOpen(false)}
              >
                Cancel
              </button>
              <button
                className="primary-button"
                disabled={
                  !parsed || Boolean(parsed.validationErrors?.length) || loading
                }
                onClick={submitImport}
              >
                {loading ? "Importing..." : "Import validated records"}
              </button>
            </div>
          </div>
        </div>
      )}
      */}
      {editCell && (
        <div
          className="modal-backdrop leads-blur-backdrop"
          onMouseDown={() => setEditCell(null)}
        >
          <div
            className="modal-card leads-edit-modal"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="leads-edit-heading">
              <div>
                <small>{editCell.label}</small>
                <h3>Edit</h3>
              </div>
              <button
                className="table-action"
                onClick={() => setEditCell(null)}
              >
                Close
              </button>
            </div>
            <textarea
              autoFocus
              rows="7"
              value={editCell.value}
              onChange={(e) =>
                setEditCell({ ...editCell, value: e.target.value })
              }
            />
            <div className="leads-modal-actions">
              <button
                className="table-action"
                onClick={() => setEditCell(null)}
              >
                Cancel
              </button>
              <button
                className="primary-button"
                onClick={async () => {
                  await save(editCell.row, editCell.key, editCell.value);
                  setEditCell(null);
                }}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
      {scoreModalRow &&
        (() => {
          const row = rows.find((item) => item.id === scoreModalRow);
          if (!row) return null;
          const band = getManualScoreBand(row.manual_data_score);
          return (
            <div
              className="modal-backdrop leads-blur-backdrop"
              onMouseDown={() => setScoreModalRow(null)}
            >
              <div
                className="modal-card leads-edit-modal"
                onMouseDown={(e) => e.stopPropagation()}
              >
                <div className="leads-edit-heading">
                  <div>
                    <small>Manual Data Score</small>
                    <h3>
                      {Number(row.manual_data_score ?? 0).toFixed(1)}/10
                      {band && <span className={`manual-score-band manual-score-band-${band.key}`}> · {band.label}</span>}
                    </h3>
                  </div>
                  <button
                    className="table-action"
                    onClick={() => setScoreModalRow(null)}
                  >
                    Close
                  </button>
                </div>
                <p className="manual-score-modal-label">Reason</p>
                <p>
                  {row.manual_data_score_reason ||
                    "No reason was recorded for this score."}
                </p>
              </div>
            </div>
          );
        })()}
      {notesModal && (
        <div
          className="modal-backdrop leads-blur-backdrop"
          onMouseDown={() => setNotesModal(null)}
        >
          <div
            className="modal-card leads-edit-modal leads-notes-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="full-notes-title"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="leads-edit-heading">
              <div>
                <small>Notes</small>
                <h3 id="full-notes-title">Full notes</h3>
              </div>
              <button
                type="button"
                className="table-action"
                onClick={() => setNotesModal(null)}
              >
                Close
              </button>
            </div>
            <p className="leads-full-notes">
              {notesModal.value || "No notes were provided for this call."}
            </p>
          </div>
        </div>
      )}
      {leadDetailModal && (
        <div
          className="modal-backdrop leads-blur-backdrop"
          onMouseDown={() => setLeadDetailModal(null)}
        >
          <div
            className="modal-card leads-edit-modal leads-notes-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="lead-detail-title"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="leads-edit-heading">
              <div>
                <small>{leadDetailModal.label}</small>
                <h3 id="lead-detail-title">Full content</h3>
              </div>
              <button
                type="button"
                className="table-action"
                onClick={() => setLeadDetailModal(null)}
              >
                Close
              </button>
            </div>
            <p className="leads-full-notes">
              {leadDetailModal.value || `No ${leadDetailModal.label.toLowerCase()} was provided.`}
            </p>
            {/* Only managers edit from here; agents read the full text (they change nothing but the Lead Category of their own leads). */}
            {canManage && (
              <div className="leads-modal-actions">
                <button
                  type="button"
                  className="primary-button"
                  onClick={() => {
                    setEditCell(leadDetailModal);
                    setLeadDetailModal(null);
                  }}
                >
                  Edit
                </button>
              </div>
            )}
          </div>
        </div>
      )}
      {transcriptModalCall && <TranscriptModal call={transcriptModalCall} onClose={() => setTranscriptModalCall(null)} />}
      {audioModalCall && <AudioModal call={audioModalCall} url={audioUrl} loading={audioLoading} error={audioError} onClose={() => { if (audioUrl) URL.revokeObjectURL(audioUrl); setAudioUrl(""); setAudioModalCall(null); }} />}
    </section>
  );
}

function Filter({ label, children }) {
  return (
    <label className="calls-filter-group calls-inline-filter">
      <span className="calls-toolbar-label">{label}</span>
      {children}
    </label>
  );
}
