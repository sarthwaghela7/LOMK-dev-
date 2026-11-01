import { usePersistentTableState } from "../data/persistentTableState.js";
import { formatDisplayDate } from "../data/displayDate.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { CollapsiblePanel } from "../components/CollapsiblePanel.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";
import {
  deleteTasks,
  getFDUsers,
  getTasks,
  importTasks,
  saveTask,
} from "../data/api.js";
import { getDefaultDateRange } from "../data/filterDates.js";
import {
  mapImportHeaders,
  parseDelimitedTable,
  parsePastedTable,
  recordsFromImportTable,
} from "../data/importMapping.js";

const TYPES = {
  general: {
    title: "General Tasks",
    fields: [
      ["date", "Date", "date"],
      ["tasks", "Tasks"],
      ["timeline", "Timeline"],
      ["owner", "Owner", "owner"],
      ["status", "Status"],
      ["comments", "Comments"],
    ],
  },
  missed_call: {
    title: "Missed Calls",
    fields: [
      ["missed_call_date", "Missed Call Date", "date"],
      ["miscall_added_date", "Missed Call Added Date", "date"],
      ["name", "Name"],
      ["contact_number", "Contact Number"],
      ["summary", "Summary"],
      ["owner", "Owner", "owner"],
      ["action_status", "Action Status"],
      ["comments", "Comments"],
    ],
  },
  non_responsiveness: {
    title: "Non-Responsive Calls",
    fields: [
      ["date_added", "Non Responsive Call Added Date", "date"],
      ["name", "Name"],
      ["type", "Type"],
      ["phone_number", "Phone Number"],
      ["email", "Email", "email"],
      ["status", "Status"],
      ["owner", "Owner", "owner"],
      ["action_status", "Action Status"],
      ["comments", "Comments"],
    ],
  },
};

const ALIASES = {
  general: {
    task: "tasks",
    duedate: "timeline",
    assignedto: "owner",
    agent: "owner",
  },
  missed_call: {
    emaildate: "missed_call_date",
    date: "miscall_added_date",
    contactno: "contact_number",
  },
  non_responsiveness: {
    date: "date_added",
    dateadded: "date_added",
    nonresponsiveaddeddate: "date_added",
    phone: "phone_number",
  },
};
const CALL_TASK_TYPES = ["missed_call", "non_responsiveness"];
const MAX_IMPORT_ROWS = 500;

export default function Tasks({ user, sharedAgent = null }) {
  const month = getDefaultDateRange();
  const [draft, setDraft] = usePersistentTableState("Tasks.draft", { start: month.start, end: month.end });
  // Date controls start with a helpful month, but they do not constrain the
  // table until the user explicitly applies them.
  const [filters, setFilters] = usePersistentTableState("Tasks.filters", {});
  const [agentFilter, setAgentFilter] = usePersistentTableState("Tasks.agentFilter", "");
  // The Agent filter is owned by "Assigning the Calls": the shared selection is both the draft and the applied owner filter.
  const sharedAgentId = sharedAgent?.id || "";
  useEffect(() => {
    setAgentFilter(sharedAgentId);
    setFilters((current) => {
      if ((current.agent || "") === sharedAgentId) return current;
      const { agent: _previous, ...rest } = current;
      return sharedAgentId ? { ...rest, agent: sharedAgentId } : rest;
    });
  }, [sharedAgentId]);
  const [view, setView] = usePersistentTableState("Tasks.view", "general");
  const [focusedTaskType, setFocusedTaskType] = usePersistentTableState("Tasks.focusedTaskType", "");
  const [rows, setRows] = useState([]);
  const [taskOwners, setTaskOwners] = useState([]);
  const [selected, setSelected] = useState({
    general: [],
    missed_call: [],
    non_responsiveness: [],
  });
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [importWarnings, setImportWarnings] = useState([]);
  const [saving, setSaving] = useState("");
  // Rows whose latest edits were saved with their row Submit/Save button.
  // Any later edit to the row turns its button back from green to blue.
  const [savedRowIds, setSavedRowIds] = useState(() => new Set());
  const [addOpen, setAddOpen] = useState(false);
  const [kind, setKind] = useState("general");
  // "missed_call" is intentionally no longer a selectable option below (its
  // Add/import UI moved to the dedicated Missed Calls page), so the default
  // now points at the one call-task subtype still offered from this page.
  const [subtype, setSubtype] = useState("non_responsiveness");
  const [importType, setImportType] = useState("");
  const [paste, setPaste] = useState("");
  const [parsed, setParsed] = useState(null);
  const [importing, setImporting] = useState(false);

  const rawRole = String(user?.role || "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
  const role =
    {
      teamlead: "team_lead",
      sme: "subject_matter_expert",
      subjectmatterexpert: "subject_matter_expert",
    }[rawRole] || rawRole;
  const canEdit = [
    "admin",
    "team_lead",
    "subject_matter_expert",
    "superuser",
    "super_user",
  ].includes(role);
  const load = useCallback(
    () =>
      getTasks(filters)
        .then((nextRows) => {
          setRows(nextRows);
          setSelected({ general: [], missed_call: [], non_responsiveness: [] });
        })
        .catch((exception) => setError(exception.message)),
    [filters],
  );

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    // Match the Leads page's agent directory, role filtering, and display names.
    if (canEdit)
      getFDUsers()
        .then((data) =>
          setTaskOwners(
            (data || []).filter(
              (item) => String(item.role || "agent").toLowerCase() === "agent",
            ),
          ),
        )
        .catch((exception) => setError(exception.message));
  }, [canEdit]);
  useEffect(() => {
    const applyNotificationFocus = () => {
      const storedFocus = window.sessionStorage.getItem(
        "flintdesk_task_notification_focus",
      );
      if (!CALL_TASK_TYPES.includes(storedFocus)) return;
      setView("calls");
      setFocusedTaskType(storedFocus);
      window.sessionStorage.removeItem("flintdesk_task_notification_focus");
    };
    applyNotificationFocus();
    window.addEventListener(
      "flintdesk-task-notification-focus",
      applyNotificationFocus,
    );
    return () =>
      window.removeEventListener(
        "flintdesk-task-notification-focus",
        applyNotificationFocus,
      );
  }, []);

  const shown = useMemo(() => rows, [rows]);
  const markRowSaved = (id, isSaved) =>
    setSavedRowIds((current) => {
      if (!id || current.has(id) === isSaved) return current;
      const next = new Set(current);
      if (isSaved) next.add(id);
      else next.delete(id);
      return next;
    });

  // After a save/submit the server returns the task as it now is: put it into its row instead of re-fetching the whole list (a slow
  // request that kept the button busy and made the page feel reloaded). If the row cannot be found any more, fall back to a reload.
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const patchSavedRow = async (row, saved) => {
    const found = saved?.id && rowsRef.current.some((item) => item === row || item.id === saved.id);
    if (!found) { await load(); return; }
    setRows((current) => current.map((item) => (item === row || item.id === saved.id ? { ...item, ...saved } : item)));
  };

  const change = (row, key, value) => {
    markRowSaved(row.id, false);
    setRows((current) =>
      current.map((item) =>
        item === row ? { ...item, data: { ...item.data, [key]: value } } : item,
      ),
    );
  };

  const submit = async (row) => {
    setSaving(row.id || row);
    setError("");
    setNotice("");
    try {
      const saved = await saveTask(row, {
        task_type: row.task_type,
        data: row.data,
        submit: true,
      });
      markRowSaved(saved?.id || row.id, true);
      setNotice(
        row.task_type === "general"
          ? "Task saved and the assigned agent was notified."
          : "Task saved.",
      );
      await patchSavedRow(row, saved);
    } catch (exception) {
      setError(exception.message);
    } finally {
      setSaving("");
    }
  };

  const saveCallRow = async (row) => {
    setSaving(row.id || row);
    setError("");
    setNotice("");
    try {
      const saved = await saveTask(row, {
        task_type: row.task_type,
        data: row.data,
        submit: Boolean(row.data?.owner),
      });
      markRowSaved(saved?.id || row.id, true);
      setNotice(
        row.data?.owner
          ? "Task saved and assigned agent notified."
          : "Task changes saved.",
      );
      await patchSavedRow(row, saved);
    } catch (exception) {
      setError(exception.message);
    } finally {
      setSaving("");
    }
  };

  const prepare = (data, type) => {
    if (!data.headers.some((header) => header.trim()))
      return setError("No header row was found.");
    const result = mapImportHeaders(
      data.headers,
      TYPES[type].fields,
      ALIASES[type],
    );
    const problems = result.mapping.some(Boolean)
      ? result.problems
      : [
          ...result.problems,
          "Import at least one recognized task column (for example Name, Contact Number, Summary, or Email).",
        ];
    setParsed({ ...data, ...result, problems });
    setError(
      problems.length
        ? `Cannot import until duplicate or ambiguous headers are corrected: ${problems.join(" ")}`
        : "",
    );
  };

  const upload = async (file) => {
    if (!file || !/\.(csv|tsv|txt)$/i.test(file.name))
      return setError("Choose a CSV, TSV, or TXT file.");
    prepare(
      parseDelimitedTable(
        await file.text(),
        /\.csv$/i.test(file.name) ? "," : "\t",
      ),
      importType,
    );
  };

  const runImport = async () => {
    if (!parsed || parsed.problems.length)
      return setError(
        "Duplicate or ambiguous task headers must be corrected before importing.",
      );
    const records = recordsFromImportTable(parsed).filter((record) =>
      Object.values(record).some((value) => String(value || "").trim()),
    );
    if (!records.length) return setError("No data rows were found to import.");
    if (records.length > MAX_IMPORT_ROWS)
      return setError(
        `Cannot import more than ${MAX_IMPORT_ROWS} rows at once. Split this into smaller batches.`,
      );
    setImporting(true);
    setError("");
    setImportWarnings([]);
    try {
      const result = await importTasks(importType, records);
      const warnings = result.invalid_fields || [];
      setImportWarnings(warnings);
      const warningSummary = warnings.length
        ? ` ${warnings.length} invalid field value${warnings.length === 1 ? " was" : "s were"} left blank.`
        : "";
      setNotice(
        `${result.inserted} ${TYPES[importType].title.toLowerCase()} imported.${warningSummary}`,
      );
      setImportType("");
      setParsed(null);
      setPaste("");
      await load();
    } catch (exception) {
      setError(exception.message);
    } finally {
      setImporting(false);
    }
  };

  const addBlank = () => {
    const type = kind === "general" ? "general" : subtype;
    setRows((current) => [{ task_type: type, data: {} }, ...current]);
    setView(type === "general" ? "general" : "calls");
    setAddOpen(false);
  };

  const toggleRow = (type, id) =>
    setSelected((current) => ({
      ...current,
      [type]: current[type].includes(id)
        ? current[type].filter((value) => value !== id)
        : [...current[type], id],
    }));

  const toggleAll = (type, items) =>
    setSelected((current) => ({
      ...current,
      [type]:
        items.length && items.every((item) => current[type].includes(item.id))
          ? []
          : items.map((item) => item.id),
    }));

  const deleteSelected = async (type) => {
    const ids = selected[type];
    if (
      !ids.length ||
      !window.confirm("Are you sure you want to delete the selected task(s)?")
    )
      return;
    setError("");
    setNotice("");
    try {
      const result = await deleteTasks(type, ids);
      setNotice(
        `${result.deleted} ${TYPES[type].title.toLowerCase()} deleted.`,
      );
      await load();
    } catch (exception) {
      setError(exception.message);
    }
  };

  const submitSelected = async (type) => {
    const selectedRows = rows.filter(
      (row) =>
        row.task_type === type &&
        row.id &&
        (selected[type] || []).includes(row.id),
    );
    if (!selectedRows.length) return;
    setSaving(`selected:${type}`);
    setError("");
    setNotice("");
    try {
      await Promise.all(
        selectedRows.map((row) =>
          saveTask(row, {
            task_type: row.task_type,
            data: row.data,
            submit: Boolean(row.data?.owner),
          }),
        ),
      );
      setNotice(
        `${selectedRows.length} ${TYPES[type].title.toLowerCase()} saved.`,
      );
      await load();
    } catch (exception) {
      setError(exception.message);
    } finally {
      setSaving("");
    }
  };

  const assignOwner = async (row, ownerId) => {
    const nextData = { ...row.data, owner: ownerId };
    // A newly added row has no database identity yet. Keep its owner in the
    // existing draft until its normal Save/Submit action creates the task.
    if (!row.id) return change(row, "owner", ownerId);
    setSaving(`${row.id}:owner`);
    setError("");
    setNotice("");
    try {
      const saved = await saveTask(row, {
        task_type: row.task_type,
        data: nextData,
        submit: true,
      });
      setRows((current) =>
        current.flatMap((item) => {
          if (item.id !== row.id) return [item];
          // Keep a filtered table truthful after a reassignment: a task moved
          // away from the selected owner should no longer remain in that view.
          return !filters.agent || filters.agent === saved.owner_id
            ? [saved]
            : [];
        }),
      );
      setNotice(
        ownerId ? "Owner updated and assignment saved." : "Owner cleared.",
      );
    } catch (exception) {
      setError(exception.message);
    } finally {
      setSaving("");
    }
  };

  const table = (type) => {
    const definition = TYPES[type];
    const items = shown.filter((row) => row.task_type === type);
    if (focusedTaskType && type !== focusedTaskType) return null;
    const agentReadOnly = !canEdit;
    const selectionControls = canEdit;
    const isCallTask = CALL_TASK_TYPES.includes(type);
    const ownerOptions = taskOwners;
    const selectedIds = selected[type] || [];
    return (
      <section className="history-table-card" key={type}>
        <div className="tasks-table-heading">
          <div>
            <h2>{definition.title}</h2>
            <p className="result-count">{items.length} entries</p>
          </div>
          {canEdit && (
            <div className="tasks-filter-actions">
              {isCallTask && (
                <>
                  <button
                    type="button"
                    className="primary-button"
                    onClick={() => setImportType(type)}
                  >
                    Add / import
                  </button>
                  <button
                    type="button"
                    className="primary-button"
                    title="Save changes for the selected tasks; owners are assigned in their Owner cells."
                    disabled={
                      !selectedIds.length || saving === `selected:${type}`
                    }
                    onClick={() => submitSelected(type)}
                  >
                    Submit selected ({selectedIds.length})
                  </button>
                </>
              )}
              <button
                type="button"
                className="table-action danger-button"
                disabled={!selectedIds.length}
                onClick={() => deleteSelected(type)}
              >
                Delete selected ({selectedIds.length})
              </button>
            </div>
          )}
        </div>
        <TableScrollWrap className="calls-table-wrap">
          <table className="calls-table dependencies-table tasks-table">
            <thead>
              <tr>
                {selectionControls && (
                  <th>
                    <input
                      aria-label={`Select all ${definition.title}`}
                      type="checkbox"
                      checked={
                        Boolean(items.length) &&
                        items.every((item) => selectedIds.includes(item.id))
                      }
                      onChange={() =>
                        toggleAll(
                          type,
                          items.filter((item) => item.id),
                        )
                      }
                    />
                  </th>
                )}
                {definition.fields.map(([, label]) => (
                  <th key={label}>{label}</th>
                ))}
                {!agentReadOnly && <th>{isCallTask ? "Save" : "Submit"}</th>}
              </tr>
            </thead>
            <tbody>
              {items.map((row, index) => (
                <tr key={row.id || index}>
                  {selectionControls && (
                    <td>
                      <input
                        aria-label={`Select row ${index + 1}`}
                        type="checkbox"
                        disabled={!row.id}
                        checked={Boolean(
                          row.id && selectedIds.includes(row.id),
                        )}
                        onChange={() => toggleRow(type, row.id)}
                      />
                    </td>
                  )}
                  {definition.fields.map(([key, label, fieldType]) => {
                    const value = row.data?.[key] || "";
                    if (agentReadOnly)
                      return (
                        <td key={key}>
                          {key === "owner"
                            ? row.owner_name || "-"
                            : fieldType === "date"
                              ? formatDisplayDate(value)
                              : value || "-"}
                        </td>
                      );
                    return (
                      <td key={key}>
                        {fieldType === "owner" ? (
                          <select
                            className="calls-filter-select"
                            aria-label={`Choose owner for ${definition.title} row ${index + 1}`}
                            disabled={!canEdit || saving === `${row.id}:owner`}
                            value={row.owner_id ?? value}
                            onChange={(event) =>
                              assignOwner(row, event.target.value)
                            }
                          >
                            <option value="">Select owner</option>
                            {ownerOptions.map((agent) => (
                              <option key={agent.id} value={agent.id}>
                                {agent.name}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            className="calls-filter-input"
                            disabled={!canEdit}
                            aria-label={label}
                            type={fieldType || "text"}
                            value={value}
                            onChange={(event) =>
                              change(row, key, event.target.value)
                            }
                          />
                        )}
                      </td>
                    );
                  })}
                  {!agentReadOnly && (
                    <td>
                      <button
                        type="button"
                        className={`table-action primary ${row.id && savedRowIds.has(row.id) ? "completed-button" : ""}`}
                        disabled={!canEdit || saving === (row.id || row)}
                        onClick={() =>
                          isCallTask ? saveCallRow(row) : submit(row)
                        }
                      >
                        {row.id && savedRowIds.has(row.id)
                          ? isCallTask
                            ? "Saved"
                            : "Submitted"
                          : isCallTask
                            ? "Save"
                            : "Submit"}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {!items.length && (
                <tr>
                  <td
                    colSpan={
                      definition.fields.length +
                      (agentReadOnly ? 0 : 1) +
                      (selectionControls ? 1 : 0)
                    }
                    className="muted"
                  >
                    No entries.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </TableScrollWrap>
      </section>
    );
  };

  const importFeedback = parsed && (
    <div className="tasks-import-feedback">
      <p>
        <strong>Mapped source columns</strong>
      </p>
      <ul>
        {parsed.headers.map((header, sourceIndex) => {
          if (!parsed.mapping[sourceIndex]) return null;
          const destination = TYPES[importType].fields.find(
            ([key]) => key === parsed.mapping[sourceIndex],
          )?.[1];
          return (
            <li key={`${header}-${sourceIndex}`}>
              {header} → {destination}
            </li>
          );
        })}
      </ul>
      {parsed.unmatchedHeaders.length > 0 && (
        <p>
          <strong>Ignored source columns:</strong>{" "}
          {parsed.unmatchedHeaders.join(", ")}
        </p>
      )}
      <p>
        <strong>Left blank/manual:</strong>{" "}
        {TYPES[importType].fields
          .filter(([key]) => !parsed.mapping.includes(key))
          .map(([, label]) => label)
          .join(", ") || "None"}
      </p>
    </div>
  );

  const assignmentGuide = canEdit && (
    <CollapsiblePanel id="tasks-assignment-guide" className="tasks-assignment-instruction" title="Assignment guide">
      <span>
        Selecting an agent above filters tasks by their current owner. To assign
        or reassign one task, choose its Owner directly in the table; the change
        is saved immediately.
      </span>
    </CollapsiblePanel>
  );
  const addTaskControls = canEdit && (
    <div className="tasks-add-controls">
      <button
        type="button"
        className="tasks-add-pill"
        onClick={() => setAddOpen((open) => !open)}
      >
        Add task
      </button>
      {addOpen && (
        <>
          <select
            className="calls-filter-select tasks-kind-select"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="general">General task</option>
            <option value="call">Call assignment task</option>
          </select>
          {kind === "call" && (
            <select
              className="calls-filter-select tasks-kind-select"
              value={subtype}
              onChange={(event) => setSubtype(event.target.value)}
            >
              {/* Missed call Add/import moved to the dedicated Missed Calls
                  page; kept here (commented out, not deleted) so the
                  underlying "missed_call" import flow can be restored to
                  this page if ever needed. */}
              {/* <option value="missed_call">Missed call</option> */}
              <option value="non_responsiveness">Non-responsive call</option>
            </select>
          )}
          <button
            type="button"
            className="tasks-add-pill"
            onClick={() =>
              kind === "general" ? addBlank() : setImportType(subtype)
            }
          >
            {kind === "general" ? "Continue" : "Add / import"}
          </button>
        </>
      )}
    </div>
  );

  return (
    <section className="process-page tasks-page">
      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector value={draft} onChange={setDraft} />
        <div className="calls-filter-group">
          <span className="calls-toolbar-label">Task view</span>
          <div className="calls-stage-view-tabs">
            <button
              type="button"
              className={`table-action ${view === "general" ? "active" : ""}`}
              onClick={() => {
                setFocusedTaskType("");
                setView("general");
              }}
            >
              General tasks
            </button>
            <button
              type="button"
              className={`table-action ${view === "calls" ? "active" : ""}`}
              onClick={() => {
                setFocusedTaskType("");
                setView("calls");
              }}
            >
              Call assignments
            </button>
          </div>
        </div>
      </ScrollableFilterBelt>
      <div className="filter-belt-actions tasks-filter-belt-actions">
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
        {addTaskControls}
      </div>
      </StickyFilterRegion>
      {assignmentGuide}
      {!canEdit && (
        <p className="muted">
          View-only access. You can see every task here; only a Team Lead, Admin or SME can change them.
        </p>
      )}
      {error && <p className="leads-error">{error}</p>}
      {notice && <p className="tasks-success">{notice}</p>}
      {importWarnings.length > 0 && (
        <div className="tasks-import-feedback">
          <strong>Imported with warnings</strong>
          <ul>
            {importWarnings.map((warning, index) => (
              <li key={`${warning.row}-${warning.field}-${index}`}>
                Row {warning.row || "-"}:{" "}
                {TYPES[warning.task_type]?.fields.find(
                  ([key]) => key === warning.field,
                )?.[1] || warning.field}{" "}
                was left blank — {warning.reason}.
              </li>
            ))}
          </ul>
        </div>
      )}
      {view === "general" ? (
        table("general")
      ) : (
        <>
          {/*
            Missed Calls now has its own dedicated page (see
            src/pages/MissedCalls.jsx). Its UI is intentionally hidden here
            rather than removed: the underlying "missed_call" task_type,
            data, and assignment/notification logic in this file (and on the
            backend) are unchanged and still power that new page, so this
            table() call is kept working and simply not rendered.
          */}
          {/* {table("missed_call")} */}
          {table("non_responsiveness")}
        </>
      )}
      {importType && (
        <div
          className="modal-backdrop leads-blur-backdrop"
          onClick={() => setImportType("")}
        >
          <div
            className="modal-card leads-import-modal"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="call-modal-header">
              <h3>Import {TYPES[importType].title}</h3>
              <button
                type="button"
                className="call-modal-close"
                onClick={() => setImportType("")}
              >
                Close
              </button>
            </div>
            <p className="muted">
              Import one or more matching columns in any order. Extra columns
              are ignored and any task columns not included remain blank.
            </p>
            <input
              type="file"
              accept=".csv,.tsv,.txt"
              onChange={(event) => upload(event.target.files?.[0])}
            />
            <textarea
              aria-label="Paste tabular task data"
              value={paste}
              onChange={(event) => setPaste(event.target.value)}
            />
            <p className="tasks-import-note">
              <em>
                Click Validate pasted data before clicking Import validated
                records.
              </em>
            </p>
            <button
              type="button"
              className="table-action"
              onClick={() => prepare(parsePastedTable(paste), importType)}
            >
              Validate pasted data
            </button>
            {importFeedback}
            <div className="leads-modal-actions">
              <button
                type="button"
                className="table-action"
                onClick={() => setImportType("")}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={!parsed || parsed.problems.length || importing}
                onClick={runImport}
              >
                Import validated records
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
