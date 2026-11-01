import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePersistentTableState } from "../data/persistentTableState.js";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { Pagination } from "../components/Pagination.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";
import {
  deleteSMLeads,
  getFDUsers,
  getSMLeads,
  importSMLeads,
  submitSMLeads,
} from "../data/api.js";
import { canApproveDependencies } from "../data/accessControl.js";
import { getDefaultDateRange } from "../data/filterDates.js";
import { formatDisplayDate } from "../data/displayDate.js";
import { downloadExcelWorkbook } from "../data/export.js";
import {
  mapSmLeadHeaders,
  parseSmLeadTable,
  smLeadMappingProblems,
  smLeadRecordProblems,
  smLeadRecords,
} from "../data/smLeadsImport.js";

const PAGE_SIZE = 50;
const COLUMNS = [
  ["date_added", "Date of Added"],
  ["date_received", "Date of Received"],
  ["name", "Name"],
  ["email", "Email ID"],
  ["phone", "Phone Number"],
  ["enquiry_question", "Enquiry Question"],
  ["sm_lead_id", "SM_Lead ID"],
  ["owner", "Owner"],
  ["submit", "Submit"],
];
const STICKY_COLUMNS = ["date_added", "date_received", "name", "email"];
const EMPTY_FILTERS = { start_date: "", end_date: "", status: "", phone: "", sm_lead_id: "" };

function formatAddedDate(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return formatDisplayDate(value, "—");
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(parsed);
}

export default function SMLeads({ user, sharedAgent = null }) {
  // The page opens on the current month and loads it straight away; Apply
  // is only needed to change the filters.
  const month = getDefaultDateRange();
  const monthFilters = { ...EMPTY_FILTERS, start_date: month.start, end_date: month.end };
  const [draft, setDraft] = usePersistentTableState("SMLeads.draft.v3", monthFilters);
  const [filters, setFilters] = usePersistentTableState("SMLeads.filters.v3", monthFilters);
  const [page, setPage] = useState(1);
  // The Agent filter is owned by "Assigning the Calls": the shared selection becomes the server's owner_id filter, in the draft and
  // the applied filters together, and the list returns to its first page.
  const sharedAgentId = sharedAgent?.id || "";
  useEffect(() => {
    const withOwner = (current) => {
      if ((current.owner_id || "") === sharedAgentId) return current;
      const { owner_id: _previous, ...rest } = current;
      return sharedAgentId ? { ...rest, owner_id: sharedAgentId } : rest;
    };
    setDraft(withOwner);
    setFilters(withOwner);
    setPage(1);
  }, [sharedAgentId]);
  const [selected, setSelected] = useState(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [ownerDrafts, setOwnerDrafts] = useState({});
  const [busyIds, setBusyIds] = useState(new Set());
  const [enquiryModal, setEnquiryModal] = useState(null);
  const [importOpen, setImportOpen] = useState(false);
  const [paste, setPaste] = useState("");
  const [table, setTable] = useState(null);
  const [choices, setChoices] = useState({});
  const [selectedFileName, setSelectedFileName] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const fileInputRef = useRef(null);
  const canManage = canApproveDependencies(user);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Set before an import/delete so the follow-up refresh updates the rows in
  // place instead of swapping the whole table for the loading spinner.
  const silentLoad = useRef(false);
  const load = useCallback(async () => {
    const silent = silentLoad.current;
    silentLoad.current = false;
    if (!silent) setLoading(true);
    setError("");
    try {
      const data = await getSMLeads({ ...filters, page, page_size: PAGE_SIZE });
      setRows(data.items || []);
      setSelected(new Set());
      setTotal(data.total || 0);
    } catch (err) {
      setError(err.message || "Failed to load SM Leads.");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [filters, page]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!canManage) return;
    getFDUsers()
      .then((data) => setAgents((data || []).filter((item) => String(item.role || "agent").toLowerCase() === "agent")))
      .catch(() => setAgents([]));
  }, [canManage]);

  const flash = (message) => {
    setNotice(message);
    setTimeout(() => setNotice(""), 2500);
  };
  const markBusy = (ids, busy) =>
    setBusyIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => (busy ? next.add(id) : next.delete(id)));
      return next;
    });
  const patchRows = (items) => {
    const byId = new Map(items.map((item) => [item.id, item]));
    setRows((current) => current.map((row) => byId.get(row.id) || row));
  };

  const handleSubmit = async (row) => {
    const ownerId = ownerDrafts[row.id] || row.owner_id;
    if (!ownerId) return;
    markBusy([row.id], true);
    setError("");
    try {
      const result = await submitSMLeads([row.id], { [row.id]: ownerId });
      if (result?.submitted !== 1) throw new Error("The SM Lead could not be assigned.");
      patchRows(result.items);
      setOwnerDrafts((current) => {
        const next = { ...current };
        delete next[row.id];
        return next;
      });
      flash("SM Lead assigned.");
    } catch (err) {
      setError(err.message || "Assignment failed.");
    } finally {
      markBusy([row.id], false);
    }
  };

  const toggleSelected = (id) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // Submit every selected row that still needs an assignment. Each needs an
  // owner (picked in its dropdown or already saved); completed rows are skipped.
  const handleBulkSubmit = async () => {
    const chosen = rows.filter((row) => selected.has(row.id) && row.status !== "Completed");
    const ownerIds = {};
    const missing = [];
    chosen.forEach((row) => {
      const ownerId = ownerDrafts[row.id] || row.owner_id;
      if (ownerId) ownerIds[row.id] = ownerId;
      else missing.push(row.sm_lead_id);
    });
    if (missing.length) {
      setError(`Choose an owner before submitting: ${missing.join(", ")}.`);
      return;
    }
    const toSubmit = chosen.filter((row) => row.status === "Pending" || (ownerDrafts[row.id] && ownerDrafts[row.id] !== row.owner_id));
    if (!toSubmit.length) {
      flash("Nothing to submit: the selected rows are already assigned.");
      return;
    }
    setBulkBusy(true);
    setError("");
    try {
      const ids = toSubmit.map((row) => row.id);
      const result = await submitSMLeads(ids, Object.fromEntries(ids.map((id) => [id, ownerIds[id]])));
      if (result?.submitted !== ids.length) throw new Error("Not every selected SM Lead was assigned.");
      patchRows(result.items);
      setOwnerDrafts((current) => {
        const next = { ...current };
        ids.forEach((id) => delete next[id]);
        return next;
      });
      setSelected(new Set());
      flash(`${ids.length} SM Lead${ids.length === 1 ? "" : "s"} assigned and the owner${ids.length === 1 ? " was" : "s were"} notified.`);
    } catch (err) {
      setError(err.message || "Bulk assignment failed.");
    } finally {
      setBulkBusy(false);
    }
  };

  const handleBulkDelete = async () => {
    if (!selected.size) return;
    if (!window.confirm(`Delete ${selected.size} selected SM lead(s)? This cannot be undone.`)) return;
    setBulkBusy(true);
    setError("");
    try {
      const count = selected.size;
      await deleteSMLeads([...selected]);
      flash(`${count} SM lead${count === 1 ? "" : "s"} deleted.`);
      silentLoad.current = true;
      if (page > 1 && count >= rows.length) setPage(page - 1);
      else await load();
    } catch (err) {
      setError(err.message || "Delete failed.");
    } finally {
      setBulkBusy(false);
    }
  };

  // Exports every row matching the applied filters (not just the visible page).
  const exportExcel = async () => {
    setExporting(true);
    setError("");
    try {
      const all = [];
      for (let exportPage = 1; ; exportPage += 1) {
        const data = await getSMLeads({ ...filters, page: exportPage, page_size: 200 });
        all.push(...(data.items || []));
        if (all.length >= (data.total || 0) || !(data.items || []).length) break;
      }
      downloadExcelWorkbook(
        [{
          name: "SM Leads",
          columns: ["Date of Added", "Date of Received", "Name", "Email ID", "Phone Number", "Enquiry Question", "SM_Lead ID", "Owner", "Status"],
          rows: all.map((row) => [
            formatAddedDate(row.date_added), formatDisplayDate(row.date_received, ""), row.name || "", row.email || "",
            row.phone || "", row.enquiry_question || "", row.sm_lead_id || "", row.owner || "", row.status || "",
          ]),
        }],
        `FlintDesk_SM_Leads_${filters.start_date || "all"}_to_${filters.end_date || "all"}.xls`,
      );
    } catch (err) {
      setError(err.message || "Export failed.");
    } finally {
      setExporting(false);
    }
  };

  // ---- Import -----------------------------------------------------------
  const analysis = useMemo(() => (table ? mapSmLeadHeaders(table.headers) : null), [table]);
  const records = useMemo(() => (table && analysis ? smLeadRecords(table, analysis, choices) : []), [table, analysis, choices]);
  const validationErrors = useMemo(() => {
    if (!table || !analysis) return [];
    const mappingProblems = smLeadMappingProblems(table, analysis, choices);
    return mappingProblems.length ? mappingProblems : smLeadRecordProblems(records);
  }, [table, analysis, choices, records]);

  const resetImport = () => {
    setTable(null);
    setChoices({});
    setPaste("");
    setSelectedFileName("");
    setImportError("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  };
  const onFile = async (file) => {
    if (!file) return;
    if (!/\.(csv|txt|tsv)$/i.test(file.name)) {
      setImportError("Choose a CSV or TXT file.");
      return;
    }
    setImportError("");
    setSelectedFileName(file.name);
    setPaste("");
    setChoices({});
    setTable(parseSmLeadTable(await file.text(), file.name.toLowerCase().endsWith(".csv") ? "," : "\t"));
  };
  const validatePasted = () => {
    setSelectedFileName("");
    if (fileInputRef.current) fileInputRef.current.value = "";
    setImportError("");
    setChoices({});
    const firstLine = paste.split(/\r?\n/).find((line) => line.trim()) || "";
    setTable(parseSmLeadTable(paste, firstLine.includes("\t") || !firstLine.includes(",") ? "\t" : ","));
  };
  const submitImport = async () => {
    if (!table || validationErrors.length) return;
    setImporting(true);
    setImportError("");
    try {
      const result = await importSMLeads(records);
      const skipped = Number(result.skipped_duplicates || 0);
      flash(`${result.inserted} SM Lead${result.inserted === 1 ? "" : "s"} imported${skipped ? ` (${skipped} duplicate${skipped === 1 ? "" : "s"} skipped)` : ""}.`);
      setImportOpen(false);
      resetImport();
      // Update the rows in place for the selected date range (no spinner).
      silentLoad.current = true;
      setPage(1);
      setFilters({ ...draft });
    } catch (err) {
      setImportError(err.message || "Import failed.");
    } finally {
      setImporting(false);
    }
  };

  const applyFilters = () => {
    setPage(1);
    setFilters({ ...draft });
  };

  const renderOwner = (row) => {
    if (!canManage)
      return <span className="leads-owner-name" title={row.owner || ""}>{row.owner || "—"}</span>;
    return (
      <select
        className="calls-stage-select"
        aria-label="Choose Owner"
        value={ownerDrafts[row.id] ?? row.owner_id ?? ""}
        disabled={row.status === "Completed" || busyIds.has(row.id)}
        onChange={(event) => setOwnerDrafts((current) => ({ ...current, [row.id]: event.target.value }))}
      >
        <option value="">Choose owner</option>
        {agents.map((agent) => (
          <option key={agent.id} value={agent.id}>{agent.name}</option>
        ))}
      </select>
    );
  };

  const renderSubmit = (row) => {
    if (!canManage) return row.status === "Completed" ? "Completed" : row.status === "Assigned" ? "Assigned" : "—";
    const busy = busyIds.has(row.id);
    const done = row.status !== "Pending";
    const pendingOwner = ownerDrafts[row.id] && ownerDrafts[row.id] !== row.owner_id;
    return (
      <div className="leads-row-actions">
        <button
          type="button"
          className={`primary-button leads-row-action ${done && !pendingOwner ? "completed-button" : ""}`}
          disabled={busy || row.status === "Completed" || !(ownerDrafts[row.id] || row.owner_id) || (done && !pendingOwner)}
          onClick={() => handleSubmit(row)}
        >
          {busy ? "Submitting..." : row.status === "Completed" ? "Completed" : done && !pendingOwner ? "Assigned" : "Submit"}
        </button>
      </div>
    );
  };

  const renderCell = (row, key) => {
    switch (key) {
      case "date_added": return formatAddedDate(row.date_added);
      case "date_received": return formatDisplayDate(row.date_received, "—");
      case "enquiry_question":
        return (
          <button
            type="button"
            className="leads-edit-cell leads-notes-viewer"
            onClick={() => setEnquiryModal(row)}
            aria-label="View full enquiry question"
            title="View full enquiry question"
          >
            {row.enquiry_question || "—"}
          </button>
        );
      case "owner": return renderOwner(row);
      case "submit": return renderSubmit(row);
      default: return <span title={row[key] || ""}>{row[key] || "—"}</span>;
    }
  };
  const stickyProps = (key) => (STICKY_COLUMNS.includes(key) ? { "data-lead-sticky-column": STICKY_COLUMNS.indexOf(key) } : {});

  return (
    <section className="process-page leads-page sm-leads-page">
      <div className="leads-heading">
        <h1>SM Leads</h1>
      </div>
      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector
          value={{ start: draft.start_date, end: draft.end_date }}
          onChange={(range) => setDraft((current) => ({ ...current, start_date: range.start, end_date: range.end }))}
        />
        <Filter label="Status">
          <select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>
            <option value="">All statuses</option>
            <option value="Pending">Pending</option>
            <option value="Assigned">Assigned</option>
            <option value="Completed">Completed</option>
          </select>
        </Filter>
        <Filter label="Client Phone Number">
          <input
            value={draft.phone}
            onChange={(e) => setDraft({ ...draft, phone: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && applyFilters()}
          />
        </Filter>
        <Filter label="SM_Lead ID">
          <input
            value={draft.sm_lead_id}
            onChange={(e) => setDraft({ ...draft, sm_lead_id: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && applyFilters()}
          />
        </Filter>
      </ScrollableFilterBelt>
      <div className="filter-belt-actions sm-leads-actions">
        {canManage && (
          <button className="leads-add-button" type="button" onClick={() => setImportOpen(true)}>
            Add / Import
          </button>
        )}
        {canManage && selected.size > 0 && (
          <>
            <button type="button" className="primary-button" disabled={bulkBusy} onClick={handleBulkSubmit}>
              {bulkBusy ? "Working..." : `Submit selected (${selected.size})`}
            </button>
            <button type="button" className="primary-button danger-button" disabled={bulkBusy} onClick={handleBulkDelete}>
              Delete selected ({selected.size})
            </button>
          </>
        )}
        <button className="primary-button calls-toolbar-apply" type="button" onClick={applyFilters}>
          Apply
        </button>
        <button className="table-action" type="button" onClick={exportExcel} disabled={exporting}>
          <i className="fa-solid fa-file-excel" /> {exporting ? "Exporting..." : "Export to Excel"}
        </button>
      </div>
      </StickyFilterRegion>
      {notice && <div className="leads-actions"><span>{notice}</span></div>}
      {error && <p className="leads-error">{error}</p>}
      <section className="history-table-card">
        <h2>SM Leads records</h2>
        <p className="result-count">{total} SM leads</p>
        {loading ? (
          <div className="calls-table-loading">
            <BufferingLoader text="Fetching SM leads" size="md" />
          </div>
        ) : (
          <TableScrollWrap className="calls-table-wrap">
            <table className="calls-table leads-table leads-stage-s1 sm-leads-table">
              <thead>
                <tr>
                  {COLUMNS.map(([key, label]) => (
                    <th key={key} className="leads-column-s1" {...stickyProps(key)}>{label}</th>
                  ))}
                  {canManage && (
                    <th className="leads-column-s1">
                      <input
                        type="checkbox"
                        aria-label="Select all rows"
                        checked={rows.length > 0 && rows.every((row) => selected.has(row.id))}
                        onChange={(e) => setSelected(e.target.checked ? new Set(rows.map((row) => row.id)) : new Set())}
                      />
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  // Green: the client has been called (outbound) since the lead was assigned.
                  <tr key={row.id} className={row.call_made ? "leads-row-call-made" : ""}>
                    {COLUMNS.map(([key]) => (
                      <td key={key} {...stickyProps(key)}>{renderCell(row, key)}</td>
                    ))}
                    {canManage && (
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${row.sm_lead_id}`}
                          checked={selected.has(row.id)}
                          onChange={() => toggleSelected(row.id)}
                        />
                      </td>
                    )}
                  </tr>
                ))}
                {!rows.length && (
                  <tr>
                    <td colSpan={COLUMNS.length + (canManage ? 1 : 0)}>No SM leads found.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableScrollWrap>
        )}
        <Pagination page={page} totalPages={totalPages} onChange={setPage} loading={loading} ariaLabel="SM Leads pages" />
      </section>

      {importOpen && (
        <div className="modal-backdrop leads-blur-backdrop">
          <div className="modal-card leads-import-modal" role="dialog" aria-modal="true" aria-labelledby="add-sm-leads-title">
            <div className="leads-import-modal-heading">
              <h3 id="add-sm-leads-title">Add / Import SM Leads</h3>
              <button
                type="button"
                className="leads-modal-close"
                aria-label="Close import dialog"
                title="Close"
                onClick={() => { setImportOpen(false); resetImport(); }}
              >
                <i className="fa-solid fa-xmark" />
              </button>
            </div>
            {importError && <p className="leads-error leads-import-error" role="alert">{importError}</p>}
            <label className="leads-file-picker">
              <span>Import CSV/TXT File</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.txt,.tsv,text/csv,text/plain"
                onChange={(e) => onFile(e.target.files?.[0])}
              />
            </label>
            {selectedFileName && (
              <div className="leads-selected-file">
                <span>{selectedFileName}</span>
                <button type="button" className="table-action" onClick={resetImport}>Remove file</button>
              </div>
            )}
            <p className="leads-import-divider">or copy/paste data from Excel</p>
            <textarea
              rows="9"
              value={paste}
              onChange={(e) => setPaste(e.target.value)}
              placeholder="Paste header row and data here"
              aria-label="Paste header row and data here"
            />
            <p className="leads-import-hint">
              <em>Columns are matched by header name, so order and exact spelling do not matter.</em>
            </p>
            <button className="table-action leads-validate-button" type="button" onClick={validatePasted} disabled={!paste.trim()}>
              Validate pasted data
            </button>
            {analysis?.ambiguities.map((item) => (
              <label className="leads-file-picker" key={item.key}>
                <span>Which column is {item.label}?</span>
                <select value={choices[item.key] ?? ""} onChange={(e) => setChoices((current) => ({ ...current, [item.key]: e.target.value }))}>
                  <option value="">Choose column</option>
                  {item.candidates.map((candidate) => (
                    <option key={candidate.index} value={candidate.index}>{candidate.header}</option>
                  ))}
                </select>
              </label>
            ))}
            {table && (
              <p className={`leads-import-feedback ${validationErrors.length ? "is-error" : "is-success"}`} role="status">
                {validationErrors.length
                  ? validationErrors.slice(0, 5).join(" ") + (validationErrors.length > 5 ? ` (+${validationErrors.length - 5} more)` : "")
                  : `Data validated successfully. ${records.length} ${records.length === 1 ? "record is" : "records are"} ready to import.`}
              </p>
            )}
            {table && !!analysis?.ignored.length && (
              <p className="leads-import-hint"><em>Ignored columns: {analysis.ignored.join(", ")}</em></p>
            )}
            <div className="leads-modal-actions">
              <button className="table-action" type="button" onClick={() => { setImportOpen(false); resetImport(); }}>Cancel</button>
              <button className="primary-button" type="button" disabled={!table || !!validationErrors.length || importing} onClick={submitImport}>
                {importing ? "Importing..." : "Import validated records"}
              </button>
            </div>
          </div>
        </div>
      )}
      {enquiryModal && (
        <div className="modal-backdrop leads-blur-backdrop" onMouseDown={() => setEnquiryModal(null)}>
          <div
            className="modal-card leads-edit-modal leads-notes-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="sm-enquiry-title"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="leads-edit-heading">
              <div>
                <small>{enquiryModal.sm_lead_id}</small>
                <h3 id="sm-enquiry-title">Enquiry Question</h3>
              </div>
              <button type="button" className="table-action" onClick={() => setEnquiryModal(null)}>Close</button>
            </div>
            <p className="leads-full-notes">{enquiryModal.enquiry_question || "No enquiry question was provided."}</p>
          </div>
        </div>
      )}
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
