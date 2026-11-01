import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { formatDisplayDate, formatDisplayDateTime } from "../data/displayDate.js";
import { updateClientProfile } from "../data/api.js";

const display = (value) => (value === null || value === undefined || value === "" ? "N/A" : value);

export function ClientLifecycleModal({ data, loading, error, onClose, onSaved }) {
  const client = data?.client || {};
  const [draftName, setDraftName] = useState(client.client_name || "");
  const [draftEmail, setDraftEmail] = useState(client.client_email || "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    setDraftName(client.client_name || "");
    setDraftEmail(client.client_email || "");
  }, [client.client_id, client.client_name, client.client_email]);

  const hasChanges =
    draftName.trim() !== (client.client_name || "").trim() ||
    draftEmail.trim() !== (client.client_email || "").trim();

  const save = async () => {
    setSaving(true);
    setSaveError("");
    try {
      const changes = {};
      if (draftName.trim() !== (client.client_name || "").trim()) {
        changes.client_name = draftName;
      }
      if (draftEmail.trim() !== (client.client_email || "").trim()) {
        changes.client_email = draftEmail;
      }
      const updated = await updateClientProfile(client.client_id, changes);
      onSaved?.(updated.client);
      onClose();
    } catch (err) {
      setSaveError(err.message || "Unable to save client details.");
    } finally {
      setSaving(false);
    }
  };

  // Group the history by the page each call belongs to: the Calls page, or the
  // routing records the Calls page hides (they belong to the Missed Calls page).
  const historyCalls = data?.calls || [];
  const callGroups = !historyCalls.length ? [] : historyCalls.some((call) => "on_calls_page" in call)
    ? [
        { key: "calls", title: "Calls page", calls: historyCalls.filter((call) => call.on_calls_page) },
        {
          key: "missed",
          title: "Missed call page",
          hint: "Call IDs created when the client's call went through the missed-call handling",
          calls: historyCalls.filter((call) => !call.on_calls_page),
        },
      ].filter((group) => group.calls.length)
    : [{ key: "all", title: "", calls: historyCalls }];

  return createPortal(
    <div className="call-modal-overlay" onClick={onClose}>
      <div className="call-modal-card call-modal-medium client-lifecycle-modal" onClick={(event) => event.stopPropagation()}>
        <div className="call-modal-header">
          <div><p className="call-modal-kicker">Client lifecycle</p><h2>{display(client.client_id)}</h2></div>
          <button type="button" className="call-modal-close" onClick={onClose}>Close</button>
        </div>
        <div className="call-modal-content client-lifecycle-content">
          {loading ? <p>Loading client history…</p> : error ? <p className="muted">{error}</p> : <div className="client-lifecycle-columns">
            <div className="client-lifecycle-left">
            <div className="client-lifecycle-summary">
              <span>
                <b>Client Name</b>
                <input
                  type="text"
                  value={draftName}
                  onChange={(event) => setDraftName(event.target.value)}
                  placeholder="N/A"
                />
              </span>
              <span>
                <b>Email ID</b>
                <input
                  type="email"
                  value={draftEmail}
                  onChange={(event) => setDraftEmail(event.target.value)}
                  placeholder="N/A"
                />
              </span>
              <span><b>Phone</b>{display(client.phone_number)}</span>
              <span><b>First call</b>{formatDisplayDate(client.first_call_date, "N/A")}</span>
              <span><b>Last call</b>{formatDisplayDate(client.last_call_date, "N/A")}</span>
              <span>
                <b>Total calls</b>
                {display(client.total_calls)}
                {data?.breakdown && (
                  <div className="client-lifecycle-breakdown">
                    <div className="client-lifecycle-breakdown-row">
                      <em>Calls page</em>
                      <strong>{data.breakdown.calls_page}</strong>
                    </div>
                    <div
                      className="client-lifecycle-breakdown-row"
                      title={`The calls the Calls page hides (automated-attendant routing records). They make up ${data.breakdown.missed_call_entries} Missed Calls entries.`}
                    >
                      <em>Missed call page</em>
                      <strong>{data.breakdown.missed_call_page}</strong>
                    </div>
                  </div>
                )}
              </span>
              <span><b>Status</b>{display(client.client_status)}</span>
            </div>
            <div className="client-lifecycle-save-row">
              {saveError && <span className="identifier-modal-error">{saveError}</span>}
              <button
                type="button"
                className="primary-button"
                disabled={!hasChanges || saving}
                onClick={save}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
            </div>
            <div className="client-lifecycle-right">
            <h3>Call history</h3>
            <div className="client-lifecycle-list">
              {callGroups.length ? callGroups.map((group) => (
                <section key={group.key} className="client-lifecycle-group">
                  {group.title && (
                    <h4 className="client-lifecycle-group-title">
                      {group.title} <em>({group.calls.length})</em>
                      {group.hint && <small>{group.hint}</small>}
                    </h4>
                  )}
                  {group.calls.map((call) => (
                    <div key={call.call_id} className="client-lifecycle-row">
                      <span>{formatDisplayDateTime(`${call.call_date || ""} ${call.call_time || ""}`, "N/A")}</span>
                      <strong>{display(call.call_reference || call.call_id)}</strong>
                      <small>Call ID: {display(call.call_id)}</small>
                    </div>
                  ))}
                </section>
              )) : <p>No calls found for this client.</p>}
            </div>
            </div>
          </div>}
        </div>
      </div>
    </div>
  , document.body);
}
