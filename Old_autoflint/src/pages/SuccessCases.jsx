import { useEffect, useMemo, useState } from "react";
import {
  getSuccessCases,
  updateSuccessCaseReviewStatus,
  updateSuccessCaseReviewComment,
  updateSuccessCaseReviewDate,
} from "../data/api";
import { BufferingOverlay } from "../components/BufferingLoader";
import { Clock, ShieldAlert, X, Eye } from "lucide-react"; // Eye for the Info view button, matching Pending Cases
import { scoreColor, parseScore } from "../utils/reportMetrics"; // reuse Dashboard's score coloring helper for the Score column

const LS_SUCCESS_CASES = "autoflint_success_cases_v1";

// Success Cases: only these two values are valid for review_status
// (mirrors STATUS_OPTIONS in PendingCases.jsx, sized down to what the
// backend's VALID_REVIEW_STATUSES accepts).
const REVIEW_STATUS_OPTIONS = ["Review Received", "Review Not Received"];
const DEFAULT_REVIEW_STATUS = "Review Not Received";

// Maps a review_status value to one of the pill color classes already
// defined for Pending Cases' Action Status pill (.pending-action-status-select.status-*)
// — reused as-is rather than adding new CSS.
function reviewStatusClass(status) {
  return status === "Review Received" ? "status-success" : "status-open";
}

function daysSince(dateStr) {
  if (!dateStr) return "—";
  const parsed = new Date(dateStr);
  if (Number.isNaN(parsed.getTime())) return "—";
  const diffMs = Date.now() - parsed.getTime();
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  return days < 0 ? 0 : days;
}

// Formats a date-ish value as YYYY-MM-DD, same as PendingCases.jsx.
function formatIsoDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—";
}

// Defaults the date filters to yesterday (start === end) so the page never
// opens with an unfiltered/empty date range.
function getYesterdayISO() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

function loadSavedSuccessCases() {
  try {
    const saved = localStorage.getItem(LS_SUCCESS_CASES);
    return saved ? JSON.parse(saved) : [];
  } catch {
    return [];
  }
}

function SuccessCases() {
  const [cases, setCases] = useState(loadSavedSuccessCases);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [updatingId, setUpdatingId] = useState(null);
  // Same filter set as Pending Cases, minus "Case Status" — the backend
  // already restricts this endpoint to Success - PC / Success - GE calls,
  // and the Action Status column/filter isn't shown on this page.
  const [filters, setFilters] = useState({
    startDate: getYesterdayISO(),
    endDate: getYesterdayISO(),
    search: "",
    frontDesk: "",
  });

  // Status-change modal — opened when the Review Status dropdown is set to
  // "Review Received"; requires a date + comment before saving, same flow
  // as PendingCases' statusChangeModal.
  // { id, label, newStatus, date, comment } | null
  const [statusChangeModal, setStatusChangeModal] = useState(null);

  // Info popup — read-only view of a case's Review Status Updated Date +
  // Review Comment, opened via the Eye button (mirrors PendingCases' infoModal).
  // { label, date, comment } | null
  const [infoModal, setInfoModal] = useState(null);

  async function fetchCases() {
    setLoading(true);
    setError("");
    try {
      const data = await getSuccessCases();
      setCases(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || "Failed to load success cases.");
      setCases([]);
    } finally {
      setLoading(false);
    }
  }

  // Generalized optimistic-update helper, same pattern as PendingCases'
  // handleFieldUpdate: apply the change to local state immediately, call
  // the backend, and roll back on failure. Unlike Pending Cases (one
  // combined PATCH), Success Cases has a separate PATCH endpoint per
  // field, so the caller passes in which API call to run.
  const handleReviewUpdate = async (id, fieldPayload, apiCall) => {
    setUpdatingId(id);
    const previous = cases;
    setCases((prev) =>
      prev.map((c) => (c.id === id ? { ...c, ...fieldPayload } : c))
    );
    try {
      await apiCall();
    } catch (err) {
      setCases(previous); // restore previous value on failure, per spec
      setError(err.message || "Failed to update case.");
    } finally {
      setUpdatingId(null);
    }
  };

  // Reverting to the default status skips the modal entirely (no date/comment
  // needed to go back), same as PendingCases' "Open" shortcut. Moving to
  // "Review Received" opens the modal, pre-filled with the row's existing
  // date/comment so edits are easy.
  const openStatusChangeModal = (item, newStatus) => {
    if (newStatus === DEFAULT_REVIEW_STATUS) {
      handleReviewUpdate(
        item.id,
        { review_status: newStatus },
        () => updateSuccessCaseReviewStatus(item.id, newStatus)
      );
      return;
    }
    setStatusChangeModal({
      id: item.id,
      label: item.formatted_call_id || item.client_name || "this case",
      newStatus,
      date: item.review_status_updated_date
        ? String(item.review_status_updated_date).split("T")[0]
        : new Date().toISOString().slice(0, 10),
      comment: item.review_status_comment || "",
    });
  };

  // Saves status + date + comment together once both required fields are
  // filled, mirroring PendingCases' saveStatusChangeModal.
  const saveStatusChangeModal = async () => {
    if (!statusChangeModal || !statusChangeModal.date || !statusChangeModal.comment) {
      return; // Require both date and comment
    }
    await handleReviewUpdate(
      statusChangeModal.id,
      {
        review_status: statusChangeModal.newStatus,
        review_status_updated_date: statusChangeModal.date,
        review_status_comment: statusChangeModal.comment,
      },
      async () => {
        await updateSuccessCaseReviewStatus(statusChangeModal.id, statusChangeModal.newStatus);
        await updateSuccessCaseReviewDate(statusChangeModal.id, statusChangeModal.date);
        await updateSuccessCaseReviewComment(statusChangeModal.id, statusChangeModal.comment);
      }
    );
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

      return matchesDate && matchesSearch && matchesFrontDesk;
    });
  }, [cases, filters]);

  const hasActiveFilters = Object.values(filters).some(Boolean);

  const clearAllFilters = () => {
    setFilters({ startDate: "", endDate: "", search: "", frontDesk: "" });
  };

  useEffect(() => {
    const loadTimer = window.setTimeout(fetchCases, 0);
    return () => window.clearTimeout(loadTimer);
  }, []);

  // Show cached rows immediately on return visits, then replace them with a
  // fresh response in the background — same caching behavior as Pending Cases.
  useEffect(() => {
    try {
      localStorage.setItem(LS_SUCCESS_CASES, JSON.stringify(cases));
    } catch {
      // A full browser store should not prevent Success Cases from working.
    }
  }, [cases]);

  return (
    <div className="business-page">
      <div className="page-header">
        <h1>Success Cases</h1>
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
        {/* Reuses the exact same base classes as Pending Cases
            (.data-table for the sticky header + cell padding, plus
            .pending-cases-table so every scoped rule for column alignment,
            the status pill, the date input, and the remarks preview button
            applies here unchanged — no CSS was added or modified). */}
        <table className="data-table pending-cases-table success-cases-table no-scroll-table">
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
                <th>Review Status</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={12} className="no-data">Loading success cases...</td>
                </tr>
              ) : filteredCases.length === 0 ? (
                <tr>
                  <td colSpan={12} className="no-data">No success cases match the selected filters.</td>
                </tr>
              ) : (
                filteredCases.map((item) => {
                  const recordingDate = item.call_recording_date || "";
                  // Default to "Review Not Received" when null, per spec.
                  // The backend already applies this default, but this
                  // keeps the UI correct even against cached/local rows.
                  const reviewStatus = item.review_status || DEFAULT_REVIEW_STATUS;
                  const reviewCommentText = item.review_status_comment || "";
                  const reviewDate = item.review_status_updated_date || "";
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
                      <td>{daysSince(recordingDate)}</td>
                      {/* Info — view icon opens a read-only popup with Review
                          Status Updated Date + Review Comment, same as
                          PendingCases' Info column. */}
                      <td>
                        <button
                          type="button"
                          className="pending-info-btn"
                          disabled={!reviewDate && !reviewCommentText}
                          onClick={() =>
                            setInfoModal({
                              label: item.formatted_call_id || item.client_name || "this case",
                              date: reviewDate,
                              comment: reviewCommentText,
                            })
                          }
                          title="View review date & comment"
                        >
                          <Eye size={16} />
                        </button>
                      </td>
                      {/* Review Status — visible pill; picking a value opens the
                          date+comment modal (or saves immediately when
                          reverting to the default status). */}
                      <td>
                        <select
                          className={`pending-action-status-select ${reviewStatusClass(reviewStatus)}`}
                          value={reviewStatus}
                          disabled={updatingId === item.id}
                          onChange={(e) => openStatusChangeModal(item, e.target.value)}
                        >
                          {REVIEW_STATUS_OPTIONS.map((option) => (
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
      <BufferingOverlay loading={loading} text="Fetching success cases..." />

      {/* Status change modal — requires both date and comment, same shell as
          PendingCases' statusChangeModal. */}
      {statusChangeModal && (
        <div className="audio-modal-overlay" onClick={() => setStatusChangeModal(null)}>
          <div className="audio-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Change Review Status</p>
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
                  {REVIEW_STATUS_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </div>
              <div className="status-modal-field">
                <label>Review Status Updated Date <span className="status-modal-required">Required</span></label>
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

      {/* Info popup — read-only view of Review Status Updated Date + Comment */}
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
                <label>Review Status Updated Date</label>
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

export default SuccessCases;