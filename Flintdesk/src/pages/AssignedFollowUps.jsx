import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";
import { buildCallRecordsFromAnalysis, getCallAnalysis, getCallAudioBlobUrl, getCallContent, getCallSummaries, getFDUsers, getFollowUps, saveFollowUpFields, submitFollowUp } from "../data/api.js";
import { AudioModal, TranscriptModal } from "../components/CallMediaModals.jsx";
import PhoneLogsModal from "../components/PhoneLogsModal.jsx";
import { buildCopyTable, writeTableToClipboard } from "../data/callsClipboard.js";
import { enquiryOpenDuration } from "../data/enquiryOpenDuration.js";
import { ColumnHeaderLabel } from "../components/ColumnHeaderLabel.jsx";
import {
  CALL_DETAIL_LABELS, CALL_GROUP_LABELS, FOLLOW_UP_STAGE_GROUPS, TEAM_LEAD_COLUMNS, canSubmitRow, isSubmitted, normalizeAmount, stageCellText, stageSource, submitLabel,
  successPcRows, followUpSignature,
} from "../data/followUps.js";
import { formatDisplayDate, formatDisplayDateTime } from "../data/displayDate.js";
import { ClientIdCell, LeadIdCell } from "../components/IdentifierCells.jsx";
import { getCallKind } from "../data/callKind.js";
import { getDefaultDateRange, isValidDateRange } from "../data/filterDates.js";
import { usePersistentTableState } from "../data/persistentTableState.js";
import { startPolling } from "../data/polling.js";
import { useActivePageRefresh } from "../data/useActivePageRefresh.js";
import { downloadExcelWorkbook } from "../data/export.js";
import { exportTablePdf } from "../data/tablePdf.js";

// "Assigning the Calls > Follow Ups": the calls whose Case Status Change is Success PC, with the Team Lead Fills columns and a
// Submit that assigns the call to its Retainer Owner (like assigning a lead). Admin / Team Lead / SME edit; an agent sees only the
// calls submitted to them.
const displayDirection = (value) => String(value || "-").replace(/brevo outbound/gi, "Leads Outbound");
const scoreText = (call) => (call?.raw?.manual_data_score != null ? `${Number(call.raw.manual_data_score).toFixed(1)}/10` : "");

export default function AssignedFollowUps({ sharedAgent = null, isActive = true }) {
  const initial = getDefaultDateRange();
  const [draft, setDraft] = usePersistentTableState("FollowUps.draft", { start: initial.start, end: initial.end });
  const [range, setRange] = usePersistentTableState("FollowUps.range", { start: initial.start, end: initial.end });
  // The Agent filter is owned by "Assigning the Calls" (one selection for every tab); the server filters by the Retainer Owner's id.
  const agent = sharedAgent?.id || "all";
  const [items, setItems] = useState([]);
  const [stageData, setStageData] = useState({ analysis: {}, summaries: {}, records: {} });   // Calls-page Stage 1-4 values, by call id
  const [canEdit, setCanEdit] = useState(false);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState({});          // `${callId}:${field}` or `${callId}:submit` -> true while its request runs
  const [amountDrafts, setAmountDrafts] = useState({});
  const [textEditor, setTextEditor] = useState(null);   // { callId, key, label, value }
  const [selected, setSelected] = useState(() => new Set());      // call ids ticked in the Copy / Repeated Logs column
  const [copied, setCopied] = useState({ id: null, text: "" });
  const [exporting, setExporting] = useState(false);
  const [transcriptCall, setTranscriptCall] = useState(null);     // { id, transcript }
  const [audio, setAudio] = useState(null);                       // { call, url, loading, error }
  const [phoneLogs, setPhoneLogs] = useState("");                 // the client phone whose logs are open

  // `quiet`: refresh in place (no loading screen, the rows on screen stay until the new ones arrive, a failure keeps them). Used by the
  // background refresh that picks up reassignments; Submit and Save never reload: they update only their own row.
  const loadState = useRef({ running: false, endedAt: 0 });
  const load = useCallback(async ({ quiet = false, isCurrent = () => true } = {}) => {
    loadState.current.running = true;
    if (!quiet) setLoading(true);
    if (!quiet) setError("");
    try {
      const result = await getFollowUps(range.start, range.end, agent);
      const rows = result.items || [];
      // The Stage 1-4 columns are the Calls page's own values for the same Success PC calls (the same request the Calls table
      // makes). They only decorate the rows: if they cannot be loaded the Follow Ups rows still show, with empty stage cells.
      let analysis = {};
      let summaries = {};
      if (rows.length) {
        try {
          const records = await getCallAnalysis(range.start, range.end, "", "", "", "calls", "", "", null, null,
            "", "", "", "", "", null, false, "", "", "Success PC");
          analysis = Object.fromEntries((Array.isArray(records) ? records : records?.items || []).map((record) => [String(record.call_id), record]));
          summaries = (await getCallSummaries(rows.map((row) => row.call_id))) || {};
        } catch { /* stage cells stay empty */ }
      }
      // The Calls table's own rows for these calls (same builder), so Agent, Client ID, Lead ID, Ext, Direction, Call Type and
      // Duration are exactly what the Calls page shows.
      let records = {};
      try {
        const merged = Object.values(analysis).map((record) => ({ ...record, ...(summaries[String(record.call_id)] || {}) }));
        records = Object.fromEntries(buildCallRecordsFromAnalysis(merged).map((call) => [String(call.callId), call]));
      } catch { /* the cells fall back to the Follow Ups row */ }
      if (!isCurrent()) return;
      setStageData({ analysis, summaries, records });
      setItems(rows);
      setCanEdit(Boolean(result.can_edit));
    } catch (loadError) {
      if (quiet) return;
      setError(loadError.message || "Unable to load Follow Ups.");
      setItems([]);
    } finally {
      if (!quiet) setLoading(false);
      loadState.current = { running: false, endedAt: Date.now() };
    }
  }, [range.start, range.end, agent]);

  useEffect(() => { load(); }, [load]);
  // Reassignments made by someone else reach this page by themselves: on the shared page-refresh timer and whenever the app sees a new
  // notification (the "assigned to you" / "no longer assigned" alerts), without a reload and without the loading screen.
  // Loading the Stage values itself posts to /calls/..., which the app announces as "data changed": that announcement must not start
  // another load (an endless reload), so changes seen while loading, or just after, are ignored here; the check below catches them.
  const refreshQuietly = useCallback(({ silent, isCurrent }) => {
    const { running, endedAt } = loadState.current;
    if (!silent || running || Date.now() - endedAt < 2000) return undefined;
    return load({ quiet: true, isCurrent });
  }, [load]);
  useActivePageRefresh(isActive, refreshQuietly);
  // A light check every few seconds while this tab is on screen: one list request, and a full quiet reload only when an owner, a
  // submission or a status differs from what is shown (a reassignment by someone else). Skipped while a save or submit is running.
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    if (!isActive) return undefined;
    const poller = startPolling({
      intervalMs: 15_000,
      isActive: () => document.visibilityState === "visible" && !Object.keys(busyRef.current).length,
      run: async () => {
        const result = await getFollowUps(range.start, range.end, agent);
        if (followUpSignature(result.items) !== followUpSignature(itemsRef.current)) await load({ quiet: true });
      },
    });
    return () => poller.stop();
  }, [isActive, range.start, range.end, agent, load]);
  // The agent list feeds the Agent filter and the Retainer Owner dropdown, both for editors only (an agent may not read the user list).
  useEffect(() => {
    if (!canEdit) return;
    getFDUsers()
      .then((data) => setAgents((data || []).filter((item) => String(item.role || "agent").toLowerCase() === "agent")))
      .catch(() => setAgents([]));
  }, [canEdit]);

  // The frozen CALL columns (Copy / Repeated Logs ... Audio) stay in view while the table scrolls sideways, exactly like the
  // Call Records table. Each is pinned at the width it actually renders with, so the frozen headers and cells line up.
  const pinCallColumns = useCallback((table) => {
    if (!table) return undefined;
    const measure = () => {
      const frozen = [...table.querySelectorAll("thead tr:nth-child(2) > th[data-fu-sticky]")]
        .sort((a, b) => Number(a.dataset.fuSticky) - Number(b.dataset.fuSticky));
      let left = 0;
      frozen.forEach((th) => { table.style.setProperty(`--fu-left-${th.dataset.fuSticky}`, `${left}px`); left += th.getBoundingClientRect().width; });
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(table);
    table.querySelectorAll("thead tr:nth-child(2) > th[data-fu-sticky]").forEach((th) => observer.observe(th));
    return () => observer.disconnect();
  }, []);

  const openTranscript = async (row) => {
    try {
      const content = await getCallContent(row.call_id);
      setTranscriptCall({ id: row.call_reference || row.call_id, transcript: content.transcript || "No transcript available." });
    } catch (failure) { setError(failure?.message || "Failed to load transcript."); }
  };
  const openAudio = async (row) => {
    const call = { id: row.call_reference || row.call_id, date: row.date };
    setAudio({ call, url: "", loading: true, error: "" });
    try { setAudio({ call, url: await getCallAudioBlobUrl(row.call_id), loading: false, error: "" }); }
    catch (failure) { setAudio({ call, url: "", loading: false, error: failure?.message || "Unable to load audio." }); }
  };
  const closeAudio = () => { setAudio((current) => { if (current?.url) URL.revokeObjectURL(current.url); return null; }); };

  const apply = () => {
    if (!isValidDateRange(draft)) { setError("Date From must be on or before Date To."); return; }
    setRange({ start: draft.start, end: draft.end });
  };

  const merge = (callId, patch) => setItems((current) => current.map((row) => (row.call_id === callId ? { ...row, ...patch } : row)));
  const withBusy = async (key, work) => {
    setBusy((current) => ({ ...current, [key]: true }));
    setError("");
    try { return await work(); } catch (failure) { setError(failure?.message || "Could not save."); return null; }
    finally { setBusy((current) => { const next = { ...current }; delete next[key]; return next; }); }
  };

  const saveField = (row, key, value) => withBusy(`${row.call_id}:${key}`, async () => {
    const saved = await saveFollowUpFields(row.call_id, { [key]: value });
    merge(row.call_id, saved);
    // Handing the call to another Retainer Owner while the list is filtered to one owner takes it out of this view, like a reload would.
    if (key === "retainer_owner_id" && agent !== "all" && saved.retainer_owner_id !== agent) {
      setItems((current) => current.filter((item) => item.call_id !== row.call_id));
      setNotice(`${row.call_reference || "Call"} moved to ${saved.retainer_owner_name || "no owner"}.`);
      window.setTimeout(() => setNotice(""), 4000);
    }
    return saved;
  });

  const saveAmount = async (row) => {
    const typed = amountDrafts[row.call_id];
    if (typed === undefined) return;
    const amount = normalizeAmount(typed);
    if (amount === null) { setError("Paid Amount must be a number with up to two decimals, for example 1250.50."); return; }
    const saved = await saveField(row, "paid_amount", amount);
    if (saved) setAmountDrafts((current) => { const next = { ...current }; delete next[row.call_id]; return next; });
  };

  const submit = (row) => withBusy(`${row.call_id}:submit`, async () => {
    const saved = await submitFollowUp(row.call_id);
    merge(row.call_id, saved);
    setNotice(`${row.call_reference || "Call"} assigned to ${saved.retainer_owner_name || "the Retainer Owner"}.`);
    window.setTimeout(() => setNotice(""), 4000);
    // Only this row changes (the response carries its same-day "Follow Up Call" marker too); the table is not reloaded.
  });

  const renderCell = (row, column) => {
    const value = row[column.key] ?? "";
    const saving = Boolean(busy[`${row.call_id}:${column.key}`]);
    if (!canEdit) {
      const shown = column.kind === "owner" ? row.retainer_owner_name : value;
      return <span className="follow-ups-readonly" title={String(shown || "")}>{shown || "-"}</span>;
    }
    if (column.kind === "select") {
      return (
        <select className="calls-stage-select" aria-label={column.label} value={value} disabled={saving}
          onChange={(event) => saveField(row, column.key, event.target.value)}>
          <option value="">Choose</option>
          {column.options.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      );
    }
    if (column.kind === "owner") {
      return (
        <select className="calls-stage-select" aria-label="Retainer Owner" value={value} disabled={saving}
          onChange={(event) => saveField(row, column.key, event.target.value)}>
          <option value="">Choose owner</option>
          {agents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      );
    }
    if (column.kind === "amount") {
      return (
        <input className="calls-stage-input follow-ups-amount" inputMode="decimal" aria-label="Paid Amount" placeholder="0.00" disabled={saving}
          value={amountDrafts[row.call_id] ?? (value || "")}
          onChange={(event) => setAmountDrafts((current) => ({ ...current, [row.call_id]: event.target.value }))}
          onBlur={() => saveAmount(row)}
          onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
      );
    }
    return (
      <button type="button" className="calls-cell-clickable follow-ups-text-cell" title={value || `Add ${column.label}`} disabled={saving}
        onClick={() => setTextEditor({ callId: row.call_id, key: column.key, label: column.label, value: value || "" })}>
        {value || <span className="muted">Add</span>}
      </button>
    );
  };

  const saveText = async (event) => {
    event.preventDefault();
    const row = items.find((item) => item.call_id === textEditor.callId);
    if (!row) { setTextEditor(null); return; }
    const saved = await saveField(row, textEditor.key, textEditor.value);
    if (saved) setTextEditor(null);
  };

  // Only calls whose Case Status Change is Success PC are listed (the server already limits the list; this is the same rule here).
  const rows = successPcRows(items, stageData.analysis);
  // The Calls-table row for a Follow Ups row; for a call the viewer cannot load from the Calls API (an agent's assigned call that another
  // agent owns) the Follow Ups row's own values stand in.
  const callFor = (row) => stageData.records[String(row.call_id)] || {
    id: row.call_reference, callId: row.call_id, date: row.date, timestamp: row.time, clientPhoneNumber: row.client_phone,
    agent: row.agent_name, clientId: "", leadId: "", extension: "", direction: "", duration: "", raw: stageData.analysis[String(row.call_id)] || {},
  };
  const columnCount = CALL_GROUP_LABELS.length + CALL_DETAIL_LABELS.length + FOLLOW_UP_STAGE_GROUPS.reduce((sum, group) => sum + group.columns.length, 0)
    + TEAM_LEAD_COLUMNS.length + (canEdit ? 1 : 0);
  const allSelected = rows.length > 0 && rows.every((row) => selected.has(row.call_id));
  const toggleSelected = (callId) => setSelected((current) => { const next = new Set(current); if (next.has(callId)) next.delete(callId); else next.add(callId); return next; });
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(rows.map((row) => row.call_id)));
  // Copy: a header row plus each chosen call's displayed values, tab separated (the Calls page's clipboard format).
  const copyColumns = useMemo(() => [
    { key: "id", label: "Call_ID" }, { key: "date", label: "Date" }, { key: "timestamp", label: "Timestamp" }, { key: "clientId", label: "Client ID" },
    { key: "leadId", label: "Lead ID" }, { key: "clientPhoneNumber", label: "Client Phone Number" }, { key: "agent", label: "Agent" },
    { key: "extension", label: "Ext Number" }, { key: "direction", label: "Direction" }, { key: "callKind", label: "Call Type" },
    { key: "duration", label: "Duration" }, { key: "manualDataScore", label: "Manual Data Score" },
    ...FOLLOW_UP_STAGE_GROUPS.flatMap((group) => group.columns.map((field) => ({ key: field.key, label: field.label }))),
    ...TEAM_LEAD_COLUMNS.map((column) => ({ key: column.key, label: column.label })),
  ], []);
  // One row of displayed values, in copyColumns order: what Copy, Excel and PDF all use.
  const rowValues = (row) => {
    const call = callFor(row);
    const source = stageSource(row, call.raw, stageData.summaries[String(row.call_id)]);
    return [
      call.id, formatDisplayDate(row.date), call.timestamp, call.clientId, call.leadId, call.clientPhoneNumber, call.agent,
      call.extension, displayDirection(call.direction), getCallKind(call), call.duration, scoreText(call),
      ...FOLLOW_UP_STAGE_GROUPS.flatMap((group) => group.columns.map((field) => stageCellText(source, field.key))),
      ...TEAM_LEAD_COLUMNS.map((column) => (column.kind === "owner" ? row.retainer_owner_name : row[column.key])),
    ];
  };
  const exportName = `FlintDesk_Follow_Ups_${range.start}_to_${range.end}`;
  const agentNote = sharedAgent?.name ? `Retainer Owner: ${sharedAgent.name}` : "All agents";
  const exportExcel = () => {
    const paidAt = copyColumns.findIndex((column) => column.key === "paid_amount");
    const dateAt = copyColumns.findIndex((column) => column.key === "date");
    const scoreAt = copyColumns.findIndex((column) => column.key === "manualDataScore");
    downloadExcelWorkbook([{
      name: "Follow Ups",
      columns: copyColumns.map((column) => ({ label: column.label, wrap: /plan|verification|summary|comment|question|item|note/i.test(column.label) })),
      // Shaped like the Calls page's Excel export: the date is the ISO day (Excel reads it as a date), a "-" placeholder is blank and the
      // Manual Data Score reads "x / 10". Paid Amount is a number so Excel can add it up; everything else is what the table shows.
      rows: rows.map((row) => rowValues(row).map((value, index) => {
        if (index === dateAt) return row.date || "";
        if (index === scoreAt && value) return String(value).replace("/", " / ");
        if (value === "-") return "";
        return index === paidAt && value !== "" && value != null && Number.isFinite(Number(value)) ? Number(value) : value;
      })),
    }], `${exportName}.xls`);
  };
  const exportPdf = async () => {
    setExporting(true);
    try {
      await exportTablePdf({
        title: "FlintDesk - Follow Ups", subtitle: `${formatDisplayDate(range.start)} to ${formatDisplayDate(range.end)}  |  ${agentNote}`,
        columns: copyColumns.map((column) => column.label), rows: rows.map(rowValues), filename: `${exportName}.pdf`, repeatColumns: 2,
      });
    } catch (failure) { setError(failure?.message || "Unable to export the PDF."); }
    finally { setExporting(false); }
  };
  const copyRows = async (callIds, rowId) => {
    const chosen = rows.filter((row) => callIds.includes(row.call_id));
    const values = chosen.map(rowValues);
    try {
      await writeTableToClipboard(buildCopyTable(copyColumns, values));
      setCopied({ id: rowId, text: chosen.length === 1 ? "Copied" : `Copied ${chosen.length} calls` });
      window.setTimeout(() => setCopied((current) => (current.id === rowId ? { id: null, text: "" } : current)), 1600);
    } catch { setError("Unable to copy calls. Please allow clipboard access and try again."); }
  };

  return (
    <section className="process-page follow-ups-page">
      <StickyFilterRegion>
        <ScrollableFilterBelt>
          <DateRangeSelector value={draft} onChange={setDraft} />
        </ScrollableFilterBelt>
        <div className="filter-belt-actions">
          <button type="button" className="primary-button calls-toolbar-apply" onClick={apply} disabled={loading}>{loading ? "..." : "Apply"}</button>
          <button type="button" className="table-action calls-export-button" onClick={exportExcel} disabled={loading || !rows.length} title="Export the displayed follow ups to Excel">
            <i className="fa-solid fa-file-excel" /> Export to Excel
          </button>
          <button type="button" className="table-action calls-export-button" onClick={exportPdf} disabled={loading || exporting || !rows.length} title="Export the displayed follow ups to PDF">
            <i className="fa-solid fa-file-pdf" /> {exporting ? "Exporting..." : "Export to PDF"}
          </button>
        </div>
      </StickyFilterRegion>
      {error && <p className="leads-error" role="alert">{error}</p>}
      {notice && <p className="tasks-success" role="status">{notice}</p>}
      {!canEdit && !loading && <p className="unanswered-view-only-notice" role="status">
        <i className="fa-solid fa-circle-info" aria-hidden="true" /> You can see every follow-up call here. Only a Team Lead, Admin or SME can fill or submit them.
      </p>}
      <section className="history-table-card">
        {loading ? (
          <div className="calls-table-loading"><BufferingLoader text="Loading follow ups" size="md" /></div>
        ) : (
          <TableScrollWrap className="calls-table-wrap follow-ups-wrap">
            <table className="calls-table calls-table-expanded follow-ups-table" ref={pinCallColumns}>
              <thead>
                <tr className="calls-table-group-row">
                  <th colSpan={CALL_GROUP_LABELS.length} className="group-call follow-ups-frozen-group">Call</th>
                  <th colSpan={CALL_DETAIL_LABELS.length} className="group-details">Call details</th>
                  {FOLLOW_UP_STAGE_GROUPS.map((group) => <th key={group.key} colSpan={group.columns.length} className={group.groupClass}>{group.label}</th>)}
                  <th colSpan={TEAM_LEAD_COLUMNS.length + (canEdit ? 1 : 0)} className="follow-ups-group-head">Follow Ups &middot; Team Lead Fills</th>
                </tr>
                <tr>
                  <th data-fu-sticky="0" style={{ left: "var(--fu-left-0, 0px)" }} className="col-call calls-selection-header">
                    <label className="calls-selection-header-label">
                      <input type="checkbox" aria-label="Select all displayed calls" checked={allSelected} onChange={toggleAll} />
                      <span>{CALL_GROUP_LABELS[0]}</span>
                    </label>
                  </th>
                  {CALL_GROUP_LABELS.slice(1).map((label, index) => (
                    <th key={label} data-fu-sticky={index + 1} style={{ left: `var(--fu-left-${index + 1}, 0px)` }} className="col-call">{label}</th>
                  ))}
                  {CALL_DETAIL_LABELS.map((label) => <th key={label} className="col-details">{label}</th>)}
                  {FOLLOW_UP_STAGE_GROUPS.map((group) => group.columns.map((field) => (
                    <th key={field.key} className={group.headerClass}><ColumnHeaderLabel field={field} /></th>
                  )))}
                  {TEAM_LEAD_COLUMNS.map((column) => <th key={column.key} className="follow-ups-sub-head">{column.label}</th>)}
                  {canEdit && <th className="follow-ups-sub-head">Submit</th>}
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={columnCount} className="muted follow-ups-empty">No calls with Case Status Change "Success PC" in this date range.</td></tr>
                )}
                {rows.map((row, index) => {
                  const analysis = stageData.analysis[String(row.call_id)];
                  const call = callFor(row);
                  const source = stageSource(row, call.raw, stageData.summaries[String(row.call_id)]);
                  const phone = call.clientPhoneNumber || row.client_phone;
                  const agentName = call.agent && call.agent !== "-" ? call.agent : row.agent_name;
                  const frozen = (position) => ({ "data-fu-sticky": position, style: { left: `var(--fu-left-${position}, 0px)` } });
                  return (
                    <tr key={row.call_id} className={row.follow_up_call ? "leads-row-call-made" : ""}>
                      <td {...frozen(0)}>
                        <div className="calls-selection-cell">
                          <input type="checkbox" aria-label={`Select ${row.call_reference || row.call_id}`} checked={selected.has(row.call_id)} onChange={() => toggleSelected(row.call_id)} />
                          <button type="button" className={`icon-action-btn${copied.id === row.call_id ? " icon-action-btn-copied" : ""}`} title="Copy call row"
                            onClick={() => copyRows(selected.has(row.call_id) ? [...selected] : [row.call_id], row.call_id)}>
                            <i className={copied.id === row.call_id ? "fa-solid fa-check" : "fa-regular fa-copy"} />
                          </button>
                          {phone && <button type="button" className="table-action calls-more-logs-btn" onClick={() => setPhoneLogs(phone)}>More logs</button>}
                        </div>
                      </td>
                      <td {...frozen(1)}>{index + 1}</td>
                      <td {...frozen(2)}>{formatDisplayDateTime(call.date)}</td>
                      <td {...frozen(3)} title="Days since call registration until case status change">{enquiryOpenDuration(call)}</td>
                      <td {...frozen(4)}>{call.timestamp || row.time || "-"}</td>
                      <td {...frozen(5)}>
                        <button type="button" className="icon-action-btn" title="View transcript" onClick={() => openTranscript(row)}><i className="fa-regular fa-file-lines" /></button>
                      </td>
                      <td {...frozen(6)}>
                        <button type="button" className="icon-action-btn" title={analysis && !(analysis.audio_s3_uri || analysis.audio_s3_key) ? "No audio for this call" : "Play audio"}
                          disabled={Boolean(analysis) && !(analysis.audio_s3_uri || analysis.audio_s3_key)} onClick={() => openAudio(row)}><i className="fa-solid fa-play" /></button>
                      </td>
                      <td className="calls-cell-wide follow-ups-id">
                        {call.id || row.call_reference || "-"}
                        {row.follow_up_call && <small className="leads-call-made-on follow-up-call-label" title={`Follow-up call ${row.follow_up_call.call_reference || ""} made by the Retainer Owner today`}>Follow Up Call</small>}
                      </td>
                      <td>{call.clientId ? <ClientIdCell clientId={call.clientId} onClientUpdated={load} /> : "-"}</td>
                      <td><LeadIdCell leadId={call.leadId} /></td>
                      <td>{phone || "-"}</td>
                      <td>{agentName || "-"}</td>
                      <td><span title={call.extension || "-"}>{call.extension || "-"}</span></td>
                      <td><span title={displayDirection(call.direction)}>{displayDirection(call.direction)}</span></td>
                      <td>{call.callId ? <span className={`call-kind-badge ${getCallKind(call).toLowerCase()}`}>{getCallKind(call)}</span> : "-"}</td>
                      <td><span title="Actual audio recording length">{call.duration || "-"}</span></td>
                      <td>
                        {call.raw?.manual_data_score != null
                          ? <span className="manual-score-cell"><span className="manual-score-value">{Number(call.raw.manual_data_score).toFixed(1)}<small>/10</small></span></span>
                          : "-"}
                      </td>
                      {FOLLOW_UP_STAGE_GROUPS.map((group) => group.columns.map((field) => {
                        const text = stageCellText(source, field.key);
                        const shown = field.attachment ? (text ? "Attached" : "") : text;
                        const long = ["fd_a_call_summary", "enquiry_details", "fd_m_self_action_item"].includes(field.key);
                        return (
                          <td key={field.key} className="calls-cell-wide">
                            {long ? <span className="follow-ups-clamp" title={text}>{shown || "-"}</span> : (shown || "-")}
                          </td>
                        );
                      }))}
                      {TEAM_LEAD_COLUMNS.map((column) => <td key={column.key} className="calls-cell-wide follow-ups-fill">{renderCell(row, column)}</td>)}
                      {canEdit && (
                        <td className="follow-ups-fill">
                          <button type="button" className={`primary-button leads-row-action ${isSubmitted(row) ? "completed-button" : ""}`}
                            disabled={!canSubmitRow(row) || Boolean(busy[`${row.call_id}:submit`])} onClick={() => submit(row)}
                            title={row.retainer_owner_id ? "" : "Choose a Retainer Owner first"}>
                            {submitLabel(row, Boolean(busy[`${row.call_id}:submit`]))}
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableScrollWrap>
        )}
      </section>
      {copied.text && <p className="tasks-success calls-copy-notice" role="status">{copied.text}</p>}
      {textEditor && (
        <div className="call-modal-overlay" onClick={() => setTextEditor(null)}>
          <form className="call-modal-card call-modal-medium" role="dialog" aria-modal="true" aria-labelledby="follow-up-text-title"
            onClick={(event) => event.stopPropagation()} onSubmit={saveText}>
            <div className="call-modal-header">
              <div>
                <p className="call-modal-kicker">{textEditor.label}</p>
                <h2 id="follow-up-text-title">Edit</h2>
              </div>
              <button type="button" className="call-modal-close" onClick={() => setTextEditor(null)}>Close</button>
            </div>
            <div className="call-modal-content">
              <textarea className="cell-modal-textarea" maxLength={4000} value={textEditor.value} autoFocus
                onChange={(event) => setTextEditor((current) => ({ ...current, value: event.target.value }))} />
            </div>
            <div className="call-modal-footer">
              <button type="button" className="table-action" onClick={() => setTextEditor(null)}>Cancel</button>
              <button type="submit" className="table-action primary">Save</button>
            </div>
          </form>
        </div>
      )}
      {transcriptCall && <TranscriptModal call={transcriptCall} onClose={() => setTranscriptCall(null)} />}
      {audio && <AudioModal call={audio.call} url={audio.url} loading={audio.loading} error={audio.error} onClose={closeAudio} />}
      {phoneLogs && <PhoneLogsModal phone={phoneLogs} onClose={() => setPhoneLogs("")} />}
    </section>
  );
}
