import { usePersistentTableState } from "../data/persistentTableState.js";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import {
  getCallAudioBlobUrl,
  getDashboardFrontDeskAgents,
  getTasks,
} from "../data/api.js";
import { getDefaultDateRange } from "../data/filterDates.js";
import { formatDisplayDate } from "../data/displayDate.js";
import { ClientIdCell, LeadIdCell } from "../components/IdentifierCells.jsx";
import { AudioModal, TranscriptModal } from "../components/CallMediaModals.jsx";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";
import { isRestrictedAgent } from "../data/accessControl.js";

// This is the single sizing source for both Missed Calls and Non-Responsive
// Calls. With a fixed table layout, the colgroup and sticky offsets use these
// same values, so header/body columns cannot drift as cell content changes.
const COLUMN_WIDTHS = {
  name: 140,
  client_id: 100,
  missed_call_date: 110,
  date_added: 150,
  transcript: 56,
  audio: 56,
  lead_id: 120,
  miscall_added_date: 150,
  contact_number: 150,
  call_id: 135,
  summary: 340,
  action_status: 150,
  comments: 340,
  type: 145,
  phone_number: 150,
  email: 220,
  status: 130,
};
const OWNER_COLUMN_WIDTH = 160;
const WRAP_FIELDS = new Set(["summary", "comments"]);

const COLUMNS = {
  missed_call: {
    title: "Missed Calls",
    fields: [
      ["name", "Name", null, true],
      ["client_id", "Client ID", null, true],
      ["missed_call_date", "Missed Call Date", "date", true],
      ["transcript", "Transcript", "transcript", true],
      ["audio", "Audio", "audio", true],
      ["lead_id", "Lead ID"],
      ["miscall_added_date", "Missed Call Added Date", "date"],
      ["contact_number", "Contact Number"],
      ["call_id", "Call ID"],
      ["summary", "Summary"],
      ["action_status", "Action Status"],
      ["comments", "Comments"],
    ],
  },
  non_responsiveness: {
    title: "Non-Responsive Calls",
    fields: [
      ["name", "Name", null, true],
      ["client_id", "Client ID", null, true],
      ["date_added", "Non Responsive Call Added Date", "date", true],
      ["transcript", "Transcript", "transcript", true],
      ["audio", "Audio", "audio", true],
      ["lead_id", "Lead ID"],
      ["type", "Type"],
      ["phone_number", "Phone Number"],
      ["email", "Email"],
      ["call_id", "Call ID"],
      ["summary", "Call Summary"],
      ["status", "Status"],
      ["action_status", "Action Status"],
      ["comments", "Comments"],
    ],
  },
};

export default function UnansweredCalls({ user }) {
  const month = getDefaultDateRange();
  const [draft, setDraft] = usePersistentTableState("UnansweredCalls.draft", { start: month.start, end: month.end });
  const [filters, setFilters] = usePersistentTableState("UnansweredCalls.filters", {});
  const [agentFilter, setAgentFilter] = usePersistentTableState("UnansweredCalls.agentFilter", "");
  // Missed Calls now has its own dedicated page (src/pages/MissedCalls.jsx),
  // so this page only ever shows Non-Responsive Calls -- no toggle needed.
  // Fixed rather than left as persisted state, so a user with an old
  // "missed_call" choice saved from before that page existed doesn't land
  // back on a view this page no longer offers.
  const taskType = "non_responsiveness";
  const [rows, setRows] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [transcriptModalCall, setTranscriptModalCall] = useState(null);
  const [audioModalCall, setAudioModalCall] = useState(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setRows(await getTasks(filters));
    } catch (exception) {
      setError(exception.message || "Unable to load call tasks.");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    // This is a shared, read-only directory endpoint.  Do not use the
    // Settings user-management endpoint here: Agents may view call tasks but
    // are intentionally not authorized to read Settings account data.
    getDashboardFrontDeskAgents()
      .then((data) => setAgents(data || []))
      .catch((exception) => setError(exception.message));
  }, []);

  const isViewOnlyAgent = isRestrictedAgent(user);

  const items = useMemo(
    () => rows.filter((row) => row.task_type === taskType),
    [rows, taskType],
  );
  const definition = COLUMNS[taskType];
  const durations = useMemo(() => {
    // One call may be referenced by more than one manually logged task. Count
    // each matched call once so neither total is inflated by duplicate tasks.
    const seenCallIds = new Set();
    return items.reduce(
      (totals, row) => {
        const callId = row.data?.call_id;
        if (!callId || seenCallIds.has(callId)) return totals;
        seenCallIds.add(callId);
        const seconds = Number(row.data?.billsec) || 0;
        if (row.data?.is_voicemail) totals.voicemail += seconds;
        else totals.total += seconds;
        return totals;
      },
      { total: 0, voicemail: 0 },
    );
  }, [items]);
  const stickyFields = definition.fields
    .filter(([, , , sticky]) => sticky)
    .map(([key]) => key);
  const columnWidths = definition.fields.map(
    ([key]) => COLUMN_WIDTHS[key] || 160,
  );
  const stickyWidths = stickyFields.map((key) => COLUMN_WIDTHS[key]);
  const stickyOffsets = stickyWidths.reduce(
    (offsets, width) => [...offsets, offsets[offsets.length - 1] + width],
    [0],
  );
  const stickyTableStyle = Object.fromEntries(
    stickyOffsets.map((offset, index) => [
      `--unanswered-sticky-offset-${index}`,
      `${offset}px`,
    ]),
  );
  const tableWidth =
    columnWidths.reduce((total, width) => total + width, 0) +
    OWNER_COLUMN_WIDTH;
  const stickyIndexOf = (key) => stickyFields.indexOf(key);

  const openAudio = async (callId, callReference, callDate) => {
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

  // A row is "done" once its phone number has actually been matched to a
  // real call (the missed/non-responsive call was made) -- the manually
  // typed Summary column is a separate note an agent can still add, not a
  // precondition for the call having happened.
  const isRowDone = (row) => Boolean(row.data?.call_id);

  const renderCell = (row, key, fieldType) => {
    const value = row.data?.[key] || "";
    if (key === "client_id") return <ClientIdCell clientId={value} />;
    if (key === "lead_id") return <LeadIdCell leadId={value} />;
    if (key === "call_id") return row.data?.call_reference || value || "-";
    if (fieldType === "transcript")
      return (
        <button
          type="button"
          className="icon-action-btn"
          disabled={!value}
          onClick={() =>
            setTranscriptModalCall({
              id: row.data?.call_reference || row.data?.call_id,
              transcript: value,
            })
          }
          title="View transcript"
        >
          <i className="fa-regular fa-file-lines" />
        </button>
      );
    if (fieldType === "audio")
      return (
        <button
          type="button"
          className="icon-action-btn"
          disabled={!value}
          onClick={() =>
            openAudio(
              row.data?.call_id,
              row.data?.call_reference,
              row.data?.call_date || row.data?.missed_call_date,
            )
          }
          title="Play audio"
        >
          <i className="fa-solid fa-play" />
        </button>
      );
    if (fieldType === "date") return formatDisplayDate(value);
    return value || "-";
  };

  return (
    <section className="process-page unanswered-calls-page">
      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector value={draft} onChange={setDraft} />
        <div className="calls-filter-group">
          <span className="calls-toolbar-label">Agent</span>
          <select
            className="calls-filter-select"
            value={agentFilter}
            onChange={(event) => setAgentFilter(event.target.value)}
          >
            <option value="">All agents</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </div>
      </ScrollableFilterBelt>
      <div className="filter-belt-actions unanswered-calls-toggle">
        <button
          type="button"
          className="primary-button calls-toolbar-apply"
          onClick={() =>
            setFilters({
              ...(draft.start ? { start_date: draft.start } : {}),
              ...(draft.end ? { end_date: draft.end } : {}),
              ...(agentFilter ? { agent: agentFilter } : {}),
            })
          }
        >
          Apply filters
        </button>
      </div>
      </StickyFilterRegion>
      {error && <p className="leads-error">{error}</p>}
      {isViewOnlyAgent && (
        <p className="unanswered-view-only-notice" role="status">
          <i className="fa-solid fa-circle-info" aria-hidden="true" />
          This page is view-only for you.
        </p>
      )}
      <section className="history-table-card">
        <div className="tasks-table-heading">
          <div>
            <h2>{definition.title}</h2>
            <p className="result-count">{items.length} entries</p>
          </div>
          <div className="unanswered-duration-summary" aria-label="Call duration summary">
            <span>Total Call Duration <strong>{formatDuration(durations.total)}</strong></span>
            <span>Voicemail Duration <strong>{formatDuration(durations.voicemail)}</strong></span>
          </div>
        </div>
        {loading ? (
          <div className="calls-table-loading">
            <BufferingLoader text={`Fetching ${definition.title.toLowerCase()}`} size="md" />
          </div>
        ) : <TableScrollWrap className="calls-table-wrap">
          <table
            className="calls-table dependencies-table tasks-table unanswered-calls-table"
            style={{
              ...stickyTableStyle,
              "--unanswered-table-width": `${tableWidth}px`,
            }}
          >
            <colgroup>
              {definition.fields.map(([key]) =>
                <col key={key} style={{ width: COLUMN_WIDTHS[key] || 160 }} />,
              )}
              <col style={{ width: OWNER_COLUMN_WIDTH }} />
            </colgroup>
            <thead>
              <tr>
                {definition.fields.map(([key, label, fieldType]) => {
                  const stickyIndex = stickyIndexOf(key);
                  return (
                    <th
                      key={label}
                      className={
                        fieldType === "transcript" || fieldType === "audio"
                          ? "unanswered-icon-col"
                          : ""
                      }
                      {...(stickyIndex >= 0
                        ? { "data-unanswered-sticky-column": stickyIndex }
                        : {})}
                    >
                      {label}
                    </th>
                  );
                })}
                <th>Owner</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <tr
                  key={row.id}
                  className={
                    isRowDone(row)
                      ? "unanswered-row-done"
                      : "unanswered-row-pending"
                  }
                >
                  {definition.fields.map(([key, , fieldType]) => {
                    const stickyIndex = stickyIndexOf(key);
                    return (
                      <td
                        key={key}
                        className={
                          fieldType === "transcript" || fieldType === "audio"
                            ? "unanswered-icon-col"
                            : WRAP_FIELDS.has(key)
                              ? "unanswered-wrap-col"
                              : ""
                        }
                        {...(stickyIndex >= 0
                          ? { "data-unanswered-sticky-column": stickyIndex }
                          : {})}
                      >
                        {renderCell(row, key, fieldType)}
                      </td>
                    );
                  })}
                  <td>{row.owner_name || "-"}</td>
                </tr>
              ))}
              {!items.length && (
                <tr>
                  <td colSpan={definition.fields.length + 1} className="muted">
                    No entries.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </TableScrollWrap>}
      </section>
      {transcriptModalCall && (
        <TranscriptModal
          call={transcriptModalCall}
          onClose={() => setTranscriptModalCall(null)}
        />
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

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours, minutes, remainder]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}
