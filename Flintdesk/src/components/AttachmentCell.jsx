import { useRef, useState } from "react";
import { uploadFlintDeskAttachment, getFlintDeskAttachmentUrl, deleteFlintDeskAttachment } from "../data/api.js";

const ALLOWED_EXTENSIONS = [".jpg", ".jpeg", ".png"];
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/jpg"]);
const MAX_SIZE_BYTES = 5 * 1024 * 1024; // Keep in sync with backend MAX_ATTACHMENT_SIZE_MB.

function isAllowedFile(file) {
  const extension = `.${(file.name.split(".").pop() || "").toLowerCase()}`;
  return ALLOWED_EXTENSIONS.includes(extension) && (!file.type || ALLOWED_TYPES.has(file.type));
}

function storedFileName(path) {
  if (!path) return "";
  const segments = path.split("/");
  return segments[segments.length - 1];
}

// Optional image attachment control shared by the Calls and Case Closing
// tables. Reuses the FlintDesk attachment upload/sign/delete API so both
// pages stay backed by the same Supabase Storage-backed endpoints.
export function AttachmentCell({ callId, target = "fd_m_attachment", value, onChange, onPreview }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const hasAttachment = Boolean(value);

  const pickFile = () => inputRef.current?.click();

  const handleFileSelected = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError("");

    if (!isAllowedFile(file)) {
      setError("Only .jpg, .jpeg, or .png images are allowed.");
      return;
    }
    if (file.size > MAX_SIZE_BYTES) {
      setError("Attachment must be 5MB or smaller.");
      return;
    }

    setBusy(true);
    try {
      const result = await uploadFlintDeskAttachment(callId, file, target);
      onChange?.(result[target] ?? null);
    } catch (err) {
      setError(err.message || "Failed to upload attachment.");
    } finally {
      setBusy(false);
    }
  };

  const viewAttachment = async () => {
    setError("");
    try {
      const url = await getFlintDeskAttachmentUrl(callId, target);
      if (onPreview) onPreview(url, storedFileName(value));
      else window.open(url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setError(err.message || "Failed to open attachment.");
    }
  };

  const removeAttachment = async () => {
    setBusy(true);
    setError("");
    try {
      await deleteFlintDeskAttachment(callId, target);
      onChange?.(null);
    } catch (err) {
      setError(err.message || "Failed to remove attachment.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="attachment-cell">
      <input
        ref={inputRef}
        type="file"
        accept=".jpg,.jpeg,.png,image/jpeg,image/png"
        className="attachment-cell-input"
        onChange={handleFileSelected}
      />
      <button type="button" className="table-action attachment-cell-choose" onClick={pickFile} disabled={busy}>
        {busy ? "..." : "Choose file"}
      </button>
      {hasAttachment ? (
        <>
          <button type="button" className="calls-cell-clickable attachment-cell-view" onClick={viewAttachment} title="View attachment">
            {storedFileName(value)}
          </button>
          <button type="button" className="icon-action-btn attachment-cell-remove" onClick={removeAttachment} disabled={busy} title="Remove attachment">
            <i className="fa-solid fa-xmark" />
          </button>
        </>
      ) : (
        <span className="attachment-cell-optional">Optional</span>
      )}
      {error && <span className="attachment-cell-error">{error}</span>}
    </div>
  );
}
