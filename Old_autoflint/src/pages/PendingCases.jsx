import { useEffect, useMemo, useState } from "react";
import { getPendingCases, updatePendingCase } from "../data/api";
import { BufferingOverlay } from "../components/BufferingLoader";
import { Clock, ShieldAlert, X, Eye } from "lucide-react"; // NEW: X for the Remarks modal close button, Eye for the Info column
import { scoreColor, parseScore } from "../utils/reportMetrics"; // reuse Dashboard's score coloring helper for the Score column

const LS_PENDING_CASES = "autoflint_pending_cases_v1";

const STATUS_OPTIONS = [
  "Open",
  "Success - PC",
  "Success - GE",
  "PC Follow Up",
  "PC shared and lost",
  "Fee details shared & FU",
  "Client Side delay",
  "Moved to next date",
  "Blocker",
  "Dropped",
];

// NEW: maps an action_status value to one of a small set of color classes
// for the pill dropdown (mirrors how the Dashboard tracker color-codes its
// follow_up_status badge, just extended to cover all 10 dropdown values).
function actionStatusClass(status) {
  const s = String(status || "Open").toLowerCase();
  if (s.startsWith("success")) return "status-success";
  if (s === "blocker" || s === "dropped") return "status-blocker";
  if (s === "open") return "status-open";
  return "status-followup"; // PC Follow Up, shared and lost, Fee details, Client Side delay, Moved to next date
}

function daysSince(dateStr) {
  if (!dateStr) return "—";
  const parsed = new Date(dateStr);
  if (Number.isNaN(parsed.getTime())) return "—";
  const diffMs = Date.now() - parsed.getTime();
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  return days < 0 ? 0 : days;
}

// Defaults the date filters to yesterday (start === end) so the page never
// opens with an unfiltered/empty date range.
function getYesterdayISO() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

// Formats a date-ish value as YYYY-MM-DD, same as the Dashboard tracker's Deadline column.
function formatIsoDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—";
}

function loadSavedPendingCases() {
  try {
    const saved = localStorage.getItem(LS_PENDING_CASES);
    return saved ? JSON.parse(saved) : [];
  } catch {
    return [];
  }
}

function PendingCases() {
  const [cases, setCases] = useState(loadSavedPendingCases);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [updatingId, setUpdatingId] = useState(null);
  const [filters, setFilters] = useState({
    startDate: getYesterdayISO(),
    endDate: getYesterdayISO(),
    search: "",
    frontDesk: "",
    status: "",
  });

  // NEW: Status change modal state
  // { id, currentStatus, newStatus, date, comment } | null
  const [statusChangeModal, setStatusChangeModal] = useState(null);

  // Info popup state — read-only view of a case's Action Date + Comment
  // { label, date, comment } | null
  const [infoModal, setInfoModal] = useState(null);

  async function fetchCases() {
    setLoading(true);
    setError("");
    try {
      const data = await getPendingCases();
      setCases(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || "Failed to load pending cases.");
      setCases([]);
    } finally {
      setLoading(false);
    }
  };

  // Generalized field updater shared by Action Status / Action Date / Remarks
  // so all three editable fields save through the same optimistic-update path
  // and hit the same PATCH /calls/pending/{id} endpoint.
  const handleFieldUpdate = async (id, fieldPayload) => {
    setUpdatingId(id);
    const previous = cases;
    setCases((prev) =>
      prev.map((c) => (c.id === id ? { ...c, ...fieldPayload } : c))
    );
    try {
      await updatePendingCase(id, fieldPayload);
    } catch (err) {
      setCases(previous);
      setError(err.message || "Failed to update case.");
    } finally {
      setUpdatingId(null);
    }
  };

  // Opens the status-change modal pre-filled with the value just picked
  // from the inline status dropdown; date + comment are still required
  // before the status change is actually saved. Reverting to "Open" skips
  // the modal entirely (no date/comment needed to go back to the default
  // status) so Days Past immediately falls back to counting from
  // call_recording_date (Open is excluded from statusesForActionDate below).
  const openStatusChangeModal = (item, newStatus) => {
    if (newStatus === "Open") {
      handleFieldUpdate(item.id, { action_status: "Open" });
      return;
    }
    setStatusChangeModal({
      id: item.id,
      label: item.formatted_call_id || item.client_name || "this case",
      currentStatus: item.action_status || "Open",
      newStatus: newStatus || item.action_status || "Open",
      date: "",
      comment: "",
    });
  };

  // NEW: handle status change confirmation
  const saveStatusChangeModal = async () => {
    if (!statusChangeModal || !statusChangeModal.date || !statusChangeModal.comment) {
      return; // Require both date and comment
    }
    await handleFieldUpdate(statusChangeModal.id, {
      action_status: statusChangeModal.newStatus,
      action_date: statusChangeModal.date,
      remarks: statusChangeModal.comment,
    });
    setStatusChangeModal(null);
  };

  const filteredCases = useMemo(() => {
    const search = filters.search.trim().toLowerCase();
    const frontDesk = filters.frontDesk.trim().toLowerCase();

    return cases.filter((item) => {
      const recordingDate = String(item.call_recording_date || "").split("T")[0];
      const matchesDate =
        (!filters.startDate || recordingDate >= filters.startDate) &&
        (!filters.endDate || recordingDate <= filters.endDate);
      const searchableValues = [
        item.formatted_call_id,
        item.client_name,
        item.front_desk_person_name,
      ].map((value) => String(value || "").toLowerCase());
      const matchesSearch = !search || searchableValues.some((value) => value.includes(search));
      const matchesFrontDesk = !frontDesk || String(item.front_desk_person_name || "").toLowerCase().includes(frontDesk);
      const matchesStatus = !filters.status || (item.action_status || "Open") === filters.status;

      return matchesDate && matchesSearch && matchesFrontDesk && matchesStatus;
    });
  }, [cases, filters]);

  const hasActiveFilters = Object.values(filters).some(Boolean);

  const clearAllFilters = () => {
    setFilters({ startDate: "", endDate: "", search: "", frontDesk: "", status: "" });
  }

  useEffect(() => {
    const loadTimer = window.setTimeout(fetchCases, 0);
    return () => window.clearTimeout(loadTimer);
  }, []);

  // Show cached rows immediately on return visits, then replace them with a
  // fresh response in the background, mirroring the Call Records experience.
  useEffect(() => {
    try {
      localStorage.setItem(LS_PENDING_CASES, JSON.stringify(cases));
    } catch {
      // A full browser store should not prevent Pending Cases from working.
    }
  }, [cases]);

  return (
    <div className="business-page">
      <div className="page-header">
        <h1>Pending Cases</h1>
      </div>

      <div className="filters-panel call-filters-panel">
        <div className="filters-row">
          <div className="filter-group filter-group-compact">
            <label>Start Date</label>
            <input
              className="filter-date-input"
              type="date"
              value={filters.startDate}
              onChange={(e) => setFilters((current) => ({ ...current, startDate: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-compact">
            <label>End Date</label>
            <input
              className="filter-date-input"
              type="date"
              value={filters.endDate}
              onChange={(e) => setFilters((current) => ({ ...current, endDate: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-extension">
            <label>Search Cases</label>
            <input
              type="text"
              placeholder="Search call ID or client..."
              value={filters.search}
              onChange={(e) => setFilters((current) => ({ ...current, search: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-extension">
            <label>Front Desk Person</label>
            <input
              type="text"
              placeholder="Filter by front desk person..."
              value={filters.frontDesk}
              onChange={(e) => setFilters((current) => ({ ...current, frontDesk: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-compact">
            <label>Case Status</label>
            <select
              className="filter-date-input"
              value={filters.status}
              onChange={(e) => setFilters((current) => ({ ...current, status: e.target.value }))}
            >
              <option value="">All</option>
              {STATUS_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </div>
          {hasActiveFilters && (
            <div className="filter-actions" style={{ display: "flex", alignItems: "flex-end" }}>
              <button type="button" onClick={clearAllFilters} className="btn-secondary filter-clear-btn">
                Clear All Filters
              </button>
            </div>
          )}
        </div>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div className="table-wrapper">
        <table className="data-table pending-cases-table no-scroll-table">
            <thead>
              <tr>
                <th>Call Number</th>
                <th>Date</th>
                <th>Client Name</th>
                <th>Front Desk</th>
                <th>Case Type</th>
                <th>Follow Up</th>
                <th>Deadline</th>
                <th>Escalation</th>
                <th>Score</th>
                <th>Days Past</th>
                <th>Info</th>
                <th>Case Status</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={12} className="no-data">Loading pending cases...</td>
                </tr>
              ) : filteredCases.length === 0 ? (
                <tr>
                  <td colSpan={12} className="no-data">No pending cases match the selected filters.</td>
                </tr>
              ) : (
                filteredCases.map((item) => {
                  const recordingDate = item.call_recording_date || "";
                  const actionDate = item.action_date || "";
                  const remarks = item.remarks || "";
                  
                  // Calculate Days Past based on status and action_date
                  const statusesForActionDate = ["Success - PC", "Success - GE", "Blocker", "Dropped"];
                  const daysPastValue = 
                    statusesForActionDate.includes(item.action_status) && actionDate
                      ? daysSince(actionDate)
                      : daysSince(recordingDate);
                  
                  return (
                    <tr key={item.id}>
                      <td>{item.formatted_call_id || "—"}</td>
                      <td>{formatIsoDate(recordingDate)}</td>
                      <td className="pending-case-client-name">{item.client_name || "—"}</td>
                      <td className="pending-case-front-desk">{item.front_desk_person_name || "—"}</td>
                      <td><span className="case-badge">{item.case_type || "—"}</span></td>
                      <td>
                        {String(item.follow_up_required || "").toLowerCase() === "yes" ? (
                          <span className="badge followup-badge"><Clock size={12} /> REQ</span>
                        ) : (
                          <span className="badge none-badge">—</span>
                        )}
                      </td>
                      <td>{formatIsoDate(item.follow_up_deadline)}</td>
                      <td>
                        {String(item.escalation_required || "").toLowerCase() === "yes" ? (
                          <span className="badge escalation-badge"><ShieldAlert size={12} /> Yes</span>
                        ) : (
                          <span className="badge none-badge">—</span>
                        )}
                      </td>
                      <td>
                        <span className="score-pill" style={{ color: scoreColor(parseScore(item.overall_call_quality_score)) }}>
                          {item.overall_call_quality_score ?? "—"}
                        </span>
                      </td>
                      <td>{daysPastValue}</td>
                      {/* Info — view icon opens a read-only popup with Action Date
                          + Comment; blurred/disabled when both are unset. */}
                      <td>
                        <button
                          type="button"
                          className="pending-info-btn"
                          disabled={!actionDate && !remarks}
                          onClick={() =>
                            setInfoModal({
                              label: item.formatted_call_id || item.client_name || "this case",
                              date: actionDate,
                              comment: remarks,
                            })
                          }
                          title="View action date & comment"
                        >
                          <Eye size={16} />
                        </button>
                      </td>
                      {/* Case Status — visible pill; opening it reveals the
                          dropdown, and picking a value opens the date+comment modal. */}
                      <td>
                        <select
                          className={`pending-action-status-select ${actionStatusClass(item.action_status)}`}
                          value={item.action_status || "Open"}
                          disabled={updatingId === item.id}
                          onChange={(e) => openStatusChangeModal(item, e.target.value)}
                        >
                          {STATUS_OPTIONS.map((option) => (
                            <option key={option} value={option}>
                              {option}
                            </option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
      </div>
      <BufferingOverlay loading={loading} text="Fetching pending cases..." />

      {/* Status change modal — requires both date and comment */}
      {statusChangeModal && (
        <div className="audio-modal-overlay" onClick={() => setStatusChangeModal(null)}>
          <div className="audio-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Change Case Status</p>
                <h2>{statusChangeModal.label}</h2>
              </div>
              <div className="audio-modal-actions">
                <button type="button" className="audio-modal-close" onClick={() => setStatusChangeModal(null)}>
                  <X size={16} />
                </button>
              </div>
            </div>
            <div className="status-modal-body">
              <div className="status-modal-field">
                <label>New Status</label>
                <select
                  className="filter-date-input"
                  value={statusChangeModal.newStatus}
                  onChange={(e) => setStatusChangeModal((current) => ({ ...current, newStatus: e.target.value }))}
                >
                  {STATUS_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </div>
              <div className="status-modal-field">
                <label>Action Date <span className="status-modal-required">Required</span></label>
                <input
                  className="filter-date-input"
                  type="date"
                  value={statusChangeModal.date}
                  onChange={(e) => setStatusChangeModal((current) => ({ ...current, date: e.target.value }))}
                />
              </div>
              <div className="status-modal-field">
                <label>Comment <span className="status-modal-required">Required</span></label>
                <textarea
                  className="remarks-modal-textarea"
                  value={statusChangeModal.comment}
                  placeholder="Add comment for this status change..."
                  onChange={(e) => setStatusChangeModal((current) => ({ ...current, comment: e.target.value }))}
                />
              </div>
            </div>
            <div className="remarks-modal-footer">
              <button type="button" className="btn-secondary" onClick={() => setStatusChangeModal(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="dashboard-filter-btn btn-apply"
                onClick={saveStatusChangeModal}
                disabled={!statusChangeModal.date || !statusChangeModal.comment || updatingId === statusChangeModal.id}
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Info popup — read-only view of Action Date + Comment */}
      {infoModal && (
        <div className="audio-modal-overlay" onClick={() => setInfoModal(null)}>
          <div className="audio-modal-card info-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Case Info</p>
                <h2>{infoModal.label}</h2>
              </div>
              <div className="audio-modal-actions">
                <button type="button" className="audio-modal-close" onClick={() => setInfoModal(null)}>
                  <X size={16} />
                </button>
              </div>
            </div>
            <div className="status-modal-body">
              <div className="status-modal-field">
                <label>Action Date</label>
                <p className="info-modal-value">{formatIsoDate(infoModal.date)}</p>
              </div>
              <div className="status-modal-field">
                <label>Comment</label>
                <p className="info-modal-value info-modal-comment">{infoModal.comment || "—"}</p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default PendingCases;