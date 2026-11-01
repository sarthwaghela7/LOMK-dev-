import { CASE_STATUS_OPTIONS as SHARED_CASE_STATUS_OPTIONS } from "../data/statusOptions.js";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  analyseFlintDeskCall,
  getFlintDeskCall,
  updateFlintDeskManualFields,
  updateManualFields,
  uploadFlintDeskAttachment,
  getFlintDeskAttachmentUrl,
} from "../data/api.js";
import { displayClientType } from "../data/displayLabels.js";
import {
  formatDisplayDate,
  formatDisplayDateTime,
} from "../data/displayDate.js";

const MANUAL_FIELDS = [
  {
    key: "selfActionItem",
    apiKey: "fd_m_self_action_item",
    label: "Self Action Item",
    type: "text",
  },
  {
    key: "actionDueDate",
    apiKey: "fd_m_action_due_date",
    label: "Action Due Date",
    type: "date",
  },
  {
    key: "manualActionStatus",
    apiKey: "fd_m_manual_action_status",
    label: "Action Status",
    type: "text",
  },
  {
    key: "dependency",
    apiKey: "fd_m_dependency",
    label: "Dependency",
    type: "text",
  },
  {
    key: "followUpDates",
    apiKey: "fd_m_follow_up_dates",
    label: "Follow Up Dates",
    type: "text",
  },
  {
    key: "followUpAttempts",
    apiKey: "fd_m_follow_up_attempts",
    label: "# of Follow Up Attempts",
    type: "number",
  },
  {
    key: "blockerComment",
    apiKey: "fd_m_blocker_comment",
    label: "Blocker Comment",
    type: "textarea",
  },
];

const CASE_STATUS_OPTIONS = SHARED_CASE_STATUS_OPTIONS;

const CLIENT_STATUS_OPTIONS = ["Open", "Pending", "Closed", "Reopened"];

const STAGE1_FIELDS = [
  { key: "fd_a_client_name", label: "Client Name", type: "text" },
  { key: "fd_a_client_type", label: "Client Type", type: "text" },
  { key: "fd_a_lead_source", label: "Lead Source", type: "text" },
  { key: "fd_a_email_address", label: "Email Address", type: "text" },
  { key: "fd_a_urgency_flag", label: "Urgency Flag", type: "text" },
  { key: "fd_a_case_type", label: "Case Type", type: "text" },
  {
    key: "fd_a_case_worker_assigned",
    label: "Case Worker Assigned",
    type: "text",
  },
  { key: "fd_a_call_outcome", label: "Call Outcome", type: "text" },
  { key: "fd_a_call_summary", label: "Call Summary", type: "textarea" },
];

const valueOrEmpty = (value) =>
  value === null || value === undefined ? "" : value;

function buildStage2Fields(source = {}) {
  return MANUAL_FIELDS.reduce((acc, field) => {
    const rawValue = source[field.apiKey] ?? "";
    acc[field.key] =
      field.type === "number"
        ? rawValue === "" || rawValue === null
          ? ""
          : Number(rawValue)
        : String(rawValue ?? "");
    return acc;
  }, {});
}

export default function CallDetail({ call, onBack }) {
  const callId = call?.callId || call?.id;

  const [fields, setFields] = useState(() =>
    buildStage2Fields(call?.raw || {}),
  );
  const [saveState, setSaveState] = useState("idle");

  const [stage1Fields, setStage1Fields] = useState(() =>
    STAGE1_FIELDS.reduce((acc, field) => ({ ...acc, [field.key]: "" }), {}),
  );
  const [fdProcessingStatus, setFdProcessingStatus] = useState("not processed");
  const [analyzeState, setAnalyzeState] = useState("idle");
  const analysisRequest = useRef(null);
  const analysisInFlight = useRef(false);
  const [detailLoading, setDetailLoading] = useState(Boolean(callId));
  const [detailError, setDetailError] = useState("");

  const [caseStatus, setCaseStatus] = useState("Open");
  const [client, setClient] = useState(null);
  const [clientStatus, setClientStatus] = useState("Open");
  const [clientCallHistory, setClientCallHistory] = useState([]);
  const [closeDate, setCloseDate] = useState("");
  const [statusComment, setStatusComment] = useState("");
  const [attachmentValue, setAttachmentValue] = useState("");
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const [googleReview, setGoogleReview] = useState("");
  const [stage3SaveState, setStage3SaveState] = useState("idle");
  const [stage4SaveState, setStage4SaveState] = useState("idle");
  const [stage3Error, setStage3Error] = useState("");
  const [stage4Error, setStage4Error] = useState("");

  const isStage1Processed = useMemo(
    () => fdProcessingStatus === "processed",
    [fdProcessingStatus],
  );

  useEffect(() => {
    let cancelled = false;

    const loadFlintDeskData = async () => {
      if (!callId) {
        setDetailLoading(false);
        setDetailError("Missing call identifier.");
        return;
      }

      setDetailLoading(true);
      setDetailError("");

      try {
        const detail = await getFlintDeskCall(callId);
        if (cancelled) return;

        // Stage 1 uses AI fields from the FlintDesk backend.
        const analysis = detail?.analysis || {};
        setStage1Fields(
          STAGE1_FIELDS.reduce(
            (acc, field) => ({
              ...acc,
              [field.key]:
                // The client ID popup is the source of truth for the name.
                field.key === "fd_a_client_name" && detail?.client?.client_name
                  ? detail.client.client_name
                  : valueOrEmpty(analysis[field.key]),
            }),
            {},
          ),
        );
        setFdProcessingStatus(detail?.fd_processing_status || "not processed");
        setClient(detail?.client || null);
        setClientStatus(detail?.client?.client_status || "Open");
        setClientCallHistory(
          Array.isArray(detail?.client_call_history)
            ? detail.client_call_history
            : [],
        );

        // Stage 2 intentionally reuses the existing /calls/manual-fields/{analysis_id}
        // response already present in the call list data; keep manual fields separate
        // from the AI fields loaded here.
        const stage2Source = {
          ...(call?.raw || {}),
          ...(detail?.manual || {}),
        };
        setFields(buildStage2Fields(stage2Source));

        // Stage 3 / Stage 4 use the FlintDesk-specific /api/flintdesk/calls/{call_id}/manual-fields
        // route and keep the fd_m_* values separate from the AI-generated Stage 1 values.
        const manual = detail?.manual || {};
        setCaseStatus(manual.fd_m_case_status_change || "Open");
        setCloseDate(valueOrEmpty(manual.fd_m_date_entry) || "");
        setStatusComment(valueOrEmpty(manual.fd_m_verification_comment) || "");
        setAttachmentValue(valueOrEmpty(manual.fd_m_attachment) || "");
        setGoogleReview(valueOrEmpty(manual.fd_m_google_review) || "");
      } catch (error) {
        if (!cancelled) {
          setDetailError(
            error.message || "Could not load FlintDesk call details.",
          );
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    };

    loadFlintDeskData();

    return () => {
      cancelled = true;
    };
  }, [callId, call?.raw]);

  const updateField = (key, value) => {
    setFields((prev) => ({ ...prev, [key]: value }));
  };

  const saveStage2Fields = async () => {
    if (!call?.analysisId) {
      setSaveState("error");
      return;
    }

    setSaveState("saving");
    try {
      const payload = MANUAL_FIELDS.reduce((acc, field) => {
        const value = fields[field.key];
        acc[field.apiKey] =
          field.type === "number"
            ? value === ""
              ? null
              : Number(value)
            : value === ""
              ? null
              : value;
        return acc;
      }, {});

      const refreshed = await updateManualFields(call.analysisId, payload);
      const refreshedFields = buildStage2Fields(refreshed || {});
      setFields(refreshedFields);
      setSaveState("saved");
    } catch {
      setSaveState("error");
    }
  };

  const handleAnalyze = async () => {
    if (!callId || analysisInFlight.current) return;

    // Retain the same ID on errors so retrying an explicit re-analysis cannot
    // start another paid request after a lost HTTP response.
    if (analysisRequest.current?.callId !== callId) {
      analysisRequest.current = {
        callId,
        requestId: isStage1Processed ? crypto.randomUUID() : undefined,
      };
    }
    analysisInFlight.current = true;

    setAnalyzeState("analyzing");
    setDetailError("");

    try {
      const result = await analyseFlintDeskCall(callId, analysisRequest.current);
      const updated = result?.analysis || {};

      setStage1Fields(
        STAGE1_FIELDS.reduce(
          (acc, field) => ({
            ...acc,
            [field.key]: valueOrEmpty(updated[field.key]),
          }),
          {},
        ),
      );
      setFdProcessingStatus(result?.fd_processing_status || "processed");
      analysisRequest.current = null;
      setAnalyzeState("idle");
    } catch (error) {
      setAnalyzeState("idle");
      setDetailError(error.message || "FlintDesk analysis failed.");
    } finally {
      analysisInFlight.current = false;
    }
  };

  const saveStage3Fields = async () => {
    if (!callId) {
      setStage3Error("Missing call identifier.");
      setStage3SaveState("idle");
      return;
    }

    setStage3SaveState("saving");
    setStage3Error("");

    try {
      const response = await updateFlintDeskManualFields(callId, {
        fd_m_case_status_change: caseStatus || null,
        fd_m_date_entry: closeDate || null,
        fd_m_verification_comment: statusComment || null,
        client_status: clientStatus || null,
      });

      const manual = response?.manual || {};
      setCaseStatus(manual.fd_m_case_status_change || caseStatus || "Open");
      setCloseDate(valueOrEmpty(manual.fd_m_date_entry) || "");
      setStatusComment(valueOrEmpty(manual.fd_m_verification_comment) || "");
      if (response?.client) {
        setClient(response.client);
        setClientStatus(response.client.client_status || "Open");
      }
      setStage3SaveState("saved");
    } catch (error) {
      setStage3SaveState("idle");
      setStage3Error(error.message || "Could not save Stage 3 fields.");
    }
  };

  const saveStage4Fields = async () => {
    if (!callId) {
      setStage4Error("Missing call identifier.");
      setStage4SaveState("idle");
      return;
    }

    setStage4SaveState("saving");
    setStage4Error("");

    try {
      const response = await updateFlintDeskManualFields(callId, {
        fd_m_google_review: googleReview || null,
      });

      const manual = response?.manual || {};
      setGoogleReview(valueOrEmpty(manual.fd_m_google_review) || "");
      setStage4SaveState("saved");
    } catch (error) {
      setStage4SaveState("idle");
      setStage4Error(error.message || "Could not save Stage 4 field.");
    }
  };

  const handleAttachmentUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const validTypes = ["image/png", "image/jpeg"];
    if (!validTypes.includes(file.type) && !/\.(png|jpe?g)$/i.test(file.name)) {
      setStage3Error("Unsupported attachment type. Allowed: .png, .jpg, .jpeg");
      event.target.value = "";
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setStage3Error("Attachment must be 5MB or smaller.");
      event.target.value = "";
      return;
    }

    setAttachmentUploading(true);
    setStage3Error("");

    try {
      const response = await uploadFlintDeskAttachment(callId, file);
      if (response?.fd_m_attachment) {
        setAttachmentValue(response.fd_m_attachment);
      }
      setStage3SaveState("saved");
    } catch (error) {
      setStage3Error(error.message || "Attachment upload failed.");
    } finally {
      setAttachmentUploading(false);
      event.target.value = "";
    }
  };

  return (
    <section className="review-call-page">
      <div className="review-page-heading">
        <div>
          <h1>Call Details</h1>
          <p>Overview of the recording, AI analysis, and backend record.</p>
        </div>
        <button className="table-action" onClick={onBack}>
          <i className="fa-solid fa-arrow-left" /> Back to calls
        </button>
      </div>

      <div className="review-meta">
        <span>
          <b>Call ID</b> {call?.id || callId}
        </span>
        <span>
          <b>Client ID</b> {client?.client_id || call?.clientId || "-"}
        </span>
        <span>
          <b>Agent</b> {call?.agent || "-"}
        </span>
        <span>
          <b>Number</b> {call?.number || "-"}
        </span>
        <span>
          <b>Date</b> {formatDisplayDateTime(call?.date)}
        </span>
        <span>
          <b>Duration</b> {call?.duration || "-"}
        </span>
        <span>
          <b>Status</b>{" "}
          {fdProcessingStatus === "processed" ? "Processed" : "Not processed"}
        </span>
      </div>

      {detailLoading && <p className="muted">Loading FlintDesk details…</p>}
      {detailError && <p className="muted error">{detailError}</p>}

      <div className="review-workspace review-workspace-stages">
        <section className="transcript-panel">
          <div className="review-panel-header">
            <div>
              <h2>Stage 1</h2>
              <p>AI-generated FlintDesk intake fields.</p>
            </div>
            <button
              type="button"
              className="table-action primary"
              onClick={handleAnalyze}
              disabled={analyzeState === "analyzing" || !callId}
            >
              <i className="fa-solid fa-wand-magic-sparkles" />
              {analyzeState === "analyzing"
                ? "Analysing..."
                : isStage1Processed
                  ? "Re-analyse"
                  : "Analyse"}
            </button>
          </div>
          <div className="detail-block">
            <h3>Processing Status</h3>
            <input
              className="manual-field-input"
              type="text"
              value={fdProcessingStatus}
              readOnly
            />
          </div>
          {STAGE1_FIELDS.map((field) => (
            <div className="detail-block" key={field.key}>
              <h3>{field.label}</h3>
              {field.type === "textarea" ? (
                <textarea
                  className="manual-field-input"
                  rows={3}
                  value={
                    field.key === "fd_a_client_type"
                      ? displayClientType(
                          stage1Fields[field.key] ||
                            (isStage1Processed ? "N/A" : ""),
                        )
                      : stage1Fields[field.key] ||
                        (isStage1Processed ? "N/A" : "")
                  }
                  readOnly
                />
              ) : (
                <input
                  className="manual-field-input"
                  type="text"
                  value={
                    stage1Fields[field.key] || (isStage1Processed ? "N/A" : "")
                  }
                  readOnly
                />
              )}
            </div>
          ))}
        </section>

        <section className="extracted-panel">
          <div className="review-panel-header">
            <div>
              <h2>Stage 2</h2>
              <p>Agent-entered action tracking.</p>
            </div>
          </div>
          {MANUAL_FIELDS.map((field) => (
            <div className="detail-block" key={field.key}>
              <h3>{field.label}</h3>
              {field.type === "textarea" ? (
                <textarea
                  className="manual-field-input"
                  rows={3}
                  value={fields[field.key]}
                  onChange={(event) =>
                    updateField(field.key, event.target.value)
                  }
                />
              ) : (
                <input
                  className="manual-field-input"
                  type={field.type}
                  value={fields[field.key]}
                  onChange={(event) =>
                    updateField(field.key, event.target.value)
                  }
                />
              )}
            </div>
          ))}
          <div className="detail-block manual-fields-save">
            <button
              type="button"
              className="table-action primary"
              onClick={saveStage2Fields}
              disabled={saveState === "saving" || !call?.analysisId}
            >
              <i className="fa-solid fa-floppy-disk" />{" "}
              {saveState === "saving" ? "Saving..." : "Save"}
            </button>
            {saveState === "saved" && (
              <span className="manual-fields-status success">Saved</span>
            )}
            {saveState === "error" && (
              <span className="manual-fields-status error">
                Could not save Stage 2.
              </span>
            )}
          </div>
        </section>

        <section className="stage-panel">
          <div className="review-panel-header">
            <div>
              <h2>Stage 3</h2>
              <p>Case status tracking.</p>
            </div>
          </div>
          <div className="detail-block">
            <h3>Client Lifecycle Status</h3>
            <select
              className="manual-field-input"
              value={clientStatus}
              onChange={(event) => setClientStatus(event.target.value)}
              disabled={!client?.client_id}
            >
              {CLIENT_STATUS_OPTIONS.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
            {!client?.client_id && (
              <span className="manual-fields-status">
                No client is linked to this historical call yet.
              </span>
            )}
          </div>
          <div className="detail-block">
            <h3>Case Status</h3>
            <select
              className="manual-field-input"
              value={caseStatus}
              onChange={(event) => setCaseStatus(event.target.value)}
            >
              {CASE_STATUS_OPTIONS.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </div>
          {client?.client_id && (
            <div className="detail-block">
              <h3>Client Call History ({client.total_calls})</h3>
              <p className="manual-fields-status">
                First call: {formatDisplayDate(client.first_call_date)} &middot;
                Last call: {formatDisplayDate(client.last_call_date)}
              </p>
              <ul className="manual-fields-status">
                {clientCallHistory.map((item) => (
                  <li key={item.call_id}>
                    {item.call_reference || item.call_id} &middot;{" "}
                    {formatDisplayDateTime(
                      `${item.call_date || ""} ${item.call_time || ""}`,
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {caseStatus !== "Open" && (
            <>
              <div className="detail-block">
                <h3>Close Date</h3>
                <input
                  className="manual-field-input"
                  type="date"
                  value={closeDate}
                  onChange={(event) => setCloseDate(event.target.value)}
                />
              </div>
              <div className="detail-block">
                <h3>Comment</h3>
                <textarea
                  className="manual-field-input"
                  rows={3}
                  value={statusComment}
                  onChange={(event) => setStatusComment(event.target.value)}
                />
              </div>
            </>
          )}
          <div className="detail-block">
            <h3>Attachment</h3>
            <input
              className="manual-field-input"
              type="file"
              accept=".png,.jpg,.jpeg"
              onChange={handleAttachmentUpload}
              disabled={attachmentUploading || !callId}
            />
            <span className="manual-fields-status">
              Optional &middot; .jpg, .jpeg, .png
            </span>
            {attachmentUploading && (
              <span className="manual-fields-status">Uploading…</span>
            )}
            {attachmentValue && (
              <button
                type="button"
                className="manual-fields-status success attachment-cell-view"
                onClick={async () => {
                  try {
                    const url = await getFlintDeskAttachmentUrl(callId);
                    window.open(url, "_blank", "noopener,noreferrer");
                  } catch (error) {
                    setStage3Error(
                      error.message || "Failed to open attachment.",
                    );
                  }
                }}
              >
                Open saved attachment
              </button>
            )}
          </div>
          <div className="detail-block manual-fields-save">
            <button
              type="button"
              className="table-action primary"
              onClick={saveStage3Fields}
              disabled={stage3SaveState === "saving" || !callId}
            >
              <i className="fa-solid fa-floppy-disk" />{" "}
              {stage3SaveState === "saving" ? "Saving..." : "Save"}
            </button>
            {stage3SaveState === "saved" && (
              <span className="manual-fields-status success">Saved</span>
            )}
            {stage3Error && (
              <span className="manual-fields-status error">{stage3Error}</span>
            )}
          </div>
        </section>

        <section className="stage-panel">
          <div className="review-panel-header">
            <div>
              <h2>Stage 4</h2>
              <p>Google review tracking.</p>
            </div>
          </div>
          <div className="detail-block">
            <h3>Google Review</h3>
            <textarea
              className="manual-field-input"
              rows={3}
              placeholder="Add a Google review link or note"
              value={googleReview}
              onChange={(event) => setGoogleReview(event.target.value)}
            />
          </div>
          <div className="detail-block manual-fields-save">
            <button
              type="button"
              className="table-action primary"
              onClick={saveStage4Fields}
              disabled={stage4SaveState === "saving" || !callId}
            >
              <i className="fa-solid fa-floppy-disk" />{" "}
              {stage4SaveState === "saving" ? "Saving..." : "Save"}
            </button>
            {stage4SaveState === "saved" && (
              <span className="manual-fields-status success">Saved</span>
            )}
            {stage4Error && (
              <span className="manual-fields-status error">{stage4Error}</span>
            )}
          </div>
        </section>
      </div>
    </section>
  );
}
