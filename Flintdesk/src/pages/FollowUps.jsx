import { useEffect, useMemo, useState } from "react";
import { getCallsList } from "../data/api.js";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import { DateRangeSelector } from "../components/DateRangeSelector.jsx";
import { ScrollableFilterBelt, StickyFilterRegion } from "../components/ScrollableFilterBelt.jsx";
import { TableScrollWrap } from "../components/TableScrollWrap.jsx";
import { getCurrentMonthRange, isValidDateRange } from "../data/filterDates.js";

// A call needs a follow up when it has a scheduled follow-up date, is still
// pending an outreach attempt, or its case status is explicitly "PC Follow Up".
function needsFollowUp(call) {
  const status = String(call.raw?.fd_m_case_status_change || "").toLowerCase();
  const outcome = String(call.raw?.fd_a_call_outcome || "").toLowerCase();
  return Boolean(call.followUpDates) || status.includes("follow up") || outcome.includes("follow up needed");
}

export default function FollowUps() {
  const [range, setRange] = useState(() => getCurrentMonthRange());
  const [draftRange, setDraftRange] = useState(() => getCurrentMonthRange());
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const applyFilters = () => {
    if (isValidDateRange(draftRange)) setRange(draftRange);
    else setError("Date From must be on or before Date To.");
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getCallsList(range.start, range.end)
      .then((data) => {
        if (!cancelled) setCalls(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Failed to load follow ups.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [range.start, range.end]);

  const followUps = useMemo(() => calls.filter(needsFollowUp), [calls]);

  return (
    <section className="process-page">
      <div className="calls-header-block">
        <h1>Follow Ups</h1>
        <p>Calls that still need a follow-up attempt or scheduled callback.</p>
      </div>

      <StickyFilterRegion>
      <ScrollableFilterBelt>
        <DateRangeSelector value={draftRange} onChange={setDraftRange} />
      </ScrollableFilterBelt>
      <div className="filter-belt-actions"><button type="button" className="primary-button calls-toolbar-apply" onClick={applyFilters} disabled={loading}>{loading ? "..." : "Apply"}</button></div>
      </StickyFilterRegion>

      {error && <p className="api-status">{error}</p>}

      <div className="history-table-card">
        <TableScrollWrap className="calls-table-wrap">
          {loading ? (
            <div className="calls-table-loading">
              <BufferingLoader />
            </div>
          ) : (
            <table className="calls-table">
              <thead>
                <tr>
                  <th>Call ID</th>
                  <th>Agent</th>
                  <th>Client</th>
                  <th>Case Type</th>
                  <th>Follow Up Dates</th>
                  <th>Follow Up Attempts</th>
                  <th>Blocker Comment</th>
                  <th>Case Status</th>
                </tr>
              </thead>
              <tbody>
                {followUps.length ? (
                  followUps.map((call) => (
                    <tr key={call.id}>
                      <td>{call.id}</td>
                      <td>{call.agent}</td>
                      <td>{call.raw?.client_name || "-"}</td>
                      <td>{call.caseType}</td>
                      <td>{call.followUpDates || "-"}</td>
                      <td>{call.followUpAttempts || "-"}</td>
                      <td>{call.blockerComment || "-"}</td>
                      <td>{call.raw?.fd_m_case_status_change || "-"}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={8} className="dashboard-empty-state">
                      No follow ups needed for this range.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </TableScrollWrap>
      </div>
    </section>
  );
}
