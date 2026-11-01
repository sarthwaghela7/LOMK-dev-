import { useEffect, useRef } from "react";
import { describeRelatedClosure } from "../data/caseClosure.js";

const FOCUSABLE = 'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Offered to the agent who has just closed a call (Dropped / Success - GE / Success - PC): their own other open
 * call logs for the same client and case context, to close together with it.
 *
 *  - Cancel (button or Escape): changes nothing.
 *  - Confirm and Close Related Calls: the server re-validates every log and closes it exactly as a Confirm on a
 *    closure notification does; the result - successes and failures - is then shown until Done.
 */
export default function RelatedClosureDialog({ offer, busy, error, result, onCancel, onConfirm, onDone }) {
  const dialog = useRef(null);
  const confirmButton = useRef(null);
  const logs = offer.logs || [];
  const finished = Boolean(result);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const opener = document.activeElement;
    (confirmButton.current || dialog.current)?.focus();
    return () => { document.body.style.overflow = previous; opener?.focus?.(); };
  }, [offer.closure_id]);

  const onKeyDown = (event) => {
    if (event.key === "Escape" && !busy) { event.stopPropagation(); (finished ? onDone : onCancel)(); return; }
    if (event.key !== "Tab") return;
    const items = [...(dialog.current?.querySelectorAll(FOCUSABLE) || [])];
    if (!items.length) { event.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  const count = logs.length;
  return (
    <div className="closure-notification-overlay" role="presentation">
      <section
        ref={dialog}
        tabIndex={-1}
        className="closure-notification related-closure"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="related-closure-title"
        aria-describedby="related-closure-message"
        onKeyDown={onKeyDown}
      >
        <h2 id="related-closure-title">{finished ? "Related call logs" : "Related Call Logs Found"}</h2>
        {finished ? (
          <div id="related-closure-message" className="closure-notification-result" role="status">
            {describeRelatedClosure(result).map((line) => <p key={line}>{line}</p>)}
          </div>
        ) : (
          <>
            <p id="related-closure-message">
              Other call logs for this client have the same context. Would you like to close these related call logs as well?
            </p>
            <p className="related-closure-count">
              <b>{count}</b> related call log{count === 1 ? "" : "s"} will be set to <b>{offer.closure_status}</b>.
            </p>
            <div className="related-closure-table-wrap">
              <table className="related-closure-table">
                <caption className="sr-only">Related call logs that would be closed</caption>
                <thead>
                  <tr>
                    <th scope="col">Call</th>
                    <th scope="col">Client</th>
                    <th scope="col">Phone</th>
                    <th scope="col">Context</th>
                    <th scope="col">Case Status</th>
                    <th scope="col">Agent</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr key={log.analysis_id}>
                      <td>{log.call_reference || log.call_id}{log.call_date ? <small>{log.call_date}</small> : null}</td>
                      <td>{offer.client_name || "-"}</td>
                      <td>{offer.client_number || "-"}</td>
                      <td>{log.case_context || offer.case_context || "-"}</td>
                      <td>{log.status || "Open"}</td>
                      <td>{log.agent_name || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        {error && <p className="closure-notification-error" role="alert">{error}</p>}
        <footer>
          {finished ? (
            <button type="button" ref={confirmButton} className="closure-notification-confirm" onClick={onDone}>Done</button>
          ) : (
            <>
              <button type="button" className="related-closure-cancel" disabled={busy} onClick={onCancel}>Cancel</button>
              <button type="button" ref={confirmButton} className="closure-notification-confirm" disabled={busy} aria-busy={busy} onClick={onConfirm}>
                {busy ? <><span className="closure-spinner" aria-hidden="true" /> Closing...</> : error ? "Try again" : "Confirm and Close Related Calls"}
              </button>
            </>
          )}
        </footer>
      </section>
    </div>
  );
}
