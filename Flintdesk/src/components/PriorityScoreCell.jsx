import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { calculatePriority, getLeadScoreBreakdown } from "../data/priorityEngine.js";
import { mapTypicalLawOfficeFee } from "../data/businessOpportunityFees.js";

function PriorityDetail({ label, title, children }) {
  const [open, setOpen] = useState(false);
  const titleId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event) => { if (event.key === "Escape") setOpen(false); };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return <>
    <button type="button" className="priority-detail-trigger" onClick={() => setOpen(true)} aria-haspopup="dialog">{label}</button>
    {open && createPortal(
      <div className="call-modal-overlay priority-detail-overlay" onMouseDown={() => setOpen(false)}>
        <section className="call-modal-card call-modal-compact priority-detail-modal" role="dialog" aria-modal="true" aria-labelledby={titleId} onMouseDown={(event) => event.stopPropagation()}>
          <header className="call-modal-header">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="call-modal-close" onClick={() => setOpen(false)}>Close</button>
          </header>
          <div className="call-modal-content priority-detail-content">{children}</div>
        </section>
      </div>,
      document.body,
    )}
  </>;
}

export function LeadScoreCell({ item }) {
  const priority = calculatePriority(item);
  const breakdown = getLeadScoreBreakdown(item);
  if (priority.leadScore === null) return <span className="priority-na">N/A</span>;
  return <PriorityDetail label={`${priority.leadScore.toFixed(0)} / 100`} title="Lead Score Details">
    <strong>{priority.leadBand.replaceAll("_", " ")}</strong>
    {priority.urgencyOverride && <span>Same-day urgency override</span>}
    {breakdown ? breakdown.map((entry) => <span key={entry.label}>{entry.label}: {entry.value}/{entry.max}</span>) : <span>Criteria breakdown unavailable for this persisted score.</span>}
  </PriorityDetail>;
}

export function BusinessOpportunityCell({ item }) {
  const { business } = calculatePriority(item);
  const fee = mapTypicalLawOfficeFee(item);
  if (fee === null) return <span className="priority-na">Unclassified / N/A</span>;
  return <PriorityDetail label={`$${fee.toLocaleString()}`} title="Business Opportunity Details">
    <strong>{business.tier || "Business opportunity"}</strong>
    <span>{business.serviceLine || "Mapped from call context"}</span>
    {business.feeSource === "ESTIMATED" && <span>Confirm exact amount before quoting.</span>}
  </PriorityDetail>;
}
