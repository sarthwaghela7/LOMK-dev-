import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";

import { getFlintDeskClientHistory, getLeadDetails } from "../data/api.js";
import {
  formatDisplayDate,
  formatDisplayDateTime,
} from "../data/displayDate.js";
import { isVoicemailCall } from "../data/callKind.js";
import { ClientLifecycleModal } from "./ClientLifecycleModal.jsx";

const LEAD_LABELS = {
  lead_id: "Lead ID",
  name: "Name",
  source: "Source",
  s1_status: "Status",
  phone: "Phone",
  email: "Email",
  client_type: "Client Type",
  client_profile: "Client Profile",
  client_enquiry: "Client Enquiry",
  lead_category: "Lead Category",
  owner: "Owner",
  date_received: "Date Received",
  extension: "Extension",
  sid: "SID",
  matched_call_id: "Matched Call ID",
  agent_name: "Agent Name",
  call_direction: "Call Direction",
  call_duration: "Call Duration",
  case_type: "Case Type",
  case_worker_assigned: "Case Worker Assigned",
  urgency_flag: "Urgency Flag",
  redirect_escalation_to: "Redirect / Escalation",
  call_outcome: "Call Outcome",
  call_summary: "Call Summary",
  enquiry_question: "Enquiry Question",
  s2_status: "S2 Status",
  self_action_item: "Self Action Item",
  action_due_date: "Action Due Date",
  action_status: "Action Status",
  dependency: "Dependency",
  follow_up_dates: "Follow-up Dates",
  follow_up_attempts: "Follow-up Attempts",
  blocker_comment: "Blocker Comment",
  case_status: "Case Status",
  case_closing_date: "Case Closing Date",
  verification_comment: "Verification Comment",
  match_status: "Match Status",
  match_count: "Match Count",
  s3_completed: "S3 Completed",
  s3_completed_at: "S3 Completed At",
  created_at: "Created At",
};

const voicemailStatus = (lead) => {
  const hasClassification =
    String(lead?.voicemail_left_if_no_answer || "").trim() ||
    String(lead?.fd_a_call_outcome || "").trim() ||
    String(lead?.call_outcome || "").trim();
  if (!hasClassification) return "Unknown";
  return isVoicemailCall({ raw: lead }) ? "Yes" : "No";
};

const show = (value) =>
  value === null || value === undefined || value === ""
    ? "-"
    : typeof value === "boolean"
      ? value
        ? "Yes"
        : "No"
      : String(value);

function Modal({ title, kicker, loading, error, onClose, children }) {
  return (
    <div className="call-modal-overlay" onClick={onClose}>
      <div
        className="call-modal-card call-modal-medium identifier-modal"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="call-modal-header">
          <div>
            <p className="call-modal-kicker">{kicker}</p>
            <h2>{title}</h2>
          </div>
          <button type="button" className="call-modal-close" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="call-modal-content">
          {loading ? (
            <p>Loading details…</p>
          ) : error ? (
            <p className="identifier-modal-error">{error}</p>
          ) : (
            children
          )}
        </div>
      </div>
    </div>
  );
}

function IdentifierModal({
  title,
  kicker,
  loading,
  error,
  onClose,
  variant = "",
  children,
}) {
  const titleId = useId();
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [onClose]);

  return createPortal(
    <div
      className="call-modal-overlay identifier-modal-overlay"
      onMouseDown={onClose}
    >
      <div
        className={`call-modal-card call-modal-medium identifier-modal ${variant}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="call-modal-header">
          <div>
            <p className="call-modal-kicker">{kicker}</p>
            <h2 id={titleId}>{title}</h2>
          </div>
          <button type="button" className="call-modal-close" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="call-modal-content">
          {loading ? (
            <p>Loading details…</p>
          ) : error ? (
            <p className="identifier-modal-error">{error}</p>
          ) : (
            children
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

void Modal;

export function LeadIdCell({ leadId }) {
  const [state, setState] = useState(null);
  const open = async (event) => {
    event?.stopPropagation();
    setState({ loading: true, data: null, error: "" });
    try {
      setState({
        loading: false,
        data: await getLeadDetails(leadId),
        error: "",
      });
    } catch (error) {
      setState({
        loading: false,
        data: null,
        error: error.message || "Unable to load lead details.",
      });
    }
  };
  if (!leadId) return <span>-</span>;
  return (
    <>
      <button
        type="button"
        className="calls-client-id-link"
        onClick={open}
        title="Open lead details"
      >
        {leadId}
      </button>
      {state && (
        <IdentifierModal
          title={leadId}
          kicker="Lead details"
          loading={state.loading}
          error={state.error}
          onClose={() => setState(null)}
        >
          <div className="client-lifecycle-summary identifier-lifecycle-summary">
            <span>
              <b>Stage 1</b>
              {show(state.data?.s1_status)}
            </span>
            <span>
              <b>Stage 2</b>
              {show(state.data?.s2_status)}
            </span>
            <span>
              <b>Stage 3 completed</b>
              {show(state.data?.s3_completed)}
            </span>
            <span>
              <b>Case status</b>
              {show(state.data?.case_status)}
            </span>
          </div>
          <h3>Lead details</h3>
          <div className="identifier-detail-grid">
            <div>
              <span>Voicemail</span>
              <strong>{voicemailStatus(state.data)}</strong>
            </div>
            {Object.entries(LEAD_LABELS).map(([key, label]) => (
              <div key={key}>
                <span>{label}</span>
                <strong>{show(state.data?.[key])}</strong>
              </div>
            ))}
          </div>
        </IdentifierModal>
      )}
    </>
  );
}

export function ClientIdCell({ clientId, onClientUpdated }) {
  const [state, setState] = useState(null);
  const open = async (event) => {
    event?.stopPropagation();
    setState({ loading: true, data: null, error: "" });
    try {
      setState({
        loading: false,
        data: await getFlintDeskClientHistory(clientId),
        error: "",
      });
    } catch (error) {
      setState({
        loading: false,
        data: null,
        error: error.message || "Unable to load client lifecycle.",
      });
    }
  };
  if (!clientId) return <span>-</span>;
  return (
    <>
      <button
        type="button"
        className="calls-client-id-link"
        onClick={open}
        title="Open client lifecycle"
      >
        {clientId}
      </button>
      {state && <ClientLifecycleModal data={state.data} loading={state.loading} error={state.error} onClose={() => setState(null)} onSaved={onClientUpdated} />}
    </>
  );
}
