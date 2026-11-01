import { useEffect, useRef } from "react";
import { PENDING_REASON_LABELS } from "../data/submitState.js";

const FOCUSABLE = 'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
const MAX_LISTED = 8;

/**
 * Shown when an agent leaves the Calls page while Case Status changes still need Submit.
 * Centred on the viewport (same overlay as the other FlintDesk confirmation dialogs), focus stays inside it, and while a
 * Submit is running nothing can dismiss it.
 *  - Stay on Page     closes it; nothing changes.
 *  - Submit and Leave runs the page's own Submit for every listed call; leaves only if all succeed.
 * There is deliberately no way to leave with the Case Status changes unsubmitted.
 */
export default function UnsubmittedChangesDialog({ rows, unsavedCount, busy, failed, onStay, onSubmitAndLeave }) {
  const dialog = useRef(null);
  const primary = useRef(null);
  const count = rows.length;
  const failures = failed || [];

  useEffect(() => {
    const previous = document.body.style.overflow;
    const opener = document.activeElement;
    document.body.style.overflow = "hidden";
    primary.current?.focus();
    return () => { document.body.style.overflow = previous; opener?.focus?.(); };
  }, []);

  const onKeyDown = (event) => {
    if (event.key === "Escape") { event.stopPropagation(); if (!busy) onStay(); return; }
    if (event.key !== "Tab") return;
    const items = [...(dialog.current?.querySelectorAll(FOCUSABLE) || [])];
    if (!items.length) { event.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  return (
    <div className="closure-notification-overlay" role="presentation">
      <section
        ref={dialog}
        tabIndex={-1}
        className="closure-notification unsubmitted-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="unsubmitted-title"
        aria-describedby="unsubmitted-message"
        aria-busy={busy}
        onKeyDown={onKeyDown}
      >
        <h2 id="unsubmitted-title">Unsubmitted Changes</h2>
        <p id="unsubmitted-message">
          You have changed the Case Status of {count === 1 ? "1 call log" : `${count} call logs`} but have not clicked Submit.
          Leaving this page now may leave {count === 1 ? "that change" : "those changes"} unsubmitted. Submit {count === 1 ? "it" : "them"} before you leave.
        </p>
        <ul className="unsubmitted-list">
          {rows.slice(0, MAX_LISTED).map((row) => (
            <li key={row.id}>
              <strong>{row.ref}</strong>
              <span>{row.status ? row.status : "No status"}</span>
              <em>{PENDING_REASON_LABELS[row.reason] || row.reason}</em>
            </li>
          ))}
          {count > MAX_LISTED && <li className="unsubmitted-more">and {count - MAX_LISTED} more</li>}
        </ul>
        {unsavedCount > 0 && (
          <p className="closure-notification-error" role="alert">
            {unsavedCount === 1 ? "1 call log also has" : `${unsavedCount} call logs also have`} edits that are not saved yet.
            Submit and Leave saves them first.
          </p>
        )}
        {failures.length > 0 && (
          <div className="closure-notification-error" role="alert">
            <strong>{failures.length === 1 ? "1 call log could not be submitted:" : `${failures.length} call logs could not be submitted:`}</strong>
            <ul className="unsubmitted-failures">
              {failures.map((item) => <li key={item.ref}>{item.ref}: {item.error}</li>)}
            </ul>
            You are still on the Calls page and nothing was discarded. Fix the call log(s) above, or stay on the page and complete them, then try again.
          </div>
        )}
        <footer className="unsubmitted-actions">
          <button type="button" className="related-closure-cancel" disabled={busy} onClick={onStay}>Stay on Page</button>
          <button type="button" ref={primary} className="closure-notification-confirm" disabled={busy} aria-busy={busy} onClick={onSubmitAndLeave}>
            {busy ? <><span className="closure-spinner" aria-hidden="true" /> Submitting...</> : failures.length ? "Try Submit Again" : "Submit and Leave"}
          </button>
        </footer>
      </section>
    </div>
  );
}
