import React, { useState, useRef, useCallback } from "react";
import jsPDF from "jspdf";
import { API_BASE_URL } from "../data/api";

// This used to call ElevenLabs directly from the browser with an API key
// read from a VITE_ env var -- Vite bakes VITE_-prefixed values into the
// built JS bundle, so that key was extracted straight out of dev tools by
// anyone visiting the site. The backend already exposes a proxy
// (POST /ai/transcribe) for exactly this ad-hoc transcription tool; it
// keeps the real key server-side and only requires the caller to be an
// authenticated user, same as every other page in this app.

export default function Transcript() {
  const [file, setFile] = useState(null);
  const [transcript, setTranscript] = useState("");
  const [segments, setSegments] = useState([]); // [{ speaker, text }]
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef(null);

  const isValidFile = (f) =>
    f && (f.type === "audio/wav" || f.type === "audio/mpeg" || /\.(wav|mp3)$/i.test(f.name));

  const handleFile = (f) => {
    if (!isValidFile(f)) {
      setError("Please upload a .wav or .mp3 file.");
      return;
    }
    setError("");
    setTranscript("");
    setFile(f);
  };

  const onDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }, []);

  const onDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(true);
  };

  const onDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
  };

  const onInputChange = (e) => {
    const f = e.target.files?.[0];
    if (f) handleFile(f);
  };

  // Turns ElevenLabs' diarized word list into readable speaker-grouped lines,
  // e.g. [{ speaker: "Speaker 1", text: "Hi, thanks for calling..." }, ...]
  const buildSegmentsFromWords = (words) => {
    if (!Array.isArray(words) || words.length === 0) return [];

    const segments = [];
    let current = null;

    for (const w of words) {
      // ElevenLabs marks non-speech tokens (pauses, etc.) with type "spacing";
      // skip those for grouping purposes but keep real words/punctuation.
      if (w.type === "spacing") {
        if (current) current.text += w.text ?? "";
        continue;
      }

      const speakerId = w.speaker_id ?? "unknown";
      if (!current || current.speakerId !== speakerId) {
        current = { speakerId, text: w.text ?? "" };
        segments.push(current);
      } else {
        current.text += w.text ?? "";
      }
    }

    // Turn raw speaker ids (e.g. "speaker_0") into friendlier labels
    // while keeping the same speaker consistently labeled throughout.
    const labelMap = {};
    let nextLabel = 1;
    return segments
      .map((seg) => {
        if (!(seg.speakerId in labelMap)) {
          labelMap[seg.speakerId] = `Speaker ${nextLabel++}`;
        }
        return { speaker: labelMap[seg.speakerId], text: seg.text.trim() };
      })
      .filter((seg) => seg.text.length > 0);
  };

  const transcribe = async () => {
    if (!file) return;
    setLoading(true);
    setError("");
    setTranscript("");
    setSegments([]);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("diarize", "true");

      const token = localStorage.getItem("autoflint_token");
      const response = await fetch(`${API_BASE_URL}/ai/transcribe`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`STT request failed (${response.status}): ${errText}`);
      }

      const data = await response.json();
      // ElevenLabs Scribe returns { text: "...", words: [{ text, speaker_id, type, start, end }] }
      const text = data.text || data.transcript || "";
      setTranscript(text.trim());

      const builtSegments = buildSegmentsFromWords(data.words);
      setSegments(builtSegments);
    } catch (err) {
      console.error(err);
      setError(err.message || "Failed to transcribe audio.");
    } finally {
      setLoading(false);
    }
  };

  const copyTranscript = async () => {
    if (!transcript) return;
    await navigator.clipboard.writeText(transcript);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const downloadPDF = () => {
    if (!transcript) return;
    const doc = new jsPDF({ unit: "pt", format: "a4" });
    const margin = 48;
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const maxWidth = pageWidth - margin * 2;

    doc.setFont("helvetica", "bold");
    doc.setFontSize(16);
    doc.text("Transcript", margin, margin);

    let y = margin + 30;
    const lineHeight = 16;

    const ensureRoom = () => {
      if (y > pageHeight - margin) {
        doc.addPage();
        y = margin;
      }
    };

    if (segments.length > 0) {
      segments.forEach((seg) => {
        ensureRoom();
        doc.setFont("helvetica", "bold");
        doc.setFontSize(11);
        doc.text(`${seg.speaker}:`, margin, y);
        y += lineHeight;

        doc.setFont("helvetica", "normal");
        const lines = doc.splitTextToSize(seg.text, maxWidth);
        lines.forEach((line) => {
          ensureRoom();
          doc.text(line, margin, y);
          y += lineHeight;
        });
        y += lineHeight * 0.5; // small gap between speaker turns
      });
    } else {
      doc.setFont("helvetica", "normal");
      doc.setFontSize(11);
      const lines = doc.splitTextToSize(transcript, maxWidth);
      lines.forEach((line) => {
        ensureRoom();
        doc.text(line, margin, y);
        y += lineHeight;
      });
    }

    const fileName = (file?.name || "transcript").replace(/\.(wav|mp3)$/i, "");
    doc.save(`${fileName}.pdf`);
  };

  const reset = () => {
    setFile(null);
    setTranscript("");
    setSegments([]);
    setError("");
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div style={styles.page}>
      <div style={styles.container}>
        <h1 style={styles.title}>Audio Transcript</h1>
        <p style={styles.subtitle}>Upload a WAV or MP3 file to generate a transcript.</p>

        <div
          style={{
            ...styles.dropzone,
            ...(dragActive ? styles.dropzoneActive : {}),
          }}
          onDrop={onDrop}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onClick={() => inputRef.current?.click()}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".wav,.mp3,audio/wav,audio/mpeg"
            onChange={onInputChange}
            style={{ display: "none" }}
          />
          {file ? (
            <div style={styles.fileInfo}>
              <span style={styles.fileName}>{file.name}</span>
              <span style={styles.fileSize}>{(file.size / 1024 / 1024).toFixed(2)} MB</span>
            </div>
          ) : (
            <div style={styles.dropText}>
              <div style={styles.dropIcon}>⬆</div>
              <div>Drag & drop audio here, or click to browse</div>
              <div style={styles.dropHint}>.wav or .mp3</div>
            </div>
          )}
        </div>

        {error && <div style={styles.error}>{error}</div>}

        <div style={styles.actions}>
          <button
            style={{ ...styles.btn, ...styles.btnPrimary, opacity: !file || loading ? 0.5 : 1 }}
            onClick={transcribe}
            disabled={!file || loading}
          >
            {loading ? "Transcribing..." : "Generate Transcript"}
          </button>
          <button style={{ ...styles.btn, ...styles.btnGhost }} onClick={reset} disabled={loading}>
            Reset
          </button>
        </div>

        {transcript && (
          <div style={styles.resultCard}>
            <div style={styles.resultHeader}>
              <h2 style={styles.resultTitle}>Transcript</h2>
              <div style={styles.resultActions}>
                <button style={{ ...styles.btn, ...styles.btnSmall }} onClick={copyTranscript}>
                  {copied ? "Copied!" : "Copy"}
                </button>
                <button style={{ ...styles.btn, ...styles.btnSmall, ...styles.btnPrimary }} onClick={downloadPDF}>
                  Download PDF
                </button>
              </div>
            </div>
            {segments.length > 0 ? (
              <div style={styles.segmentList}>
                {segments.map((seg, i) => (
                  <div key={i} style={styles.segmentRow}>
                    <span
                      style={{
                        ...styles.speakerBadge,
                        ...(seg.speaker === "Speaker 1" ? styles.speakerBadgeA : styles.speakerBadgeB),
                      }}
                    >
                      {seg.speaker}
                    </span>
                    <span style={styles.segmentText}>{seg.text}</span>
                  </div>
                ))}
              </div>
            ) : (
              <pre style={styles.transcriptText}>{transcript}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const styles = {
  page: {
    minHeight: "100vh",
    background: "#0b0f14",
    color: "#e6edf3",
    display: "flex",
    justifyContent: "center",
    padding: "48px 20px",
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  container: { width: "100%", maxWidth: 720 },
  title: { fontSize: 28, fontWeight: 700, marginBottom: 4 },
  subtitle: { color: "#8b98a5", marginBottom: 28, fontSize: 14 },
  dropzone: {
    border: "2px dashed #2a3542",
    borderRadius: 12,
    padding: "48px 24px",
    textAlign: "center",
    cursor: "pointer",
    background: "#111820",
    transition: "border-color 0.2s, background 0.2s",
  },
  dropzoneActive: { borderColor: "#2dd4bf", background: "#132320" },
  dropText: { color: "#8b98a5" },
  dropIcon: { fontSize: 28, marginBottom: 8, color: "#2dd4bf" },
  dropHint: { fontSize: 12, marginTop: 6, color: "#5b6772" },
  fileInfo: { display: "flex", flexDirection: "column", gap: 4 },
  fileName: { fontWeight: 600, color: "#e6edf3" },
  fileSize: { fontSize: 12, color: "#8b98a5" },
  error: {
    marginTop: 14,
    padding: "10px 14px",
    background: "rgba(239,68,68,0.1)",
    border: "1px solid rgba(239,68,68,0.3)",
    borderRadius: 8,
    color: "#f87171",
    fontSize: 13,
  },
  actions: { display: "flex", gap: 12, marginTop: 20 },
  btn: {
    padding: "10px 20px",
    borderRadius: 8,
    border: "1px solid #2a3542",
    background: "#161f29",
    color: "#e6edf3",
    fontSize: 14,
    fontWeight: 500,
    cursor: "pointer",
  },
  btnPrimary: { background: "#2dd4bf", color: "#07120f", border: "1px solid #2dd4bf" },
  btnGhost: { background: "transparent" },
  btnSmall: { padding: "6px 14px", fontSize: 13 },
  resultCard: {
    marginTop: 32,
    background: "#111820",
    border: "1px solid #1f2937",
    borderRadius: 12,
    padding: 20,
  },
  resultHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 14,
  },
  resultTitle: { fontSize: 16, fontWeight: 600, margin: 0 },
  resultActions: { display: "flex", gap: 8 },
  transcriptText: {
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    fontFamily: "inherit",
    fontSize: 14,
    lineHeight: 1.6,
    color: "#c9d1d9",
    margin: 0,
    maxHeight: 400,
    overflowY: "auto",
  },
  segmentList: {
    display: "flex",
    flexDirection: "column",
    gap: 12,
    maxHeight: 400,
    overflowY: "auto",
  },
  segmentRow: {
    display: "flex",
    gap: 10,
    alignItems: "flex-start",
  },
  speakerBadge: {
    flexShrink: 0,
    fontSize: 11,
    fontWeight: 700,
    padding: "3px 8px",
    borderRadius: 6,
    whiteSpace: "nowrap",
    marginTop: 1,
  },
  speakerBadgeA: {
    background: "rgba(45,212,191,0.15)",
    color: "#2dd4bf",
    border: "1px solid rgba(45,212,191,0.3)",
  },
  speakerBadgeB: {
    background: "rgba(168,85,247,0.15)",
    color: "#c084fc",
    border: "1px solid rgba(168,85,247,0.3)",
  },
  segmentText: {
    fontSize: 14,
    lineHeight: 1.6,
    color: "#c9d1d9",
    wordBreak: "break-word",
  },
};