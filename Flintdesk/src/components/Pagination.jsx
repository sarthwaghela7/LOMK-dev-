import { Fragment } from "react";

// Shared conventional (page-number) pagination control -- originally
// VisaUpdates.jsx's own, extracted here so the Calls page's server-side
// pagination (see routes/calls.py get_analysis) can reuse the exact same
// control/markup/styling instead of a second implementation.
export function Pagination({ page, totalPages, onChange, ariaLabel = "Pages", cursorMode = false, hasNext = false, loading = false }) {
  if (!cursorMode && totalPages <= 1) return null;
  const visible = Array.from(new Set([1, page - 1, page, page + 1, totalPages]))
    .filter((number) => number >= 1 && number <= totalPages)
    .sort((a, b) => a - b);
  return (
    <nav className={`table-pagination${cursorMode ? " table-pagination-cursor" : ""}`} aria-label={ariaLabel}>
      <button
        type="button"
        disabled={loading || page === 1}
        onClick={() => onChange(page - 1)}
        aria-label="Previous page"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m14 6-6 6 6 6" /></svg>
        Previous
      </button>
      {cursorMode ? <span className="pagination-status" aria-live="polite">Page <strong>{page}</strong></span> : visible.map((number, index) => (
        <Fragment key={number}>
          {index > 0 && number - visible[index - 1] > 1 && <span>…</span>}
          <button
            type="button"
            className={number === page ? "active" : ""}
            aria-current={number === page ? "page" : undefined}
            disabled={loading}
            onClick={() => onChange(number)}
          >
            {number}
          </button>
        </Fragment>
      ))}
      <button
        type="button"
        disabled={loading || (cursorMode ? !hasNext : page === totalPages)}
        onClick={() => onChange(page + 1)}
        aria-label="Next page"
      >
        Next
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m10 6 6 6-6 6" /></svg>
      </button>
    </nav>
  );
}
