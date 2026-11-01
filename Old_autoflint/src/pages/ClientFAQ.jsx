import { useEffect, useMemo, useState } from "react";
import { getClientFaq } from "../data/api";
import { BufferingOverlay } from "../components/BufferingLoader";

const LS_CLIENT_FAQ = "autoflint_client_faq_v1";

function formatIsoDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : String(value).split("T")[0];
}

function loadSavedClientFaq() {
  try {
    const saved = localStorage.getItem(LS_CLIENT_FAQ);
    return saved ? JSON.parse(saved) : [];
  } catch {
    return [];
  }
}

// Defaults the date filters to yesterday (start === end) so the page never
// opens with an unfiltered/empty date range.
function getYesterdayISO() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

function ClientFAQ() {
  const [rows, setRows] = useState(loadSavedClientFaq);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState({
    startDate: getYesterdayISO(),
    endDate: getYesterdayISO(),
    search: "",
  });

  async function fetchRows() {
    setLoading(true);
    setError("");
    try {
      const data = await getClientFaq();
      setRows(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || "Failed to load client questions.");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }

  const filteredRows = useMemo(() => {
    const search = filters.search.trim().toLowerCase();

    return rows.filter((item) => {
      const recordingDate = String(item.call_date || "").split("T")[0];
      const matchesDate =
        (!filters.startDate || recordingDate >= filters.startDate) &&
        (!filters.endDate || recordingDate <= filters.endDate);
      const searchableValues = [item.formatted_call_id, item.call_reference, item.question].map(
        (value) => String(value || "").toLowerCase()
      );
      const matchesSearch = !search || searchableValues.some((value) => value.includes(search));

      return matchesDate && matchesSearch;
    });
  }, [rows, filters]);

  const hasActiveFilters = Object.values(filters).some(Boolean);

  const clearAllFilters = () => {
    setFilters({ startDate: "", endDate: "", search: "" });
  };

  useEffect(() => {
    const loadTimer = window.setTimeout(fetchRows, 0);
    return () => window.clearTimeout(loadTimer);
  }, []);

  // Show cached rows immediately on return visits, then replace them with a
  // fresh response in the background — same caching behavior as other pages.
  useEffect(() => {
    try {
      localStorage.setItem(LS_CLIENT_FAQ, JSON.stringify(rows));
    } catch {
      // A full browser store should not prevent Client FAQ from working.
    }
  }, [rows]);

  return (
    <div className="faq-page">
      <div className="page-header">
        <h1>Client FAQ</h1>
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
            <label>Search Questions</label>
            <input
              type="text"
              placeholder="Search call ref or question..."
              value={filters.search}
              onChange={(e) => setFilters((current) => ({ ...current, search: e.target.value }))}
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
        <table className="data-table pending-cases-table no-scroll-table">
          <thead>
            <tr>
              <th>Sr No</th>
              <th>Date</th>
              <th>Call Ref</th>
              <th>Question</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={4} className="no-data">Loading client questions...</td>
              </tr>
            ) : filteredRows.length === 0 ? (
              <tr>
                <td colSpan={4} className="no-data">No client questions match the selected filters.</td>
              </tr>
            ) : (
              filteredRows.map((item, index) => (
                <tr key={item.id}>
                  <td>{index + 1}</td>
                  <td>{formatIsoDate(item.call_date)}</td>
                  <td>{item.formatted_call_id || item.call_reference || "—"}</td>
                  <td className="pending-case-client-name">{item.question || "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <BufferingOverlay loading={loading} text="Fetching client questions..." />
    </div>
  );
}

export default ClientFAQ;
