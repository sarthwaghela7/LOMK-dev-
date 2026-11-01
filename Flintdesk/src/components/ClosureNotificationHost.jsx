import { useCallback, useEffect, useRef, useState } from "react";
import {
  confirmClosureNotification, dismissClosureNotification, getPendingClosureNotifications,
} from "../data/api.js";
import { describeConfirmation, formatClosureTime } from "../data/caseClosure.js";
import { callFocusFromConfirmation } from "../data/callFocus.js";

const POLL_MS = 60_000;
const FOCUSABLE = 'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Persistent, centred notification for cross-agent case closures.
 *
 * Pending ones come from the database (so they survive refresh and logout/login) and are shown one at a time.
 * It leaves ONLY through its buttons: no timeout, no click-outside and no Escape, because X is permanent.
 *  - X: closes it for good and changes nothing (never a confirmation).
 *  - Confirm: the server sets the recipient's own listed call record(s) to the closure status; success is shown
 *    only after the server answers, a failure shows an error and Confirm stays available to retry.
 */
export default function ClosureNotificationHost({ user, onOpenCall }) {
  const [queue, setQueue] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [outcome, setOutcome] = useState(null);       // confirm result shown until the user presses Done
  const dialog = useRef(null);
  const busyRef = useRef(false);
  const submitting = useRef(false);                   // blocks double clicks before React re-renders
  busyRef.current = busy;

  const refresh = useCallback(async () => {
    try {
      const pending = await getPendingClosureNotifications();
      // Never let a refresh swap the notification under the user mid-action or while the result is on screen.
      if (!busyRef.current && !submitting.current) setQueue((current) => (current.length && outcomeRef.current ? current : pending));
    } catch {
      // Keep what is shown; try again on the next poll.
    }
  }, []);
  const outcomeRef = useRef(null);
  outcomeRef.current = outcome;

  useEffect(() => {
    if (!user) { setQueue([]); return undefined; }
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", refresh);
    };
  }, [user, refresh]);

  const current = queue[0];
  const open = Boolean(current);

  // Modal behaviour: lock page scroll, keep Tab inside the dialog, move focus in when it opens.
  useEffect(() => {
    if (!open) return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, [open, current?.id]);

  const trapTab = (event) => {
    if (event.key !== "Tab") return;
    const items = [...(dialog.current?.querySelectorAll(FOCUSABLE) || [])];
    if (!items.length) { event.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  const leave = () => { setOutcome(null); setError(""); setQueue((items) => items.slice(1)); };

  // Done: close the result and, when the confirmation changed a call record, open that exact call on the Calls page.
  const done = () => {
    const target = callFocusFromConfirmation(outcome);
    leave();
    if (target && onOpenCall) onOpenCall(target);
  };

  const dismiss = async () => {
    if (!current || submitting.current) return;
    submitting.current = true; setBusy(true); setError("");
    try {
      await dismissClosureNotification(current.id);
      leave();
    } catch (failure) {
      setError(failure?.message || "That did not work. Please try again.");
    } finally {
      submitting.current = false; setBusy(false);
    }
  };

  const confirm = async () => {
    if (!current || submitting.current) return;
    submitting.current = true; setBusy(true); setError("");
    try {
      const result = await confirmClosureNotification(current.id);
      setOutcome(result);                              // success is shown only now, after the server answered
    } catch (failure) {
      setError(failure?.message || "Could not confirm. Nothing was changed - please try again.");
    } finally {
      submitting.current = false; setBusy(false);
    }
  };

  if (!current) return null;
  const ctx = current.change_context || {};
  const reversed = current.notification_type === "case_closure_reversed";
  const logs = Array.isArray(ctx.logs) ? ctx.logs : [];
  const client = [ctx.client_name, ctx.client_number].filter(Boolean).join(" · ");
  const rows = reversed
    ? [["Changed by", ctx.closing_agent_name], ["Client", client], ["Case context", ctx.case_context]]
    : [["For", user?.name], ["Changed by", ctx.closing_agent_name], ["Client", client], ["Case context", ctx.case_context],
       ["Closed on", formatClosureTime(ctx.closed_at)]];
  return (
    <div className="closure-notification-overlay" role="presentation">
      <section
        ref={dialog}
        tabIndex={-1}
        className="closure-notification"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="closure-notification-title"
        aria-describedby="closure-notification-message"
        onKeyDown={trapTab}
      >
        <button type="button" className="closure-notification-x" aria-label="Close notification" disabled={busy || Boolean(outcome)}
          onClick={dismiss}>×</button>
        <h2 id="closure-notification-title">{outcome ? "Closure confirmed" : current.title}</h2>
        {outcome ? (
          <div id="closure-notification-message" className="closure-notification-result" role="status">
            {describeConfirmation(outcome, ctx.closing_agent_name).map((line) => <p key={line}>{line}</p>)}
          </div>
        ) : (
          <>
            <p id="closure-notification-message">{current.message}</p>
            <dl className="closure-notification-facts">
              {rows.filter(([, value]) => value).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
            </dl>
            {!reversed && logs.length > 0 && (
              <div className="closure-notification-logs">
                <strong>Needs your confirmation</strong>
                <p>Set your call record{logs.length > 1 ? "s" : ""} to <b>{ctx.closure_status}</b>:</p>
                <ul>{logs.map((log) => <li key={log.analysis_id}>{log.call_reference || log.call_id}{log.call_date ? ` · ${log.call_date}` : ""}{log.status ? ` · now ${log.status}` : ""}</li>)}</ul>
              </div>
            )}
          </>
        )}
        {error && <p className="closure-notification-error" role="alert">{error}</p>}
        <footer>
          {outcome ? (
            <button type="button" className="closure-notification-confirm" onClick={done}>Done</button>
          ) : reversed ? (
            <button type="button" className="closure-notification-confirm" disabled={busy} onClick={dismiss}>Got it</button>
          ) : (
            <button type="button" className="closure-notification-confirm" disabled={busy} aria-busy={busy} onClick={confirm}>
              {busy ? <><span className="closure-spinner" aria-hidden="true" /> Confirming...</> : error ? "Try again" : "Confirm"}
            </button>
          )}
          {queue.length > 1 && <span className="closure-queue">{queue.length - 1} more waiting</span>}
        </footer>
      </section>
    </div>
  );
}
