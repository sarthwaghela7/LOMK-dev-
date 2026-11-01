import { useEffect } from "react";
import { formatClosureTime } from "../data/caseClosure.js";

/** Read-only details of a closure another agent made and this agent confirmed. */
export default function ClosureInfoDialog({ closure, onClose }) {
  useEffect(() => {
    const onKeyDown = (event) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);
  const rows = [
    ["Changed by", closure.closing_agent_name],
    ["Changed to", closure.closure_status],
    ["Client number", closure.client_number],
    ["Client name", closure.client_name],
    ["Case context", closure.case_context],
    ["Closed on", formatClosureTime(closure.closed_at)],
  ];
  return (
    <div className="call-modal-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="closure-dialog" role="dialog" aria-modal="true" aria-labelledby="closure-info-title">
        <header>
          <h2 id="closure-info-title">Status changed by another agent</h2>
          <button type="button" className="call-modal-close" onClick={onClose} aria-label="Close">×</button>
        </header>
        <p className="closure-dialog-note">
          {closure.closing_agent_name} changed this matter to {closure.closure_status}. You confirmed it, so this call
          record was set to {closure.closure_status} and no longer counts as open.
        </p>
        <dl>
          {rows.filter(([, value]) => value).map(([label, value]) => (
            <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
          ))}
        </dl>
      </section>
    </div>
  );
}
