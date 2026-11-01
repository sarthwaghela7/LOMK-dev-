export function formatTranscript(value) {
  if (value === null || value === undefined || value === "") return "No transcript available.";
  if (typeof value === "string") return value.replace(/<Object Object[^>]*>:/gi, "").replace(/\[object Object\]/gi, "").trim() || "No transcript available.";
  if (Array.isArray(value)) return value.map(formatTranscript).filter((item) => item !== "No transcript available.").join("\n") || "No transcript available.";
  if (typeof value === "object") {
    const text = value.text || value.transcript || value.content || value.message;
    if (text) return formatTranscript(text);
    return Object.entries(value).map(([speaker, message]) => `${speaker}: ${formatTranscript(message)}`).join("\n");
  }
  return String(value);
}

export function TranscriptModal({ call, onClose }) {
  return <div className="call-modal-overlay" onClick={onClose}><div className="call-modal-card" onClick={(event) => event.stopPropagation()}>
    <div className="call-modal-header"><div><p className="call-modal-kicker">Call Transcript</p><h2>{call.id}</h2></div><button type="button" className="call-modal-close" onClick={onClose}>Close</button></div>
    <div className="call-modal-content"><p>{formatTranscript(call.transcript)}</p></div>
  </div></div>;
}

// Read-only full-text view for a Call Summary cell, matching the Call
// Records table's same-sized popup (see Calls.jsx's cell-edit modal) --
// used where the summary itself isn't editable on the page (e.g. Missed
// Calls), so there's no textarea/Save, just the text and a Close button.
export function SummaryModal({ call, onClose, title = "Call Summary" }) {
  return <div className="call-modal-overlay" onClick={onClose}><div className="call-modal-card call-modal-medium" onClick={(event) => event.stopPropagation()}>
    <div className="call-modal-header"><div><p className="call-modal-kicker">{title}</p><h2>{call.id}</h2></div><button type="button" className="call-modal-close" onClick={onClose}>Close</button></div>
    <div className="call-modal-content"><p>{call.summary || "No summary available."}</p></div>
  </div></div>;
}

// The audio is loaded as a blob: URL (it needs an auth header, so it can't
// be a plain <audio src>), which carries no filename of its own -- the
// browser's native "Download" on the player falls back to a generic name.
// Name the downloaded file after the call's AF# id and the date it
// happened, so it's identifiable outside the tool.
export function audioDownloadName(call) {
  const label = String(call?.call_reference || call?.id || "call").trim();
  const callDate =
    call?.date && call.date !== "-" ? call.date : "unknown-date";
  return `${label}_${callDate}.wav`.replace(/[\\/:*?"<>|]/g, "-");
}

export function AudioModal({ call, url, loading, error, onClose }) {
  const downloadAudio = () => {
    if (!url) return;
    const link = document.createElement("a");
    link.href = url;
    link.download = audioDownloadName(call);
    document.body.appendChild(link);
    link.click();
    link.remove();
  };
  return <div className="call-modal-overlay" onClick={onClose}><div className="call-modal-card call-modal-compact" onClick={(event) => event.stopPropagation()}>
    <div className="call-modal-header"><div><p className="call-modal-kicker">Call Audio</p><h2>{call.id}</h2></div><div className="call-modal-header-actions">{url && <button type="button" className="table-action" onClick={downloadAudio}>Download</button>}<button type="button" className="call-modal-close" onClick={onClose}>Close</button></div></div>
    <div className="call-modal-audio-body">{loading && <p className="muted">Loading audio...</p>}{error && <p className="muted">{error}</p>}{url && <audio className="call-modal-player" controls autoPlay src={url} onError={() => {}} />}</div>
  </div></div>;
}
