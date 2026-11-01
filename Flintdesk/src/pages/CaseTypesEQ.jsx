import { useCallback, useEffect, useRef, useState } from "react";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { CaseTypeEnquiryTable } from "../components/CaseTypeEnquiryTable.jsx";
import CaseTypeTrendsView from "../components/CaseTypeTrendsView.jsx";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { getCaseTypesEq } from "../data/api.js";
import { startPolling } from "../data/polling.js";
import { downloadExcelWorkbook } from "../data/export.js";
import { getDefaultDateRange } from "../data/filterDates.js";
import { usePersistentTableState } from "../data/persistentTableState.js";
import { describeCoverage, formatAsOf } from "../data/caseTypeTrends.js";

// Two views share this page and its filter belt: Trends (inline report, own reporting-period controls) and
// Questions (the Case Type + Enquiry Question table for the belt's date range).
// View-only report. The data is the Dashboard's own automated Case Type +
// Enquiry Question summary (weekly grouping done by the same summary
// calculation), requested for this page's own date range.
const REFRESH_MS = 45_000;                                   // live refresh of the Questions table while it is on screen
// Today's calendar date in the reporting timezone (Central Time), whatever the browser's own timezone is.
const todayCentral = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Chicago" });

export default function CaseTypesEQ({ isActive = true }) {
  const month = getDefaultDateRange();
  const [draft, setDraft] = usePersistentTableState("CaseTypesEQ.draft", { start: month.start, end: month.end });
  const [range, setRange] = usePersistentTableState("CaseTypesEQ.range", { start: month.start, end: month.end });
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [view, setView] = usePersistentTableState("CaseTypesEQ.view", "questions");
  const isTrends = view === "trends";
  // Trends mounts on first visit only (no extra requests for people who never open it) and then stays mounted,
  // hidden, so switching back keeps its period, range and loaded report without refetching.
  const [trendsVisited, setTrendsVisited] = useState(isTrends);
  const showView = (next) => { setView(next); if (next === "trends") setTrendsVisited(true); };

  const [updatedAt, setUpdatedAt] = useState(null);       // last successful load or refresh (browser clock; used if the server sent no time)
  const [summary, setSummary] = useState(null);             // server time (Central) and the call-record counts of that moment
  const loadedRange = useRef(null);
  const latest = useRef({});
  // `silent` = a background refresh: no spinner, the rows on screen stay if the request fails, and they are only
  // replaced when the server's data actually changed (so nothing flickers or scrolls away under the reader).
  const load = useCallback(async ({ silent = false } = {}) => {
    const wanted = `${range.start}|${range.end}`;
    if (!silent) { setLoading(true); setError(""); }
    try {
      const response = await getCaseTypesEq(range.start, range.end);
      if (loadedRange.current !== wanted) return;      // the range was changed while this was loading
      const next = response?.caseTypeEnquiryRows || [];
      setRows((current) => (JSON.stringify(current) === JSON.stringify(next) ? current : next));
      setUpdatedAt(new Date());
      setSummary(summary_ => (JSON.stringify(summary_) === JSON.stringify(response?.summary) ? summary_ : response?.summary || null));
    } catch (loadError) {
      if (silent) return;
      setError(loadError.message || "Unable to load Case Types + EQ.");
      setRows([]);
      loadedRange.current = null;                  // let a later visit to this view try again
    } finally {
      if (!silent) setLoading(false);
    }
  }, [range]);

  // The table is fetched only while its view is shown, and again when the applied range changes.
  useEffect(() => {
    if (isTrends) return;
    const key = `${range.start}|${range.end}`;
    if (loadedRange.current === key) return;
    loadedRange.current = key;
    load();
  }, [load, isTrends, range]);

  // Live updates: re-read the same endpoint about every 45 s while the Questions view is visible, plus once when
  // the view or page comes back into sight. Requests never overlap (see data/polling.js).
  latest.current = { active: isActive && !isTrends, loading };
  useEffect(() => {
    const poll = startPolling({
      run: () => load({ silent: true }), intervalMs: REFRESH_MS,
      isActive: () => document.visibilityState === "visible" && latest.current.active && !latest.current.loading,
    });
    const onVisible = () => { if (document.visibilityState === "visible" && latest.current.active) poll.catchUp(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { poll.stop(); document.removeEventListener("visibilitychange", onVisible); };
  }, [load]);
  useEffect(() => {
    if (isActive && !isTrends && loadedRange.current === `${range.start}|${range.end}` && !loading) load({ silent: true });
  }, [isActive, isTrends]);                         // eslint-disable-line react-hooks/exhaustive-deps -- only when the view comes back

  const asOf = formatAsOf(summary?.asOf);
  const coverage = describeCoverage(summary);
  const today = todayCentral();
  const rangeEndsBeforeToday = range.end < today;

  // Exports the rows currently shown (the applied date range): one line per
  // enquiry question so the sheet can be filtered/sorted in Excel.
  const exportExcel = () => {
    const columns = [
      { label: "Month & Date", minWidth: 110 },
      { label: "Date Range", minWidth: 110 },
      { label: "Visa Type", minWidth: 110 },
      { label: "Enquiry Question", wrap: true, width: 420 },
    ];
    const sheetRows = rows.flatMap((week) =>
      week.types.flatMap((item) =>
        (item.questions.length ? item.questions : [""]).map((question) => [
          week.label,
          week.range,
          item.type,
          question,
        ]),
      ),
    );
    downloadExcelWorkbook(
      [{ name: "Case Types + EQ", columns, rows: sheetRows }],
      `FlintDesk_Case_Types_EQ_${range.start}_to_${range.end}.xls`,
    );
  };

  const canApply = Boolean(draft.start && draft.end && draft.start <= draft.end);

  return (
    <section className="process-page case-types-eq-page">
      <div className="leads-heading">
        <h1>Case Types + EQ</h1>
      </div>
      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector value={draft} onChange={setDraft} />
      </ScrollableFilterBelt>
      <div className="filter-belt-actions">
        {!isTrends && <>
        <button
          type="button"
          className="table-action calls-export-button"
          disabled={loading || !rows.length}
          onClick={exportExcel}
          title="Export the Case Types + EQ table to Excel"
        >
          <i className="fa-solid fa-file-excel" /> Export to Excel
        </button>
        <button
          type="button"
          className="primary-button calls-toolbar-apply"
          disabled={!canApply || loading}
          onClick={() => setRange({ ...draft })}
        >
          Apply
        </button>
        </>}
      </div>
      {isTrends && <p className="muted case-types-eq-hint">The date range above applies to the Questions table. Trends use the reporting period chosen below.</p>}
      <div className="calls-stage-view-tabs case-types-eq-views" role="group" aria-label="Case Types + EQ view">
        <button type="button" aria-pressed={isTrends} className={`table-action ${isTrends ? "active" : ""}`} onClick={() => showView("trends")}>Trends</button>
        <button type="button" aria-pressed={!isTrends} className={`table-action ${!isTrends ? "active" : ""}`} onClick={() => showView("questions")}>Questions</button>
      </div>
      {!isTrends && (
        <p className="muted case-types-eq-hint" role="status">
          {asOf ? `Updated ${asOf.ct} (Central Time) · ${asOf.local} (your time) · refreshes automatically. `
            : updatedAt ? `Updated ${updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · refreshes automatically. ` : ""}
          {coverage && <span className="case-types-eq-coverage">{coverage} </span>}
          {rangeEndsBeforeToday && (
            <>Showing calls up to {range.end.slice(5, 7)}/{range.end.slice(8, 10)}/{range.end.slice(0, 4)}; newer calls are outside this range.{" "}
              <button type="button" className="calls-save-retry" onClick={() => { const next = { start: range.start, end: today }; setDraft(next); setRange(next); }}>Include up to today</button></>
          )}
        </p>
      )}
      </StickyFilterRegion>
      {trendsVisited && <div hidden={!isTrends}><CaseTypeTrendsView initialRange={range} active={isActive && isTrends} /></div>}
      <article className="case-types-eq-card" hidden={isTrends}>
        {loading ? (
          <div className="calls-table-loading">
            <BufferingLoader text="Fetching case types and enquiry questions" size="md" />
          </div>
        ) : error ? (
          <p className="leads-error" role="alert">{error}</p>
        ) : rows.length ? (
          <CaseTypeEnquiryTable rows={rows} />
        ) : (
          <p className="muted case-types-eq-empty">No Case Type or Enquiry Question data available.</p>
        )}
      </article>
    </section>
  );
}
