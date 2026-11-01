import { usePersistentTableState } from "../data/persistentTableState.js";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getDependencies,
  createDependency,
  updateDependency,
  approveDependency,
  deleteDependency,
  getCallSummaryByIdAndDate,
} from "../data/api.js";
import { canApproveDependencies } from "../data/accessControl.js";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { getDefaultDateRange, isValidDateRange } from "../data/filterDates.js";
import { formatDisplayDate } from "../data/displayDate.js";
import { downloadExcelWorkbook } from "../data/export.js";
import { ClientIdCell, LeadIdCell } from "../components/IdentifierCells.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";

const toISO = (d) => d.toISOString().slice(0, 10);

const isPending = (dep) =>
  String(dep.status || "").toLowerCase() !== "approved";
const REQUIRED_AGENT_FIELDS = [
  "call_id",
  "client_enquiry_date",
  "question",
  "agent_enquiry_date",
  "agent_research_about_query",
];
const hasCompleteAgentData = (dependency) =>
  REQUIRED_AGENT_FIELDS.every((field) =>
    String(dependency?.[field] ?? "").trim(),
  );

const cellValue = (value) => {
  if (!value) return "-";
  if (typeof value === "string" && value.trim() === "") return "-";
  return value;
};
const DATE_FIELDS = new Set(["client_enquiry_date", "agent_enquiry_date"]);

const radioLabelStyle = {
  display: "flex",
  alignItems: "center",
  gap: "6px",
  cursor: "pointer",
  userSelect: "none",
  fontSize: "13px",
  color: "#354055",
};

export default function Dependencies({ user, isTeamLead = false }) {
  const canReview = canApproveDependencies(user);
  const [dependencies, setDependencies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Filter states
  const [range, setRange] = usePersistentTableState("Dependencies.range", () => getDefaultDateRange());
  const [draftRange, setDraftRange] = usePersistentTableState("Dependencies.draftRange", () => getDefaultDateRange());
  const [statusFilter, setStatusFilter] = usePersistentTableState("Dependencies.statusFilter", "all"); // Applied status filter
  const [draftStatusFilter, setDraftStatusFilter] = usePersistentTableState("Dependencies.draftStatusFilter", "all");
  const [searchCallId, setSearchCallId] = usePersistentTableState("Dependencies.searchCallId", ""); // Applied search
  const [draftSearchCallId, setDraftSearchCallId] = usePersistentTableState("Dependencies.draftSearchCallId", "");
  const [searchClientId, setSearchClientId] = usePersistentTableState("Dependencies.searchClientId", "");
  const [draftSearchClientId, setDraftSearchClientId] = usePersistentTableState("Dependencies.draftSearchClientId", "");
  const [searchLeadId, setSearchLeadId] = usePersistentTableState("Dependencies.searchLeadId", "");
  const [draftSearchLeadId, setDraftSearchLeadId] = usePersistentTableState("Dependencies.draftSearchLeadId", "");

  // Row editing
  const [editingRowId, setEditingRowId] = useState(null);
  const [editingField, setEditingField] = useState(null);
  const [editingValue, setEditingValue] = useState("");
  const [cellError, setCellError] = useState("");
  const [cellSaving, setCellSaving] = useState(false);

  // Analyse modal
  const [analysingRowId, setAnalysingRowId] = useState(null);
  const [analysing, setAnalysing] = useState(false);
  const [analyseError, setAnalyseError] = useState("");
  const [newDependencyOpen, setNewDependencyOpen] = useState(false);
  const [newDependency, setNewDependency] = useState({
    call_id: "",
    client_enquiry_date: "",
    question: "",
    agent_enquiry_date: "",
    agent_research_about_query: "",
  });
  const [newDependencyError, setNewDependencyError] = useState("");
  const [newDependencySaving, setNewDependencySaving] = useState(false);
  const [deletingRowId, setDeletingRowId] = useState(null);

  // Load dependencies
  const refreshDependencies = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const pending = statusFilter === "pending";
    const completed = statusFilter === "completed";

    getDependencies(
      pending,
      completed,
      searchCallId,
      searchClientId,
      searchLeadId,
    )
      .then((data) => {
        if (!cancelled) setDependencies(data || []);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Failed to load dependencies.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [statusFilter, searchCallId, searchClientId, searchLeadId]);

  useEffect(() => {
    return refreshDependencies();
  }, [refreshDependencies]);

  const applyFilters = () => {
    if (!isValidDateRange(draftRange)) {
      setError("Date From must be on or before Date To.");
      return;
    }
    setRange(draftRange);
    setStatusFilter(draftStatusFilter);
    setSearchCallId(draftSearchCallId.trim());
    setSearchClientId(draftSearchClientId.trim());
    setSearchLeadId(draftSearchLeadId.trim());
  };

  const displayedDependencies = useMemo(
    () =>
      dependencies.filter((dependency) => {
        const date = String(dependency.client_enquiry_date || "").slice(0, 10);
        return !date || (date >= range.start && date <= range.end);
      }),
    [dependencies, range],
  );

  const openCellEditor = (rowId, field, value) => {
    setEditingRowId(rowId);
    setEditingField(field);
    setEditingValue(value && value !== "-" ? value : "");
    setCellError("");
  };

  const closeCellEditor = () => {
    setEditingRowId(null);
    setEditingField(null);
    setEditingValue("");
    setCellError("");
  };

  const saveCellEdit = async () => {
    if (!editingRowId || !editingField) return;

    setCellSaving(true);
    setCellError("");

    try {
      const dep = dependencies.find((d) => d.id === editingRowId);
      if (!dep) throw new Error("Dependency not found");

      const reviewerFields = [
        "subject_matter_expert",
        "subject_matter_expert_comment",
      ];
      if (reviewerFields.includes(editingField) && !canReview) {
        throw new Error(
          "Only Subject Matter Experts, Team Leads, and Admins can edit this field.",
        );
      }

      const payload = {};

      // Map field names to camelCase for the API
      if (editingField === "client_enquiry_date") {
        payload.client_enquiry_date = editingValue ? editingValue : null;
      } else if (editingField === "call_id") {
        payload.call_id = editingValue;
      } else if (editingField === "question") {
        payload.question = editingValue;
      } else if (editingField === "agent_enquiry_date") {
        payload.agent_enquiry_date = editingValue ? editingValue : null;
      } else if (editingField === "agent_research_about_query") {
        payload.agent_research_about_query = editingValue;
      } else if (
        editingField === "subject_matter_expert" ||
        editingField === "subject_matter_expert_comment"
      ) {
        payload[editingField] = editingValue;
      }

      const saved = await updateDependency(editingRowId, payload);

      // Update local state
      setDependencies((current) =>
        current.map((dep) => {
          if (dep.id !== editingRowId) return dep;
          return { ...dep, ...saved, [editingField]: editingValue || null };
        }),
      );

      closeCellEditor();
    } catch (err) {
      setCellError(err.message || "Failed to save");
    } finally {
      setCellSaving(false);
    }
  };

  const handleApproval = async (dep) => {
    if (!canReview) return;
    if (!hasCompleteAgentData(dep)) {
      setError(
        "Complete Call ID, Client Enquiry Date, Query, Agent Enquiry Date, and Agent Research before approval.",
      );
      return;
    }
    setCellSaving(true);
    setError(null);
    try {
      const saved = await approveDependency(dep.id, {
        subject_matter_expert: dep.subject_matter_expert || "",
        subject_matter_expert_comment: dep.subject_matter_expert_comment || "",
      });
      setDependencies((current) =>
        current.map((item) =>
          item.id === dep.id ? { ...item, ...saved, status: "approved" } : item,
        ),
      );
    } catch (approvalError) {
      setError(approvalError.message || "Failed to approve dependency.");
    } finally {
      setCellSaving(false);
    }
  };

  const handleAnalyse = async (rowId) => {
    const dep = dependencies.find((d) => d.id === rowId);
    if (!dep) return;

    if (!dep.call_id || !dep.call_id.trim()) {
      setAnalyseError("Please enter a Call ID first");
      return;
    }

    if (!dep.client_enquiry_date) {
      setAnalyseError("Please enter Client Enquiry Date first");
      return;
    }

    setAnalysingRowId(rowId);
    setAnalysing(true);
    setAnalyseError("");

    try {
      // Format date for API
      const dateStr =
        typeof dep.client_enquiry_date === "string"
          ? dep.client_enquiry_date
          : toISO(new Date(dep.client_enquiry_date));

      const result = await getCallSummaryByIdAndDate(
        dep.call_id.trim(),
        dateStr,
      );

      if (!result.call_summary) {
        setAnalyseError(result.message || "No matching call found.");
        return;
      }

      // Update the dependency with the fetched call summary
      await updateDependency(rowId, { call_summary: result.call_summary });

      setDependencies((current) =>
        current.map((d) => {
          if (d.id !== rowId) return d;
          return { ...d, call_summary: result.call_summary };
        }),
      );

      setAnalysingRowId(null);
    } catch (err) {
      setAnalyseError(err.message || "Failed to fetch call summary");
    } finally {
      setAnalysing(false);
    }
  };

  const createNewDependency = async () => {
    if (
      !newDependency.call_id.trim() ||
      !newDependency.client_enquiry_date ||
      !newDependency.question.trim() ||
      !newDependency.agent_enquiry_date ||
      !newDependency.agent_research_about_query.trim()
    ) {
      setNewDependencyError(
        "Call ID, Client Enquiry Date, Query, Agent Enquiry Date, and Agent Research are required.",
      );
      return;
    }
    setNewDependencySaving(true);
    setNewDependencyError("");
    try {
      const newDep = await createDependency({
        call_id: newDependency.call_id.trim(),
        client_enquiry_date: newDependency.client_enquiry_date,
        question: newDependency.question.trim(),
        agent_enquiry_date: newDependency.agent_enquiry_date || null,
        agent_research_about_query:
          newDependency.agent_research_about_query.trim(),
      });
      if (!newDep?.id)
        throw new Error("The server did not return the created dependency.");
      const refreshed = await getDependencies(
        statusFilter === "pending",
        statusFilter === "completed",
        searchCallId,
        searchClientId,
        searchLeadId,
      );
      setDependencies(Array.isArray(refreshed) ? refreshed : [newDep]);
      setError(null);
      setNewDependency({
        call_id: "",
        client_enquiry_date: "",
        question: "",
        agent_enquiry_date: "",
        agent_research_about_query: "",
      });
      setNewDependencyOpen(false);
    } catch (err) {
      setNewDependencyError(err.message || "Failed to create dependency");
    } finally {
      setNewDependencySaving(false);
    }
  };

  const exportDependencies = () => {
    const columns = [
      "Sl No",
      "Client Enquiry Date",
      "Call ID",
      "Client ID",
      "Lead ID",
      "Call Summary",
      "Query",
      "Agent Enquiry Date",
      "Agent Name",
      "Agent Research About Query",
      "Subject Matter Expert",
      "Subject Matter Expert Comment",
      "Approval",
    ];
    const rows = displayedDependencies.map((dep, index) => [
      index + 1,
      dep.client_enquiry_date || "",
      dep.call_id || "",
      dep.client_id || "",
      dep.lead_id || "",
      dep.call_summary || "",
      dep.question || "",
      dep.agent_enquiry_date || "",
      dep.agent_name || dep.agent?.name || "",
      dep.agent_research_about_query || dep.agent_research || "",
      dep.subject_matter_expert || "",
      dep.subject_matter_expert_comment || "",
      dep.status || "pending",
    ]);
    downloadExcelWorkbook(
      [{ name: "Dependencies", columns, rows }],
      `FlintDesk_Dependencies_${range.start}_to_${range.end}.xls`,
    );
  };

  const handleDelete = async (dependency) => {
    if (!isTeamLead || deletingRowId) return;
    if (
      !window.confirm(
        `Delete dependency for ${dependency.call_id || "this call"}?`,
      )
    )
      return;
    setDeletingRowId(dependency.id);
    setError(null);
    try {
      await deleteDependency(dependency.id);
      setDependencies((current) =>
        current.filter((item) => item.id !== dependency.id),
      );
    } catch (err) {
      setError(err.message || "Failed to delete dependency.");
    } finally {
      setDeletingRowId(null);
    }
  };

  // Render a cell based on field type and permissions
  const renderCell = (dep, field) => {
    const isReviewerField = [
      "subject_matter_expert",
      "subject_matter_expert_comment",
    ].includes(field);
    const isEditable = !isReviewerField || canReview;
    const value = dep[field];
    const displayValue = DATE_FIELDS.has(field)
      ? formatDisplayDate(value)
      : cellValue(value);

    // If editing this cell
    if (editingRowId === dep.id && editingField === field) {
      if (field === "client_enquiry_date" || field === "agent_enquiry_date") {
        return (
          <input
            type="date"
            value={editingValue}
            onChange={(e) => setEditingValue(e.target.value)}
            disabled={cellSaving}
            autoFocus
          />
        );
      } else {
        return (
          <input
            type="text"
            value={editingValue}
            onChange={(e) => setEditingValue(e.target.value)}
            disabled={cellSaving}
            autoFocus
          />
        );
      }
    }

    // Normal display
    if (isReviewerField && !canReview) {
      // Agents see persisted reviewer values but cannot alter them locally.
      return <span>{displayValue}</span>;
    }

    // Editable cell
    if (isEditable && field !== "call_summary") {
      return (
        <span
          className="calls-cell-clickable"
          onClick={() => openCellEditor(dep.id, field, value)}
          title={displayValue === "-" ? "Click to enter" : displayValue}
        >
          {displayValue}
        </span>
      );
    }

    // Read-only cell
    return <span>{displayValue}</span>;
  };

  return (
    <section className="process-page">
      <div className="calls-header-block">
        <h1>Doubts</h1>
        <p>Track queries and follow-ups from client calls.</p>
      </div>

      <StickyFilterRegion>
      <ScrollableFilterBelt className="dependencies-filter-belt">
        <DateRangeSelector value={draftRange} onChange={setDraftRange} />

        <div className="calls-filter-group dependency-search-filter">
          <label>
            <span>Search by Call ID</span>
            <input
              type="text"
              placeholder="Enter Call ID"
              value={draftSearchCallId}
              onChange={(e) => setDraftSearchCallId(e.target.value)}
            />
          </label>
        </div>

        <div className="calls-filter-group dependency-search-filter">
          <label>
            <span>Search by Client ID</span>
            <input
              type="text"
              placeholder="Enter Client ID"
              value={draftSearchClientId}
              onChange={(e) => setDraftSearchClientId(e.target.value)}
            />
          </label>
        </div>

        <div className="calls-filter-group dependency-search-filter">
          <label>
            <span>Search by Lead ID</span>
            <input
              type="text"
              placeholder="Enter Lead ID"
              value={draftSearchLeadId}
              onChange={(e) => setDraftSearchLeadId(e.target.value)}
            />
          </label>
        </div>

        <div className="calls-filter-group">
          <label style={radioLabelStyle}>
            <input
              type="radio"
              name="status"
              value="all"
              checked={draftStatusFilter === "all"}
              onChange={(e) => setDraftStatusFilter(e.target.value)}
            />
            <span>All</span>
          </label>
          <label style={radioLabelStyle}>
            <input
              type="radio"
              name="status"
              value="pending"
              checked={draftStatusFilter === "pending"}
              onChange={(e) => setDraftStatusFilter(e.target.value)}
            />
            <span>Pending</span>
          </label>
          <label style={radioLabelStyle}>
            <input
              type="radio"
              name="status"
              value="completed"
              checked={draftStatusFilter === "completed"}
              onChange={(e) => setDraftStatusFilter(e.target.value)}
            />
            <span>Completed</span>
          </label>
        </div>

        <button
          type="button"
          className="leads-add-button"
          onClick={() => {
            setNewDependencyError("");
            setNewDependencyOpen(true);
          }}
          style={{ whiteSpace: "nowrap" }}
        >
          + New Doubt
        </button>
      </ScrollableFilterBelt>
      <div className="filter-belt-actions">
        <button
          type="button"
          className="primary-button calls-toolbar-apply"
          onClick={applyFilters}
          disabled={loading}
        >
          {loading ? "..." : "Apply"}
        </button>
        <button
          type="button"
          className="table-action"
          onClick={exportDependencies}
          title="Export all filtered dependencies to Excel"
        >
          <i className="fa-solid fa-file-excel" /> Export to Excel
        </button>
      </div>
      </StickyFilterRegion>

      <section className="history-table-card">
        <h2>Doubts</h2>
        <p className="result-count">
          {loading
            ? "Loading..."
            : `${displayedDependencies.length} doubts`}
        </p>
        {error && (
          <p className="muted" style={{ color: "#dc2626" }}>
            {error}
          </p>
        )}

        {loading ? (
          <div className="calls-table-loading">
            <BufferingLoader text="Fetching doubts" size="md" />
          </div>
        ) : (
          <TableScrollWrap className="calls-table-wrap">
            <table className="calls-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Client Enquiry Date</th>
                  <th>Call ID</th>
                  <th>Client ID</th>
                  <th>Lead ID</th>
                  <th>Call Summary</th>
                  <th style={{ position: "relative" }}>
                    <div>Query</div>
                  </th>
                  <th>Agent Enquiry Date</th>
                  <th>Agent Name</th>
                  <th>Agent Research About Query</th>
                  <th style={{ color: "#b91c1c" }}>Subject Matter Expert</th>
                  <th style={{ color: "#b91c1c" }}>
                    Subject Matter Expert Comment
                  </th>
                  {canReview && <th>Approval</th>}
                  {isTeamLead && <th>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {displayedDependencies.map((dep, index) => {
                  const pending = isPending(dep);
                  return (
                    <tr key={dep.id} style={{ opacity: pending ? 1 : 0.9 }}>
                      <td>{index + 1}</td>
                      <td>{renderCell(dep, "client_enquiry_date")}</td>
                      <td>{renderCell(dep, "call_id")}</td>
                      <td>
                        <ClientIdCell clientId={dep.client_id} />
                      </td>
                      <td>
                        <LeadIdCell leadId={dep.lead_id} />
                      </td>
                      <td className="calls-cell-wide calls-call-summary-cell">
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "8px",
                          }}
                        >
                          <span
                            className="calls-call-summary-text"
                            title={cellValue(dep.call_summary)}
                          >
                            {cellValue(dep.call_summary)}
                          </span>
                          <button
                            type="button"
                            className="icon-action-btn dependency-analyse-button"
                            onClick={() => handleAnalyse(dep.id)}
                            disabled={analysing || analysingRowId === dep.id}
                            title="Fetch call summary using Call ID and date"
                          >
                            {analysingRowId === dep.id && analysing ? (
                              <i
                                className="fa-solid fa-spinner"
                                style={{ animation: "spin 1s linear infinite" }}
                              />
                            ) : (
                              <>
                                <i className="fa-solid fa-magnifying-glass" />{" "}
                                Analyse
                              </>
                            )}
                          </button>
                        </div>
                        {analysingRowId === dep.id && analyseError && (
                          <p
                            style={{
                              color: "#dc2626",
                              fontSize: "12px",
                              marginTop: "4px",
                            }}
                          >
                            {analyseError}
                          </p>
                        )}
                      </td>
                      <td className="calls-cell-wide">
                        {renderCell(dep, "question")}
                      </td>
                      <td>{renderCell(dep, "agent_enquiry_date")}</td>
                      <td>{cellValue(dep.agent_name || dep.agent?.name)}</td>
                      <td className="calls-cell-wide">
                        {renderCell(dep, "agent_research_about_query")}
                      </td>
                      <td>{renderCell(dep, "subject_matter_expert")}</td>
                      <td className="calls-cell-wide">
                        {renderCell(dep, "subject_matter_expert_comment")}
                      </td>
                      {canReview && (
                        <td>
                          <button
                            type="button"
                            className="table-action primary"
                            onClick={() => handleApproval(dep)}
                            disabled={
                              cellSaving ||
                              String(dep.status || "").toLowerCase() ===
                                "approved" ||
                              !hasCompleteAgentData(dep)
                            }
                            title={
                              !hasCompleteAgentData(dep)
                                ? "Complete all agent fields before approval"
                                : "Approve dependency"
                            }
                          >
                            {String(dep.status || "").toLowerCase() ===
                            "approved"
                              ? "Approved"
                              : "Approve"}
                          </button>
                        </td>
                      )}
                      {isTeamLead && (
                        <td>
                          <button
                            type="button"
                            className="table-action dependency-delete-button"
                            onClick={() => handleDelete(dep)}
                            disabled={deletingRowId === dep.id}
                            title="Delete dependency"
                          >
                            <i className="fa-solid fa-trash" />{" "}
                            {deletingRowId === dep.id
                              ? "Deleting..."
                              : "Delete"}
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
                {!displayedDependencies.length && !loading && (
                  <tr>
                    <td
                      colSpan={12 + (canReview ? 1 : 0) + (isTeamLead ? 1 : 0)}
                      className="muted"
                    >
                      No doubts found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableScrollWrap>
        )}
      </section>

      {newDependencyOpen && (
        <div
          className="call-modal-overlay"
          onClick={() => !newDependencySaving && setNewDependencyOpen(false)}
        >
          <form
            className="call-modal-card call-modal-medium dependency-form"
            onSubmit={(event) => {
              event.preventDefault();
              createNewDependency();
            }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="call-modal-header">
              <div>
                <p className="call-modal-kicker">Doubts</p>
                <h2>New Doubt</h2>
              </div>
              <button
                type="button"
                className="call-modal-close"
                onClick={() => setNewDependencyOpen(false)}
                disabled={newDependencySaving}
              >
                Close
              </button>
            </div>
            <div className="call-modal-content dependency-form-fields">
              {newDependencyError && (
                <p className="dependency-form-error">{newDependencyError}</p>
              )}
              <label>
                Call ID
                <input
                  value={newDependency.call_id}
                  onChange={(event) =>
                    setNewDependency((current) => ({
                      ...current,
                      call_id: event.target.value,
                    }))
                  }
                />
              </label>
              <label>
                Client Enquiry Date
                <input
                  type="date"
                  value={newDependency.client_enquiry_date}
                  onChange={(event) =>
                    setNewDependency((current) => ({
                      ...current,
                      client_enquiry_date: event.target.value,
                    }))
                  }
                />
              </label>
              <label>
                Query
                <textarea
                  rows={4}
                  value={newDependency.question}
                  onChange={(event) =>
                    setNewDependency((current) => ({
                      ...current,
                      question: event.target.value,
                    }))
                  }
                />
              </label>
              <label>
                Agent Enquiry Date
                <input
                  type="date"
                  value={newDependency.agent_enquiry_date}
                  onChange={(event) =>
                    setNewDependency((current) => ({
                      ...current,
                      agent_enquiry_date: event.target.value,
                    }))
                  }
                />
              </label>
              <label>
                Agent Research About Query
                <textarea
                  rows={4}
                  value={newDependency.agent_research_about_query}
                  onChange={(event) =>
                    setNewDependency((current) => ({
                      ...current,
                      agent_research_about_query: event.target.value,
                    }))
                  }
                />
              </label>
            </div>
            <div className="call-modal-footer">
              <button
                type="button"
                className="table-action"
                onClick={() => setNewDependencyOpen(false)}
                disabled={newDependencySaving}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="table-action primary"
                disabled={newDependencySaving}
              >
                {newDependencySaving ? "Creating..." : "Create doubt"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Cell Editing Modal */}
      {editingRowId && editingField && (
        <div className="call-modal-overlay" onClick={closeCellEditor}>
          <div
            className="call-modal-card call-modal-medium"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="call-modal-header">
              <div>
                <p className="call-modal-kicker">Edit Field</p>
                <h2>{editingField.replace(/_/g, " ")}</h2>
              </div>
              <button
                type="button"
                className="call-modal-close"
                onClick={closeCellEditor}
              >
                Close
              </button>
            </div>
            <div className="call-modal-content">
              {cellError && (
                <p style={{ color: "#dc2626", marginBottom: "12px" }}>
                  {cellError}
                </p>
              )}
              {editingField === "client_enquiry_date" ||
              editingField === "agent_enquiry_date" ? (
                <input
                  type="date"
                  value={editingValue}
                  onChange={(e) => setEditingValue(e.target.value)}
                  disabled={cellSaving}
                  autoFocus
                  style={{
                    width: "100%",
                    padding: "8px",
                    fontSize: "14px",
                    border: "1px solid #e5e7eb",
                    borderRadius: "6px",
                  }}
                />
              ) : (
                <textarea
                  value={editingValue}
                  onChange={(e) => setEditingValue(e.target.value)}
                  disabled={cellSaving}
                  autoFocus
                  className="cell-modal-textarea"
                  rows={4}
                />
              )}
            </div>
            <div className="call-modal-footer">
              <button
                type="button"
                className="table-action"
                onClick={closeCellEditor}
                disabled={cellSaving}
              >
                Cancel
              </button>
              <button
                type="button"
                className="table-action primary"
                onClick={saveCellEdit}
                disabled={cellSaving}
              >
                {cellSaving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </section>
  );
}
