import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { getCallUpdates, saveCallUpdateEntry } from "../data/api.js";
import { LIFECYCLE_GROUPS, agentFigures, dayTotals, groupFigures, longDate, MANUAL_FIELDS, validateRange } from "../data/callUpdatesCalc.js";
import { CALL_UPDATE_SECTIONS, cellValue, exportCallUpdatesPdf, initialEmailNoteRows } from "../data/callUpdatesPdf.js";
import { callUpdatesExcelFilename, callUpdatesExcelSheet } from "../data/callUpdatesExcel.js";
import { downloadExcelWorkbook } from "../data/export.js";
import { canApproveDependencies } from "../data/accessControl.js";
import { getDefaultDateRange } from "../data/filterDates.js";
import { usePersistentTableState } from "../data/persistentTableState.js";

// Daily summary: one table per date. Incoming/Outgoing/Missed and the Case
// Status rows are automated server-side from the Calls, Missed Calls and
// Dashboard data; WhatsApp calls and Initial Emails Sent are entered by each
// agent for themselves. Every total here is derived from the displayed cells.
export default function CallUpdates({ user }) {
  const month = getDefaultDateRange();
  const [draft, setDraft] = usePersistentTableState("CallUpdates.draft", { start: month.start, end: month.end });
  const [range, setRange] = usePersistentTableState("CallUpdates.range", { start: month.start, end: month.end });
  const [data, setData] = useState({ agents: [], days: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [rangeError, setRangeError] = useState("");
  const [editor, setEditor] = useState(null);
  const [saving, setSaving] = useState(false);
  const [editorError, setEditorError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [noteViewer, setNoteViewer] = useState(null);
  const requestId = useRef(0);
  // Agents edit their own column; admins, team leads and SMEs edit any agent's
  // (the API enforces the same rule).
  const canEditAnyAgent = canApproveDependencies(user);

  const load = useCallback(async () => {
    const current = ++requestId.current;
    setLoading(true);
    setError("");
    try {
      const result = await getCallUpdates(range.start, range.end);
      if (current === requestId.current) setData(result);
    } catch (loadError) {
      if (current === requestId.current) {
        setError(loadError.message || "Unable to load Call Updates.");
        setData({ agents: [], days: [] });
      }
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, [range]);

  useEffect(() => {
    load();
  }, [load]);

  const agentIds = useMemo(() => data.agents.map((agent) => agent.id), [data.agents]);

  const apply = () => {
    const problem = validateRange(draft.start, draft.end);
    setRangeError(problem);
    if (!problem) setRange({ start: draft.start, end: draft.end });
  };

  const exportExcel = () => {
    setError("");
    try {
      downloadExcelWorkbook([callUpdatesExcelSheet({ days: data.days, agents: data.agents })], callUpdatesExcelFilename(range.start, range.end));
    } catch (excelError) {
      setError(excelError.message || "Unable to export to Excel.");
    }
  };

  const exportPdf = async () => {
    setExporting(true);
    try {
      await exportCallUpdatesPdf({ days: data.days, agents: data.agents, start: range.start, end: range.end });
    } catch (pdfError) {
      setError(pdfError.message || "Unable to export the PDF.");
    } finally {
      setExporting(false);
    }
  };

  const openEditor = (day, agent, metric, values) => {
    setEditorError("");
    setEditor({
      date: day.date, agent, metric,
      value: String(Number(values[metric]) || 0),
      note: metric === "initial_emails" ? values.initial_emails_note || "" : "",
    });
  };

  const submitEditor = async (event) => {
    event.preventDefault();
    if (saving || !editor) return;
    const value = Number(editor.value);
    if (editor.value.trim() === "" || !Number.isInteger(value) || value < 0 || value > 100000) {
      setEditorError("Enter a whole number from 0 to 100,000.");
      return;
    }
    setSaving(true);
    setEditorError("");
    try {
      const saved = await saveCallUpdateEntry({
        entryDate: editor.date, agentId: editor.agent.id, metric: editor.metric, value,
        note: editor.metric === "initial_emails" ? editor.note : "",
      });
      // Update just this cell; totals and the match colour are derived from it.
      setData((current) => ({
        ...current,
        days: current.days.map((day) => day.date !== saved.entry_date ? day : {
          ...day,
          agents: {
            ...day.agents,
            [saved.agent_id]: {
              ...day.agents[saved.agent_id],
              [saved.metric]: saved.value,
              ...(saved.metric === "initial_emails" ? { initial_emails_note: saved.note } : {}),
            },
          },
        }),
      }));
      setEditor(null);
    } catch (saveError) {
      setEditorError(saveError.message || "Unable to save.");
    } finally {
      setSaving(false);
    }
  };

  const renderDay = (day) => {
    const totals = dayTotals(day, agentIds);
    const compareClass = totals.matches ? "call-updates-match" : "call-updates-mismatch";
    const noteRows = initialEmailNoteRows(day, data.agents);
    const noteRowCount = noteRows.reduce((sum, item) => sum + item.lines.length, 0);
    const compareTitle = `Calls + WhatsApp = ${totals.callsAndWhatsApp}; Case Status (Open Leads + GE + PC + Dropped) = ${totals.caseStatusTotal} — ${totals.matches ? "match" : "mismatch"}`;
    return (
      <section className="call-updates-day" key={day.date}>
        <h2>{longDate(day.date)}</h2>
        <div className="call-updates-table-box">
          <table className="calls-table leads-table call-updates-table">
            <colgroup>
              <col className="call-updates-col-category" />
              <col className="call-updates-col-metric" />
              {data.agents.map((agent) => <col key={agent.id} />)}
              <col className="call-updates-col-total" />
            </colgroup>
            <thead>
              <tr>
                <th className="leads-column-call">Category</th>
                <th className="leads-column-call">Metric</th>
                {data.agents.map((agent) => <th key={agent.id} className="leads-column-call">{agent.name}</th>)}
                <th className="leads-column-call call-updates-total-col">Total</th>
              </tr>
            </thead>
            <tbody>
              {CALL_UPDATE_SECTIONS.map((section) => section.rows.map((row, rowIndex) => (
                <tr key={row.key} className={row.derived ? "call-updates-derived-row" : ""}>
                  {rowIndex === 0 && (
                    <th rowSpan={section.rows.length} className="call-updates-category" scope="rowgroup">{section.category}</th>
                  )}
                  <td className="call-updates-metric">
                    {row.label}
                    {row.hint && <small>{row.hint}</small>}
                  </td>
                  {data.agents.map((agent) => {
                    const values = day.agents?.[agent.id] || {};
                    const shown = cellValue(row, values, agentFigures(values));
                    const group = row.group ? LIFECYCLE_GROUPS.find((item) => item.key === row.group) : null;
                    const lines = group ? groupFigures(group, values).lines : [];
                    const note = row.key === "initial_emails" ? String(values.initial_emails_note || "").trim() : "";
                    const canEdit = row.manual && (canEditAnyAgent || (user?.id && String(user.id) === String(agent.id)));
                    return (
                      <td key={agent.id} className="call-updates-value">
                        {canEdit ? (
                          <button
                            type="button"
                            className="call-updates-edit"
                            title={`Edit ${MANUAL_FIELDS[row.key]} for ${longDate(day.date)}`}
                            onClick={() => openEditor(day, agent, row.key, values)}
                          >
                            {shown}
                            <i className="fa-solid fa-pen" aria-hidden="true" />
                          </button>
                        ) : (
                          <span>{shown}</span>
                        )}
                        {group && (
                          <ul className="call-updates-values" aria-label={`${row.label} by Case Status Change`}>
                            {lines.map((line) => <li key={line.label}><span>{line.label}</span><strong>{line.count}</strong></li>)}
                            {!lines.length && <li className="muted">None</li>}
                          </ul>
                        )}
                        {note && (
                          <button
                            type="button"
                            className="call-updates-note"
                            title="Click to read the full note"
                            onClick={() => setNoteViewer({ agent: agent.name, date: day.date, value: shown, note })}
                          >
                            <i className="fa-regular fa-note-sticky" aria-hidden="true" /> {note}
                          </button>
                        )}
                      </td>
                    );
                  })}
                  <td
                    className={`call-updates-value call-updates-total-col ${row.compare ? compareClass : ""}`}
                    title={row.compare ? compareTitle : undefined}
                  >
                    {row.group ? totals.groupColumn(row.group) : row.derived ? totals[row.derived] : totals.column(row.key)}
                  </td>
                </tr>
              )))}
              <tr className="call-updates-grand-row">
                <th colSpan={2} className="call-updates-category">Total (A+B)</th>
                {data.agents.map((agent) => (
                  <td key={agent.id} className="call-updates-value">{agentFigures(day.agents?.[agent.id]).callsAndWhatsApp}</td>
                ))}
                <td className={`call-updates-value call-updates-total-col ${compareClass}`} title={compareTitle}>
                  {totals.callsAndWhatsApp}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className={`call-updates-compare ${compareClass}`}>
          Calls + WhatsApp: <strong>{totals.callsAndWhatsApp}</strong> &middot; Case Status (Open Leads + GE + PC + Dropped): <strong>{totals.caseStatusTotal}</strong> &middot; {totals.matches ? "Match" : "Mismatch"}
        </p>
        {noteRows.length > 0 && (
          <table className="call-updates-notes-table">
            <tbody>
              {noteRows.flatMap((item, itemIndex) => item.lines.map((line, lineIndex) => (
                <tr key={`${item.agent}-${lineIndex}`}>
                  {itemIndex === 0 && lineIndex === 0 && (
                    <th rowSpan={noteRowCount} scope="rowgroup">Initial Email Sent Categories</th>
                  )}
                  {lineIndex === 0 && <td rowSpan={item.lines.length} className="call-updates-notes-agent">{item.agent}<small>{item.count} sent</small></td>}
                  <td className="call-updates-notes-text">{line}</td>
                </tr>
              )))}
            </tbody>
          </table>
        )}
      </section>
    );
  };

  return (
    <section className="process-page leads-page call-updates-page">
      <div className="leads-heading">
        <h1>Call Updates</h1>
      </div>
      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector value={draft} onChange={setDraft} />
      </ScrollableFilterBelt>
      <div className="filter-belt-actions">
        <button type="button" className="primary-button calls-toolbar-apply" onClick={apply} disabled={loading}>
          Apply
        </button>
        <button type="button" className="table-action calls-export-button" onClick={exportExcel} disabled={loading || !data.days.length}>
          <i className="fa-solid fa-file-excel" /> Export to Excel
        </button>
        <button type="button" className="table-action" onClick={exportPdf} disabled={exporting || loading || !data.days.length}>
          <i className="fa-solid fa-file-pdf" /> {exporting ? "Exporting..." : "Export to PDF"}
        </button>
      </div>
      </StickyFilterRegion>
      {rangeError && <p className="leads-error" role="alert">{rangeError}</p>}
      {error && <p className="leads-error" role="alert">{error}</p>}
      {loading ? (
        <div className="calls-table-loading">
          <BufferingLoader text="Fetching call updates (a long range can take up to a minute)" size="md" />
        </div>
      ) : (
        data.days.map(renderDay)
      )}

      {noteViewer && (
        <div className="modal-backdrop leads-blur-backdrop" onMouseDown={() => setNoteViewer(null)}>
          <div
            className="modal-card call-updates-manual-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="call-updates-note-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="leads-edit-heading">
              <div>
                <small>{longDate(noteViewer.date)} &middot; {noteViewer.agent}</small>
                <h3 id="call-updates-note-title">Initial Emails Sent: {noteViewer.value}</h3>
              </div>
              <button type="button" className="table-action" onClick={() => setNoteViewer(null)}>Close</button>
            </div>
            <p className="call-updates-note-full">{noteViewer.note}</p>
          </div>
        </div>
      )}

      {editor && (
        <div className="modal-backdrop leads-blur-backdrop" onMouseDown={() => !saving && setEditor(null)}>
          <form
            className="modal-card call-updates-manual-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="call-updates-editor-title"
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={submitEditor}
          >
            <div className="leads-edit-heading">
              <div>
                <small>{longDate(editor.date)} &middot; {editor.agent.name}</small>
                <h3 id="call-updates-editor-title">{MANUAL_FIELDS[editor.metric]}</h3>
              </div>
            </div>
            <label className="call-updates-field">
              <span>{editor.metric === "initial_emails" ? "Number of initial emails sent" : "Number of calls"}</span>
              <input
                type="number"
                min="0"
                max="100000"
                step="1"
                inputMode="numeric"
                value={editor.value}
                onChange={(event) => setEditor((current) => ({ ...current, value: event.target.value }))}
                autoFocus
                disabled={saving}
              />
            </label>
            {editor.metric === "initial_emails" && (
              <label className="call-updates-field">
                <span>Note (optional)</span>
                <textarea
                  rows="4"
                  maxLength={1000}
                  value={editor.note}
                  onChange={(event) => setEditor((current) => ({ ...current, note: event.target.value }))}
                  placeholder="Add a note about these emails"
                  disabled={saving}
                />
              </label>
            )}
            {editorError && <p className="leads-error" role="alert">{editorError}</p>}
            <div className="leads-modal-actions">
              <button type="button" className="table-action" onClick={() => setEditor(null)} disabled={saving}>Cancel</button>
              <button type="submit" className="primary-button" disabled={saving}>{saving ? "Saving..." : "Submit"}</button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}
