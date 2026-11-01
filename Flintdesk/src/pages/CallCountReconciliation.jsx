import { useCallback, useEffect, useRef, useState } from "react";
import { BufferingLoader } from "../components/BufferingLoader.jsx";
import {
  getCallCountAffectedCalls,
  getCallCountHistory,
  getCallCountReconciliation,
  retryCallCountSnapshot,
} from "../data/api.js";

// Admin-only. Compares the official end-of-day call counts saved at 5:05 PM
// Central with what the database says now, per agent and per extension.
const TIMEZONE = "America/Chicago";
const SECTIONS = [
  ["total", "Total"],
  ["agent", "By agent (Agent column)"],
  ["extension", "By extension (information only)"],
];

const longDate = (iso) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", {
    weekday: "short", month: "long", day: "numeric", year: "numeric",
  });
const centralTime = (iso) =>
  iso
    ? new Date(iso).toLocaleString("en-US", {
        timeZone: TIMEZONE, month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      }) + " CT"
    : "-";
const signed = (value) => (value > 0 ? `+${value}` : String(value));
const statusClass = (status) =>
  status === "MATCH" ? "ccr-ok" : status === "MISMATCH" || status.includes("ERROR") || status.includes("FAILED") ? "ccr-bad" : "ccr-neutral";

function StatusBadge({ status }) {
  return <span className={`ccr-status ${statusClass(status)}`}>{status}</span>;
}

export default function CallCountReconciliation() {
  const [history, setHistory] = useState([]);
  const [historyError, setHistoryError] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState("");
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [affected, setAffected] = useState(null);
  const [retrying, setRetrying] = useState(false);
  const requestId = useRef(0);

  const loadHistory = useCallback(async () => {
    setLoadingHistory(true);
    setHistoryError("");
    try {
      const response = await getCallCountHistory();
      setHistory(response.items || []);
    } catch (error) {
      setHistoryError(error.message || "Could not load the snapshot history.");
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  const openDate = useCallback(async (businessDate) => {
    const id = ++requestId.current;
    setSelected(businessDate);
    setAffected(null);
    setDetail(null);
    setDetailError("");
    setLoadingDetail(true);
    try {
      const response = await getCallCountReconciliation(businessDate);
      if (id === requestId.current) setDetail(response);
    } catch (error) {
      if (id === requestId.current) setDetailError(error.message || "Could not compare this date.");
    } finally {
      if (id === requestId.current) setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const showAffected = async (row) => {
    setAffected({ title: `${row.dimension === "extension" ? "Extension" : "Agent"} ${row.key}`, loading: true, calls: [] });
    try {
      const response = await getCallCountAffectedCalls(selected, {
        agent: row.dimension === "agent" ? row.key : undefined,
        extension: row.dimension === "extension" ? row.key : undefined,
      });
      setAffected({ title: `${row.dimension === "extension" ? "Extension" : "Agent"} ${row.key}`, loading: false, calls: response.calls || [] });
    } catch (error) {
      setAffected({ title: `Agent ${row.key}`, loading: false, calls: [], error: error.message || "Could not load the affected calls." });
    }
  };

  const retry = async () => {
    setRetrying(true);
    setDetailError("");
    try {
      await retryCallCountSnapshot(selected);
      await Promise.all([openDate(selected), loadHistory()]);
    } catch (error) {
      setDetailError(error.message || "The snapshot could not be taken.");
    } finally {
      setRetrying(false);
    }
  };

  const canRetry = detail && (detail.status === "NOT YET SNAPSHOTTED" || detail.status === "SNAPSHOT FAILED");

  const renderSection = ([dimension, title]) => {
    const rows = (detail.rows || []).filter((row) => row.dimension === dimension);
    if (!rows.length) return null;
    return (
      <div key={dimension} className="ccr-section">
        <h3>{title}</h3>
        <div className="call-updates-table-box">
          <table className="calls-table leads-table ccr-table ccr-counts-table">
            <colgroup>
              <col className="ccr-col-key" /><col className="ccr-col-num" /><col className="ccr-col-num" />
              <col className="ccr-col-num" /><col className="ccr-col-status" /><col className="ccr-col-action" />
            </colgroup>
            <thead>
              <tr>
                <th className="leads-column-call ccr-key">{dimension === "extension" ? "Extension" : dimension === "agent" ? "Agent" : "Count"}</th>
                <th className="leads-column-call ccr-num">Snapshot</th>
                <th className="leads-column-call ccr-num">Current</th>
                <th className="leads-column-call ccr-num">Difference</th>
                <th className="leads-column-call ccr-status-col">Status</th>
                <th className="leads-column-call ccr-action"><span className="ccr-sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.dimension}-${row.key}`}
                    className={`${dimension === "total" ? "ccr-total-row" : ""} ${row.status === "MISMATCH" ? (row.informational ? "ccr-row-changed" : "ccr-row-bad") : ""}`.trim()}>
                  <td className="ccr-key">{row.key}</td>
                  <td className="ccr-num">{row.snapshot}</td>
                  <td className="ccr-num">{row.current}</td>
                  <td className={`ccr-num ccr-diff ${row.difference > 0 ? "ccr-diff-up" : row.difference < 0 ? "ccr-diff-down" : "ccr-diff-zero"}`}>{signed(row.difference)}</td>
                  <td className="ccr-status-col">
                    {row.informational && row.status === "MISMATCH"
                      ? <span className="ccr-status ccr-neutral">CHANGED</span>
                      : <StatusBadge status={row.status} />}
                  </td>
                  <td className="ccr-action">
                    {row.status === "MISMATCH" && dimension !== "total" && (
                      <button type="button" className="table-action ccr-view-btn" onClick={() => showAffected(row)}>
                        View affected calls
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  return (
    <section className="process-page leads-page ccr-page">
      <div className="leads-heading">
        <h1>Call Count Reconciliation</h1>
      </div>
      <p className="ccr-note">
        The official count is saved automatically at 5:05 PM Central on working days, then compared with the
        current database. The count is the Calls page number, split by the Agent column. A difference for any agent is a mismatch even when the total is unchanged; extensions are shown for investigation only.
      </p>
      {historyError && <p className="leads-error" role="alert">{historyError}</p>}

      <div className="ccr-layout">
        <div className="ccr-history">
          <h2>History</h2>
          {loadingHistory ? (
            <BufferingLoader text="Loading snapshot history" size="sm" />
          ) : (
            <div className="call-updates-table-box">
              <table className="calls-table leads-table ccr-table ccr-history-table">
                <colgroup><col className="ccr-hcol-date" /><col className="ccr-hcol-status" /><col className="ccr-hcol-total" /></colgroup>
                <thead>
                  <tr>
                    <th className="leads-column-call ccr-key">Date</th>
                    <th className="leads-column-call ccr-status-col">Status</th>
                    <th className="leads-column-call ccr-num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((item) => (
                    <tr key={item.business_date} className={`ccr-history-row ${selected === item.business_date ? "ccr-selected" : ""}`}
                        onClick={() => openDate(item.business_date)}>
                      <td className="ccr-key">{longDate(item.business_date)}</td>
                      <td className="ccr-status-col"><StatusBadge status={item.status} /></td>
                      <td className="ccr-num">{item.total_calls ?? "-"}</td>
                    </tr>
                  ))}
                  {!history.length && <tr><td colSpan={3} className="ccr-empty">No snapshots yet.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="ccr-detail">
          {!selected && <p className="ccr-note">Select a date to compare its snapshot with the current count.</p>}
          {loadingDetail && <BufferingLoader text="Comparing with the current database" size="md" />}
          {detailError && <p className="leads-error" role="alert">{detailError}</p>}
          {detail && !loadingDetail && (
            <>
              <div className="ccr-detail-head">
                <h2>{longDate(detail.business_date)}</h2>
                <StatusBadge status={detail.status} />
              </div>
              <p className="ccr-note ccr-meta">
                Timezone: {detail.timezone} · Day closed: {centralTime(detail.snapshot_at)}
                {detail.late && " · Taken late: this is not a true end-of-day baseline."}
              </p>
              {detail.error && <p className="leads-error" role="alert">{detail.error}</p>}
              {canRetry && (
                <button type="button" className="primary-button" onClick={retry} disabled={retrying}>
                  {retrying ? "Taking snapshot..." : "Take snapshot now"}
                </button>
              )}
              {SECTIONS.map(renderSection)}
            </>
          )}
          {affected && (
            <div className="ccr-section">
              <h3>Affected calls: {affected.title}</h3>
              {affected.loading && <BufferingLoader text="Finding the calls" size="sm" />}
              {affected.error && <p className="leads-error" role="alert">{affected.error}</p>}
              {!affected.loading && !affected.error && (
                <div className="call-updates-table-box">
                  <table className="calls-table leads-table ccr-table ccr-affected-table">
                    <colgroup><col className="ccr-acol-call" /><col className="ccr-acol-change" /><col className="ccr-acol-pair" /><col className="ccr-acol-pair" /></colgroup>
                    <thead>
                      <tr>
                        <th className="leads-column-call ccr-key">Call</th>
                        <th className="leads-column-call ccr-key">What changed</th>
                        <th className="leads-column-call ccr-key">Agent (snapshot → now)</th>
                        <th className="leads-column-call ccr-key">Extension (snapshot → now)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {affected.calls.map((call) => (
                        <tr key={call.call_id}>
                          <td className="ccr-key">{call.call_reference || call.call_id}</td>
                          <td className="ccr-key">{call.change}</td>
                          <td className="ccr-key">{call.snapshot_agent ?? "-"} → {call.current_agent ?? "-"}</td>
                          <td className="ccr-key">{call.snapshot_extension ?? "-"} → {call.current_extension ?? "-"}</td>
                        </tr>
                      ))}
                      {!affected.calls.length && <tr><td colSpan={4} className="ccr-empty">No individual calls differ for this row.</td></tr>}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
