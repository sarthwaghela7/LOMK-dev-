import { useEffect, useState } from "react";
import { BufferingLoader } from "./BufferingLoader.jsx";
import { getCallsByClientPhone } from "../data/api.js";
import { formatDisplayDate } from "../data/displayDate.js";
import { stageCellText } from "../data/followUps.js";

// "More logs": every log of one client phone number, in the same popup frame as the Calls page's "All Logs of Phone Number"
// (the Calls page itself shows its full Call Records table there; here a compact table of the same records).
export default function PhoneLogsModal({ phone, onClose }) {
  const [state, setState] = useState({ loading: true, records: [], missed: 0, error: "" });
  useEffect(() => {
    let cancelled = false;
    getCallsByClientPhone(phone)
      .then((result) => { if (!cancelled) setState({ loading: false, records: result.records || [], missed: result.missedCallsCount || 0, error: "" }); })
      .catch((failure) => { if (!cancelled) setState({ loading: false, records: [], missed: 0, error: failure?.message || "Unable to load call logs." }); });
    return () => { cancelled = true; };
  }, [phone]);
  const { loading, records, missed, error } = state;
  return (
    <div className="modal-backdrop leads-blur-backdrop" onMouseDown={onClose}>
      <div className="modal-card all-phone-logs-modal" role="dialog" aria-modal="true" aria-labelledby="follow-up-phone-logs-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="leads-edit-heading">
          <h3 id="follow-up-phone-logs-title">All Logs of Phone Number &ndash; {phone}</h3>
          <button type="button" className="leads-modal-close" aria-label="Close All Logs of Phone Number" title="Close" onClick={onClose}><i className="fa-solid fa-xmark" /></button>
        </div>
        {error && <p className="leads-error" role="alert">{error}</p>}
        <div className="all-phone-logs-results">
          {loading ? (
            <div className="calls-table-loading"><BufferingLoader text="Fetching call logs" size="md" /></div>
          ) : (
            <>
              <div className="all-phone-logs-breakdown" aria-label={`Where ${phone} appears`}>
                <span className="all-phone-logs-breakdown-title">{phone} appears</span>
                <span className="all-phone-logs-chip"><strong>{missed}</strong> {missed === 1 ? "time" : "times"} on the Missed Calls page</span>
                <span className="all-phone-logs-chip"><strong>{records.length}</strong> {records.length === 1 ? "time" : "times"} on the Calls page</span>
              </div>
              {records.length ? (
                <div className="calls-table-wrap">
                  <table className="calls-table follow-ups-logs-table">
                    <thead><tr>{["Call ID", "Date", "Timestamp", "Agent", "Direction", "Duration", "Case Status Change", "Call Summary"].map((label) => <th key={label}>{label}</th>)}</tr></thead>
                    <tbody>
                      {records.map((call) => (
                        <tr key={call.id || call.callId}>
                          <td>{call.id || "-"}</td>
                          <td>{formatDisplayDate(call.date) || "-"}</td>
                          <td>{call.timestamp || "-"}</td>
                          <td>{call.agent || "-"}</td>
                          <td>{call.direction || "-"}</td>
                          <td>{call.duration || "-"}</td>
                          <td>{stageCellText(call.raw, "fd_m_case_status_change") || "-"}</td>
                          <td className="follow-ups-summary" title={stageCellText(call.raw, "fd_a_call_summary")}>{stageCellText(call.raw, "fd_a_call_summary") || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="muted">No call logs found for this client phone number.</p>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
