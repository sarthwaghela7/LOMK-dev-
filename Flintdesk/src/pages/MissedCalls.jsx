import { usePersistentTableState } from "../data/persistentTableState.js";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { CollapsiblePanel } from "../components/CollapsiblePanel.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import {
  assignCallTasks,
  deleteTasks,
  formatAudioDuration,
  getCallAudioBlobUrl,
  getDashboardFrontDeskAgents,
  getTasks,
} from "../data/api.js";
import { getDefaultDateRange } from "../data/filterDates.js";
import { formatDisplayDate } from "../data/displayDate.js";
import { ClientIdCell, LeadIdCell } from "../components/IdentifierCells.jsx";
import { AudioModal, SummaryModal, TranscriptModal } from "../components/CallMediaModals.jsx";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";
import { canApproveDependencies, isRestrictedAgent } from "../data/accessControl.js";
import { CLIENT_TYPE_OPTIONS } from "../data/callFieldOptions.js";
import {
  filterMissedCallsByStatus,
  isAttendedMissedCall,
  MISSED_CALL_STATUS_FILTERS,
  missedCallClientNumber,
  repeatedClientNumbers,
} from "../data/missedCallHighlights.js";

// Single sizing source for the Missed Calls table -- with a fixed table
// layout, the colgroup and sticky offsets below use these same values so
// header/body columns cannot drift, matching the pattern already proven on
// the Non Responsive Calls (UnansweredCalls.jsx) table.
const COLUMN_WIDTHS = {
  missed_call_date: 130,
  // Keep the frozen region compact so the horizontally-scrollable call
  // details remain usable on normal laptop screens. Header text wraps rather
  // than taking space away from the moving columns.
  name: 150,
  contact_number: 190,
  transcript: 56,
  audio: 56,
  email: 210,
  client_id: 100,
  lead_id: 110,
  call_id: 130,
  call_duration: 110,
  case_type: 150,
  client_type: 140,
  agent_name_in_call: 150,
  lead_source: 140,
  summary: 320,
  cb_agent: 140,
  cb_called_back_date: 150,
  cb_transcript: 56,
  cb_audio: 56,
  cb_call_id: 130,
  cb_call_duration: 110,
  cb_call_summary: 320,
};
const CHECKBOX_COLUMN_WIDTH = 40;
const OWNER_COLUMN_WIDTH = 170;
const SUBMIT_COLUMN_WIDTH = 130;
// Reuses the Calls page's own wide-cell class (.calls-priority-table
// .calls-cell-wide) for the same truncate-with-ellipsis treatment it
// already gives long text columns there.
const WRAP_FIELDS = new Set(["summary", "cb_call_summary"]);

// The first five columns (Missed Call Date through Audio) stay static/sticky
// while the rest of the row scrolls horizontally, per the reference layout.
const BASE_FIELDS = [
  ["missed_call_date", "Missed Call Date", "date", true],
  ["name", "Name", null, true],
  ["contact_number", "Caller's Number", null, true],
  ["transcript", "Transcription", "transcript", true],
  ["audio", "Audio", "audio", true],
  ["email", "Email"],
  ["client_id", "Client Id"],
  ["lead_id", "Lead Id"],
  ["call_id", "Call Id"],
  ["call_duration", "Call Duration", "duration"],
  ["case_type", "Case Type"],
  ["client_type", "Client Type"],
  ["lead_source", "Lead Source"],
  ["summary", "Call Summary"],
];
// Backend values (task.data.hannah_ai_category, see
// services/hannah_ai_routing.py) mapped to the labels shown under the
// Caller's Number cell and used by the Category filter below.
const HANNAH_AI_CATEGORY_LABELS = {
  front_desk_agent: "Front Desk Agent Call",
  website: "Website Call",
};

// Both views: which agent/staff name was spoken in the transcript.
const AGENT_NAME_IN_CALL_FIELD = ["agent_name_in_call", "Agent name in call"];
const withAgentNameColumn = (fields) => {
  const index = fields.findIndex(([key]) => key === "client_type");
  return [...fields.slice(0, index + 1), AGENT_NAME_IN_CALL_FIELD, ...fields.slice(index + 1)];
};

// Client Type only ever shows one of the dropdown values; anything else
// (blank, free text, unrecognised) is N/A.
const CLIENT_TYPE_BY_LOWERCASE = new Map(CLIENT_TYPE_OPTIONS.map((option) => [option.toLowerCase(), option]));
const displayClientType = (value) => CLIENT_TYPE_BY_LOWERCASE.get(String(value || "").trim().toLowerCase()) || "N/A";

const CALLBACK_FIELDS = [
  ["cb_agent", "Agent"],
  ["cb_called_back_date", "Called back date", "date"],
  ["cb_transcript", "Agent Call Transcription", "cb_transcript"],
  ["cb_audio", "Agent Call Audio", "cb_audio"],
  ["cb_call_id", "Call Id"],
  ["cb_call_duration", "Call Duration", "cb_duration"],
  ["cb_call_summary", "Call Summary"],
];

export default function MissedCalls({ user, sharedAgent = null }) {
  const month = getDefaultDateRange();
  const [draft, setDraft] = usePersistentTableState("MissedCalls.draft", { start: month.start, end: month.end });
  const [filters, setFilters] = usePersistentTableState("MissedCalls.filters", {});
  const [agentFilter, setAgentFilter] = usePersistentTableState("MissedCalls.agentFilter", "");
  // The Agent filter is owned by "Assigning the Calls": the shared selection is both the draft and the applied filter.
  const sharedAgentId = sharedAgent?.id || "";
  useEffect(() => {
    setAgentFilter(sharedAgentId);
    setFilters((current) => {
      if ((current.agent || "") === sharedAgentId) return current;
      const { agent: _previous, ...rest } = current;
      return sharedAgentId ? { ...rest, agent: sharedAgentId } : rest;
    });
  }, [sharedAgentId]);
  // Bound to the filter belt's inputs -- these are drafts. Nothing here
  // affects `items` below until Apply copies them into the matching
  // applied* state, matching the existing draft/filters split this page
  // already uses for the date range and agent.
  const [numberFilter, setNumberFilter] = usePersistentTableState("MissedCalls.numberFilter", "");
  const [callIdFilter, setCallIdFilter] = usePersistentTableState("MissedCalls.callIdFilter", "");
  const [clientIdFilter, setClientIdFilter] = usePersistentTableState("MissedCalls.clientIdFilter", "");
  const [leadIdFilter, setLeadIdFilter] = usePersistentTableState("MissedCalls.leadIdFilter", "");
  const [categoryFilter, setCategoryFilter] = usePersistentTableState("MissedCalls.categoryFilter", "");
  // Applied snapshots -- only these drive `items` below. Set exclusively by
  // the Apply filters button (and reset by Clear), never by the inputs
  // themselves.
  const [appliedNumberFilter, setAppliedNumberFilter] = usePersistentTableState("MissedCalls.appliedNumberFilter", "");
  const [appliedCallIdFilter, setAppliedCallIdFilter] = usePersistentTableState("MissedCalls.appliedCallIdFilter", "");
  const [appliedClientIdFilter, setAppliedClientIdFilter] = usePersistentTableState("MissedCalls.appliedClientIdFilter", "");
  const [appliedLeadIdFilter, setAppliedLeadIdFilter] = usePersistentTableState("MissedCalls.appliedLeadIdFilter", "");
  const [appliedCategoryFilter, setAppliedCategoryFilter] = usePersistentTableState("MissedCalls.appliedCategoryFilter", "");
  // Pending / Completed / Same Client Number: one button at a time, held as a
  // draft until Apply like every other filter-belt control.
  const [statusFilter, setStatusFilter] = usePersistentTableState("MissedCalls.statusFilter", "");
  const [appliedStatusFilter, setAppliedStatusFilter] = usePersistentTableState("MissedCalls.appliedStatusFilter", "");
  const [view, setView] = usePersistentTableState("MissedCalls.view", "hannah_ai");
  const [rows, setRows] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState(new Set());
  const [pendingOwner, setPendingOwner] = useState({});
  const [bulkOwner, setBulkOwner] = useState("");
  const [savingIds, setSavingIds] = useState(new Set());
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [transcriptModalCall, setTranscriptModalCall] = useState(null);
  const [summaryModal, setSummaryModal] = useState(null);
  const [audioModalCall, setAudioModalCall] = useState(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState("");

  const canManage = canApproveDependencies(user);
  const isViewOnlyAgent = isRestrictedAgent(user);
  const isCalledBack = view === "called_back";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setRows(
        await getTasks({
          ...filters,
          task_type: "missed_call",
          // Both views need the attended state. Hannah AI receives only a
          // lightweight flag, while Called Back also receives its detail
          // columns below.
          include_callback_status: "true",
          ...(isCalledBack ? { include_callback: "true" } : {}),
        }),
      );
    } catch (exception) {
      setError(exception.message || "Unable to load missed calls.");
    } finally {
      setLoading(false);
    }
  }, [filters, isCalledBack]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    // Shared, read-only agent directory -- Agents may view this page but are
    // not authorized to read the Settings user-management endpoint, so this
    // must stay the same source UnansweredCalls.jsx already uses, not
    // getFDUsers().
    getDashboardFrontDeskAgents()
      .then((data) => setAgents(data || []))
      .catch((exception) => setError(exception.message));
  }, []);

  useEffect(() => {
    setSelected(new Set());
  }, [rows]);

  const filteredItems = useMemo(() => {
    const numberDigits = appliedNumberFilter.replace(/\D/g, "");
    const callIdNeedle = appliedCallIdFilter.trim().toLowerCase();
    const clientIdNeedle = appliedClientIdFilter.trim().toLowerCase();
    const leadIdNeedle = appliedLeadIdFilter.trim().toLowerCase();
    return rows.filter((row) => {
      if (numberDigits && !String(row.data?.contact_number || "").replace(/\D/g, "").includes(numberDigits))
        return false;
      if (
        callIdNeedle &&
        !`${row.data?.call_reference || ""} ${row.data?.call_id || ""}`.toLowerCase().includes(callIdNeedle)
      )
        return false;
      if (clientIdNeedle && !String(row.data?.client_id || "").toLowerCase().includes(clientIdNeedle))
        return false;
      if (leadIdNeedle && !String(row.data?.lead_id || "").toLowerCase().includes(leadIdNeedle))
        return false;
      if (appliedCategoryFilter && row.data?.hannah_ai_category !== appliedCategoryFilter) return false;
      return true;
    });
  }, [rows, appliedNumberFilter, appliedCallIdFilter, appliedClientIdFilter, appliedLeadIdFilter, appliedCategoryFilter]);

  // Repeated client numbers are counted once, over the rows that survive every
  // other active filter (date, agent, number, IDs, category) but before the
  // Pending/Completed/Same Client Number button, so the badge on a row does
  // not disappear just because its twin was filtered out by that button.
  const repeatedNumbers = useMemo(() => repeatedClientNumbers(filteredItems), [filteredItems]);
  const items = useMemo(
    () => filterMissedCallsByStatus(filteredItems, appliedStatusFilter, repeatedNumbers),
    [filteredItems, appliedStatusFilter, repeatedNumbers],
  );

  const fields = isCalledBack ? [...withAgentNameColumn(BASE_FIELDS), ...CALLBACK_FIELDS] : withAgentNameColumn(BASE_FIELDS);
  const stickyFields = fields.filter(([, , , sticky]) => sticky).map(([key]) => key);
  // Column 0 is always the checkbox slot -- kept in the layout even for a
  // view-only Agent (just left empty, see the render below) so the sticky
  // index math, and therefore the Calls page's built-in divider rule at
  // index 5, never has to shift by role.
  const stickyWidths = [CHECKBOX_COLUMN_WIDTH, ...stickyFields.map((key) => COLUMN_WIDTHS[key])];
  const stickyOffsets = stickyWidths.reduce(
    (offsets, width) => [...offsets, offsets[offsets.length - 1] + width],
    [0],
  );
  // Reuses the Calls page's own --calls-sticky-offset-* variables and
  // [data-call-sticky-column] CSS (App.css, ".calls-priority-table") --
  // this table carries the same classes, so no separate CSS is needed here.
  const stickyTableStyle = Object.fromEntries(
    stickyOffsets.map((offset, index) => [`--calls-sticky-offset-${index}`, `${offset}px`]),
  );
  // A fixed-layout table still needs an explicit total width for its colgroup
  // widths to be authoritative. Without it, the browser content-sized the
  // table while sticky offsets remained fixed, allowing Caller Number to
  // paint over the frozen Name column during horizontal scrolling.
  const tableWidth =
    CHECKBOX_COLUMN_WIDTH +
    fields.reduce((total, [key]) => total + (COLUMN_WIDTHS[key] || 160), 0) +
    OWNER_COLUMN_WIDTH +
    (isCalledBack ? 0 : SUBMIT_COLUMN_WIDTH);
  stickyTableStyle["--missed-calls-table-width"] = `${tableWidth}px`;
  const stickyIndexOf = (key) => {
    const index = stickyFields.indexOf(key);
    return index < 0 ? -1 : index + 1;
  };
  // Header color-coding: static/sticky columns are light blue; the base
  // Hannah AI columns (shown in both views) are purple; the extra columns
  // Called Back adds on the right are green -- reusing the exact same three
  // colors the Calls page already uses for its own stage groups.
  const callbackFieldKeys = new Set(CALLBACK_FIELDS.map(([key]) => key));
  const headerColorClass = (key) => {
    if (stickyFields.includes(key)) return "missed-calls-header-static";
    if (callbackFieldKeys.has(key)) return "missed-calls-header-called-back";
    return "missed-calls-header-hannah";
  };

  const toggleSelected = (id) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const openAudio = async (callId, callReference, callDate) => {
    if (!callId) return;
    setAudioModalCall({ id: callReference || callId, date: callDate });
    setAudioUrl("");
    setAudioError("");
    setAudioLoading(true);
    try {
      setAudioUrl(await getCallAudioBlobUrl(callId));
    } catch (exception) {
      setAudioError(exception.message || "Unable to load audio.");
    } finally {
      setAudioLoading(false);
    }
  };

  const submitRow = async (row) => {
    const ownerId = pendingOwner[row.id] ?? row.owner_id ?? "";
    if (!ownerId) return;
    const isReassignment = Boolean(row.owner_id && row.owner_id !== ownerId);
    setSavingIds((current) => new Set(current).add(row.id));
    setError("");
    setNotice("");
    try {
      await assignCallTasks("missed_call", [row.id], ownerId);
      const ownerName = agents.find((agent) => agent.id === ownerId)?.name || row.owner_name || "";
      // Make the selected owner visible immediately, then reload to confirm
      // the durable server state.
      setPendingOwner((current) => {
        const next = { ...current };
        delete next[row.id];
        return next;
      });
      setRows((current) => current.map((item) => item.id === row.id
        ? { ...item, owner_id: ownerId, owner_name: ownerName, submitted_at: new Date().toISOString() }
        : item));
      setNotice(
        isReassignment
          ? "Owner updated, task assigned, and the new agent was notified."
          : "Missed call assigned and the agent was notified.",
      );
      // The row now reflects the committed response locally. Avoid a full
      // table reload here: it resets the page/loading state every time an
      // owner submits a single missed call.
    } catch (exception) {
      setError(exception.message || "Unable to assign missed call.");
    } finally {
      setSavingIds((current) => {
        const next = new Set(current);
        next.delete(row.id);
        return next;
      });
    }
  };

  const submitBulk = async () => {
    if (!bulkOwner || !selected.size) return;
    setBulkSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await assignCallTasks("missed_call", [...selected], bulkOwner);
      if (result?.assigned !== selected.size) {
        throw new Error("Not every selected missed call was assigned. Please refresh and try again.");
      }
      setNotice(`${selected.size} missed call(s) assigned and the agent was notified.`);
      // Drop any unsaved per-row owner draft for rows this bulk action just
      // assigned -- otherwise a stale draft would keep overriding the
      // freshly reloaded owner in the row's own dropdown (see currentOwner).
      const submittedIds = selected;
      setPendingOwner((current) => {
        const next = { ...current };
        submittedIds.forEach((id) => delete next[id]);
        return next;
      });
      // Show the new owner on those rows straight away, like a single Submit does: no full reload of the table.
      const ownerName = agents.find((agent) => agent.id === bulkOwner)?.name || "";
      const assignedAt = new Date().toISOString();
      setRows((current) => current.map((item) => (submittedIds.has(item.id)
        ? { ...item, owner_id: bulkOwner, owner_name: ownerName, submitted_at: assignedAt }
        : item)));
      setSelected(new Set());
      setBulkOwner("");
    } catch (exception) {
      setError(exception.message || "Unable to assign missed calls.");
    } finally {
      setBulkSaving(false);
    }
  };

  const deleteBulk = async () => {
    if (!selected.size) return;
    if (!window.confirm(`Delete ${selected.size} selected missed call(s)? This cannot be undone.`)) return;
    setBulkDeleting(true);
    setError("");
    setNotice("");
    try {
      await deleteTasks("missed_call", [...selected]);
      setNotice(`${selected.size} missed call(s) deleted.`);
      setSelected(new Set());
      await load();
    } catch (exception) {
      setError(exception.message || "Unable to delete missed calls.");
    } finally {
      setBulkDeleting(false);
    }
  };

  const renderCell = (row, key, fieldType) => {
    const value = row.data?.[key] ?? "";
    const callback = row.data?.callback || null;
    if (key === "client_id") return <ClientIdCell clientId={value} />;
    if (key === "lead_id") return <LeadIdCell leadId={value} />;
    if (key === "contact_number") {
      const categoryLabel = HANNAH_AI_CATEGORY_LABELS[row.data?.hannah_ai_category];
      const isRepeatedNumber = repeatedNumbers.has(missedCallClientNumber(row));
      const numberWithBadge = (
        <span className="missed-calls-number-line">
          <span>{value || "-"}</span>
          {isRepeatedNumber && (
            <span
              className="missed-calls-repeat-badge"
              title="This client phone number appears in multiple call records."
            >
              Repeated
            </span>
          )}
        </span>
      );
      if (!categoryLabel) return numberWithBadge;
      return (
        <span className="missed-calls-agent-cell">
          {numberWithBadge}
          <span
            className="status"
            style={{ color: row.data.hannah_ai_category === "website" ? "#166534" : "#1f4a44" }}
          >
            {categoryLabel}
          </span>
        </span>
      );
    }
    if (key === "client_type") return displayClientType(value);
    if (key === "agent_name_in_call") return value || "N/A";
    if (key === "call_id") return row.data?.call_reference || value || "-";
    if (key === "call_duration") return formatAudioDuration(row.data?.billsec);
    if (key === "cb_agent")
      return callback ? (
        <span className="missed-calls-agent-cell">
          <span>{callback.agent || "-"}</span>
          <span className="status" style={{ color: callback.is_voicemail ? "#b45309" : "#2563eb" }}>
            {callback.is_voicemail ? "Voicemail" : "Answered"}
          </span>
        </span>
      ) : (
        "-"
      );
    if (key === "cb_call_id") return callback?.call_reference || callback?.call_id || "-";
    // Same click-to-expand treatment as the Call Records table's Call
    // Summary column (see Calls.jsx's cell-edit modal) -- here read-only,
    // since summary isn't an editable field on this page.
    if (key === "summary" || key === "cb_call_summary") {
      const summary = key === "cb_call_summary" ? callback?.call_summary : value;
      if (!summary) return "-";
      return (
        <span
          className="calls-cell-clickable"
          title={summary}
          onClick={() =>
            setSummaryModal({
              id:
                key === "cb_call_summary"
                  ? callback?.call_reference || callback?.call_id
                  : row.data?.call_reference || row.data?.call_id,
              summary,
            })
          }
        >
          {summary}
        </span>
      );
    }
    if (fieldType === "date") return formatDisplayDate(value);
    if (fieldType === "cb_duration") return formatAudioDuration(callback?.billsec);
    if (fieldType === "transcript" || fieldType === "cb_transcript") {
      const transcript = fieldType === "cb_transcript" ? callback?.transcript : value;
      return (
        <button
          type="button"
          className="icon-action-btn"
          disabled={!transcript}
          onClick={() =>
            setTranscriptModalCall({
              id:
                fieldType === "cb_transcript"
                  ? callback?.call_reference || callback?.call_id
                  : row.data?.call_reference || row.data?.call_id,
              transcript,
            })
          }
          title="View transcript"
        >
          <i className="fa-regular fa-file-lines" />
        </button>
      );
    }
    if (fieldType === "audio" || fieldType === "cb_audio") {
      const hasAudio = fieldType === "cb_audio" ? callback?.audio : value;
      const callId = fieldType === "cb_audio" ? callback?.call_id : row.data?.call_id;
      const callReference = fieldType === "cb_audio" ? callback?.call_reference : row.data?.call_reference;
      const callDate = fieldType === "cb_audio" ? callback?.called_back_date : row.data?.missed_call_date;
      return (
        <button
          type="button"
          className="icon-action-btn"
          disabled={!hasAudio}
          onClick={() => openAudio(callId, callReference, callDate)}
          title="Play audio"
        >
          <i className="fa-solid fa-play" />
        </button>
      );
    }
    return value || "-";
  };

  return (
    <section className="process-page missed-calls-page">
      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector value={draft} onChange={setDraft} />
        <div className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Caller's Number</span>
          <input
            value={numberFilter}
            onChange={(event) => setNumberFilter(event.target.value)}
            placeholder="Search number"
          />
        </div>
        <div className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Call ID</span>
          <input
            value={callIdFilter}
            onChange={(event) => setCallIdFilter(event.target.value)}
            placeholder="Search call ID"
          />
        </div>
        <div className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Client ID</span>
          <input
            value={clientIdFilter}
            onChange={(event) => setClientIdFilter(event.target.value)}
            placeholder="Search client ID"
          />
        </div>
        <div className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Lead ID</span>
          <input
            value={leadIdFilter}
            onChange={(event) => setLeadIdFilter(event.target.value)}
            placeholder="Search lead ID"
          />
        </div>
        <div className="calls-filter-group calls-inline-filter">
          <span className="calls-toolbar-label">Category</span>
          <select
            className="calls-filter-select"
            value={categoryFilter}
            onChange={(event) => setCategoryFilter(event.target.value)}
          >
            <option value="">All categories</option>
            {Object.entries(HANNAH_AI_CATEGORY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="calls-filter-group missed-calls-status-filter">
          <span className="calls-toolbar-label">Call status</span>
          <div className="calls-stage-view-tabs" role="group" aria-label="Call status filter">
            {Object.entries(MISSED_CALL_STATUS_FILTERS).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`table-action ${statusFilter === value ? "active" : ""}`}
                aria-pressed={statusFilter === value}
                onClick={() => setStatusFilter((current) => (current === value ? "" : value))}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </ScrollableFilterBelt>
      <div className="filter-belt-actions">
        <button
          type="button"
          className="primary-button calls-toolbar-apply"
          onClick={() => {
            setFilters({
              ...(draft.start ? { start_date: draft.start } : {}),
              ...(draft.end ? { end_date: draft.end } : {}),
              ...(agentFilter ? { agent: agentFilter } : {}),
            });
            setAppliedNumberFilter(numberFilter);
            setAppliedCallIdFilter(callIdFilter);
            setAppliedClientIdFilter(clientIdFilter);
            setAppliedLeadIdFilter(leadIdFilter);
            setAppliedCategoryFilter(categoryFilter);
            setAppliedStatusFilter(statusFilter);
          }}
        >
          Apply filters
        </button>
        <button
          type="button"
          className="table-action"
          onClick={() => {
            setDraft({ start: month.start, end: month.end });
            setAgentFilter("");
            setNumberFilter("");
            setCallIdFilter("");
            setClientIdFilter("");
            setLeadIdFilter("");
            setCategoryFilter("");
            setStatusFilter("");
            setAppliedStatusFilter("");
            setAppliedNumberFilter("");
            setAppliedCallIdFilter("");
            setAppliedClientIdFilter("");
            setAppliedLeadIdFilter("");
            setAppliedCategoryFilter("");
            setFilters({});
          }}
        >
          Clear
        </button>
        {canManage && selected.size > 0 && (
          <div className="missed-calls-bulk-actions">
            <select
              className="calls-filter-select"
              value={bulkOwner}
              onChange={(event) => setBulkOwner(event.target.value)}
              aria-label="Bulk owner"
            >
              <option value="">Choose owner</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="primary-button"
              disabled={!bulkOwner || bulkSaving}
              onClick={submitBulk}
            >
              {bulkSaving ? "Assigning..." : `Assign selected (${selected.size})`}
            </button>
            <button
              type="button"
              className="primary-button danger-button"
              disabled={bulkDeleting}
              onClick={deleteBulk}
            >
              {bulkDeleting ? "Deleting..." : `Delete selected (${selected.size})`}
            </button>
          </div>
        )}
        <div className="calls-stage-view-tabs">
          <button
            type="button"
            className={`table-action missed-view-tab missed-view-hannah ${view === "hannah_ai" ? "active" : ""}`}
            onClick={() => setView("hannah_ai")}
          >
            Hannah AI
          </button>
          <button
            type="button"
            className={`table-action missed-view-tab missed-view-called-back ${view === "called_back" ? "active" : ""}`}
            onClick={() => setView("called_back")}
          >
            Called Back
          </button>
        </div>
      </div>
      </StickyFilterRegion>
      {error && <p className="leads-error">{error}</p>}
      {notice && <p className="tasks-success">{notice}</p>}
      {isViewOnlyAgent && (
        <p className="unanswered-view-only-notice" role="status">
          <i className="fa-solid fa-circle-info" aria-hidden="true" />
          This page is view-only for you. You can see all missed calls here, but only a Team Lead, Admin or SME can assign owners or submit.
        </p>
      )}
      <section className="history-table-card">
        <div className="tasks-table-heading">
          <div>
            <h2>{isCalledBack ? "Called Back" : "Hannah AI"}</h2>
            <p className="result-count">{items.length} entries</p>
          </div>
        </div>
        <CollapsiblePanel id="missed-calls-repeat-note" className="missed-calls-repeat-note" title="About the Repeated badge">
          <span className="missed-calls-repeat-badge" aria-hidden="true">Repeated</span>{" "}
          <strong>Repeated:</strong> This badge indicates that the same client phone number appears in multiple call records.
        </CollapsiblePanel>
        {loading ? (
          <div className="calls-table-loading">
            <BufferingLoader text="Fetching missed calls" size="md" />
          </div>
        ) : (
          <TableScrollWrap className="calls-table-wrap">
            {/* calls-table + calls-priority-table: the same two classes the
                Calls page's own table uses, so the sticky header, frozen
                columns, and divider all come from that page's existing CSS
                (App.css) instead of a parallel copy of it. */}
            <table className="calls-table calls-priority-table missed-calls-table" style={stickyTableStyle}>
              <colgroup>
                <col style={{ width: CHECKBOX_COLUMN_WIDTH }} />
                {fields.map(([key]) => (
                  <col key={key} style={{ width: COLUMN_WIDTHS[key] || 160 }} />
                ))}
                <col style={{ width: OWNER_COLUMN_WIDTH }} />
                {!isCalledBack && <col style={{ width: SUBMIT_COLUMN_WIDTH }} />}
              </colgroup>
              <thead>
                <tr>
                  <th data-call-sticky-column={0} className="missed-calls-header-static">
                    {canManage && (
                      <input
                        type="checkbox"
                        aria-label="Select all rows"
                        checked={items.length > 0 && items.every((row) => selected.has(row.id))}
                        onChange={(event) =>
                          setSelected(event.target.checked ? new Set(items.map((row) => row.id)) : new Set())
                        }
                      />
                    )}
                  </th>
                  {fields.map(([key, label]) => {
                    const stickyIndex = stickyIndexOf(key);
                    return (
                      <th
                        key={key}
                        className={headerColorClass(key)}
                        {...(stickyIndex >= 0 ? { "data-call-sticky-column": stickyIndex } : {})}
                      >
                        {label}
                      </th>
                    );
                  })}
                  <th className="missed-calls-header-hannah">Owner</th>
                  {!isCalledBack && <th className="missed-calls-header-hannah">Submit</th>}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => {
                  const currentOwner = pendingOwner[row.id] ?? row.owner_id ?? "";
                  const isSubmittedForCurrentOwner =
                    Boolean(row.submitted_at) && currentOwner === (row.owner_id || "");
                  const isSaving = savingIds.has(row.id);
                  // Attended (green): the backend already restricts a
                  // callback match to an outbound call, to this same
                  // client, made on the missed call's own date (see
                  // _callback_match in routes/flintdesk/tasks.py) -- finding
                  // one at all is the full criteria. Hannah AI receives the
                  // same result as a lightweight callback_matched flag, so
                  // both views apply the identical full-row green state.
                  const isAttended = isAttendedMissedCall(row);
                  const rowStatusClass = isAttended ? "missed-calls-row-attended" : "";
                  return (
                    <tr key={row.id} className={rowStatusClass}>
                      <td data-call-sticky-column={0}>
                        {canManage && (
                          <input
                            type="checkbox"
                            aria-label={`Select row ${row.id}`}
                            checked={selected.has(row.id)}
                            onChange={() => toggleSelected(row.id)}
                          />
                        )}
                      </td>
                      {fields.map(([key, , fieldType]) => {
                        const stickyIndex = stickyIndexOf(key);
                        return (
                          <td
                            key={key}
                            className={
                              WRAP_FIELDS.has(key)
                                ? "calls-cell-wide calls-call-summary-cell"
                                : key === "email"
                                  ? "missed-calls-email-cell"
                                  : ""
                            }
                            {...(stickyIndex >= 0 ? { "data-call-sticky-column": stickyIndex } : {})}
                          >
                            {renderCell(row, key, fieldType)}
                          </td>
                        );
                      })}
                      <td>
                        {/* Called Back is a read-only enrichment view: the
                            owner was already assigned on the Hannah AI page,
                            so just display who that is here, editable or
                            not. */}
                        {canManage && !isCalledBack ? (
                          <select
                            className="calls-stage-select"
                            aria-label="Choose Owner"
                            value={currentOwner}
                            disabled={isSaving}
                            onChange={(event) =>
                              setPendingOwner((current) => ({ ...current, [row.id]: event.target.value }))
                            }
                          >
                            <option value="">Choose owner</option>
                            {agents.map((agent) => (
                              <option key={agent.id} value={agent.id}>
                                {agent.name}
                              </option>
                            ))}
                          </select>
                        ) : (
                          row.owner_name || "-"
                        )}
                      </td>
                      {!isCalledBack && (
                        <td>
                          {canManage ? (
                            <button
                              type="button"
                              className={`primary-button leads-row-action ${isSubmittedForCurrentOwner ? "completed-button" : ""}`}
                              disabled={!currentOwner || isSubmittedForCurrentOwner || isSaving}
                              onClick={() => submitRow(row)}
                            >
                              {isSaving ? "Submitting..." : isSubmittedForCurrentOwner ? "Submitted" : "Submit"}
                            </button>
                          ) : row.submitted_at ? (
                            <span className="missed-calls-submitted-badge">Submitted</span>
                          ) : (
                            "-"
                          )}
                        </td>
                      )}
                    </tr>
                  );
                })}
                {!items.length && (
                  <tr>
                    <td
                      colSpan={fields.length + (isCalledBack ? 2 : 3)}
                      className="muted"
                    >
                      No entries.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableScrollWrap>
        )}
      </section>
      {transcriptModalCall && (
        <TranscriptModal call={transcriptModalCall} onClose={() => setTranscriptModalCall(null)} />
      )}
      {summaryModal && (
        <SummaryModal call={summaryModal} onClose={() => setSummaryModal(null)} />
      )}
      {audioModalCall && (
        <AudioModal
          call={audioModalCall}
          url={audioUrl}
          loading={audioLoading}
          error={audioError}
          onClose={() => {
            if (audioUrl) URL.revokeObjectURL(audioUrl);
            setAudioUrl("");
            setAudioModalCall(null);
          }}
        />
      )}
    </section>
  );
}
