import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { useExplicitPageRefresh } from "../data/useExplicitPageRefresh.js";
import {
  getCallsByClientPhone,
  getCallsListPaged,
  getCallAudioBlobUrl,
  getCallContent,
  getDashboardFrontDeskAgents,
  getSyncFilters,
  importCalls,
  scoreManualData,
  updateCallField,
  getOwnRelatedLogs,
  closeOwnRelatedLogs,
  CLOSURE_ROWS_CHANGED_EVENT,
  updateManualFields,
} from "../data/api.js";
import {
  mapImportHeaders,
  normalizeImportHeader,
  parseDelimitedTable,
  parsePastedTable,
  recordsFromImportTable,
} from "../data/importMapping.js";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { CollapsiblePanel } from "../components/CollapsiblePanel.jsx";
import { CallRecordsUsageGuide } from "../components/CallRecordsUsageGuide.jsx";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { AttachmentCell } from "../components/AttachmentCell.jsx";
import { getDefaultDateRange, isValidDateRange } from "../data/filterDates.js";
import { markSavedToday, savedOnAnEarlierDay } from "../data/persistentTableState.js";
import { CALL_FOCUS_EVENT, focusRangeFor, takeCallFocus } from "../data/callFocus.js";
import { CASE_STATUS_OPTIONS, caseStatusKey } from "../data/statusOptions.js";
import { STAGE1_COLUMNS, STAGE2_COLUMNS, STAGE3_COLUMNS, STAGE4_COLUMNS } from "../data/callStageColumns.js";
import { ColumnHeaderLabel } from "../components/ColumnHeaderLabel.jsx";
import { validLifecycleStage } from "../data/caseLifecycle.js";
import { activeBeltFilterCount, beltServerFilters, effectiveOpenCase, validActionStatus, validLogUpdate, validOpenCase, validPriorityForm } from "../data/callsFilterBelt.js";
import CallsFilterBelt from "../components/CallsFilterBelt.jsx";
import {
  ALIAS_EXTENSION_FOR_LEAD_NUMBER,
  callMatchesLeadNumber,
  displayExtensionOption,
  getAllLeadExtensions,
  getLeadNumberExtensions,
  getOutboundLeadNumberOptions,
} from "../data/leadNumberFilter.js";
import { LEAD_SOURCE_GROUPS, LEAD_SOURCE_OPTIONS } from "../data/leadSource.js";
import {
  canAccessPriorityCases,
  canApproveDependencies,
  isRestrictedAgent,
} from "../data/accessControl.js";
import { enquiryOpenDuration } from "../data/enquiryOpenDuration.js";
import {
  calculatePriority,
  rankCallsByPriority,
} from "../data/priorityEngine.js";
import {
  BusinessOpportunityCell,
  LeadScoreCell,
} from "../components/PriorityScoreCell.jsx";
import { LeadScoreMatrixModal } from "../components/LeadScoreMatrixModal.jsx";
import { ManualScoreMatrixModal } from "../components/ManualScoreMatrixModal.jsx";
import { getManualScoreBand } from "../data/manualScoreBands.js";
import { ClientIdCell, LeadIdCell } from "../components/IdentifierCells.jsx";
import { displayClientType } from "../data/displayLabels.js";
import { getCallKind } from "../data/callKind.js";
import { downloadExcelWorkbook } from "../data/export.js";
import { callsExportValue } from "../data/callsExport.js";
import { DataFilterPanel } from "../components/DataFilterPanel.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { buildCopyTable, writeTableToClipboard } from "../data/callsClipboard.js";
import CallSubmitControls from "../components/CallSubmitControls.jsx";
import CallSaveStatus from "../components/CallSaveStatus.jsx";
import RelatedClosureDialog from "../components/RelatedClosureDialog.jsx";
import { deriveSubmitState, normalizeSaved, pendingChanges, pendingSubmissionReason } from "../data/submitState.js";
import { registerNavigationGuard, setPendingSubmissionCount, setUnsavedCount } from "../data/unsavedChanges.js";
import FollowUpDatesPopover from "../components/FollowUpDatesPopover.jsx";
import ClosureInfoDialog from "../components/ClosureInfoDialog.jsx";
import { closureBadgeText, shouldOfferRelatedClosure, shouldShowInheritedClosure } from "../data/caseClosure.js";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";
import { Pagination } from "../components/Pagination.jsx";
import {
  formatDisplayDate,
  formatDisplayDateTime,
} from "../data/displayDate.js";
import { formatTranscript, audioDownloadName, SummaryModal } from "../components/CallMediaModals.jsx";

async function fetchAudioBlobUrl(callId) {
  return getCallAudioBlobUrl(callId);
}

const normalizePhoneSearch = (value) => {
  const digits = String(value || "").replace(/\D/g, "");
  return digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
};


// enquiry_details is an AI-extracted Stage 1 field like every fd_a_* one
// above, just without that prefix -- it reuses the existing
// CallAnalysisResult.enquiry_details column (see fd_llm_node.py) instead of
// adding a new one. Treat it the same as an fd_a_ field wherever that
// prefix is used to identify Stage 1 AI output.
const isStage1AiField = (key) => key.startsWith("fd_a_") || key === "enquiry_details";


const STAGE34_KEYS = new Set([...STAGE3_COLUMNS, ...STAGE4_COLUMNS].map((field) => field.key));
const STAGE_FIELD_KEYS = new Set([
  ...STAGE1_COLUMNS,
  ...STAGE2_COLUMNS,
  ...STAGE3_COLUMNS,
  ...STAGE4_COLUMNS,
].map((field) => field.key));
// Stage 2 is the mandatory operational hand-off. Optional fields remain in
// the table but must not block Stage 3/4. This mirrors the API's
// STAGE2_REQUIRED_FIELDS contract.
const STAGE2_REQUIRED_COLUMNS = STAGE2_COLUMNS.filter((field) => field.required !== false);
const hasStage2Value = (value) =>
  value !== null && value !== undefined &&
  (typeof value !== "string" || Boolean(value.trim()));
const isDeletedStageValue = (nextValue, previousValue) =>
  !hasStage2Value(nextValue) && hasStage2Value(previousValue);
const missingStage2Labels = (call) => STAGE2_REQUIRED_COLUMNS
  .filter((field) => !hasStage2Value(call?.raw?.[field.key] ?? call?.[field.key]))
  .map((field) => field.label);

// Case Status Change values that make a call automatically "Completed" once
// Submit is clicked -- see CASE_STATUS_OPTIONS/VALID_FD_CASE_STATUS_CHANGE
// for the full closed list this is a subset of.
const COMPLETION_TRIGGER_STATUSES = new Set(["success pc", "success ge", "dropped"]);   // compared through caseStatusKey

// Add/Import recognized columns: the exact existing Stage 1-4 field/label
// pairs above, no invented columns. "call_id" is the required identity
// column (matches the "Call_ID" header already shown under Call details,
// an AF# reference or UUID) used to find the existing call an imported row
// updates -- import never creates a new Call row. "Date" and "Client Phone
// Number" are optional: if included, the backend cross-checks them against
// that call's actual recorded date/number and skips the row (reported like
// any other invalid value) on a mismatch -- a safeguard against pasting
// into the wrong row from a wide Excel sheet. They're never written
// anywhere themselves. Attachment fields are file-upload driven and are
// intentionally not part of a text/CSV import.
const CALLS_IMPORT_FIELDS = [
  ["call_id", "Call_ID"],
  ["import_call_date", "Date"],
  ["import_client_phone", "Client Phone Number"],
  ...STAGE1_COLUMNS.filter((field) => !field.attachment).map((field) => [field.key, field.label]),
  ...STAGE2_COLUMNS.filter((field) => !field.attachment).map((field) => [field.key, field.label]),
  ...STAGE3_COLUMNS.filter((field) => !field.attachment && !field.readOnly).map((field) => [field.key, field.label]),
  ...STAGE4_COLUMNS.filter((field) => !field.attachment).map((field) => [field.key, field.label]),
];

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CALL_IMPORT_REFERENCE_RE = /^(?:AF#?)?\d+$/i;

function isCallImportIdentifier(value) {
  const identifier = String(value || "").trim();
  return UUID_RE.test(identifier) || CALL_IMPORT_REFERENCE_RE.test(identifier);
}

// Imported spreadsheets can contain both a display Call ID and a raw UUID
// Call_ID. They normalize identically, so collapse them into one import
// identity column: per row, prefer the UUID, otherwise the nonblank value.
function collapseDuplicateCallIdColumns(table) {
  const target = normalizeImportHeader("Call_ID");
  const indexes = table.headers
    .map((header, index) => (normalizeImportHeader(header) === target ? index : -1))
    .filter((index) => index >= 0);
  if (indexes.length < 2) return table;
  const [keepIndex, ...dropIndexes] = indexes;
  const dropSet = new Set(dropIndexes);
  const rows = table.rows.map((cells) => {
    const candidates = indexes.map((index) => cells[index]);
    const preferred =
      candidates.find((value) => UUID_RE.test(String(value || "").trim())) ??
      candidates.find((value) => String(value || "").trim()) ??
      cells[keepIndex];
    return cells
      .map((cell, index) => (index === keepIndex ? preferred : cell))
      .filter((_, index) => !dropSet.has(index));
  });
  const headers = table.headers.filter((_, index) => !dropSet.has(index));
  return { ...table, headers, rows };
}

const MAX_CALLS_IMPORT_ROWS = 500;
// This table can otherwise hold thousands of rows for a single month --
// see routes/calls.py get_analysis's pagination. 100 keeps a page's worth
// of ~90-column rows comfortably readable without excessive scrolling.
// Calendar-day key (local) used to separate records by date in the table.
const dayKey = (value) => {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value).slice(0, 10) : parsed.toDateString();
};
// Checkbox + copy + "More logs" (28px / 4px gap / 72px) plus the cell padding.
const MORE_LOGS_SELECTION_WIDTH = 140;
const CALLS_PAGE_SIZE = 100;
// Banner text when the call(s) from a closure notification are on screen.
const focusFoundText = ({ calls, found }) => {
  const refs = calls.map((call) => call.callReference).filter(Boolean).join(", ");
  if (calls.length === 1) return `Showing the call from your notification${refs ? ` (${refs})` : ""}.`;
  const some = found < calls.length ? ` Only ${found} of ${calls.length} could be found, or you do not have access to the rest.` : "";
  return `Showing ${found} of the ${calls.length} calls changed by your confirmation${refs ? ` (${refs})` : ""}.${some}`;
};

// The Filter Data panel's own reference list per field -- independent of
// each field's cell-edit options (which intentionally also keep legacy/
// broader values selectable, e.g. Lead Source's old spellings, so historical
// records stay editable). Filtering instead always shows this exact fixed
// list, so it can't grow extra entries from off-list/legacy values agents
// have entered over time.
const FILTER_OPTION_LISTS = {
  fd_a_client_type: [
    "New Client - Individual",
    "New Client - Organization",
    "Existing client - Individual",
    "Existing client - Organization",
    "Voicemail Call",
    "Spam / Wrong Call",
  ],
  fd_a_lead_source: [
    "Client Referral",
    "Ads/Brevo",
    "LOMK seminar/ workshops",
    "Google Search",
    "Youtube",
    "Instagram",
    "Website",
    "Friends",
    "Family",
    "Tamil Sangam",
    "Email",
    "Paid Consultation Client",
  ],
  fd_a_urgency_flag: [
    "None",
    "Urgent - RFE",
    "Urgent - NOID",
    "Grace Period",
    "Visa Expiry",
    "Court Date",
    "Filing Deadline",
    "Client Distressed",
  ],
  fd_a_call_outcome: [
    "Connected - Resolved",
    "Connected - Redirected",
    "Connected - Follow up needed",
    "Voicemail Left",
    "Voicemail Full (Not Available)",
    "Call Back Scheduled",
  ],
  fd_m_manual_action_status: ["Open", "Completed", "No Action Required"],
};

const COLUMN_FILTERS = [...STAGE1_COLUMNS, ...STAGE2_COLUMNS]
  .filter((field) => field.options)
  .map(({ key, label, options }) => ({
    key,
    label,
    options: FILTER_OPTION_LISTS[key] || options,
  }));

const CALLS_FILTER_STORAGE_KEY = "flintdesk_calls_applied_filters_v2";
function callsFilterKey() {
  try { return `${CALLS_FILTER_STORAGE_KEY}:${JSON.parse(localStorage.getItem("flintdesk_user") || "null")?.id || "anonymous"}`; }
  catch { return `${CALLS_FILTER_STORAGE_KEY}:anonymous`; }
}

const BASE_EXPORT_COLUMNS = [
  { key: "id", label: "Call ID" },
  { key: "clientId", label: "Client ID" },
  { key: "leadId", label: "Lead ID" },
  { key: "clientPhoneNumber", label: "Client Phone Number" },
  { key: "agent", label: "Agent" },
  { key: "direction", label: "Direction" },
  { key: "callKind", label: "Call Type" },
  { key: "manual_data_score", label: "Manual Data Score" },
  { key: "date", label: "Date" },
  { key: "enquiryOpenDuration", label: "Enquiry Open Duration" },
  { key: "timestamp", label: "Timestamp" },
  { key: "duration", label: "Duration" },
  { key: "capturedNumber", label: "Searched Number" },
  { key: "extension", label: "Extension" },
];

// Same token the API uses for "Case Status Change not chosen yet".
const CASE_STATUS_NOT_SET = "__none__";
// Key of the Case Status entry inside the Filter Data panel (never a real column key).
const CASE_STATUS_FILTER_KEY = "__case_status";

// Rows with edits that are not on the server yet: queued Stage 3/4 edits, pending deletions, a save in flight or failed,
// or a failed Submit. (Shared by the leave-page warnings so they all agree.)
function countUnsavedRows({ stage34Changes, deletedStageData, autosave, submitErrors }) {
  const ids = new Set([...Object.keys(stage34Changes), ...Object.keys(deletedStageData), ...Object.keys(autosave), ...Object.keys(submitErrors)]);
  let unsaved = 0;
  ids.forEach((id) => {
    if (pendingChanges(stage34Changes[id]).length || Object.keys(deletedStageData[id] || {}).length
      || autosave[id]?.pending > 0 || autosave[id]?.phase === "error" || submitErrors[id]) unsaved += 1;
  });
  return unsaved;
}

function callsFilterDefaults() {
  const { start, end } = getDefaultDateRange();
  return {
    start,
    end,
    agent: "all",
    capturedNumber: "",
    extension: "",
    direction: "",
    clientNumber: "",
    clientId: "",
    leadId: "",
    callId: "",
    stageFilter: "all",
    callKind: "all",
    // Open Cases (Case Status Change not Dropped / Success GE / Success PC)
    // and Pending Action Status (Stage 2 Action Status = Pending).
    openCase: "",
    actionStatus: "",
    priorityForm: "",
    // Case Status Change dropdown filter: "" = All Case Statuses, CASE_STATUS_NOT_SET = no status chosen yet, else one option.
    caseStatus: "",
    // Case lifecycle stage button (open | in_transition | lost | dropped | success); "" = none selected.
    lifecycleStage: "",
    // Selected Follow Up Date (YYYY-MM-DD) from the Follow Up Dates popover; "" = none.
    followUpDate: "",
    columnFilters: Object.fromEntries(
      COLUMN_FILTERS.map((filter) => [filter.key, ""]),
    ),
  };
}

function readPersistedFilters() {
  const defaults = callsFilterDefaults();
  try {
    const saved = JSON.parse(sessionStorage.getItem(callsFilterKey()) || "null");
    if (!saved || !isValidDateRange(saved) || savedOnAnEarlierDay(callsFilterKey())) return defaults;   // a range from an earlier day resets to the default day
    const merged = { ...defaults, ...saved, restored: true, columnFilters: { ...defaults.columnFilters, ...saved.columnFilters } };
    // Agent filtering and Lead Number/Extension filtering are mutually
    // exclusive. A session stored before that rule existed (or corrupted by
    // a race) could have both set at once, which would otherwise render
    // every one of the three controls disabled with no way to change any
    // of them. Agent wins, matching the backend's precedence.
    if (merged.agent && merged.agent !== "all" && (merged.capturedNumber || merged.extension)) {
      merged.capturedNumber = "";
      merged.extension = "";
    }
    return merged;
  } catch { return defaults; }

}

const cellValue = (call, key) => {
  const value = call?.raw?.[key] ?? call?.[key];
  if (
    ["fd_m_action_due_date", "fd_m_date_entry"].includes(key) &&
    /^\d{4}-\d{2}-\d{2}(?:[T ].*)?$/.test(String(value || ""))
  ) {
    // Date-only display: MM-DD-YYYY (any time part is dropped).
    const [year, month, day] = String(value).slice(0, 10).split("-");
    return `${month}-${day}-${year}`;
  }
  return value === null || value === undefined || value === "" ? "-" : value;
};

const displayDirection = (value) =>
  String(value || "-").replace(/brevo outbound/gi, "Leads Outbound");

// Stage 2 Follow Up Dates: entries are appended in the exact order the agent
// adds them (never sorted/reversed) and shown as MM-DD-YYYY. The underlying
// stored value stays one comma-separated string, so the existing free-text
// backend field needs no schema change.
// Newline-separated rather than comma-separated: an entry is allowed to be
// free text (not only a date), and free text is far more likely to contain
// a comma than a newline.
function parseFollowUpDates(raw) {
  return String(raw || "")
    .split("\n")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

const FollowUpDatesEditor = forwardRef(function FollowUpDatesEditor({ value, onChange, disabled }, ref) {
  const dates = parseFollowUpDates(value);
  const [draftDate, setDraftDate] = useState("");
  const hasDraft = draftDate.trim().length > 0;

  // Saving the modal must not silently discard a date that was typed but not
  // separately added with the small Add button.
  useImperativeHandle(ref, () => ({
    valueIncludingDraft: () => (
      hasDraft ? [...dates, draftDate.trim()].join("\n") : dates.join("\n")
    ),
  }), [dates, draftDate, hasDraft]);

  const addDate = () => {
    if (!hasDraft) return;
    onChange([...dates, draftDate.trim()].join("\n"));
    setDraftDate("");
  };
  const removeDate = (index) =>
    onChange(dates.filter((_, i) => i !== index).join("\n"));

  return (
    <div className="follow-up-dates-editor">
      {dates.length ? (
        <ol className="follow-up-dates-list">
          {dates.map((date, index) => (
            <li key={`${date}-${index}`}>
              <span>{date}</span>
              {!disabled && (
                <button
                  type="button"
                  aria-label={`Remove ${date}`}
                  onClick={() => removeDate(index)}
                >
                  &times;
                </button>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p className="follow-up-dates-empty">No follow-up dates added yet.</p>
      )}
      <div className="follow-up-dates-add">
        <input
          type="text"
          placeholder="MM-DD-YYYY, or any note"
          value={draftDate}
          disabled={disabled}
          onChange={(event) => setDraftDate(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addDate();
            }
          }}
        />
        <button type="button" disabled={disabled || !hasDraft} onClick={addDate}>
          Add
        </button>
      </div>
    </div>
  );
});

export default function Calls({
  isActive = true,
  user,
  assignment,
  isTeamLead = false,
  onNavigate,
}) {
  const [initialFilters] = useState(readPersistedFilters);
  // Restore the applied query before fetching. initialFilters.start already
  // resolves to either the saved date range or the current-month default
  // (see readPersistedFilters) -- a fresh visit used to override that with
  // a literal "2000-01-01" to "load permitted history", but fetching every
  // call since 2000 on every first-time page load is what was exhausting
  // the database connection pool for the whole app (each such request held
  // a connection for the entire scan). Matches every other page's default.
  const [range, setRange] = useState({
    start: initialFilters.start,
    end: initialFilters.end,
  });
  const [customDraft, setCustomDraft] = useState({
    start: initialFilters.displayStart || initialFilters.start,
    end: initialFilters.displayEnd || initialFilters.end,
  });
  const [agentFilter, setAgentFilter] = useState(initialFilters.agent);
  const [capturedNumberFilter, setCapturedNumberFilter] = useState(
    initialFilters.capturedNumber,
  );
  const [extensionFilter, setExtensionFilter] = useState(
    initialFilters.extension,
  );
  const [directionFilter, setDirectionFilter] = useState(initialFilters.direction);
  const [clientNumberFilter, setClientNumberFilter] = useState(
    initialFilters.clientNumber,
  );
  const [clientIdFilter, setClientIdFilter] = useState(initialFilters.clientId);
  const [leadIdFilter, setLeadIdFilter] = useState(initialFilters.leadId);
  const [callIdFilter, setCallIdFilter] = useState(initialFilters.callId);
  const [draftAgentFilter, setDraftAgentFilter] = useState(
    initialFilters.agent,
  );
  const [draftCapturedNumberFilter, setDraftCapturedNumberFilter] = useState(
    initialFilters.capturedNumber,
  );
  const [draftExtensionFilter, setDraftExtensionFilter] = useState(
    initialFilters.extension,
  );
  const [draftDirectionFilter, setDraftDirectionFilter] = useState(initialFilters.direction);
  const [draftClientNumberFilter, setDraftClientNumberFilter] = useState(
    initialFilters.clientNumber,
  );
  const [draftClientIdFilter, setDraftClientIdFilter] = useState(
    initialFilters.clientId,
  );
  const [draftLeadIdFilter, setDraftLeadIdFilter] = useState(
    initialFilters.leadId,
  );
  const [draftCallIdFilter, setDraftCallIdFilter] = useState(
    initialFilters.callId,
  );
  const [callKindFilter, setCallKindFilter] = useState(initialFilters.callKind);
  // The consolidated filter belt: Open Cases, Priority Cases, Action Status (and below Case Lifecycle, Log Updates = stageFilter,
  // Follow Up Dates). Each has an applied value and a draft that only Apply turns into the applied one.
  const [openCaseFilter, setOpenCaseFilter] = useState(validOpenCase(initialFilters.openCase));
  const [actionStatusFilter, setActionStatusFilter] = useState(validActionStatus(initialFilters.actionStatus || (initialFilters.pendingAction ? "Pending" : "")));
  const [priorityFormFilter, setPriorityFormFilter] = useState(validPriorityForm(initialFilters.priorityForm));
  // A restored value that is no longer a real option falls back to "All Case Statuses" instead of silently hiding calls.
  const validCaseStatus = (value) => (value === CASE_STATUS_NOT_SET || CASE_STATUS_OPTIONS.includes(value) ? value : "");
  const [caseStatusFilter, setCaseStatusFilter] = useState(validCaseStatus(initialFilters.caseStatus));
  const [draftCaseStatusFilter, setDraftCaseStatusFilter] = useState(validCaseStatus(initialFilters.caseStatus));
  const [lifecycleStageFilter, setLifecycleStageFilter] = useState(validLifecycleStage(initialFilters.lifecycleStage));
  const [draftLifecycleStageFilter, setDraftLifecycleStageFilter] = useState(validLifecycleStage(initialFilters.lifecycleStage));
  const [followUpDateFilter, setFollowUpDateFilter] = useState(String(initialFilters.followUpDate || ""));
  const [draftFollowUpDateFilter, setDraftFollowUpDateFilter] = useState(String(initialFilters.followUpDate || ""));
  const [draftOpenCaseFilter, setDraftOpenCaseFilter] = useState(validOpenCase(initialFilters.openCase));
  // The two dependency choices need an agent: when the agent is cleared, a chosen dependency is cleared with it instead of staying
  // selected (disabled) in the dropdown and silently doing nothing on Apply.
  useEffect(() => {
    if (draftOpenCaseFilter && effectiveOpenCase(draftOpenCaseFilter, draftAgentFilter) !== draftOpenCaseFilter) setDraftOpenCaseFilter("");
  }, [draftAgentFilter, draftOpenCaseFilter]);
  const [draftActionStatusFilter, setDraftActionStatusFilter] = useState(validActionStatus(initialFilters.actionStatus || (initialFilters.pendingAction ? "Pending" : "")));
  const [draftPriorityFormFilter, setDraftPriorityFormFilter] = useState(validPriorityForm(initialFilters.priorityForm));
  const [draftCallKindFilter, setDraftCallKindFilter] = useState(
    initialFilters.callKind,
  );
  const [columnFilters, setColumnFilters] = useState(
    initialFilters.columnFilters,
  );
  const [dataFiltersOpen, setDataFiltersOpen] = useState(false);
  const [calls, setMainCalls] = useState([]);
  // "All Logs of Phone Number" popup results. Row edits are functional
  // updaters keyed by call id, so setCalls applies them to this list too:
  // the popup's rows update in place when edited. Whole-list replacements
  // (table loads/refreshes) only ever replace the main table.
  const [phoneLogs, setPhoneLogs] = useState([]);
  const setCalls = useCallback((update) => {
    setMainCalls(update);
    if (typeof update === "function") setPhoneLogs(update);
  }, []);
  // A row may live in the main table or in the popup; dialogs opened from
  // either must find it.
  const findCallRow = (rowId) =>
    calls.find((item) => item.id === rowId) ?? phoneLogs.find((item) => item.id === rowId);
  const [phoneLogsOpen, setPhoneLogsOpen] = useState(false);
  const [phoneLogsSearched, setPhoneLogsSearched] = useState("");
  const [phoneLogsMissedCount, setPhoneLogsMissedCount] = useState(0);
  const [phoneLogsLoading, setPhoneLogsLoading] = useState(false);
  const [phoneLogsError, setPhoneLogsError] = useState("");
  const [selectedCallIds, setSelectedCallIds] = useState(new Set());
  const [copyNotice, setCopyNotice] = useState("");
  // Which single row's copy icon last succeeded -- swaps that one button's
  // icon to a checkmark briefly, separate from the shared copyNotice text.
  const [justCopiedRowId, setJustCopiedRowId] = useState(null);
  const [page, setPage] = useState(1);
  const [pagination, setPagination] = useState(null);
  const [fetchRevision, setFetchRevision] = useState(0);
  const [closureInfo, setClosureInfo] = useState(null);
  const pageCursors = useRef({ scope: "", pages: { 1: "" } });

  const applyClientProfileUpdate = (client) => {
    if (!client?.client_id) return;
    setCalls((current) => current.map((call) => {
      if (String(call.clientId || "") !== String(client.client_id)) return call;
      return {
        ...call,
        raw: {
          ...call.raw,
          fd_a_client_name: client.client_name || "",
          fd_a_email_address: client.client_email || "",
          fd_m_client_name: client.client_name || "",
        },
      };
    }));
  };
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [transcriptModalCall, setTranscriptModalCall] = useState(null);
  const [audioModalCall, setAudioModalCall] = useState(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState("");
  const [imagePreview, setImagePreview] = useState(null);
  const [stageFilter, setStageFilter] = useState(initialFilters.stageFilter);
  const [draftStageFilter, setDraftStageFilter] = useState(
    initialFilters.stageFilter,
  );
  const [importOpen, setImportOpen] = useState(false);
  const [importPaste, setImportPaste] = useState("");
  const [importSelectedFileName, setImportSelectedFileName] = useState("");
  const [importParsed, setImportParsed] = useState(null);
  const [importRunning, setImportRunning] = useState(false);
  const [importResultNotice, setImportResultNotice] = useState("");
  const [importInvalidRows, setImportInvalidRows] = useState([]);
  const [importUpdatedRecords, setImportUpdatedRecords] = useState([]);
  const importFileInputRef = useRef(null);
  // Edit (pen icon) modal: the same paste/validate/import pipeline as
  // Add/Import above, scoped to one already-identified call -- see
  // openEditImportModal/runEditImport.
  const [editImportCall, setEditImportCall] = useState(null);
  const [editImportPaste, setEditImportPaste] = useState("");
  const [editImportParsed, setEditImportParsed] = useState(null);
  const [editImportRunning, setEditImportRunning] = useState(false);
  const [editImportResultNotice, setEditImportResultNotice] = useState("");
  const [editImportInvalidRows, setEditImportInvalidRows] = useState([]);
  const [cellModal, setCellModal] = useState(null); // { label, key, value, rowId, callId }
  const [summaryModal, setSummaryModal] = useState(null);
  const [cellDraft, setCellDraft] = useState("");
  const [cellSaving, setCellSaving] = useState(false);
  const followUpEditorRef = useRef(null);
  const [cellError, setCellError] = useState("");
  const [stageInputDrafts, setStageInputDrafts] = useState({});
  const [fdAgents, setFdAgents] = useState([]);
  const [syncFilters, setSyncFilters] = useState([]);
  const [viewByPriority, setViewByPriority] = useState(initialFilters.viewByPriority || false);
  const [isLeadScoreMatrixOpen, setIsLeadScoreMatrixOpen] = useState(false);
  // The Calls page always shows every stage's columns together now -- the
  // "Stage 1 & Stage 2" / "Stage 3 & Stage 4" toggle was removed, so this is
  // a fixed value (still compared against below) rather than state.
  const stageView = "stage_3_4";
  const [submittingCall, setSubmittingCall] = useState({});
  const [stage34Changes, setStage34Changes] = useState({});
  // Fields whose saved value was deleted on a completed call, per call id: { [id]: { [fieldKey]: true } }.
  // Deleting data there only takes effect on completion once Submit runs, so it counts as unsaved.
  const [deletedStageData, setDeletedStageData] = useState({});
  // Autosave outcome per call id: { pending, phase: "saving" | "saved" | "error", message, retry }.
  const [autosave, setAutosave] = useState({});
  const [submitErrors, setSubmitErrors] = useState({});
  // Calls whose Submit succeeded in this page session (nothing persistent records an earlier Submit).
  const [submittedCalls, setSubmittedCalls] = useState({});
  const submitInFlight = useRef(new Set());
  const savedTimers = useRef({});
  // Rows whose Case Status the agent changed (or confirmed closed) on this page. Only these can trigger the leave-page
  // warning, so an old call that merely loads with a closing status never does.
  const statusTouched = useRef(new Set());
  // Saves of one field are sent in order and each is checked against the last value the SERVER confirmed, so two quick
  // edits cannot fail the stale-write check against each other and an older response never overwrites a newer edit.
  // key "<row id>:<field>" -> { tail, confirmed, pending, seq }
  const fieldSaves = useRef({});
  // Related call logs of the closing agent (see RelatedClosureDialog): a queue of offers, one shown at a time.
  const [relatedOffers, setRelatedOffers] = useState([]);
  const [relatedUi, setRelatedUi] = useState({ busy: false, error: "", result: null });
  const relatedInFlight = useRef(new Set());   // lookups per row, so one closing cannot raise two offers
  const relatedConfirming = useRef(false);     // blocks a double click before React re-renders
  const beginAutosave = (id) => {
    window.clearTimeout(savedTimers.current[id]);
    setSubmittedCalls((current) => (current[id] ? { ...current, [id]: false } : current));
    setAutosave((current) => ({ ...current, [id]: { ...(current[id] || {}), pending: (current[id]?.pending || 0) + 1, phase: "saving", message: "", retry: undefined } }));
  };
  // outcome: {} = confirmed by the backend, { error, retry } = failed, { silent: true } = only stop counting it.
  const endAutosave = (id, outcome = {}) => {
    setAutosave((current) => {
      const pending = Math.max(0, (current[id]?.pending || 1) - 1);
      if (outcome.silent) return { ...current, [id]: { pending, phase: pending ? "saving" : "idle" } };
      if (outcome.error) return { ...current, [id]: { pending, phase: "error", message: outcome.error, retry: outcome.retry } };
      return { ...current, [id]: { pending, phase: pending ? "saving" : "saved" } };
    });
    if (!outcome.error && !outcome.silent) {
      window.clearTimeout(savedTimers.current[id]);
      savedTimers.current[id] = window.setTimeout(() => {
        setAutosave((current) => (current[id]?.phase === "saved" ? { ...current, [id]: { ...current[id], phase: "idle" } } : current));
      }, 2500);
    }
  };
  const [scoringManualData, setScoringManualData] = useState({});
  const [scoreModalCall, setScoreModalCall] = useState(null);
  const [isManualScoreMatrixOpen, setIsManualScoreMatrixOpen] = useState(false);
  const queueStage34Change = (call, field, value, { persisted = false } = {}) => {
    const originalValue = call.raw?.[field] ?? call?.[field] ?? "";
    setSubmittedCalls((current) => (current[call.id] ? { ...current, [call.id]: false } : current));
    setSubmitErrors((current) => (current[call.id] ? { ...current, [call.id]: "" } : current));
    setStage34Changes((current) => {
      const existing = current[call.id]?.[field];
      const saved = existing?.originalValue ?? originalValue;
      const forCall = { ...(current[call.id] || {}) };
      // Changing a value back to the saved one is no change at all: nothing to submit, nothing to send.
      if (!persisted && !existing?.persisted && normalizeSaved(value) === normalizeSaved(saved)) delete forCall[field];
      else forCall[field] = { value, originalValue: saved, persisted: existing?.persisted || persisted };
      const next = { ...current };
      if (Object.keys(forCall).length) next[call.id] = forCall; else delete next[call.id];
      return next;
    });
    setDeletedStageData((current) => {
      const forCall = { ...(current[call.id] || {}) };
      if (isDeletedStageValue(value, originalValue)) forCall[field] = true; else delete forCall[field];
      const next = { ...current };
      if (Object.keys(forCall).length) next[call.id] = forCall; else delete next[call.id];
      return next;
    });
  };
  // Mark Completed is no longer a manual action -- it's derived from Case
  // Status Change (see COMPLETION_TRIGGER_STATUSES) and is (re)computed
  // every time Submit runs. Submit itself is the old markCompleted flow
  // (save any queued Stage 3/4 changes, then persist completion), just with
  // the completion value it sends now decided by that rule instead of
  // always "not deleted => complete".
  // Returns { ok, error } so the leave-page guard can report each call's outcome; the Submit button ignores it.
  // `offerRelated: false` (Submit and Leave) skips the optional "related call logs" offer: the agent is leaving, and the
  // dialog would otherwise wait on the hidden page and appear when they come back.
  const submitCall = async (call, { offerRelated = true } = {}) => {
    if (!call.analysisId) return { ok: false, error: "This call has nothing to submit." };
    const missingStage2 = missingStage2Labels(call);
    if (missingStage2.length) {
      const message = `Complete every Stage 2 field before submitting this call: ${missingStage2.join(", ")}.`;
      setError(message);
      return { ok: false, error: message };
    }
    // A double click must never send the same Submit twice (React state is too slow to guard it).
    if (submitInFlight.current.has(call.id)) return { ok: false, error: "A Submit for this call is already running." };
    submitInFlight.current.add(call.id);
    setSubmitErrors((current) => ({ ...current, [call.id]: "" }));
    setSubmittingCall((current) => ({ ...current, [call.id]: true }));
    try {
      const changes = stage34Changes[call.id] || {};
      const changeEntries = Object.entries(changes);
      const hasDeletedData = changeEntries.some(([, change]) =>
        isDeletedStageValue(change.value, change.originalValue),
      );
      let closedNow = false;
      for (const [field, change] of changeEntries) {
        // Attachments persist while uploading/removing, unlike text fields
        // which wait here for one explicit completion save.
        if (!change.persisted) {
          const savedChange = await updateCallField(call.callId, field, change.value, change.originalValue);
          // Persisted: from now on this value IS the saved snapshot, so a retry after a later failure
          // does not resend it (which would fail the stale-write check against its own earlier save).
          setStage34Changes((current) => current[call.id]?.[field]
            ? { ...current, [call.id]: { ...current[call.id], [field]: { value: change.value, originalValue: change.value, persisted: true } } }
            : current);
          if (field === "fd_m_case_status_change") {
            // Show what the server actually stored (and its date), not just what was typed.
            const confirmedStatus = savedChange?.normalized_value ?? change.value;
            setCalls((current) => current.map((item) => item.id === call.id
              ? { ...item, raw: { ...item.raw, fd_m_case_status_change: confirmedStatus,
                  ...(savedChange?.fd_m_date_entry !== undefined ? { fd_m_date_entry: savedChange.fd_m_date_entry } : {}) } }
              : item));
            if (shouldOfferRelatedClosure(change.originalValue, confirmedStatus)) closedNow = true;
          }
        }
      }
      // A case status edit made while the call was already completed sits
      // queued in `changes` (see saveStageValue) rather than on call.raw
      // until this Submit -- prefer that pending value when present.
      const currentCaseStatus = String(
        changes.fd_m_case_status_change?.value ?? call.raw?.fd_m_case_status_change ?? "",
      ).trim();
      const shouldComplete = !hasDeletedData && COMPLETION_TRIGGER_STATUSES.has(caseStatusKey(currentCaseStatus));
      const saved = await updateManualFields(call.analysisId, {
        case_closing_completed: shouldComplete,
      });
      setCalls((current) =>
        current.map((item) =>
          item.id === call.id
            ? {
                ...item,
                raw: {
                  ...item.raw,
                  case_closing_completed: saved.case_closing_completed ?? shouldComplete,
                  case_closing_completed_at: saved.case_closing_completed_at,
                },
              }
            : item,
        ),
      );
      setStage34Changes((current) => { const next = { ...current }; delete next[call.id]; return next; });
      setDeletedStageData((current) => { const next = { ...current }; delete next[call.id]; return next; });
      setSubmittedCalls((current) => ({ ...current, [call.id]: true })); // the backend confirmed this Submit
      if (closedNow && offerRelated) offerRelatedClosure(call); // the status was only just persisted by this Submit
      // Stage 3/4 fields may have changed since the last score (which nulls
      // it out server-side on every manual-field edit); re-evaluate now
      // that the case is closed. Fire-and-forget: completion must not wait
      // on, or fail because of, scoring.
      if (shouldComplete) runManualDataScore(call, { silent: true });
      return { ok: true };
    } catch (error) {
      // Nothing is marked saved: queued edits stay queued and the row shows "Save failed" with a retry.
      setSubmitErrors((current) => ({ ...current, [call.id]: error.message || "Unable to submit this call." }));
      setError(error.message || "Unable to submit this call.");
      return { ok: false, error: error.message || "Unable to submit this call." };
    } finally {
      submitInFlight.current.delete(call.id);
      setSubmittingCall((current) => ({ ...current, [call.id]: false }));
    }
  };
  const runManualDataScore = async (call, { silent = false } = {}) => {
    if (!call.analysisId || scoringManualData[call.id]) return;
    setScoringManualData((current) => ({ ...current, [call.id]: true }));
    if (!silent) setError(null);
    try {
      const result = await scoreManualData(call.analysisId);
      setCalls((current) =>
        current.map((item) =>
          item.id === call.id
            ? {
                ...item,
                raw: {
                  ...item.raw,
                  manual_data_score: result.score,
                  manual_data_score_reason: result.reason,
                  manual_data_scored_at: result.scored_at,
                },
              }
            : item,
        ),
      );
    } catch (exception) {
      // A silent, automatic re-score (from Mark completed) must not surface
      // an error banner for a background best-effort call.
      if (!silent) setError(exception.message || "Unable to score manual data.");
    } finally {
      setScoringManualData((current) => ({ ...current, [call.id]: false }));
    }
  };

  // Agent dropdown is sourced from iis.fd_users (same source as the Settings
  // page and the Dashboard), not from agent names seen in the loaded calls.
  useEffect(() => {
    let cancelled = false;
    getDashboardFrontDeskAgents()
      .then((users) => {
        if (cancelled) return;
        const names = (Array.isArray(users) ? users : [])
          .map((user) => (user?.name || "").trim())
          .filter(Boolean)
          .sort((a, b) => a.localeCompare(b));
        setFdAgents(names);
      })
      .catch((error) => {
        console.error("Failed to load front desk agents:", error);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Reuse the existing configured number -> extension relationship. The
  // endpoint is optional for non-team-lead users, so call data remains a safe
  // fallback when the settings endpoint is not authorized. Only reviewers
  // (Admin / Team Lead / SME) may read it, so nobody else asks for it.
  const mayReadSyncFilters = canApproveDependencies(user);
  useEffect(() => {
    if (!mayReadSyncFilters) return undefined;
    let cancelled = false;
    getSyncFilters()
      .then((filters) => {
        if (!cancelled) setSyncFilters(Array.isArray(filters) ? filters : []);
      })
      .catch(() => {
        if (!cancelled) setSyncFilters([]);
      });
    return () => {
      cancelled = true;
    };
  }, [mayReadSyncFilters]);

  // rowId is the formatted/display id (call.id, used as the table row key).
  // callId is the raw call_id the backend expects for the manual-fields PATCH.
  // options is an optional array of allowed choices; when provided the modal
  // renders a <select> instead of a <textarea> for Stage 1 dropdown fields.
  const openCellModal = async (label, key, value, rowId, callId, options = null) => {
    if (["call_summary", "fd_a_call_summary", "enquiry_details", "evaluator_notes"].includes(key)) {
      try {
        const content = await getCallContent(callId);
        value = content.analysis?.[key] || "";
      } catch (error) {
        setError(error.message || "Unable to load call details; please retry.");
        return;
      }
    }
    const initial =
      value === "-" || value === undefined || value === null
        ? ""
        : String(value);
    setCellError("");
    setCellDraft(initial);
    const call = findCallRow(rowId);
    const dueNote = key === "fd_m_action_due_date" ? String(call?.raw?.fd_m_action_due_note || "") : "";
    // Keep the persisted note separately from the editable draft. The
    // compare-and-set API must receive this original value, never the text
    // the user has just typed.
    setCellModal({ label, key, value: initial, rowId, callId, options, dueNote, originalDueNote: dueNote });
  };

  const closeCellModal = () => {
    setCellModal(null);
    setCellDraft("");
    setCellError("");
  };

  const saveCellModal = async () => {
    if (!cellModal) return;
    // Every agent can correct inbound ownership. Other directions retain
    // self-identification restrictions, also enforced by the backend.
    if (
      cellModal.key === "front_desk_person_name" &&
      String(findCallRow(cellModal.rowId)?.direction || "").trim().toLowerCase() !== "inbound" &&
      isRestrictedAgent(user) &&
      cellDraft.trim() &&
      cellDraft.trim().toLowerCase() !== String(user?.name || "").trim().toLowerCase()
    ) {
      setCellError("You can only enter your own name, not another agent's name.");
      return;
    }
    setCellSaving(true);
    setCellError("");
    const previousValue = cellModal.value;
    const nextValue = cellModal.key === "fd_m_follow_up_dates"
      ? followUpEditorRef.current?.valueIncludingDraft?.() ?? cellDraft
      : cellDraft;
    // `completion`, when supplied, is the authoritative case_closing_completed
    // state the backend returned with this same save -- editing a completion-
    // relevant field (see FD_AI_FIELDS on the backend) clears completion in
    // that same update. Applying it here keeps the Mark Completed button in
    // sync with the database without a separate request or a full refetch.
    const setLocalValue = (value, completion) => setCalls((current) =>
      current.map((call) => {
        if (call.id !== cellModal.rowId) return call;
        // Saving the Agent cell (front_desk_person_name) is an agent
        // self-identifying a call they handled. Update the displayed/
        // filterable `agent` field immediately too, not just the raw
        // record, so it's visible right away instead of waiting for the
        // next background refetch.
        const agentUpdate =
          cellModal.key === "front_desk_person_name"
            ? {
                // Inbound calls are shared work, so the typed name claims the call even when the
                // extension is assigned to someone else; other directions keep the extension owner.
                agent: (
                  String(call.direction || "").trim().toLowerCase() === "inbound" && value
                    ? value
                    : call.assignedAgent && call.assignedAgent !== "-" ? call.assignedAgent : value
                ) || "-",
                rawAgent: value || "",
              }
            : {};
        if (call.raw && cellModal.key in call.raw) {
          return {
            ...call,
            ...agentUpdate,
            raw: { ...call.raw, [cellModal.key]: value, ...(completion || {}) },
          };
        }
        return {
          ...call,
          ...agentUpdate,
          [cellModal.key]: value,
          ...(completion && call.raw ? { raw: { ...call.raw, ...completion } } : {}),
        };
      }),
    );
    // Show the edit immediately, retaining the previous value for rollback.
    setLocalValue(nextValue);
    const savedRowId = cellModal.rowId;
    beginAutosave(savedRowId);
    try {
      if (cellModal.key === "fd_m_action_due_date") {
        // Save the companion note first. If it genuinely changed elsewhere,
        // the date is not written and the UI stays consistent for retry.
        if ((cellModal.dueNote || "") !== (cellModal.originalDueNote || "")) {
          await updateCallField(cellModal.callId, "fd_m_action_due_note", cellModal.dueNote || "", cellModal.originalDueNote || "");
        }
      }
      const saved = await updateCallField(
        cellModal.callId,
        cellModal.key,
        nextValue,
        previousValue,
      );
      if (cellModal.key === "fd_m_action_due_date") {
        setCalls((current) => current.map((call) => call.id === cellModal.rowId
          ? { ...call, raw: { ...call.raw, fd_m_action_due_note: cellModal.dueNote || "" } }
          : call));
      }
      setLocalValue(saved.normalized_value ?? nextValue, {
        case_closing_completed: saved.case_closing_completed,
        case_closing_completed_at: saved.case_closing_completed_at,
      });
      // Client name/email are the client profile's: show the saved value on all of this
      // client's rows, or editing another of their rows would send a stale value.
      if (saved?.client) applyClientProfileUpdate(saved.client);
      endAutosave(savedRowId);
      closeCellModal();
    } catch (err) {
      setLocalValue(previousValue);
      setCellError(err.message || "Failed to save changes.");
      endAutosave(savedRowId, { silent: true }); // the open dialog shows this error and is the retry
    } finally {
      setCellSaving(false);
    }
  };

  // Replaces the old Stage 1/Stage 2/Stage 2 Pending/Stage 2 Completed (and
  // Stage 3/Stage 4 equivalent) pills with one clean, stage-independent set.
  // "Completed" reuses case_closing_completed -- the same case-level flag
  // Submit (formerly the "Mark completed" button, see submitCall below)
  // reads/writes -- rather than a new status concept.
  // Rows with changes that are not saved yet: queued Stage 3/4 edits, pending deletions, a save in flight
  // or failed. Leaving the page (or closing the tab) warns only while this is non-zero.
  useEffect(() => {
    setUnsavedCount("calls", countUnsavedRows({ stage34Changes, deletedStageData, autosave, submitErrors }));
  }, [stage34Changes, deletedStageData, autosave, submitErrors]);
  useEffect(() => () => setUnsavedCount("calls", 0), []);

  const stageFilterOptions = [
    { value: "all", label: "All" },
    { value: "pending", label: "Submitted" },
    { value: "completed", label: "Completed" },
  ];

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

  // Call Records visibility/filtering is the same for every role: an
  // agent's extension assignment does not restrict which calls or which
  // Extension options they see here (it still governs edit/action
  // permissions elsewhere). The Lead Number dropdown is a narrower, purely
  // cosmetic exception: each agent's own assigned lead number is the one
  // they'd realistically filter by, so it's the only option offered
  // instead of every line in the system. This does not restrict the
  // underlying call data -- an agent can still see every call regardless
  // of lead number by leaving this filter unset.
  const assignedLeadNumber = String(assignment?.number || "").trim();
  const capturedNumberOptions = useMemo(
    () =>
      isRestrictedAgent(user) && assignedLeadNumber
        ? [assignedLeadNumber]
        : getOutboundLeadNumberOptions(syncFilters, calls),
    [assignedLeadNumber, calls, syncFilters, user],
  );
  const extensionOptions = useMemo(
    () =>
      draftCapturedNumberFilter
        ? getLeadNumberExtensions(draftCapturedNumberFilter)
        : getAllLeadExtensions(),
    [draftCapturedNumberFilter],
  );
  // Apply is the only point where draft controls become active filters. The
  // persisted snapshot stores these applied values, never intermediate edits.
  const applyFiltersWith = (followUpDate) => {
    if (!isValidDateRange(customDraft)) {
      setError("Date From must be on or before Date To.");
      return;
    }
    // Defense in depth: the draft controls already prevent selecting both
    // an agent and a Lead Number/Extension, but normalize here too in case
    // draft state was ever set outside that UI (e.g. restored from an older
    // persisted session). Agent wins, matching the backend's precedence.
    const agentActive = draftAgentFilter !== "all";
    const applied = {
      start: customDraft.start,
      end: customDraft.end,
      agent: draftAgentFilter,
      capturedNumber: agentActive ? "" : draftCapturedNumberFilter,
      extension: agentActive ? "" : draftExtensionFilter,
      direction: draftDirectionFilter,
      clientNumber: draftClientNumberFilter,
      clientId: draftClientIdFilter.trim(),
      leadId: draftLeadIdFilter.trim(),
      callId: draftCallIdFilter,
      stageFilter: draftStageFilter,
      callKind: draftCallKindFilter,
      openCase: draftOpenCaseFilter,
      actionStatus: draftActionStatusFilter,
      priorityForm: draftPriorityFormFilter,
      caseStatus: draftCaseStatusFilter,
      lifecycleStage: draftLifecycleStageFilter,
      followUpDate,
      columnFilters,
    };
    setRange({ start: applied.start, end: applied.end });
    setPage(1);
    setFetchRevision((value) => value + 1);
    setAgentFilter(applied.agent);
    setCapturedNumberFilter(applied.capturedNumber);
    setExtensionFilter(applied.extension);
    setDirectionFilter(applied.direction);
    setClientNumberFilter(applied.clientNumber);
    setClientIdFilter(applied.clientId);
    setLeadIdFilter(applied.leadId);
    setCallIdFilter(applied.callId);
    setStageFilter(applied.stageFilter);
    setCallKindFilter(applied.callKind);
    setOpenCaseFilter(applied.openCase);
    setActionStatusFilter(applied.actionStatus);
    setPriorityFormFilter(applied.priorityForm);
    setCaseStatusFilter(applied.caseStatus);
    setLifecycleStageFilter(applied.lifecycleStage);
    setFollowUpDateFilter(applied.followUpDate);
    setDraftFollowUpDateFilter(applied.followUpDate);
    setColumnFilters(applied.columnFilters);
    try { sessionStorage.setItem(callsFilterKey(), JSON.stringify(applied)); markSavedToday(callsFilterKey()); } catch { /* Storage may be disabled. */ }
  };
  // The Apply button keeps the selected Follow Up Date; picking a date in the popover applies the
  // whole filter belt together with that date.
  const applyFilters = () => applyFiltersWith(draftFollowUpDateFilter);
  // Clear filters: empties the belt's six filters in the drafts AND the applied filters, and reloads from page 1.
  const clearBeltFilters = () => {
    setDraftLifecycleStageFilter(""); setDraftOpenCaseFilter(""); setDraftPriorityFormFilter(""); setDraftActionStatusFilter("");
    setDraftStageFilter("all"); setDraftFollowUpDateFilter("");
    setLifecycleStageFilter(""); setOpenCaseFilter(""); setPriorityFormFilter(""); setActionStatusFilter("");
    setStageFilter("all"); setFollowUpDateFilter("");
    setPage(1);
    setFetchRevision((value) => value + 1);
  };

  // ---- Open one specific call (closure notification "Done") ----
  // The applied filters as one object (same shape Apply stores), so they can be put back after a focus.
  const appliedSnapshot = () => ({
    start: range.start, end: range.end, displayStart: customDraft.start, displayEnd: customDraft.end,
    agent: agentFilter, capturedNumber: capturedNumberFilter, extension: extensionFilter,
    direction: directionFilter, clientNumber: clientNumberFilter, clientId: clientIdFilter,
    leadId: leadIdFilter, callId: callIdFilter, stageFilter, callKind: callKindFilter,
    openCase: openCaseFilter, actionStatus: actionStatusFilter, priorityForm: priorityFormFilter, caseStatus: caseStatusFilter, lifecycleStage: lifecycleStageFilter, followUpDate: followUpDateFilter, columnFilters,
  });
  // Sets the applied filters and their draft controls together, and reloads from page 1.
  const applySnapshot = (snap) => {
    setRange({ start: snap.start, end: snap.end });
    setCustomDraft({ start: snap.displayStart || snap.start, end: snap.displayEnd || snap.end });
    setAgentFilter(snap.agent); setDraftAgentFilter(snap.agent);
    setCapturedNumberFilter(snap.capturedNumber); setDraftCapturedNumberFilter(snap.capturedNumber);
    setExtensionFilter(snap.extension); setDraftExtensionFilter(snap.extension);
    setDirectionFilter(snap.direction); setDraftDirectionFilter(snap.direction);
    setClientNumberFilter(snap.clientNumber); setDraftClientNumberFilter(snap.clientNumber);
    setClientIdFilter(snap.clientId); setDraftClientIdFilter(snap.clientId);
    setLeadIdFilter(snap.leadId); setDraftLeadIdFilter(snap.leadId);
    setCallIdFilter(snap.callId); setDraftCallIdFilter(snap.callId);
    setStageFilter(snap.stageFilter); setDraftStageFilter(snap.stageFilter);
    setCallKindFilter(snap.callKind); setDraftCallKindFilter(snap.callKind);
    setOpenCaseFilter(validOpenCase(snap.openCase)); setDraftOpenCaseFilter(validOpenCase(snap.openCase));
    setActionStatusFilter(validActionStatus(snap.actionStatus)); setDraftActionStatusFilter(validActionStatus(snap.actionStatus));
    setPriorityFormFilter(validPriorityForm(snap.priorityForm)); setDraftPriorityFormFilter(validPriorityForm(snap.priorityForm));
    setCaseStatusFilter(validCaseStatus(snap.caseStatus || "")); setDraftCaseStatusFilter(validCaseStatus(snap.caseStatus || ""));
    setLifecycleStageFilter(validLifecycleStage(snap.lifecycleStage)); setDraftLifecycleStageFilter(validLifecycleStage(snap.lifecycleStage));
    setFollowUpDateFilter(String(snap.followUpDate || "")); setDraftFollowUpDateFilter(String(snap.followUpDate || ""));
    setColumnFilters(snap.columnFilters);
    setPage(1);
    setFetchRevision((value) => value + 1);
  };
  // { calls: [{ callId, callReference }], found, state: "loading" | "found" | "missing" | "error" } while the
  // call(s) from a notification are shown.
  const [callFocus, setCallFocus] = useState(null);
  const [highlightCallIds, setHighlightCallIds] = useState([]);
  const filtersBeforeFocus = useRef(null);
  const focusSawLoading = useRef(false);
  const startCallFocus = (target) => {
    // Remember the agent's own filters once, so "Show my filters" can restore them (a second focus keeps the first).
    if (!filtersBeforeFocus.current) filtersBeforeFocus.current = appliedSnapshot();
    const fallback = getDefaultDateRange();
    const window_ = focusRangeFor(target.calls.map((call) => call.callDate)) || fallback;
    focusSawLoading.current = false;
    setHighlightCallIds([]);
    setCallFocus({ calls: target.calls, found: 0, state: "loading" });
    // Only these calls' ids and a narrow date window are applied; every other filter is cleared so nothing hides them.
    applySnapshot({
      start: window_.start, end: window_.end, agent: "all", capturedNumber: "", extension: "", direction: "",
      clientNumber: "", clientId: "", leadId: "", callId: target.calls.map((call) => call.callId).join(","), stageFilter: "all", callKind: "all",
      openCase: "", actionStatus: "", priorityForm: "", caseStatus: "", lifecycleStage: "", followUpDate: "",
      columnFilters: Object.fromEntries(COLUMN_FILTERS.map((filter) => [filter.key, ""])),
    });
  };
  const endCallFocus = () => {
    const before = filtersBeforeFocus.current;
    filtersBeforeFocus.current = null;
    setCallFocus(null);
    setHighlightCallIds([]);
    if (before) applySnapshot(before);
  };
  const startCallFocusRef = useRef(startCallFocus);
  startCallFocusRef.current = startCallFocus;
  // Picks up a request made before this page was mounted (stored) or while it is mounted (event).
  useEffect(() => {
    const take = () => { const target = takeCallFocus(); if (target) startCallFocusRef.current(target); };
    take();
    window.addEventListener(CALL_FOCUS_EVENT, take);
    return () => window.removeEventListener(CALL_FOCUS_EVENT, take);
  }, []);
  // After the filtered load that follows: scroll to and highlight the call, or say plainly why it is not there.
  useEffect(() => {
    if (!callFocus || callFocus.state !== "loading") return undefined;
    if (loading) { focusSawLoading.current = true; return undefined; }
    if (!focusSawLoading.current) return undefined;       // not the response to this request yet
    focusSawLoading.current = false;
    const wanted = new Set(callFocus.calls.map((call) => call.callId));
    const shown = calls.filter((item) => wanted.has(item.callId)).map((item) => item.callId);
    if (!shown.length) { setCallFocus({ ...callFocus, state: error ? "error" : "missing" }); return undefined; }
    setCallFocus({ ...callFocus, found: shown.length, state: "found" });
    setHighlightCallIds(shown);
    const scroll = window.setTimeout(() => {
      document.querySelector(`[data-call-row="${CSS.escape(shown[0])}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 150);
    const fade = window.setTimeout(() => setHighlightCallIds([]), 8000);
    return () => { window.clearTimeout(scroll); window.clearTimeout(fade); };
  }, [callFocus, loading, calls, error]);

  // Applied filters (the values the table request uses). Used to check a selected Follow Up
  // Date is still valid for what the table shows.
  const followUpFilters = useMemo(() => ({
    startDate: range.start, endDate: range.end, number: capturedNumberFilter, extension: extensionFilter,
    clientId: clientIdFilter, agent: agentFilter, callDirection: directionFilter,
    clientNumber: clientNumberFilter, callId: callIdFilter, leadId: leadIdFilter,
    callKind: callKindFilter, reviewStatus: stageFilter, lifecycleStage: lifecycleStageFilter,
    ...beltServerFilters({ openCase: openCaseFilter, agent: agentFilter, caseStatus: caseStatusFilter, actionStatus: actionStatusFilter, priorityForm: priorityFormFilter }),
  }), [range.start, range.end, capturedNumberFilter, extensionFilter, clientIdFilter, agentFilter,
    directionFilter, clientNumberFilter, callIdFilter, leadIdFilter, callKindFilter, stageFilter,
    openCaseFilter, actionStatusFilter, priorityFormFilter, caseStatusFilter, lifecycleStageFilter]);
  // What the filter belt shows right now (applied or not): the popover list follows it live.
  // An invalid draft date range falls back to the applied range so no bad request is sent.
  const followUpBeltFilters = useMemo(() => {
    const agentActive = draftAgentFilter !== "all";
    const dates = isValidDateRange(customDraft) ? customDraft : range;
    return {
      startDate: dates.start, endDate: dates.end,
      number: agentActive ? "" : draftCapturedNumberFilter, extension: agentActive ? "" : draftExtensionFilter,
      clientId: draftClientIdFilter.trim(), agent: draftAgentFilter, callDirection: draftDirectionFilter,
      clientNumber: draftClientNumberFilter, callId: draftCallIdFilter, leadId: draftLeadIdFilter.trim(),
      callKind: draftCallKindFilter, reviewStatus: draftStageFilter, lifecycleStage: draftLifecycleStageFilter,
      ...beltServerFilters({ openCase: draftOpenCaseFilter, agent: draftAgentFilter, caseStatus: draftCaseStatusFilter, actionStatus: draftActionStatusFilter, priorityForm: draftPriorityFormFilter }),
    };
  }, [customDraft, range, draftAgentFilter, draftCapturedNumberFilter, draftExtensionFilter,
    draftClientIdFilter, draftDirectionFilter, draftClientNumberFilter, draftCallIdFilter,
    draftLeadIdFilter, draftCallKindFilter, draftStageFilter, draftOpenCaseFilter, draftActionStatusFilter, draftPriorityFormFilter, draftCaseStatusFilter, draftLifecycleStageFilter]);
  const clearFollowUpDate = useCallback(() => setDraftFollowUpDateFilter(""), []);

  const tableRoot = useRef(null);
  const pendingSaves = useRef(0);
  const editRevision = useRef(0);
  const interaction = useRef(false);
  interaction.current = Boolean(cellModal || cellSaving || Object.keys(stageInputDrafts).length || Object.values(submittingCall).some(Boolean) || Object.values(scoringManualData).some(Boolean));
  if (interaction.current) editRevision.current += 1;

  useEffect(() => {
    try {
      sessionStorage.setItem(callsFilterKey(), JSON.stringify({
        start: range.start, end: range.end, displayStart: customDraft.start, displayEnd: customDraft.end, agent: agentFilter,
        capturedNumber: capturedNumberFilter, extension: extensionFilter,
        direction: directionFilter, clientNumber: clientNumberFilter,
        clientId: clientIdFilter, leadId: leadIdFilter, callId: callIdFilter,
        stageFilter, callKind: callKindFilter, openCase: openCaseFilter,
        actionStatus: actionStatusFilter, priorityForm: priorityFormFilter, caseStatus: caseStatusFilter, lifecycleStage: lifecycleStageFilter, followUpDate: followUpDateFilter, columnFilters, viewByPriority,
      }));
      markSavedToday(callsFilterKey());
    } catch { /* Storage may be disabled. */ }
  }, [range, customDraft, agentFilter, capturedNumberFilter, extensionFilter, directionFilter,
    clientNumberFilter, clientIdFilter, leadIdFilter, callIdFilter, stageFilter,
    callKindFilter, openCaseFilter, actionStatusFilter, priorityFormFilter, caseStatusFilter, lifecycleStageFilter, followUpDateFilter, columnFilters, viewByPriority]);

  // What the server is asked for by the consolidated belt (Open Cases / Priority / Action Status) and the Case Status data filter.
  const belt = useMemo(() => beltServerFilters({ openCase: openCaseFilter, agent: agentFilter, caseStatus: caseStatusFilter, actionStatus: actionStatusFilter, priorityForm: priorityFormFilter }),
    [openCaseFilter, agentFilter, caseStatusFilter, actionStatusFilter, priorityFormFilter]);

  const refreshCallsPage = useCallback(async ({ silent, isCurrent }) => {
    const scope = JSON.stringify([range.start, range.end, capturedNumberFilter, extensionFilter,
      clientIdFilter, agentFilter, directionFilter, clientNumberFilter, callIdFilter,
      leadIdFilter, callKindFilter, stageFilter, openCaseFilter, actionStatusFilter, priorityFormFilter, caseStatusFilter, lifecycleStageFilter, followUpDateFilter]);
    if (pageCursors.current.scope !== scope) pageCursors.current = { scope, pages: { 1: "" } };
    const cursor = pageCursors.current.pages[page] ?? "";
    const interacting = () => interaction.current || pendingSaves.current > 0 ||
      (tableRoot.current?.contains(document.activeElement) &&
       document.activeElement?.matches("input, textarea, select, [contenteditable=true]"));
      if (silent && interacting()) return;
      const revision = editRevision.current;
      if (!silent) { setLoading(true); setError(null); }
      try {
      const { records, pagination: nextPagination } = await getCallsListPaged(
        range.start, range.end, capturedNumberFilter, extensionFilter,
        clientIdFilter, agentFilter, directionFilter, page, CALLS_PAGE_SIZE, clientNumberFilter,
        callIdFilter,
        leadIdFilter, callKindFilter, stageFilter,
        cursor,
        false, belt.actionStatus,
        followUpDateFilter, belt.caseStatus, lifecycleStageFilter, belt.agingDependency, 0, belt.priorityForm,
        );
        if (isCurrent() && revision === editRevision.current && (!silent || !interacting())) {
          setCalls(records);
          setPagination(nextPagination);
          pageCursors.current.pages[page + 1] = nextPagination.next_cursor;
          setError(null);
        }
      } catch (err) {
        if (isCurrent() && !silent) setError(err.message || "Failed to load calls.");
      } finally {
        if (isCurrent() && !silent) setLoading(false);
      }
  }, [range.start, range.end, capturedNumberFilter, extensionFilter, clientIdFilter, agentFilter, directionFilter, clientNumberFilter, callIdFilter, leadIdFilter, callKindFilter, stageFilter, belt, lifecycleStageFilter, followUpDateFilter, page]);
  useExplicitPageRefresh(refreshCallsPage, fetchRevision);

  // A filter change invalidates whatever page you were on (it may not even
  // exist in the new filtered set) -- always land back on page 1.
  useEffect(() => {
    setPage(1);
  }, [range.start, range.end, capturedNumberFilter, extensionFilter, clientIdFilter, agentFilter, directionFilter, clientNumberFilter, callIdFilter, leadIdFilter, callKindFilter, stageFilter, belt, lifecycleStageFilter, followUpDateFilter]);

  // Add/Import: reuses the same generic parsing pipeline (data/importMapping.js)
  // Tasks.jsx's importer uses, mapped into CALLS_IMPORT_FIELDS instead of a
  // task schema. "call_id" (an existing call's UUID or AF# reference) is
  // optional, not required: a row that has one updates that existing call;
  // a row with a blank Call_ID cell (or no Call_ID column at all) has no
  // existing call to update -- e.g. a client with no phone number on file,
  // captured entirely offline -- and the backend creates a brand-new call
  // record for it instead (see routes/flintdesk/calls.py's import_calls).
  // Pure so runCallsImport can use the result immediately -- setImportParsed
  // is async React state, so it can't be read back synchronously right
  // after calling it in the same click handler.
  const buildCallsImportParsed = (table) => {
    if (table.headerError) {
      return { ...table, mapping: [], problems: [table.headerError], unmatchedHeaders: [] };
    }
    const collapsed = collapseDuplicateCallIdColumns(table);
    const { mapping, problems, unmatchedHeaders } = mapImportHeaders(collapsed.headers, CALLS_IMPORT_FIELDS, {});
    const callIdIndex = mapping.indexOf("call_id");
    // Rich-text pastes can carry a trailing paragraph or a newline from a
    // multi-line cell. A row is a real row if its Call_ID cell is either
    // blank (a genuinely new call, or a call with no client number) or a
    // valid identifier -- only a non-blank value that's neither is a paste
    // artifact rather than data. Filtering it here keeps the preview count
    // and the submitted records in agreement.
    const rows = callIdIndex < 0
      ? collapsed.rows
      : collapsed.rows.filter((cells) => {
          const value = String(cells[callIdIndex] ?? "").trim();
          return !value || isCallImportIdentifier(value);
        });
    return {
      ...collapsed,
      rows,
      mapping,
      problems,
      unmatchedHeaders,
      ignoredNonCallRows: collapsed.rows.length - rows.length,
    };
  };

  const prepareCallsImport = (table) => {
    setImportInvalidRows([]);
    setImportUpdatedRecords([]);
    setImportResultNotice("");
    setImportParsed(buildCallsImportParsed(table));
  };

  const uploadCallsImportFile = async (file) => {
    if (!file || !/\.(csv|tsv|txt)$/i.test(file.name)) {
      setError("Choose a CSV or TXT file.");
      return;
    }
    setImportSelectedFileName(file.name);
    setImportPaste("");
    const text = await file.text();
    const delimiter = file.name.toLowerCase().endsWith(".csv") ? "," : "\t";
    prepareCallsImport(parseDelimitedTable(text, delimiter));
  };

  // Shared by every flow that writes calls out of band (bulk import, the
  // Edit modal's scoped import) -- imported rows can land anywhere in sort
  // order, so there's no way to patch the current page in place; always
  // land back on a freshly fetched page 1.
  const refreshCallsFirstPage = async () => {
    setPage(1);
    const { records: refreshedRecords, pagination: nextPagination } = await getCallsListPaged(
      range.start, range.end, capturedNumberFilter, extensionFilter,
      clientIdFilter, agentFilter, directionFilter, 1, CALLS_PAGE_SIZE, clientNumberFilter,
      callIdFilter,
      leadIdFilter, callKindFilter, stageFilter,
      "",
      false, belt.actionStatus,
      followUpDateFilter, belt.caseStatus, lifecycleStageFilter, belt.agingDependency, 0, belt.priorityForm,
    );
    setCalls(refreshedRecords);
    setPagination(nextPagination);
    pageCursors.current.pages = { 1: "", 2: nextPagination.next_cursor };
  };

  const runCallsImport = async () => {
    // Import works directly from a fresh paste without a separate manual
    // "Validate" click first -- re-derive from the live textarea at click
    // time (rather than trusting a possibly-stale previous validation) so
    // editing the paste after validating still imports the current text.
    // A file upload is already freshly parsed by uploadCallsImportFile.
    let parsed = importParsed;
    if (!importSelectedFileName) {
      if (!importPaste.trim()) {
        setError("Paste data or choose a file to import first.");
        return;
      }
      parsed = buildCallsImportParsed(parsePastedTable(importPaste));
      setImportParsed(parsed);
    }
    if (!parsed || parsed.problems?.length) return;
    const records = recordsFromImportTable(parsed);
    if (!records.length) {
      setImportResultNotice("Nothing to import.");
      return;
    }
    if (records.length > MAX_CALLS_IMPORT_ROWS) {
      setError(`Import is limited to ${MAX_CALLS_IMPORT_ROWS} rows at a time.`);
      return;
    }
    setImportRunning(true);
    setError(null);
    setImportInvalidRows([]);
    setImportUpdatedRecords([]);
    try {
      const result = await importCalls(records);
      editRevision.current += 1;
      const hasIssues = Boolean(result.invalid_fields?.length);
      setImportResultNotice(
        `${result.imported} call${result.imported === 1 ? "" : "s"} updated.` +
          (hasIssues
            ? ` ${result.invalid_fields.length} row(s) had a problem -- see details below.`
            : "") +
          (result.skipped_empty ? ` ${result.skipped_empty} empty row(s) skipped.` : ""),
      );
      setImportInvalidRows(result.invalid_fields || []);
      setImportUpdatedRecords(result.updated_records || []);
      // Refetch regardless of hasIssues so any rows that DID succeed show up
      // immediately, even when other rows in the same paste had a problem.
      await refreshCallsFirstPage();
      // Keep the result visible even for a clean import. A count alone is
      // not enough evidence when an export contains both the short Call ID
      // and its UUID; the agent can confirm the exact target below.
    } catch (err) {
      setError(err.message || "Import failed.");
    } finally {
      setImportRunning(false);
    }
  };

  // Edit (pen icon) modal: reuses the exact same parse/map/validate pipeline
  // as Add/Import above (buildCallsImportParsed's helpers), but the target
  // call is already known from the row whose Edit icon was clicked -- a
  // "Call_ID"/"Client Call ID" column is never required, and if the paste
  // includes one anyway it's ignored: call_id is always overwritten with
  // the clicked call's own id before this goes to the same importCalls API
  // Add/Import uses, so there is one source of truth for the write, and an
  // agent can never (even by mistake) target a different call from here.
  const buildEditImportParsed = (table) => {
    if (table.headerError) {
      return { ...table, mapping: [], problems: [table.headerError], unmatchedHeaders: [] };
    }
    const collapsed = collapseDuplicateCallIdColumns(table);
    const { mapping, problems, unmatchedHeaders } = mapImportHeaders(collapsed.headers, CALLS_IMPORT_FIELDS, {});
    const rows = collapsed.rows.filter((cells) => cells.some((cell) => String(cell || "").trim()));
    const allProblems = rows.length > 1
      ? [...problems, "Paste one record's row only -- Edit updates a single call."]
      : problems;
    return { ...collapsed, rows, mapping, problems: allProblems, unmatchedHeaders };
  };

  const openEditImportModal = (call) => {
    setEditImportCall(call);
    setEditImportPaste("");
    setEditImportParsed(null);
    setEditImportResultNotice("");
    setEditImportInvalidRows([]);
    setError(null);
  };

  const closeEditImportModal = () => {
    setEditImportCall(null);
    setEditImportPaste("");
    setEditImportParsed(null);
    setEditImportResultNotice("");
    setEditImportInvalidRows([]);
  };

  const runEditImport = async () => {
    if (!editImportCall) return;
    if (!editImportPaste.trim()) {
      setError("Paste data to import first.");
      return;
    }
    const parsed = buildEditImportParsed(parsePastedTable(editImportPaste));
    setEditImportParsed(parsed);
    if (parsed.problems?.length) return;
    const [record] = recordsFromImportTable(parsed);
    if (!record) {
      setEditImportResultNotice("Nothing to import.");
      return;
    }
    const targetedRecord = { ...record, call_id: editImportCall.callId };
    setEditImportRunning(true);
    setError(null);
    setEditImportInvalidRows([]);
    setEditImportResultNotice("");
    try {
      const result = await importCalls([targetedRecord]);
      editRevision.current += 1;
      setEditImportInvalidRows(result.invalid_fields || []);
      if (result.imported) {
        setEditImportResultNotice("Call updated.");
        await refreshCallsFirstPage();
      } else if (!result.invalid_fields?.length) {
        setEditImportResultNotice("Nothing to import.");
      }
    } catch (err) {
      setError(err.message || "Import failed.");
    } finally {
      setEditImportRunning(false);
    }
  };

  // Call Records visibility is the same for every role: an agent's
  // assigned extension does not restrict which calls are shown here, so
  // this is just the API response as-is (the API itself is unrestricted
  // too -- see routes/calls.py _fd_assignment_numbers).
  const accessibleCalls = calls;
  // The Filter Data panel shows COLUMN_FILTERS' fixed reference list as-is
  // (see FILTER_OPTION_LISTS) -- it used to also merge in every distinct
  // value actually seen on loaded calls, which let legacy/off-list values
  // leak into the dropdown instead of keeping it a clean, closed list.
  // Case Status Change is a Stage 3 field, so it is not one of COLUMN_FILTERS; it is added here as one more dropdown. Unlike
  // the others it is applied by the SERVER (so paging and totals stay right) the moment it changes.
  const callDataFilterDefinitions = [
    ...COLUMN_FILTERS,
    { key: CASE_STATUS_FILTER_KEY, label: "Case Status", options: [{ value: CASE_STATUS_NOT_SET, label: "Not set (no status yet)" }, ...CASE_STATUS_OPTIONS] },
  ];
  const setCaseStatusNow = (value) => { setCaseStatusFilter(value); setDraftCaseStatusFilter(value); };

  const filtered = useMemo(
    () =>
      accessibleCalls.filter((call) => {
        const matchesDateRange =
          call.date >= range.start && call.date <= range.end;
        // Inbound calls do not have a manually entered agent until someone
        // identifies themselves. For filtering, include the same durable
        // assignment fallback used elsewhere in the table.
        const agentNames = [call.agent, call.assignedAgent]
          .map((value) => String(value || "").trim().toLowerCase())
          .filter((value) => value && value !== "-");
        const extension = String(call.extension || call.raw?.extension || "")
          .trim()
          .toLowerCase();
        // Filter the Client Number column's remote-party value, not the
        // outbound source/trunk number. This keeps browser-side matching
        // identical to the server filter and to the value users see.
        const clientNumber = normalizePhoneSearch(
          call.clientPhoneNumber ||
            call.raw?.client_phone_number ||
            call.raw?.phone_number ||
            "",
        ).toLowerCase();
        const matchesAgent =
          agentFilter === "all" ||
          agentNames.includes(agentFilter.trim().toLowerCase());
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
        const matchesDirection =
          !directionFilter ||
          String(call.direction || "").trim().toLowerCase() === directionFilter.toLowerCase();
        const matchesClientNumber =
          !clientNumberFilter.trim() ||
          clientNumber.includes(normalizePhoneSearch(clientNumberFilter));
        const matchesClientId =
          !clientIdFilter.trim() ||
          String(call.clientId || "").toLowerCase() ===
            clientIdFilter.trim().toLowerCase();
        const matchesLeadId =
          !leadIdFilter.trim() ||
          String(call.leadId || "")
            .toLowerCase()
            .includes(leadIdFilter.trim().toLowerCase());
        const matchesCallId =
          !callIdFilter.trim() ||
          callIdFilter.split(",").map((term) => term.trim().toLowerCase()).filter(Boolean).some((term) =>
            [call.id, call.callId].some((value) => String(value || "").toLowerCase().includes(term)));
        const matchesCallKind =
          callKindFilter === "all" ||
          (!call.isTaskRecord &&
            getCallKind(call).toLowerCase() === callKindFilter);
        // "All | Pending | Completed" -- reuses the same case-level
        // case_closing_completed flag Submit reads/writes (see submitCall),
        // instead of the removed per-stage field-completeness filters. A
        // stale persisted value from before this change (e.g.
        // "stage2Pending") falls through to "all" rather than matching
        // nothing.
        const matchesStage =
          stageFilter === "completed"
            ? Boolean(call.raw?.case_closing_completed)
            : stageFilter === "pending"
              ? !call.raw?.case_closing_completed
              : true;
        const matchesColumnFilters = COLUMN_FILTERS.every((filter) => {
          const selected = columnFilters[filter.key];
          if (!selected) return true;
          return String(call.raw?.[filter.key] ?? "").trim() === selected;
        });

        return (
          matchesDateRange &&
          matchesStage &&
          matchesAgent &&
          matchesCapturedNumber &&
          matchesExtension &&
          matchesDirection &&
          matchesClientNumber &&
          matchesClientId &&
          matchesLeadId &&
          matchesCallId &&
          matchesCallKind &&
          matchesColumnFilters
        );
      }),
    [
      accessibleCalls,
      calls,
      range,
      stageFilter,
      agentFilter,
      capturedNumberFilter,
      extensionFilter,
      directionFilter,
      clientNumberFilter,
      clientIdFilter,
      leadIdFilter,
      callIdFilter,
      callKindFilter,
      columnFilters,
      syncFilters,
    ],
  );

  const clientCallCounts = useMemo(() => {
    const callsByClient = new Map();
    filtered.forEach((call) => {
      const clientId = String(call.clientId || "").trim();
      if (!clientId) return;
      const key = [call.date, call.timestamp, call.sourceNumber, call.destinationNumber].join("|");
      if (!callsByClient.has(clientId)) callsByClient.set(clientId, new Set());
      callsByClient.get(clientId).add(key);
    });
    return new Map([...callsByClient].map(([clientId, calls]) => [clientId, calls.size]));
  }, [filtered]);
  const displayedCalls = useMemo(
    () => (viewByPriority ? rankCallsByPriority(filtered) : filtered),
    [filtered, viewByPriority],
  );
  const prioritySummary = useMemo(() => {
    const scores = filtered
      .map(calculatePriority)
      .map((item) => item.leadScore)
      .filter(Number.isFinite);
    return {
      average: scores.length
        ? scores.reduce((sum, score) => sum + score, 0) / scores.length
        : 0,
      high: scores.filter((score) => score >= 70).length,
      medium: scores.filter((score) => score >= 40 && score < 70).length,
      low: scores.filter((score) => score < 40).length,
    };
  }, [filtered]);
  const primaryStageColumns =
    stageView === "stage_1_2"
      ? STAGE1_COLUMNS
      : [...STAGE1_COLUMNS, ...STAGE2_COLUMNS, ...STAGE3_COLUMNS];
  const finalStageColumns =
    stageView === "stage_1_2" ? STAGE2_COLUMNS : STAGE4_COLUMNS;
  // primaryStageColumns combines three stages' fields into one array (see
  // above), so its header row needs each field's OWN stage color, not one
  // color for the whole array -- this is what keeps a Stage 2 column header
  // the same cream color as the "Stage 2 · Action Tracking" group header
  // above it, instead of just inheriting Stage 3's purple.
  const stage1Keys = new Set(STAGE1_COLUMNS.map((field) => field.key));
  const stage2Keys = new Set(STAGE2_COLUMNS.map((field) => field.key));
  const primaryStageHeaderClass = (fieldKey) => {
    if (stageView === "stage_1_2") return "stage1-column-header";
    if (stage1Keys.has(fieldKey)) return "stage1-column-header";
    if (stage2Keys.has(fieldKey)) return "stage2-column-header";
    return "stage3-column-header";
  };
  const staticCallColumn = (index) => ({ "data-call-sticky-column": index });
  const staticCallDetailsColumn = () => ({});
  const staticPriorityColumn = (index) =>
    viewByPriority ? { "data-call-sticky-column": index } : {};
  // The frozen CALL columns deliberately follow their body content rather
  // than their (often long) labels.  Header labels wrap vertically instead.
  const callColumnWidths = [38, 88, 70, 72, 44, 44];
  const priorityColumnWidths = viewByPriority ? [92, 92] : [];
  const stickyColumnWidths = [...callColumnWidths, ...priorityColumnWidths];
  const callsTableColumnWidths = [
    50,
    ...stickyColumnWidths,
    95,
    125,
    125,
    150,
    115,
    80,
    110,
    95,
    90,
    130,
    ...primaryStageColumns.map((field) =>
      field.key === "fd_a_call_summary" ? 540 : 180,
    ),
    ...finalStageColumns.map(() => 180),
    // Sized to fit Submit + Mark Completed + the Edit pen icon at their
    // compact .calls-action-row sizing (see App.css), nothing wider.
    stageView === "stage_3_4" ? 195 : 150,
  ];
  const callsStickyOffsets = stickyColumnWidths.reduce(
    (offsets, width) => [...offsets, offsets[offsets.length - 1] + width],
    [50],
  );
  const callsTableStickyStyle = Object.fromEntries(
    callsStickyOffsets.map((offset, index) => [
      `--calls-sticky-offset-${index}`,
      `${offset}px`,
    ]),
  );

  // The table's columns size themselves to their content (table-layout: auto), so the frozen CALL columns are
  // wider than the numbers assumed above. Pin each one at the width it actually renders with, and keep doing so when
  // the table resizes, so the frozen headers and cells sit exactly on top of where they scrolled from instead of
  // overlapping each other. The assumed offsets stay as the starting values.
  const pinCallColumns = useCallback((table) => {
    if (!table) return undefined;
    const measure = () => {
      const header = table.querySelector("thead tr:nth-child(2)");
      const selection = header?.querySelector("[data-call-selection-column]");
      const frozen = [...(header?.querySelectorAll("th[data-call-sticky-column]") || [])]
        .sort((a, b) => Number(a.dataset.callStickyColumn) - Number(b.dataset.callStickyColumn));
      if (!selection || !frozen.length) return;
      let left = selection.getBoundingClientRect().width;
      frozen.forEach((th) => {
        table.style.setProperty(`--calls-sticky-offset-${th.dataset.callStickyColumn}`, `${left}px`);
        left += th.getBoundingClientRect().width;
      });
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(table);
    table.querySelectorAll("thead tr:nth-child(2) > th").forEach((th, index) => { if (index < 10) observer.observe(th); });
    return () => observer.disconnect();
  }, []);

  const agentOptions = fdAgents;
  useEffect(() => {
    if (!isLeadScoreMatrixOpen) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isLeadScoreMatrixOpen]);
  // Show rows the server has just closed (related logs, or a confirmed closure notification) without reloading:
  // status, its system-set date and the closure badge. A row with an unsent Case Status edit of its own is left alone.
  const queuedChangesRef = useRef(stage34Changes);
  queuedChangesRef.current = stage34Changes;
  const applyClosedRows = (updated, closure) => {
    const byCall = new Map((updated || []).filter((log) => log?.call_id).map((log) => [String(log.call_id), log]));
    if (!byCall.size) return;
    editRevision.current += 1; // a list refresh that started before this must not put the old statuses back
    setCalls((current) => current.map((item) => {
      const log = byCall.get(String(item.callId));
      if (!log || queuedChangesRef.current[item.id]?.fd_m_case_status_change) return item;
      statusTouched.current.add(item.id);       // a closure the agent confirmed still needs their Submit to complete
      return { ...item, raw: { ...item.raw, fd_m_case_status_change: log.new_status,
        ...(log.fd_m_date_entry !== undefined ? { fd_m_date_entry: log.fd_m_date_entry } : {}),
        ...(closure ? { closure_inherited: closure } : {}) } };
    }));
  };
  const applyClosedRowsRef = useRef(applyClosedRows);
  applyClosedRowsRef.current = applyClosedRows;
  useEffect(() => {
    const onConfirmed = (event) => applyClosedRowsRef.current(event.detail?.updated, event.detail?.closure);
    window.addEventListener(CLOSURE_ROWS_CHANGED_EVENT, onConfirmed);
    return () => window.removeEventListener(CLOSURE_ROWS_CHANGED_EVENT, onConfirmed);
  }, []);

  // The agent has just closed `call`: look up their OWN other open logs for the same client and case context and, only
  // if there are some, offer to close them too. Best effort - the closing save itself is already confirmed.
  const offerRelatedClosure = async (call) => {
    if (!call?.callId || relatedInFlight.current.has(call.id)) return;
    relatedInFlight.current.add(call.id);
    try {
      const offer = await getOwnRelatedLogs(call.callId);
      if (offer?.closure_id && offer.logs?.length) {
        setRelatedOffers((current) => (current.some((item) => item.closure_id === offer.closure_id) ? current : [...current, offer]));
      }
    } catch {
      // No offer is shown; the status change was saved and can be repeated from Case Status.
    } finally {
      relatedInFlight.current.delete(call.id);
    }
  };
  const nextRelatedOffer = () => {
    setRelatedUi({ busy: false, error: "", result: null });
    setRelatedOffers((current) => current.slice(1));
  };
  // Confirm: the server re-validates each log and closes it with the closure's own status; then the rows on screen are
  // updated from its answer (no reload) and the exact outcome is shown.
  const confirmRelatedClosure = async () => {
    const offer = relatedOffers[0];
    if (!offer || relatedConfirming.current) return;
    relatedConfirming.current = true;
    setRelatedUi({ busy: true, error: "", result: null });
    try {
      const result = await closeOwnRelatedLogs(offer.closure_id, offer.logs.map((log) => log.analysis_id));
      applyClosedRows(result.updated, result.closure);
      // Their lifecycle stage changed with their status: with a lifecycle button applied, reload so the list and totals follow.
      if (lifecycleStageFilter && result.updated?.length) setFetchRevision((value) => value + 1);
      setRelatedUi({ busy: false, error: "", result });
    } catch (failure) {
      setRelatedUi({ busy: false, error: failure?.message || "Could not close the related call logs. Nothing was changed - please try again.", result: null });
    } finally {
      relatedConfirming.current = false;
    }
  };

  const saveStageValue = async (call, field, value) => {
    if (field.key === "fd_m_case_status_change") statusTouched.current.add(call.id);
    pendingSaves.current += 1;
    editRevision.current += 1;
    const previousValue = call?.raw?.[field.key] ?? call?.[field.key] ?? "";
    const saveKey = `${call.id}:${field.key}`;
    const chain = (fieldSaves.current[saveKey] ||= { tail: Promise.resolve(), confirmed: undefined, pending: 0, seq: 0 });
    const seq = ++chain.seq;                       // later edits of this field make this one "not the latest"
    const isLatest = () => chain.seq === seq;
    const setLocalValue = (nextValue) => setCalls((current) =>
      current.map((item) =>
        item.id === call.id
          ? { ...item, raw: { ...item.raw, [field.key]: nextValue } }
          : item,
      ),
    );
    setLocalValue(value);
    if (call.raw?.case_closing_completed && STAGE_FIELD_KEYS.has(field.key)) {
      setDeletedStageData((current) => {
        const forCall = { ...(current[call.id] || {}) };
        if (isDeletedStageValue(value, previousValue)) forCall[field.key] = true; else delete forCall[field.key];
        const next = { ...current };
        if (Object.keys(forCall).length) next[call.id] = forCall; else delete next[call.id];
        return next;
      });
    }
    if (call.raw?.case_closing_completed && STAGE34_KEYS.has(field.key)) {
      queueStage34Change(call, field.key, value);
      pendingSaves.current -= 1;
      return;
    }
    beginAutosave(call.id);
    // With no save of this field in flight, what the table showed is what the server has.
    if (!chain.pending) chain.confirmed = previousValue;
    chain.pending += 1;
    const previousSave = chain.tail;
    let finish;
    chain.tail = new Promise((resolve) => { finish = resolve; });
    try {
      if (!call.callId) throw new Error("Missing call identifier.");
      await previousSave;                          // saves of one field go out in order
      const expectedValue = chain.confirmed;
      // Use the call-based endpoint for every editable field. It updates the
      // shared analysis row (or creates it), which Case Closing also reads.
      const saved = await updateCallField(call.callId, field.key, value, expectedValue);
      chain.confirmed = saved.normalized_value ?? value;
      // See saveCellModal: keep this client's other rows in step with the profile.
      if (saved?.client) applyClientProfileUpdate(saved.client);
      setStageInputDrafts((current) => {
        const next = { ...current };
        const key = `${call.id}:${field.key}`;
        if (String(next[key] ?? "").trim() === String(value)) delete next[key];
        return next;
      });
      // The server's answer is the truth for this row: its stored value, the system-set Case Status date and the
      // completion flags. A newer edit of the same field wins over this (older) response - it reconciles on its own.
      if (isLatest()) {
        setCalls((current) =>
          current.map((item) =>
            item.id === call.id
              ? {
                  ...item,
                  raw: {
                    ...item.raw,
                    [field.key]: saved.normalized_value ?? value,
                    ...(field.key === "fd_m_case_status_change" && saved.fd_m_date_entry !== undefined
                      ? { fd_m_date_entry: saved.fd_m_date_entry }
                      : {}),
                    case_closing_completed: saved.case_closing_completed,
                    case_closing_completed_at: saved.case_closing_completed_at,
                  },
                }
              : item,
          ),
        );
      }
      endAutosave(call.id); // the backend confirmed it: only now does the row say Saved
      // The status decides the call's lifecycle stage on the backend: with a lifecycle button applied, reload so this
      // row, the page totals and pagination follow the new stage.
      if (field.key === "fd_m_case_status_change" && lifecycleStageFilter && isLatest()) setFetchRevision((value) => value + 1);
      if (field.key === "fd_m_case_status_change" && isLatest()
          && shouldOfferRelatedClosure(expectedValue, saved.normalized_value ?? value)) {
        offerRelatedClosure(call);
      }
    } catch (saveError) {
      // Back to the last value the server confirmed - unless a newer edit is already on screen.
      if (isLatest()) setLocalValue(chain.confirmed ?? previousValue);
      setError(saveError.message || "Failed to save stage value.");
      endAutosave(call.id, { error: saveError.message || "Failed to save", retry: () => saveStageValue(call, field, value) });
    } finally {
      chain.pending -= 1;
      finish();
      pendingSaves.current -= 1;
    }
  };

  // One call's Save / Submit state, shared by the compact status in the frozen columns and the controls in the last
  // column so the two always show the same thing.
  const rowSubmitInfo = (call) => {
    const missingStage2 = missingStage2Labels(call);
    // Derived live from the call's current Case Status Change value, not the persisted case_closing_completed
    // flag -- so it's already correct for every existing call (including ones completed under the old, manual
    // "always complete" rule) without needing a Submit click or a backend backfill first.
    const currentCaseStatus = String(call.raw?.fd_m_case_status_change || "").trim();
    const isCompleted = COMPLETION_TRIGGER_STATUSES.has(caseStatusKey(currentCaseStatus));
    const hasDeletedData = Object.keys(deletedStageData[call.id] || {}).length > 0;
    const submitState = deriveSubmitState({
      stage2Missing: missingStage2,
      submitting: Boolean(submittingCall[call.id]),
      submitError: submitErrors[call.id] || "",
      pendingFields: pendingChanges(stage34Changes[call.id]).map(([field]) => field),
      hasDeletedData,
      autosaving: (autosave[call.id]?.pending || 0) > 0,
      completionDerived: isCompleted,
      completionPersisted: Boolean(call.raw?.case_closing_completed),
      submittedThisSession: Boolean(submittedCalls[call.id]),
    });
    return { submitState, isCompleted, hasDeletedData };
  };

  // ---- Leaving the Calls page while a Case Status change still needs Submit ----
  // Decided from the same state as the Submit button (see data/submitState.js pendingSubmissionReason), only for rows the
  // agent changed here. App.jsx asks for this before any in-app navigation; the browser's own refresh / tab-close warning
  // uses the same count. "Saved" alone never counts as "Submitted".
  const pendingForLeave = () => {
    const rows = [];
    for (const id of statusTouched.current) {
      const call = calls.find((item) => item.id === id);
      if (!call || call.isTaskRecord || !call.analysisId) continue;
      const info = rowSubmitInfo(call);
      const reason = pendingSubmissionReason(info.submitState, { completionDerived: info.isCompleted, completionPersisted: Boolean(call.raw?.case_closing_completed) });
      if (reason) rows.push({ id, ref: call.call_reference || call.id, reason, status: String(call.raw?.fd_m_case_status_change || "").trim() });
    }
    return { rows, unsavedCount: countUnsavedRows({ stage34Changes, deletedStageData, autosave, submitErrors }) };
  };
  // The existing Submit, once per pending row, in order. Returns what really happened; never throws.
  const submitAllForLeave = async () => {
    const latest = () => leaveRefs.current;
    const submitted = [];
    const failed = [];
    for (const row of latest().pending().rows) {
      const until = Date.now() + 20000;
      while ((latest().autosave[row.id]?.pending || 0) > 0 && Date.now() < until) await new Promise((resolve) => setTimeout(resolve, 100));
      if ((latest().autosave[row.id]?.pending || 0) > 0) { failed.push({ ref: row.ref, error: "Still saving - try again in a moment." }); continue; }
      if (!latest().pending().rows.some((item) => item.id === row.id)) { submitted.push(row.ref); continue; }   // settled meanwhile
      const call = latest().calls.find((item) => item.id === row.id);
      let result;
      try { result = await latest().submit(call); } catch (failure) { result = { ok: false, error: failure?.message }; }
      if (result?.ok) submitted.push(row.ref); else failed.push({ ref: row.ref, error: result?.error || "Could not submit this call." });
    }
    return { submitted, failed };
  };
  const leaveRefs = useRef({});
  leaveRefs.current = { pending: pendingForLeave, submit: (call) => submitCall(call, { offerRelated: false }), submitAll: submitAllForLeave, calls, autosave };
  useEffect(() => registerNavigationGuard("calls", {
    getPending: () => leaveRefs.current.pending(),
    submitAll: () => leaveRefs.current.submitAll(),
  }), []);
  useEffect(() => {
    setPendingSubmissionCount("calls", leaveRefs.current.pending().rows.length);
  }, [calls, stage34Changes, deletedStageData, autosave, submitErrors, submittingCall, submittedCalls]);
  useEffect(() => () => setPendingSubmissionCount("calls", 0), []);

  const renderStageCell = (call, field) => {
    const storedValue = call?.raw?.[field.key] ?? call?.[field.key];
    const isMissing =
      storedValue === null || storedValue === undefined || storedValue === "";
    let value =
      isStage1AiField(field.key) &&
      call?.raw?.fd_processing_status === "processed" &&
      isMissing
        ? "N/A"
        : cellValue(call, field.key);
    // An unidentified client must remain visually blank; a dash would imply a
    // placeholder value rather than intentionally absent client evidence.
    if (field.key === "fd_a_client_name" && isMissing) value = "";
    if (call.isTaskRecord)
      return (
        <span title={`${call.recordLabel} · read-only Task data`}>{value}</span>
      );
    const missingStage2 = STAGE34_KEYS.has(field.key) ? missingStage2Labels(call) : [];
    if (missingStage2.length && field.key === "fd_m_case_status_change"
      && shouldShowInheritedClosure(call.raw?.fd_m_case_status_change, call.raw?.closure_inherited)) {
      // The agent confirmed another agent's closure, so this status was set for them even though Stage 2 is
      // still incomplete. Show the status and who changed it (read-only until Stage 2 is done).
      return (
        <div className="closure-inherited-cell">
          <span title="Set by confirming another agent's closure">{value}</span>
          <button
            type="button"
            className="closure-inherited-badge"
            title="Status changed by another agent - click for details"
            onClick={() => setClosureInfo(call.raw.closure_inherited)}
          >
            {closureBadgeText(call.raw.closure_inherited)}
          </button>
        </div>
      );
    }
    if (missingStage2.length) {
      return (
        <span className="muted" title={`Complete Stage 2 first: ${missingStage2.join(", ")}`}>
          Complete Stage 2 first
        </span>
      );
    }
    if (field.attachment) {
      return (
        <AttachmentCell
          callId={call.callId}
          target={field.key}
          value={call.raw?.[field.key]}
          onChange={(newValue) =>
            {
              setCalls((current) =>
                current.map((item) =>
                item.id === call.id
                  ? { ...item, raw: { ...item.raw, [field.key]: newValue } }
                  : item,
                ),
              );
              if (call.raw?.case_closing_completed) {
                queueStage34Change(call, field.key, newValue, { persisted: true });
                setCompletionUpdates((current) => ({ ...current, [call.id]: false }));
              }
            }
          }
          onPreview={(url, name) => setImagePreview({ url, name })}
        />
      );
    }
    if (isStage1AiField(field.key)) {
      if (["fd_a_call_summary", "enquiry_details"].includes(field.key)) {
        return (
          <span
            role="button"
            tabIndex={0}
            className="calls-cell-clickable calls-call-summary-preview"
            title={value}
            onClick={() => setSummaryModal({
              id: call.call_reference || call.id,
              summary: value,
              title: field.key === "fd_a_call_summary" ? "Call Summary" : "Enquiry Question",
            })}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                setSummaryModal({
                  id: call.call_reference || call.id,
                  summary: value,
                  title: field.key === "fd_a_call_summary" ? "Call Summary" : "Enquiry Question",
                });
              }
            }}
          >
            {value || "—"}
          </span>
        );
      }
      // Stage 1 fields are editable via click-to-modal. The popup uses an
      // options-based <select> for choice fields, or a textarea for free text.
      return (
        <span
          className="calls-cell-clickable"
          title={value}
          onClick={() =>
            openCellModal(
              field.label,
              field.key,
              value,
              call.id,
              call.callId,
              field.options || null,
            )
          }
        >
          {/* A blank value (e.g. a client name the AI could not identify)
              rendered an empty span with no clickable area, so the cell
              could not be opened to type a name. */}
          {value || (
            <span className="muted" title={`Add ${field.label}`}>
              Add {field.label.toLowerCase()}
            </span>
          )}
        </span>
      );
    }
    if (field.readOnly) {
      return <span title={value}>{value}</span>;
    }
    if (field.key === "fd_m_action_due_date") {
      const dueNote = String(call?.raw?.fd_m_action_due_note || "").trim();
      return (
        <span
          className="calls-cell-clickable action-due-date-cell"
          title={dueNote ? `Date: ${value || "Not set"}\nNote: ${dueNote}` : value}
          onClick={() =>
            openCellModal(field.label, field.key, value, call.id, call.callId)
          }
        >
          <span>{value || "—"}</span>
          {dueNote && <span className="action-due-note-preview">{dueNote}</span>}
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

    if (field.closedOptions) {
      // A controlled vocabulary (e.g. Case Status): same select styling as
      // the other stage dropdowns, but no free-text override -- matching
      // the Lead Category pattern on the Leads page, since the backend
      // validates this value against a fixed list and would reject anything
      // typed outside it.
      // A value still stored in its older spelling is shown as its new wording; any other stored value (for example the
      // automatic "PC link shared & Lost") is shown as it is, instead of looking like nothing was chosen.
      const knownOption = field.options.find((option) => caseStatusKey(option) === caseStatusKey(value));
      const isKnownOption = Boolean(knownOption);
      const storedOnlyValue = !isKnownOption && String(value ?? "").trim() && String(value).trim() !== "-" ? String(value).trim() : "";
      // Case Status Change stays available whatever Stage 1 holds (no audio, transcript or answer needed); only Stage 2 gates it.
      const inheritedClosure = field.key === "fd_m_case_status_change" &&
        shouldShowInheritedClosure(call.raw?.fd_m_case_status_change, call.raw?.closure_inherited)
        ? call.raw.closure_inherited : null;
      const select = (
        <select
          className="calls-stage-select leads-fixed-select"
          value={isKnownOption ? knownOption : storedOnlyValue}
          onChange={(event) => saveStageValue(call, field, event.target.value)}
          title={`Choose ${field.label}`}
        >
          <option value="">Choose</option>
          {storedOnlyValue && <option value={storedOnlyValue} disabled>{storedOnlyValue}</option>}
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
      if (!inheritedClosure) return select;
      // Another agent closed this matter and this agent confirmed it: shown beside (never instead of)
      // the agent's own status, so it cannot be mistaken for a closure they made themselves.
      return (
        <div className="closure-inherited-cell">
          {select}
          <button
            type="button"
            className="closure-inherited-badge"
            title="Status changed by another agent - click for details"
            onClick={() => setClosureInfo(inheritedClosure)}
          >
            {closureBadgeText(inheritedClosure)}
          </button>
        </div>
      );
    }

    const draftKey = `${call.id}:${field.key}`;
    const isCanonicalOption = field.options.includes(String(value));
    const inputValue =
      stageInputDrafts[draftKey] ??
      (isCanonicalOption || value === "-" ? "" : String(value));
    return (
      <div className="calls-stage-editor">
        <select
          className="calls-stage-select"
          value={isCanonicalOption ? String(value) : ""}
          onChange={(event) => {
            const selectedValue = event.target.value;
            setStageInputDrafts((current) => {
              const next = { ...current };
              delete next[draftKey];
              return next;
            });
            saveStageValue(call, field, selectedValue);
          }}
          title={`Choose ${field.label}`}
        >
          <option value="">Choose</option>
          {field.options.map((option) => (
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
            else setStageInputDrafts((current) => {
              const next = { ...current }; delete next[draftKey]; return next;
            });
          }}
        />
      </div>
    );
  };

  // Copy and Excel export intentionally use this same projection: a copied
  // row can be pasted directly beneath the exported headers without columns
  // shifting or hidden internal identifiers appearing.
  const callsExportColumns = () => [
      ...BASE_EXPORT_COLUMNS,
      ...(stageView === "stage_1_2"
        ? [...STAGE1_COLUMNS, ...STAGE2_COLUMNS]
        : [
            ...STAGE1_COLUMNS,
            ...STAGE2_COLUMNS,
            ...STAGE3_COLUMNS,
            ...STAGE4_COLUMNS,
          ]),
    ];
  const callsExportRow = (call, columns) =>
    columns.map((column) => {
      const value =
        column.key === "direction"
          ? displayDirection(call.direction)
          : column.key === "callKind"
            ? getCallKind(call)
            : column.key === "manual_data_score"
              ? call.raw?.manual_data_score == null
                ? ""
                : `${Number(call.raw.manual_data_score).toFixed(1)} / 10`
              : column.key === "enquiryOpenDuration"
                ? enquiryOpenDuration(call)
                : callsExportValue(call, column.key, cellValue);
      return value === "-" ? "" : value;
    });
  const exportCalls = () => {
    // Export the exact already-filtered dataset, keeping the table and export
    // on one source of truth rather than recreating filter predicates.
    const exportColumns = callsExportColumns();
    const rows = displayedCalls.map((call) =>
      callsExportRow(call, exportColumns),
    );
    downloadExcelWorkbook(
      [{
        name: "Calls",
        columns: exportColumns.map((column) => ({
          ...column,
          wrap: /summary|comment|action item|enquiry|attachment/i.test(column.label),
        })),
        rows,
      }],
      `FlintDesk_Calls_${range.start}_to_${range.end}.xls`,
    );
  };
  const copyCalls = async (items, rowId = null) => {
    const columns = callsExportColumns();
    // Header row first, then each call's displayed values (the Date column in the table's MM/DD/YYYY form).
    const table = buildCopyTable(
      columns,
      items.map((call) => callsExportRow(call, columns)),
      { date: (value) => formatDisplayDateTime(value, "") },
    );
    try {
      await writeTableToClipboard(table);
      setCopyNotice(items.length === 1 ? "Copied" : `Copied ${items.length} calls`);
      window.setTimeout(() => setCopyNotice(""), 1600);
      if (rowId != null) {
        setJustCopiedRowId(rowId);
        window.setTimeout(() => setJustCopiedRowId((current) => (current === rowId ? null : current)), 1600);
      }
    } catch { setError("Unable to copy calls. Please allow clipboard access and try again."); }
  };

  // Keep the Calls-page filters mutually exclusive in the same way as the
  // Dashboard: an individual agent scope cannot be combined with a Lead
  // Number/Extension scope. Unlike the Dashboard, Lead Number and Extension
  // may be used together (they narrow the same call-line scope), so only
  // Agent is exclusive with "either of the other two", not with each other.
  const hasAgentFilter = draftAgentFilter !== "all";
  const hasLeadOrExtensionFilter = Boolean(
    draftCapturedNumberFilter || draftExtensionFilter,
  );

  // "More logs" on a call: every log for that call's client phone number
  // (no dates), shown in a popup that reuses the Call Records table.
  const openMoreLogs = async (call) => {
    const phone = String(call.clientPhoneNumber || "").trim();
    if (!phone) return;
    setPhoneLogs([]);
    setPhoneLogsMissedCount(0);
    setPhoneLogsSearched(phone);
    setPhoneLogsError("");
    setPhoneLogsOpen(true);
    setPhoneLogsLoading(true);
    try {
      const result = await getCallsByClientPhone(phone);
      setPhoneLogs(result.records);
      setPhoneLogsMissedCount(result.missedCallsCount);
    } catch (searchError) {
      setPhoneLogsError(searchError.message || "Unable to load call logs.");
    } finally {
      setPhoneLogsLoading(false);
    }
  };

  // The Call Records table, parameterised by the rows to show. The main
  // table and the "All Logs of Phone Number" popup both render through this,
  // so columns, cells, edit dialogs and Submit are one implementation.
  const renderCallsTable = (rows, { moreLogs = true } = {}) => {
    // "More logs" shows beside the checkbox / copy buttons on a call whose
    // client has more than 2 calls (the "Total Calls" under Client ID). The
    // popup's own table never shows it.
    const hasMoreLogs = (call) => moreLogs && !call.isTaskRecord && Boolean(call.clientPhoneNumber) &&
      (clientCallCounts.get(String(call.clientId || "").trim()) || 0) > 2;
    const selectionWidth = rows.some(hasMoreLogs) ? MORE_LOGS_SELECTION_WIDTH : 66;
    const tableStickyStyle = {
      ...Object.fromEntries(callsStickyOffsets.map((offset, index) => [
        `--calls-sticky-offset-${index}`, `${offset + selectionWidth - 50}px`,
      ])),
      "--calls-selection-width": `${selectionWidth}px`,
    };
    return (
          <TableScrollWrap className="calls-table-wrap">
            <table
              className={`calls-table calls-table-expanded calls-priority-table calls-priority-table-${stageView} ${viewByPriority ? "priority-columns-visible" : ""}`}
              style={tableStickyStyle}
              ref={pinCallColumns}
            >
              <colgroup>
                {callsTableColumnWidths.map((width, index) => (
                  <col key={index} style={{ width: index === 0 ? selectionWidth : width }} />
                ))}
              </colgroup>
              <thead>
                <tr className="calls-table-group-row">
                  <th colSpan={7} data-call-sticky-group="call" className="group-call">
                    Call
                  </th>
                  {viewByPriority && (
                    <th colSpan={2} data-call-sticky-group="priority" className="group-call">
                      Priority
                    </th>
                  )}
                  <th colSpan={10} className="group-details">Call details</th>
                  {stageView === "stage_1_2" ? (
                    <>
                      <th
                        colSpan={STAGE1_COLUMNS.length}
                        className="group-stage1"
                      >
                        Stage 1 &middot; AI Intake
                      </th>
                      <th
                        colSpan={STAGE2_COLUMNS.length + 1}
                        className="group-stage2"
                      >
                        Stage 2 &middot; Action Tracking
                      </th>
                    </>
                  ) : (
                    <>
                      <th
                        colSpan={STAGE1_COLUMNS.length}
                        className="group-stage1"
                      >
                        Stage 1 &middot; AI Intake
                      </th>
                      <th
                        colSpan={STAGE2_COLUMNS.length}
                        className="group-stage2"
                      >
                        Stage 2 &middot; Action Tracking
                      </th>
                      <th
                        colSpan={STAGE3_COLUMNS.length}
                        className="group-stage3"
                      >
                        Stage 3 &middot; Case Status
                      </th>
                      <th
                        colSpan={STAGE4_COLUMNS.length + 1}
                        className="group-stage4"
                      >
                        Stage 4 &middot; Google Review
                      </th>
                    </>
                  )}
                </tr>
                <tr>
                  <th data-call-selection-column className="calls-selection-header col-call"><label className="calls-selection-header-label"><input type="checkbox" aria-label="Select all displayed calls" checked={rows.length > 0 && rows.every((call) => selectedCallIds.has(call.id))} onChange={(event) => setSelectedCallIds(event.target.checked ? new Set(rows.map((call) => call.id)) : new Set())} /><span>Copy / Repeated Logs</span></label></th>
                  <th className="col-call" {...staticCallColumn(0)}>#</th>
                  <th className="col-call" {...staticCallColumn(1)}>Date</th>
                  <th className="col-call" {...staticCallColumn(2)}>Enquiry Open Duration</th>
                  <th className="col-call" {...staticCallColumn(3)}>Timestamp</th>
                  <th className="col-call" {...staticCallColumn(4)}>Transcript</th>
                  <th className="calls-audio-header col-call" {...staticCallColumn(5)}>
                    Audio
                  </th>
                  {viewByPriority && (
                    <>
                      <th className="col-call" {...staticPriorityColumn(6)}>Lead Score</th>
                      <th className="col-call" {...staticPriorityColumn(7)}>Business Opportunity</th>
                    </>
                  )}
                  <th className="col-details" {...staticCallDetailsColumn(0)}>Call_ID</th>
                  <th className="col-details" {...staticCallDetailsColumn(1)}>Client ID</th>
                  <th className="col-details" {...staticCallDetailsColumn(2)}>Lead ID</th>
                  <th className="col-details" {...staticCallDetailsColumn(3)}>Client Phone Number</th>
                  <th className="col-details" {...staticCallDetailsColumn(4)}>Agent</th>
                  <th className="col-details" {...staticCallDetailsColumn(5)}>Ext Number</th>
                  <th className="col-details" {...staticCallDetailsColumn(6)}>Direction</th>
                  <th className="col-details" {...staticCallDetailsColumn(7)}>Call Type</th>
                  <th className="col-details" {...staticCallDetailsColumn(8)}>Duration</th>
                  <th className="col-details" {...staticCallDetailsColumn(9)}>Manual Data Score</th>
                  {primaryStageColumns.map((field) => (
                    <th key={field.key} className={primaryStageHeaderClass(field.key)}>
                      <ColumnHeaderLabel field={field} />
                    </th>
                  ))}
                  {finalStageColumns.map((field) => (
                    <th
                      key={field.key}
                      className={
                        stageView === "stage_1_2"
                          ? "stage2-column-header"
                          : "stage4-column-header"
                      }
                    >
                      <ColumnHeaderLabel field={field} />
                    </th>
                  ))}
                  {stageView === "stage_3_4" && (
                    <th className="stage4-column-header calls-actions-column">Actions</th>
                  )}
                </tr>
              </thead>
              <tbody>
                {rows.map((call, index) => (
                  <tr
                    key={call.id}
                    data-call-row={call.callId || undefined}
                    className={[
                      call.isTaskRecord ? "calls-task-record" : "",
                      highlightCallIds.includes(call.callId) ? "calls-focused-row" : "",
                      // A new calendar day starts here: draw the date divider.
                      index > 0 && dayKey(call.date) !== dayKey(rows[index - 1].date) ? "calls-date-boundary" : "",
                    ].filter(Boolean).join(" ")}
                  >
                    <td data-call-selection-column>
                      <div className="calls-selection-cell"><input type="checkbox" aria-label={`Select ${call.call_reference || call.id}`} checked={selectedCallIds.has(call.id)} onChange={() => setSelectedCallIds((current) => { const next = new Set(current); next.has(call.id) ? next.delete(call.id) : next.add(call.id); return next; })} /><button type="button" className={`icon-action-btn${justCopiedRowId === call.id ? " icon-action-btn-copied" : ""}`} title="Copy call row" onClick={() => copyCalls([call], call.id)}><i className={justCopiedRowId === call.id ? "fa-solid fa-check" : "fa-regular fa-copy"} /></button>{hasMoreLogs(call) && <button type="button" className="table-action calls-more-logs-btn" title={`All logs for ${call.clientPhoneNumber}`} onClick={() => openMoreLogs(call)}>More logs</button>}</div>
                    </td>
                    <td {...staticCallColumn(0)}>{index + 1}</td>
                    <td {...staticCallColumn(1)}>
                      {call.isTaskRecord ? (
                        <span>{formatDisplayDateTime(call.date)}</span>
                      ) : (
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
                          {formatDisplayDateTime(call.date)}
                        </span>
                      )}
                      {!call.isTaskRecord && call.analysisId && (() => {
                        const { submitState, isCompleted } = rowSubmitInfo(call);
                        return <CallSaveStatus state={submitState} autosave={autosave[call.id]} completed={isCompleted} />;
                      })()}
                    </td>
                    <td {...staticCallColumn(2)}>
                      {call.isTaskRecord ? (
                        <span>{enquiryOpenDuration(call)}</span>
                      ) : (
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
                      )}
                    </td>
                    <td {...staticCallColumn(3)}>
                      <span
                        className="calls-cell-clickable"
                        title={call.timestamp}
                      >
                        {call.timestamp || "-"}
                      </span>
                    </td>
                    <td {...staticCallColumn(4)}>
                      <button
                        type="button"
                        className="icon-action-btn"
                        onClick={async () => {
                          try {
                            const content = await getCallContent(call.callId);
                            setTranscriptModalCall({ ...call, transcript: content.transcript || "No transcript available." });
                          } catch (err) {
                            setError(err.message || "Failed to load transcript.");
                          }
                        }}
                        disabled={!call.callId}
                        title="View transcript"
                      >
                        <i className="fa-regular fa-file-lines" />
                      </button>
                    </td>
                    <td {...staticCallColumn(5)}>
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
                    {viewByPriority && (
                      <>
                        <td {...staticPriorityColumn(6)}>
                          <LeadScoreCell item={call} />
                        </td>
                        <td {...staticPriorityColumn(7)}>
                          <BusinessOpportunityCell item={call} />
                        </td>
                      </>
                    )}
                    <td {...staticCallDetailsColumn(0)}>
                      {call.isTaskRecord ? (
                        <span className="status calls-task-label">
                          {call.recordLabel}
                        </span>
                      ) : (
                        <div className="calls-call-id-cell">
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
                          {call.linkedTasks?.map((linked) => (
                            <span
                              key={`${linked.type}-${linked.id}`}
                              className={`status calls-task-label calls-linked-task-badge calls-linked-task-${linked.type}`}
                              title={`Also logged as a ${linked.label.toLowerCase()}`}
                            >
                              {linked.label}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td {...staticCallDetailsColumn(1)}>
                      {call.clientId ? (
                        <div className="calls-client-id-cell">
                          <ClientIdCell
                            clientId={call.clientId}
                            onClientUpdated={applyClientProfileUpdate}
                          />
                          <span className="calls-client-total">
                            Total Calls:{" "}
                            {clientCallCounts.get(
                              String(call.clientId).trim(),
                            ) || 0}
                          </span>
                        </div>
                      ) : (
                        "-"
                      )}
                    </td>
                    <td {...staticCallDetailsColumn(2)}>
                      <LeadIdCell leadId={call.leadId} />
                    </td>
                    <td {...staticCallDetailsColumn(3)}>
                      {call.clientPhoneNumber || "-"}
                    </td>
                    <td {...staticCallDetailsColumn(4)}>
                      {call.isTaskRecord ? (
                        <span>{call.agent || "-"}</span>
                      ) : (
                        <span
                          className="calls-cell-clickable"
                          title={call.agent}
                          onClick={() =>
                            openCellModal(
                              "Agent",
                              "front_desk_person_name",
                              // Edit from the raw self-identified value, not
                              // the displayed one -- the display falls back
                              // to the extension-assignment name when
                              // nobody has actually typed one in yet, and
                              // starting the edit from that fallback made
                              // every self-identification save fail with
                              // "updated by another user" (the stored
                              // column was still blank).
                              call.rawAgent,
                              call.id,
                              call.callId,
                            )
                          }
                        >
                          {call.agent || "-"}
                        </span>
                      )}
                    </td>
                    <td {...staticCallDetailsColumn(5)}>
                      <span title={call.extension || "-"}>
                        {call.extension || "-"}
                      </span>
                    </td>
                    <td {...staticCallDetailsColumn(6)}>
                      {call.isTaskRecord ? (
                        <span>{call.direction}</span>
                      ) : (
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
                      )}
                    </td>
                    <td {...staticCallDetailsColumn(7)}>
                      {call.isTaskRecord ? (
                        <span className="muted">Task record</span>
                      ) : (
                        <span
                          className={`call-kind-badge ${getCallKind(call).toLowerCase()}`}
                        >
                          {getCallKind(call)}
                        </span>
                      )}
                    </td>
                    <td {...staticCallDetailsColumn(8)}>
                      {call.isTaskRecord ? (
                        <span>-</span>
                      ) : (
                        <span title="Actual audio recording length">
                          {call.duration || "-"}
                        </span>
                      )}
                    </td>
                    <td {...staticCallDetailsColumn(9)}>
                      {call.isTaskRecord ? (
                        <span className="muted">—</span>
                      ) : (
                        <div className="manual-score-cell">
                          {call.raw?.manual_data_score != null && (
                            <button
                              type="button"
                              className="manual-score-value"
                              onClick={() => setScoreModalCall(call.id)}
                              aria-label="View manual data score and reason"
                            >
                              {Number(call.raw.manual_data_score).toFixed(1)}
                              <small>/10</small>
                            </button>
                          )}
                          <button
                            type="button"
                            disabled={scoringManualData[call.id]}
                            onClick={() => runManualDataScore(call)}
                          >
                            {scoringManualData[call.id]
                              ? "Scoring…"
                              : call.raw?.manual_data_score != null
                                ? "Re-score"
                                : "Score"}
                          </button>
                        </div>
                      )}
                    </td>
                    {primaryStageColumns.map((field) => (
                      <td
                        key={field.key}
                        className={`calls-cell-wide ${field.key === "fd_a_call_summary" ? "calls-call-summary-cell" : ""}`}
                      >
                        {renderStageCell(call, field)}
                      </td>
                    ))}
                    {finalStageColumns.map((field) => (
                      <td key={field.key} className="calls-cell-wide">
                        {renderStageCell(call, field)}
                      </td>
                    ))}
                    {stageView === "stage_3_4" && (
                      <td className="calls-actions-column">
                        {call.isTaskRecord ? (
                          <span className="muted">Task record</span>
                        ) : call.analysisId ? (
                          (() => {
                            const { submitState, isCompleted, hasDeletedData } = rowSubmitInfo(call);
                            return (
                              <div className="calls-completion-cell">
                                {hasDeletedData && <span className="calls-data-deleted-notice">IMP – Data deleted</span>}
                                <div className="calls-action-row">
                                  <CallSubmitControls
                                    state={submitState}
                                    autosave={autosave[call.id]}
                                    onSubmit={() => submitCall(call)}
                                  />
                                  <button
                                    type="button"
                                    className={`primary-button leads-row-action calls-completion-indicator${
                                      isCompleted ? " completed-button" : ""
                                    }`}
                                    title="Automatically set from Case Status Change once Submit is clicked"
                                    disabled
                                  >
                                    {isCompleted ? "Completed" : "Mark completed"}
                                  </button>
                                  <button
                                    type="button"
                                    className="icon-action-btn"
                                    title="Edit"
                                    disabled={!call.callId}
                                    onClick={() => openEditImportModal(call)}
                                  >
                                    <i className="fa-solid fa-pen" />
                                  </button>
                                </div>
                              </div>
                            );
                          })()
                        ) : (
                          <span className="muted">Restricted</span>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
                {!rows.length && (
                  <tr>
                    <td
                      colSpan={
                        15 +
                        (viewByPriority ? 2 : 0) +
                        (stageView === "stage_1_2"
                          ? STAGE1_COLUMNS.length + STAGE2_COLUMNS.length
                          : STAGE1_COLUMNS.length +
                            STAGE2_COLUMNS.length +
                            STAGE3_COLUMNS.length +
                            STAGE4_COLUMNS.length)
                      }
                      className="muted"
                    >
                      No calls in this date range.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableScrollWrap>
    );
  };

  return (
    <section ref={tableRoot} className="process-page calls-fill-page">
      {/* Draft controls stay local until Apply, preventing partial selections from changing results. */}
      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector value={customDraft} onChange={setCustomDraft} />

        <div className="calls-filter-group calls-agent-filter">
          <span className="calls-toolbar-label">Agent</span>
          <div className="date-input calls-toolbar-select">
            <select
              value={draftAgentFilter}
              disabled={hasLeadOrExtensionFilter}
              onChange={(event) => {
                const agent = event.target.value;
                setDraftAgentFilter(agent);
                if (agent !== "all") {
                  setDraftCapturedNumberFilter("");
                  setDraftExtensionFilter("");
                }
              }}
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
              const number = event.target.value;
              setDraftCapturedNumberFilter(number);
              if (number) setDraftAgentFilter("all");
            }}
            disabled={hasAgentFilter || !capturedNumberOptions.length}
          >
            <option value="">
              {capturedNumberOptions.length
                ? "Select Number"
                : "No numbers available"}
            </option>
            {capturedNumberOptions.map((number) => (
              <option key={number} value={number}>
                {number}
              </option>
            ))}
          </select>
        </div>

        <label className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Extension</span>
          <select
            className="calls-filter-select"
            value={draftExtensionFilter}
            onChange={(event) => {
              const extension = event.target.value;
              setDraftExtensionFilter(extension);
              if (extension) setDraftAgentFilter("all");
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
        </label>

        <label className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Call Direction</span>
          <select
            className="calls-filter-select"
            value={draftDirectionFilter}
            onChange={(event) => setDraftDirectionFilter(event.target.value)}
          >
            <option value="">All Directions</option>
            <option value="Inbound">Inbound</option>
            <option value="Outbound">Outbound</option>
          </select>
        </label>

        <label className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Client number</span>
          <input
            value={draftClientNumberFilter}
            onChange={(event) => setDraftClientNumberFilter(event.target.value)}
            placeholder="Search number"
          />
        </label>

        <label className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Client ID</span>
          <input
            value={draftClientIdFilter}
            onChange={(event) => setDraftClientIdFilter(event.target.value)}
            placeholder="CL-000123"
          />
        </label>

        <label className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Call ID</span>
          <input
            value={draftCallIdFilter}
            onChange={(event) => setDraftCallIdFilter(event.target.value)}
            placeholder="Search Call_ID"
          />
        </label>

        <label className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Call Type</span>
          <select
            className="calls-filter-select"
            value={draftCallKindFilter}
            onChange={(event) => setDraftCallKindFilter(event.target.value)}
          >
            <option value="all">All Calls</option>
            <option value="normal">Normal Calls</option>
            <option value="voicemail">Voicemail Calls</option>
          </select>
        </label>

        <label className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Lead ID</span>
          <input
            value={draftLeadIdFilter}
            onChange={(event) => setDraftLeadIdFilter(event.target.value)}
            placeholder="Search Lead ID"
          />
        </label>
      </ScrollableFilterBelt>

      <CallsFilterBelt
        draft={{ lifecycleStage: draftLifecycleStageFilter, openCase: draftOpenCaseFilter, priorityForm: draftPriorityFormFilter,
                 actionStatus: draftActionStatusFilter, logUpdate: draftStageFilter, followUpDate: draftFollowUpDateFilter }}
        applied={{ lifecycleStage: lifecycleStageFilter, openCase: openCaseFilter, priorityForm: priorityFormFilter,
                  actionStatus: actionStatusFilter, logUpdate: stageFilter, followUpDate: followUpDateFilter }}
        agentName={draftAgentFilter}
        showPriority={canAccessPriorityCases(user)}
        onChange={{
          lifecycleStage: setDraftLifecycleStageFilter, openCase: setDraftOpenCaseFilter, priorityForm: setDraftPriorityFormFilter,
          actionStatus: setDraftActionStatusFilter, logUpdate: setDraftStageFilter, followUpDate: setDraftFollowUpDateFilter,
        }}
        onClear={clearBeltFilters}
        loading={loading}
        followUpControl={(
          <FollowUpDatesPopover
            filters={followUpBeltFilters}
            appliedFilters={followUpFilters}
            selected={draftFollowUpDateFilter}
            onSelect={setDraftFollowUpDateFilter}
            onClear={clearFollowUpDate}
          />
        )}
      />

      <div className="filter-belt-actions">
        <button
          type="button"
          className="calls-lead-metrics-icon"
          onClick={() => setIsLeadScoreMatrixOpen(true)}
          aria-label="Open lead metrics score details"
          title="Lead Metrics Score Details"
        >
          <i className="fa-solid fa-chart-line" />
        </button>
        <button
          type="button"
          className={`calls-priority-button ${viewByPriority ? "active" : ""}`}
          onClick={() => setViewByPriority((active) => !active)}
          aria-pressed={viewByPriority}
        >
          View by Priority
        </button>
        <button
          type="button"
          className="calls-priority-button"
          onClick={() => setIsManualScoreMatrixOpen(true)}
        >
          Manual Score Metrics
        </button>
        <button
          type="button"
          className="leads-add-button"
          onClick={() => setImportOpen(true)}
        >
          Add / Import
        </button>
        <button
          type="button"
          className={`table-action data-filter-toggle ${dataFiltersOpen ? "active" : ""}`}
          onClick={() => setDataFiltersOpen((open) => !open)}
          aria-expanded={dataFiltersOpen}
        >
          <i className="fa-solid fa-filter" /> Filter Data
          {Object.values(columnFilters).filter(Boolean).length + (caseStatusFilter ? 1 : 0)
            ? ` (${Object.values(columnFilters).filter(Boolean).length + (caseStatusFilter ? 1 : 0)})`
            : ""}
        </button>
        <button
          type="button"
          className="table-action calls-export-button"
          onClick={exportCalls}
          title="Export applied Calls filters to Excel"
        >
          <i className="fa-solid fa-file-excel" /> Export to Excel
        </button>
        <button
          type="button"
          className="primary-button calls-toolbar-apply"
          onClick={applyFilters}
          disabled={loading}
        >
          {loading ? "..." : "Apply"}
        </button>
      </div>
      </StickyFilterRegion>

      {closureInfo && <ClosureInfoDialog closure={closureInfo} onClose={() => setClosureInfo(null)} />}
      {relatedOffers[0] && (
        <RelatedClosureDialog
          offer={relatedOffers[0]}
          busy={relatedUi.busy}
          error={relatedUi.error}
          result={relatedUi.result}
          onCancel={nextRelatedOffer}
          onConfirm={confirmRelatedClosure}
          onDone={nextRelatedOffer}
        />
      )}

      <CallRecordsUsageGuide />

      {dataFiltersOpen && (
        <DataFilterPanel
          filters={callDataFilterDefinitions}
          values={{ ...columnFilters, [CASE_STATUS_FILTER_KEY]: caseStatusFilter }}
          onChange={(key, value) =>
            key === CASE_STATUS_FILTER_KEY
              ? setCaseStatusNow(value)
              : setColumnFilters((current) => ({ ...current, [key]: value }))
          }
          onClear={() => {
            setCaseStatusNow("");
            setColumnFilters(
              Object.fromEntries(
                COLUMN_FILTERS.map((filter) => [filter.key, ""]),
              ),
            );
          }}
        />
      )}

      {viewByPriority && (
        <section
          className="calls-priority-metrics"
          aria-label="Lead scoring metrics"
        >
          <article>
            <span>Average Lead Score</span>
            <strong>{prioritySummary.average.toFixed(1)}</strong>
          </article>
          <article>
            <span>High Priority</span>
            <strong>{prioritySummary.high}</strong>
          </article>
          <article>
            <span>Medium Priority</span>
            <strong>{prioritySummary.medium}</strong>
          </article>
          <article>
            <span>Low Priority</span>
            <strong>{prioritySummary.low}</strong>
          </article>
        </section>
      )}
      {(selectedCallIds.size > 0 || copyNotice) && (
        <div className="calls-copy-actions">
          {selectedCallIds.size > 0 && <button type="button" className="table-action primary" onClick={() => copyCalls(displayedCalls.filter((call) => selectedCallIds.has(call.id)))}>Copy ({selectedCallIds.size})</button>}
          {copyNotice && <span>{copyNotice}</span>}
        </div>
      )}

      <CollapsiblePanel id="calls-completion-note" className="calls-completion-note" tone="warning" icon="fa-triangle-exclamation" title="Important note">
        <ul>
          <li>
            All Stage 2 fields are mandatory. Until every Stage 2 field is filled,
            Stage 3 and Stage 4 cannot be edited and <em>Submit</em> stays disabled.
          </li>
          <li>
            When <em>Submit Changes</em> (or <em>Submit</em>) is available, click it to save your
            changes and update the dashboard.{" "}
            <em>Mark completed</em> is automatic, not a button you click: it shows
            green <em>Completed</em> whenever Case Status Change is set to{" "}
            <em>Success PC</em>, <em>Success GE</em>, or <em>Dropped</em>, and blue{" "}
            <em>Mark completed</em> for any other value.
          </li>
          <li>
            <b>Understanding Save and Submit Status</b>
            <ul>
              <li><b>Saved:</b> Your changes have been saved automatically. You do not need to click Submit for these changes.</li>
              <li><b>Unsaved Changes:</b> Some changes still need to be saved. Click Submit Changes to save them.</li>
              <li><b>Submitted:</b> Your Submit action was successful. After refreshing the page, the status may show Saved instead.</li>
              <li><b>Completed:</b> The call meets the completion rules based on Case Status Change. Saving a call does not automatically mean it is completed.</li>
              <li><b>Saving / Submitting:</b> Wait until the operation finishes before making another submission.</li>
              <li><b>Save Failed:</b> Your changes were not saved successfully. Click Retry or Retry Submit and check that the save succeeds.</li>
            </ul>
          </li>
          <li>
            Deleting a Stage 3 or Stage 4 value on a completed call shows{" "}
            <em>IMP – Data deleted</em> beside Submit. Click <em>Submit</em> to save the
            deletion and update the dashboard.
          </li>
          <li>
            Use the pen (<em>Edit</em>) icon to paste this call's row from the offline
            Excel format -- used when FlintDesk is down -- straight into this record;
            you don't need the Call ID.
          </li>
          <li>
            Use <em>Add / Import</em> instead of the pen icon when there's no existing
            call row to edit at all -- no Call ID and no Client Phone Number (e.g. a
            client with no phone number on file, documented entirely offline). Paste
            the Excel data without a Call ID column and a brand-new call record is
            created for it automatically.
          </li>
        </ul>
      </CollapsiblePanel>

      <section className="history-table-card calls-records-card">
        <h2>Call records</h2>
        <p className="muted">Date format: MM-DD-YYYY</p>
        <p className="result-count">
          {loading
            ? "Loading..."
            : pagination
              // `filtered` includes both the paginated analysis rows and
              // newly imported CDR rows which are still waiting for their
              // analysis record.  `total_records`, on the other hand, is
              // the analysis-query total.  Comparing them produced nonsense
              // such as "36 of 31 calls".  Show the range total (analysed
              // total + still-unanalysed calls) and, separately, how many
              // rows this page holds.
              ? `${(pagination.total_records || 0) + (pagination.pending_calls || 0)} calls in range -- ${filtered.length} shown on page ${pagination.page} of ${pagination.total_pages}`
              : `${filtered.length} calls`}
        </p>
        {error && <p className="muted">{error}</p>}
        {callFocus && (
          <p className={`calls-focus-banner${callFocus.state === "missing" || callFocus.state === "error" ? " calls-focus-banner-warn" : ""}`} role="status">
            {callFocus.state === "loading" && "Opening the call from your notification…"}
            {callFocus.state === "found" && focusFoundText(callFocus)}
            {callFocus.state === "missing" && `${callFocus.calls.length > 1 ? "Those calls" : "That call"} could not be found, or you do not have access to ${callFocus.calls.length > 1 ? "them" : "it"}.`}
            {callFocus.state === "error" && "The call could not be loaded. Please try again."}
            {" "}
            <button type="button" className="calls-save-retry" onClick={endCallFocus}>Show my filters</button>
          </p>
        )}
        {loading ? (
          <div className="calls-table-loading">
            <BufferingLoader text="Fetching call records" size="md" />
          </div>
        ) : (
          renderCallsTable(displayedCalls)
        )}
        {pagination && (
          <Pagination ariaLabel="Call records pages" page={page} cursorMode
            hasNext={Boolean(pagination.next_cursor)} loading={loading} onChange={setPage} />
        )}
      </section>

      {/* Placed before the other modals so the cell-edit / transcript / audio
          dialogs opened from these rows stack above it. */}
      {phoneLogsOpen && (
        <div className="modal-backdrop leads-blur-backdrop" onMouseDown={() => setPhoneLogsOpen(false)}>
          <div
            className="modal-card all-phone-logs-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="all-phone-logs-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="leads-edit-heading">
              <h3 id="all-phone-logs-title">All Logs of Phone Number{phoneLogsSearched ? ` \u2013 ${phoneLogsSearched}` : ""}</h3>
              <button
                type="button"
                className="leads-modal-close"
                aria-label="Close All Logs of Phone Number"
                title="Close"
                onClick={() => setPhoneLogsOpen(false)}
              >
                <i className="fa-solid fa-xmark" />
              </button>
            </div>
            {phoneLogsError && <p className="leads-error" role="alert">{phoneLogsError}</p>}
            <div className="all-phone-logs-results">
              {phoneLogsLoading ? (
                <div className="calls-table-loading">
                  <BufferingLoader text="Fetching call logs" size="md" />
                </div>
              ) : phoneLogsSearched ? (
                <>
                  <div className="all-phone-logs-breakdown" aria-label={`Where ${phoneLogsSearched} appears`}>
                    <span className="all-phone-logs-breakdown-title">{phoneLogsSearched} appears</span>
                    <span className="all-phone-logs-chip">
                      <strong>{phoneLogsMissedCount}</strong> {phoneLogsMissedCount === 1 ? "time" : "times"} on the Missed Calls page
                    </span>
                    <span className="all-phone-logs-chip">
                      <strong>{phoneLogs.length}</strong> {phoneLogs.length === 1 ? "time" : "times"} on the Calls page
                    </span>
                  </div>
                  {phoneLogs.length ? (
                    <>
                      <p className="result-count">Calls page records for {phoneLogsSearched}</p>
                      {renderCallsTable(phoneLogs, { moreLogs: false })}
                    </>
                  ) : (
                    <p className="muted">No call logs found for this client phone number.</p>
                  )}
                </>
              ) : (
                <p className="muted">No client phone number selected.</p>
              )}
            </div>
          </div>
        </div>
      )}

      {isLeadScoreMatrixOpen && (
        <LeadScoreMatrixModal onClose={() => setIsLeadScoreMatrixOpen(false)} />
      )}

      {isManualScoreMatrixOpen && (
        <ManualScoreMatrixModal onClose={() => setIsManualScoreMatrixOpen(false)} />
      )}

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
              {cellModal.key === "direction" ? (
                <select
                  className="calls-filter-select"
                  value={cellDraft}
                  onChange={(event) => setCellDraft(event.target.value)}
                  disabled={cellSaving}
                  autoFocus
                >
                  <option value="">Choose</option>
                  {[
                    "Direct Inbound",
                    "Direct Outbound",
                    "Miss Call Outbound",
                    "WA-Inbound",
                    "WA-Outbound",
                    "Leads Outbound",
                  ].map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              ) : cellModal.options?.length ? (
                // Stage 1 fields that have a predefined list of choices render
                // a dropdown inside the modal so the Agent can pick a value.
                <select
                  className="calls-filter-select"
                  value={cellDraft}
                  onChange={(event) => setCellDraft(event.target.value)}
                  disabled={cellSaving}
                  autoFocus
                >
                  <option value="">Choose</option>
                  {cellModal.options.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              ) : cellModal.key === "fd_m_follow_up_dates" ? (
                <FollowUpDatesEditor
                  ref={followUpEditorRef}
                  value={cellDraft}
                  onChange={setCellDraft}
                  disabled={cellSaving}
                />
              ) : cellModal.key === "fd_m_action_due_date" ? (
                <div className="action-due-date-editor">
                  <input className="action-due-date-input" type="text" inputMode="numeric" placeholder="MM-DD-YYYY" value={cellDraft} onChange={(event) => setCellDraft(event.target.value)} disabled={cellSaving} autoFocus />
                  <textarea className="action-due-note-input" placeholder="Add a note (optional)" value={cellModal.dueNote} onChange={(event) => setCellModal((current) => ({ ...current, dueNote: event.target.value }))} disabled={cellSaving} />
                </div>
              ) : cellModal.key === "fd_m_date_entry" ? (
                <input
                  className="cell-modal-textarea"
                  type="text"
                  inputMode="numeric"
                  placeholder="MM-DD-YYYY"
                  value={cellDraft}
                  onChange={(event) => setCellDraft(event.target.value)}
                  disabled={cellSaving}
                  autoFocus
                />
              ) : (
                <textarea
                  className="cell-modal-textarea"
                  value={cellDraft}
                  onChange={(event) => setCellDraft(event.target.value)}
                  disabled={cellSaving}
                  autoFocus
                />
              )}
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

      {summaryModal && (
        <SummaryModal call={summaryModal} title={summaryModal.title} onClose={() => setSummaryModal(null)} />
      )}

      {imagePreview && (
        <div className="call-modal-overlay call-image-overlay" onClick={() => setImagePreview(null)}>
          <img
            className="call-image-preview"
            src={imagePreview.url}
            alt={imagePreview.name || "Attached call evidence"}
            onClick={(event) => event.stopPropagation()}
          />
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
                {formatTranscript(transcriptModalCall.transcript)}
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
                  src={audioUrl}
                  onError={() =>
                    setAudioError("The audio file could not be played.")
                  }
                />
              )}
            </div>
          </div>
        </div>
      )}

      {scoreModalCall &&
        (() => {
          const call = findCallRow(scoreModalCall);
          if (!call) return null;
          const band = getManualScoreBand(call.raw?.manual_data_score);
          return (
            <div
              className="call-modal-overlay"
              onClick={() => setScoreModalCall(null)}
            >
              <div
                className="call-modal-card call-modal-medium"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="call-modal-header">
                  <div>
                    <p className="call-modal-kicker">Manual Data Score</p>
                    <h2>
                      {Number(call.raw?.manual_data_score ?? 0).toFixed(1)}/10
                      {band && <span className={`manual-score-band manual-score-band-${band.key}`}> · {band.label}</span>}
                    </h2>
                  </div>
                  <button
                    type="button"
                    className="call-modal-close"
                    onClick={() => setScoreModalCall(null)}
                  >
                    Close
                  </button>
                </div>
                <div className="call-modal-content">
                  <p className="manual-score-modal-label">Reason</p>
                  <p>
                    {call.raw?.manual_data_score_reason ||
                      "No reason was recorded for this score."}
                  </p>
                </div>
              </div>
            </div>
          );
        })()}

      {importOpen && (
        <div
          className="modal-backdrop leads-blur-backdrop"
          onClick={() => setImportOpen(false)}
        >
          <div
            className="modal-card leads-import-modal"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="call-modal-header">
              <h3>Add / Import Calls</h3>
              <button
                type="button"
                className="call-modal-close"
                onClick={() => setImportOpen(false)}
              >
                Close
              </button>
            </div>
            <p className="muted">
              <strong>Call ID</strong> is optional -- just the number after
              AF# (e.g. 147), in the first column. Only paste one ID column
              -- skip "Call_ID" (the UUID) if your sheet has both.
              <br />
              If a row has a Call ID, that existing call is updated, and any{" "}
              <strong>Date</strong>/<strong>Client Phone Number</strong>{" "}
              column is just a safety check -- it must match that call's real
              date/number, or the row is skipped.
              <br />
              If a row has <strong>no</strong> Call ID (e.g. a client with no
              phone number on file), a brand-new call record is created for
              it instead; any Date/Client Phone Number pasted then becomes
              that new call's actual date/number rather than a check.
              <br />
              Everything else is optional too -- paste as many or as few of
              the columns below as you like; anything left out stays
              unchanged (or blank, for a new call). Attachments can't be
              imported this way.
              <br />
              Importable columns:{" "}
              {CALLS_IMPORT_FIELDS.filter(
                ([key]) => key !== "call_id" && key !== "import_call_date" && key !== "import_client_phone",
              )
                .map(([, label]) => label)
                .join(", ")}
              .
            </p>
            <label className="leads-file-picker">
              <span>Import CSV/TXT File</span>
              <input
                ref={importFileInputRef}
                type="file"
                accept=".csv,.tsv,.txt,text/csv,text/plain"
                onChange={(event) => uploadCallsImportFile(event.target.files?.[0])}
              />
            </label>
            {importSelectedFileName && (
              <div className="leads-selected-file">
                <span>{importSelectedFileName}</span>
                <button
                  type="button"
                  className="table-action"
                  onClick={() => {
                    setImportSelectedFileName("");
                    if (importFileInputRef.current) importFileInputRef.current.value = "";
                  }}
                >
                  Remove file
                </button>
              </div>
            )}
            <p className="leads-import-divider">or copy/paste data from Excel</p>
            <textarea
              rows="9"
              value={importPaste}
              onChange={(event) => setImportPaste(event.target.value)}
              placeholder="Paste header row and data here"
              aria-label="Paste header row and data here"
            />
            <p className="tasks-import-note">
              <em>
                Import works directly from a paste or file -- Validate is
                optional, just a preview of what will be imported.
              </em>
            </p>
            <button
              type="button"
              className="table-action leads-validate-button"
              onClick={() => {
                setImportSelectedFileName("");
                if (importFileInputRef.current) importFileInputRef.current.value = "";
                prepareCallsImport(parsePastedTable(importPaste));
              }}
            >
              Preview pasted data
            </button>
            {importParsed && (
              <div className="tasks-import-feedback">
                {importParsed.mapping?.some(Boolean) && (
                  <>
                    <p>
                      <strong>Mapped source columns</strong>
                    </p>
                    <ul>
                      {importParsed.headers.map((header, sourceIndex) => {
                        if (!importParsed.mapping[sourceIndex]) return null;
                        const destination = CALLS_IMPORT_FIELDS.find(
                          ([key]) => key === importParsed.mapping[sourceIndex],
                        )?.[1];
                        return (
                          <li key={`${header}-${sourceIndex}`}>
                            {header} → {destination}
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}
                {importParsed.unmatchedHeaders?.length > 0 && (
                  <p>
                    <strong>Ignored source columns:</strong>{" "}
                    {importParsed.unmatchedHeaders.join(", ")}
                  </p>
                )}
                {importParsed.problems?.length > 0 && (
                  <p className="leads-error">{importParsed.problems.join(" ")}</p>
                )}
                {!importParsed.problems?.length && (
                  <p>
                    {recordsFromImportTable(importParsed).length} record(s) ready to import.
                    {importParsed.ignoredNonCallRows
                      ? ` ${importParsed.ignoredNonCallRows} pasted text fragment(s) without a Call ID ignored.`
                      : ""}
                  </p>
                )}
              </div>
            )}
            {importResultNotice && <p className="tasks-success">{importResultNotice}</p>}
            {importUpdatedRecords.length > 0 && (
              <div className="import-row-errors" role="status">
                <strong>Updated calls</strong>
                <ul>
                  {importUpdatedRecords.map((record) => (
                    <li key={record.call_id}>
                      {record.call_reference || record.call_id} (Call_ID: {record.call_id})
                      {record.updated_fields?.length
                        ? ` — ${record.updated_fields.join(", ")}`
                        : ""}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {importInvalidRows.length > 0 && (
              <div className="tasks-import-feedback">
                <p>
                  <strong>Rows skipped:</strong>
                </p>
                <ul>
                  {importInvalidRows.map((issue, index) => (
                    <li key={`${issue.row}-${issue.field}-${index}`} className="leads-error">
                      Row {issue.row}
                      {issue.field ? ` (${issue.field})` : ""}: {issue.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="leads-modal-actions">
              <button
                type="button"
                className="table-action"
                onClick={() => setImportOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={
                  importRunning ||
                  (!importPaste.trim() && !importSelectedFileName)
                }
                onClick={runCallsImport}
              >
                {importRunning ? "Importing..." : "Import"}
              </button>
            </div>
          </div>
        </div>
      )}

      {editImportCall && (
        <div
          className="modal-backdrop leads-blur-backdrop"
          onClick={closeEditImportModal}
        >
          <div
            className="modal-card leads-import-modal"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="call-modal-header">
              <h3>
                Edit Call{editImportCall.call_reference ? ` — ${editImportCall.call_reference}` : ""}
              </h3>
              <button
                type="button"
                className="call-modal-close"
                onClick={closeEditImportModal}
              >
                Close
              </button>
            </div>
            <p className="muted">
              Paste this call's row from the offline Excel format, including
              the header row. <strong>Call ID is not needed</strong> -- this
              always updates the call whose Edit icon you clicked; any Call
              ID/Client Call ID column in the paste is ignored.
              <br />
              <strong>Date</strong> and <strong>Client Phone Number</strong>{" "}
              are optional. If included, they must match this call's real
              date/number, or the update is skipped.
              <br />
              Importable columns:{" "}
              {CALLS_IMPORT_FIELDS.filter(
                ([key]) => key !== "call_id" && key !== "import_call_date" && key !== "import_client_phone",
              )
                .map(([, label]) => label)
                .join(", ")}
              .
            </p>
            <textarea
              rows="9"
              value={editImportPaste}
              onChange={(event) => setEditImportPaste(event.target.value)}
              placeholder="Paste header row and this call's data here"
              aria-label="Paste header row and this call's data here"
            />
            <button
              type="button"
              className="table-action leads-validate-button"
              onClick={() => setEditImportParsed(buildEditImportParsed(parsePastedTable(editImportPaste)))}
            >
              Validate
            </button>
            {editImportParsed && (
              <div className="tasks-import-feedback">
                {editImportParsed.mapping?.some(Boolean) && (
                  <>
                    <p>
                      <strong>Mapped source columns</strong>
                    </p>
                    <ul>
                      {editImportParsed.headers.map((header, sourceIndex) => {
                        const mappedKey = editImportParsed.mapping[sourceIndex];
                        if (!mappedKey || mappedKey === "call_id") return null;
                        const destination = CALLS_IMPORT_FIELDS.find(
                          ([key]) => key === mappedKey,
                        )?.[1];
                        return (
                          <li key={`${header}-${sourceIndex}`}>
                            {header} → {destination}
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}
                {editImportParsed.unmatchedHeaders?.length > 0 && (
                  <p>
                    <strong>Ignored source columns:</strong>{" "}
                    {editImportParsed.unmatchedHeaders.join(", ")}
                  </p>
                )}
                {editImportParsed.problems?.length > 0 && (
                  <p className="leads-error">{editImportParsed.problems.join(" ")}</p>
                )}
                {!editImportParsed.problems?.length && (
                  <p>
                    {editImportParsed.rows.length ? "1 record ready to import." : "Nothing to import."}
                  </p>
                )}
              </div>
            )}
            {editImportResultNotice && <p className="tasks-success">{editImportResultNotice}</p>}
            {editImportInvalidRows.length > 0 && (
              <div className="tasks-import-feedback">
                <p>
                  <strong>Problem:</strong>
                </p>
                <ul>
                  {editImportInvalidRows.map((issue, index) => (
                    <li key={`${issue.row}-${issue.field}-${index}`} className="leads-error">
                      {issue.field ? `${issue.field}: ` : ""}{issue.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="leads-modal-actions">
              <button
                type="button"
                className="table-action"
                onClick={closeEditImportModal}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={editImportRunning || !editImportPaste.trim()}
                onClick={runEditImport}
              >
                {editImportRunning ? "Importing..." : "Import"}
              </button>
            </div>
          </div>
        </div>
      )}

    </section>
  );
}

function LeadSourceEditor({ value, onSelect }) {
  const [open, setOpen] = useState(false);
  const [hoveredGroup, setHoveredGroup] = useState(null);
  const hasCanonicalValue = LEAD_SOURCE_OPTIONS.includes(String(value));
  const [customValue, setCustomValue] = useState(
    hasCanonicalValue || value === "-" ? "" : value,
  );
  const saveCustomValue = () => {
    const nextValue = customValue.trim();
    if (nextValue && nextValue !== value) onSelect(nextValue);
  };
  return (
    <div className="calls-stage-editor lead-source-menu">
      <div style={{ position: "relative", minWidth: "160px" }}>
        <button
          type="button"
          className="calls-stage-select"
          style={{ width: "100%" }}
          onClick={() => setOpen((current) => !current)}
          title="Choose Lead Source"
          aria-haspopup="menu"
          aria-expanded={open}
        >
          {hasCanonicalValue ? value : "Choose"} <span aria-hidden="true">⌄</span>
        </button>
        {open && (
          <div
            style={{
              position: "absolute",
              zIndex: 20,
              top: "100%",
              left: 0,
              minWidth: "160px",
              background: "#fff",
              border: "1px solid #d7dce5",
              borderRadius: "6px",
              boxShadow: "0 8px 18px rgba(0,0,0,.12)",
            }}
          >
            <button
              type="button"
              className="calls-stage-select"
              style={{ display: "block", width: "100%", textAlign: "left" }}
              onClick={() => {
                onSelect("");
                setCustomValue("");
                setOpen(false);
              }}
            >
              Choose
            </button>
            {LEAD_SOURCE_GROUPS.map((group) => (
              <div
                key={group.label}
                style={{ position: "relative" }}
                onMouseEnter={() => setHoveredGroup(group.label)}
                onMouseLeave={() => setHoveredGroup(null)}
              >
                <button
                  type="button"
                  className="calls-stage-select"
                  style={{ width: "100%", textAlign: "left" }}
                >
                  {group.label} ›
                </button>
                {hoveredGroup === group.label && (
                  <div
                    style={{
                      position: "absolute",
                      left: "100%",
                      top: 0,
                      minWidth: "190px",
                      background: "#fff",
                      border: "1px solid #d7dce5",
                      borderRadius: "6px",
                      boxShadow: "0 8px 18px rgba(0,0,0,.12)",
                    }}
                  >
                    {group.options.map((option) => (
                      <button
                        key={option}
                        type="button"
                        className="calls-stage-select"
                        style={{
                          display: "block",
                          width: "100%",
                          textAlign: "left",
                        }}
                        onClick={() => {
                          onSelect(option);
                          setCustomValue(option);
                          setOpen(false);
                        }}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
      <input
        className="calls-stage-input"
        value={customValue}
        placeholder="Type custom"
        aria-label="Type custom Lead Source"
        onChange={(event) => setCustomValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
        onBlur={saveCustomValue}
      />
    </div>
  );
}

function LegacyClientLifecycleModal({ data, loading, onClose }) {
  const client = data.client || {};
  return (
    <div className="call-modal-overlay" onClick={onClose}>
      <div
        className="call-modal-card call-modal-medium"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="call-modal-header">
          <div>
            <p className="call-modal-kicker">Client lifecycle</p>
            <h2>{client.client_id}</h2>
          </div>
          <button type="button" className="call-modal-close" onClick={onClose}>
            Close
          </button>
        </div>
        {loading ? (
          <div className="call-modal-content">
            <p>Loading client history…</p>
          </div>
        ) : (
          <div className="call-modal-content client-lifecycle-content">
            <div className="client-lifecycle-summary">
              <span>
                <b>Client Name</b>
                {client.client_name || "-"}
              </span>
              <span>
                <b>Email ID</b>
                {client.client_email || "-"}
              </span>
              <span>
                <b>Phone</b>
                {client.phone_number || "-"}
              </span>
              <span>
                <b>First call</b>
                {formatDisplayDate(client.first_call_date)}
              </span>
              <span>
                <b>Last call</b>
                {formatDisplayDate(client.last_call_date)}
              </span>
              <span>
                <b>Total calls</b>
                {client.total_calls ?? 0}
              </span>
              <span>
                <b>Status</b>
                {client.client_status || "-"}
              </span>
            </div>
            <h3>Call history</h3>
            <div className="client-lifecycle-list">
              {data.calls?.length ? (
                data.calls.map((call) => (
                  <div key={call.call_id} className="client-lifecycle-row">
                    <span>
                      {formatDisplayDateTime(
                        `${call.call_date || ""} ${call.call_time || ""}`,
                      )}
                    </span>
                    <strong>{call.call_reference || call.call_id}</strong>
                    <small>Call ID: {call.call_id}</small>
                  </div>
                ))
              ) : (
                <p>No calls found for this client.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
