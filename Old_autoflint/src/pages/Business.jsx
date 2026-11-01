import { useEffect, useMemo, useRef, useState } from "react";
import {
  Briefcase, CheckCircle2, Clock3,
  Eye, Play, X, Loader2,
} from "lucide-react";
import { API_BASE_URL, getBusinessSummary, getBusinessCalls } from "../data/api";

const formatCallDate = (value) => {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(0, 10);
};

const formatCallTime = (value) => {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
};

// Same KPI card look as the Dashboard's DashboardKpiCard, kept local since
// Dashboard.jsx doesn't export that component — reuses the same classNames
// (dashboard-card, dashboard-card-icon, etc.), so all styling — hover, icon
// chip, spacing — comes from the existing CSS, not duplicated.
function BusinessKpiCard({ title, value, detail, icon, color }) {
  return (
    <article className="dashboard-card business-kpi-card">
      <div className="dashboard-card-icon" style={{ color, backgroundColor: `${color}15` }}>
        {icon}
      </div>
      <div className="dashboard-card-content">
        <div className="dashboard-card-title">{title}</div>
        <div className="dashboard-card-value">{value}</div>
        {detail && <div className="dashboard-card-detail">{detail}</div>}
      </div>
    </article>
  );
}

// Defaults the date filters to yesterday (start === end) so the page never
// opens with an unfiltered/empty date range.
function getMonthRange() {
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const toISO = (d) => d.toISOString().slice(0, 10);
  return { startDate: toISO(yesterday), endDate: toISO(yesterday) };
}

function Business() {
  // ── Filters — same instant-apply pattern as Pending Cases (no draft /
  //    Apply / Reset step; every change flows straight into `filters`). ──
  const [filters, setFilters] = useState(() => ({
    ...getMonthRange(),
    search: "",
    frontDesk: "",
  }));

  const hasActiveFilters = Boolean(filters.search || filters.frontDesk);

  const clearAllFilters = () => {
    setFilters((current) => ({ ...current, search: "", frontDesk: "" }));
  };

  // ── Data state ────────────────────────────────────────────────────────
  const [summary, setSummary] = useState(null);
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Defined inside the effect (rather than called by reference from the
  // component body) so state updates happen inside a locally-scoped async
  // function instead of being invoked synchronously from the effect body.
  // `ignore` guards against a stale response landing after a newer filter
  // change has already kicked off a fresh request.
  useEffect(() => {
    if (!filters.startDate || !filters.endDate) return;

    let ignore = false;

    const fetchData = async (startDate, endDate) => {
      setLoading(true);
      setError("");
      try {
        const [summaryResp, callsResp] = await Promise.all([
          getBusinessSummary(startDate, endDate),
          getBusinessCalls(startDate, endDate),
        ]);
        if (ignore) return;

        const callsArray = Array.isArray(callsResp)
          ? callsResp
          : callsResp?.calls || callsResp?.results || callsResp?.items || [];

        setSummary(summaryResp || null);
        setCalls(callsArray);
      } catch (err) {
        if (ignore) return;
        console.error("Business data load failed", err);
        setError(err.message || "Unable to load business opportunities right now.");
        setSummary(null);
        setCalls([]);
      } finally {
        if (!ignore) setLoading(false);
      }
    };

    fetchData(filters.startDate, filters.endDate);

    return () => {
      ignore = true;
    };
  }, [filters.startDate, filters.endDate]);

  // Search Cases (Call Number / Call ID / Client Name) and Front Desk Person
  // are applied client-side over the fetched rows — same pattern Pending
  // Cases uses for its own text filters — so the table (and the KPI counts
  // derived from it) refresh instantly on every keystroke without an extra
  // round trip for filters the backend doesn't accept as query params.
  const filteredCalls = useMemo(() => {
    const search = filters.search.trim().toLowerCase();
    const frontDesk = filters.frontDesk.trim().toLowerCase();

    return calls.filter((row) => {
      const searchableValues = [
        row.call_number,
        row.phone_number,
        row.call_reference,
        row.call_id,
        row.client_name,
      ].map((value) => String(value || "").toLowerCase());
      const matchesSearch = !search || searchableValues.some((value) => value.includes(search));
      const matchesFrontDesk =
        !frontDesk || String(row.front_desk_person_name || "").toLowerCase().includes(frontDesk);

      return matchesSearch && matchesFrontDesk;
    });
  }, [calls, filters.search, filters.frontDesk]);

  // Backend already computes these — the frontend only displays them, with
  // a couple of fallback key names in case the API's final field naming
  // differs slightly from this first pass.
  const kpis = useMemo(() => ({
    businessOpportunities: summary?.business_opportunities ?? summary?.total ?? 0,
    convertedToPaidConsultation: summary?.converted_to_paid_consultation ?? summary?.success_pc ?? 0,
    pendingBusinessOpportunities: summary?.pending_business_opportunities ?? summary?.pending ?? 0,
  }), [summary]);
  // Backend summary reflects the full date range; when Search Cases or Front
  // Desk Person narrow the visible rows, cap the displayed KPI count so the
  // cards stay consistent with what's actually shown in the table below.
  const displayedBusinessOpportunities = hasActiveFilters
    ? filteredCalls.length
    : kpis.businessOpportunities;

  // ── Transcript modal — reuses the same overlay/card classNames as the
  //    Dashboard/Call Records transcript modal. ───────────────────────────
  const [transcriptModalRow, setTranscriptModalRow] = useState(null);
  const closeTranscriptModal = () => setTranscriptModalRow(null);

  // ── Audio modal — identical logic/markup to Dashboard's handlePlayAudio,
  //    so the existing audio player is reused rather than reimplemented. ──
  const [audioModalRow, setAudioModalRow] = useState(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState("");
  const audioRef = useRef(null);
  const audioCacheRef = useRef(new Map());

  const closeAudioModal = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    setAudioModalRow(null);
    setAudioUrl("");
    setAudioLoading(false);
    setAudioError("");
  };

  const handlePlayAudio = async (row) => {
    const callId = row.call_id;
    setAudioModalRow(row);
    setAudioUrl("");
    setAudioError("");

    if (!callId) {
      setAudioError("Audio is not available for this call.");
      return;
    }

    const cached = audioCacheRef.current.get(callId);
    if (cached) {
      setAudioUrl(cached);
      return;
    }

    setAudioLoading(true);
    try {
      const token = localStorage.getItem("autoflint_token");
      const response = await fetch(`${API_BASE_URL}/calls/audio?call_id=${encodeURIComponent(callId)}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new Error("Unable to load audio.");
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      audioCacheRef.current.set(callId, blobUrl);
      setAudioUrl(blobUrl);
    } catch (err) {
      setAudioError(err.message || "Unable to load audio.");
    } finally {
      setAudioLoading(false);
    }
  };

  useEffect(() => {
    return () => {
      audioCacheRef.current.forEach((url) => URL.revokeObjectURL(url));
      audioCacheRef.current.clear();
    };
  }, []);

  return (
    <div className="business-page">
      <div className="page-header">
        <h1>Business Opportunities</h1>
      </div>

      <div className="filters-panel call-filters-panel">
        <div className="filters-row">
          <div className="filter-group filter-group-compact">
            <label>Start Date</label>
            <input
              className="filter-date-input"
              type="date"
              value={filters.startDate}
              onChange={(e) => setFilters((current) => ({ ...current, startDate: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-compact">
            <label>End Date</label>
            <input
              className="filter-date-input"
              type="date"
              value={filters.endDate}
              onChange={(e) => setFilters((current) => ({ ...current, endDate: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-extension">
            <label>Search Cases</label>
            <input
              type="text"
              placeholder="Search call number, call ID, or client..."
              value={filters.search}
              onChange={(e) => setFilters((current) => ({ ...current, search: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-extension">
            <label>Front Desk Person</label>
            <input
              type="text"
              placeholder="Filter by front desk person..."
              value={filters.frontDesk}
              onChange={(e) => setFilters((current) => ({ ...current, frontDesk: e.target.value }))}
            />
          </div>
          {hasActiveFilters && (
            <div className="filter-actions" style={{ display: "flex", alignItems: "flex-end" }}>
              <button type="button" onClick={clearAllFilters} className="btn-secondary filter-clear-btn">
                Clear All Filters
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── KPI Cards — only 3, centered and given more width each ───────── */}
      <section className="dashboard-grid business-kpi-grid">
        <BusinessKpiCard
          title="Business Opportunities"
          value={loading ? "—" : displayedBusinessOpportunities}
          detail="Total calls flagged as business opportunities"
          icon={<Briefcase size={18} />}
          color="var(--sky-400)"
        />
        <BusinessKpiCard
          title="Converted to Paid Consultation"
          value={loading ? "—" : kpis.convertedToPaidConsultation}
          detail="Status: Success PC"
          icon={<CheckCircle2 size={18} />}
          color="var(--emerald-400)"
        />
        <BusinessKpiCard
          title="Pending Business Opportunities"
          value={loading ? "—" : kpis.pendingBusinessOpportunities}
          detail="Not yet converted"
          icon={<Clock3 size={18} />}
          color="var(--amber-400)"
        />
      </section>

      {error && <div className="report-error" style={{ marginBottom: 16 }}>{error}</div>}

      {/* ── Business Opportunities Table ─────────────────────────────────── */}
      <div className="dashboard-panel">
        <div className="dashboard-panel-header">
          <Briefcase size={18} />
          <span>Business Opportunity Calls</span>
          <span className="panel-badge">{filteredCalls.length} calls</span>
        </div>

        {loading ? (
          <div className="tracker-empty">
            <Loader2 size={28} className="spin" />
            <p>Loading business opportunities...</p>
          </div>
        ) : filteredCalls.length === 0 ? (
          <div className="tracker-empty">
            <Briefcase size={32} />
            <p>No business opportunity calls for the selected filters.</p>
          </div>
        ) : (
          <div className="tracker-table-wrapper" style={{ maxHeight: "70vh", overflowY: "auto" }}>
            <table className="tracker-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Call Number</th>
                  <th>Client Name</th>
                  <th>Time</th>
                  <th>Date</th>
                  <th>Call ID</th>
                  <th>Front Desk Person</th>
                  <th>Transcript</th>
                  <th style={{ textAlign: "center" }}>Audio</th>
                </tr>
              </thead>
              <tbody>
                {filteredCalls.map((row, idx) => {
                  const transcript = String(row.transcript || "").trim();
                  const preview = transcript.length > 80 ? `${transcript.slice(0, 80)}…` : transcript;
                  return (
                    <tr key={row.id ?? row.call_reference ?? row.call_id ?? idx}>
                      <td>{idx + 1}</td>
                      <td>{row.call_number || row.phone_number || "—"}</td>
                      <td>{row.client_name || "—"}</td>
                      <td>{formatCallTime(row.call_recording_date || row.call_time)}</td>
                      <td>{formatCallDate(row.call_recording_date || row.call_date)}</td>
                      <td>{row.call_reference || row.call_id || "—"}</td>
                      <td>{row.front_desk_person_name || "N/A"}</td>
                      <td>
                        <span style={{ color: "var(--slate-400)", fontSize: 13 }}>
                          {preview || "—"}
                        </span>
                        {transcript && (
                          <button
                            type="button"
                            className="transcript-view-btn"
                            onClick={() => setTranscriptModalRow(row)}
                            aria-label="View full transcript"
                            title="View full transcript"
                            style={{ marginLeft: 8 }}
                          >
                            <Eye size={16} />
                          </button>
                        )}
                      </td>
                      <td style={{ textAlign: "center" }}>
                        <button
                          type="button"
                          className="audio-play-btn"
                          onClick={() => handlePlayAudio(row)}
                          disabled={!row.call_id}
                          aria-label="Play audio"
                          title="Play audio"
                        >
                          <Play size={16} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Transcript modal ──────────────────────────────────────────────── */}
      {transcriptModalRow && (
        <div className="transcript-modal-overlay" onClick={closeTranscriptModal}>
          <div className="transcript-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="transcript-modal-header">
              <div>
                <p className="transcript-modal-kicker">Call Transcript</p>
                <h2>{transcriptModalRow.call_reference || transcriptModalRow.call_id}</h2>
              </div>
              <div className="transcript-modal-actions">
                <button type="button" className="transcript-modal-close" onClick={closeTranscriptModal}>
                  <X size={18} />
                </button>
              </div>
            </div>
            <div className="transcript-modal-meta">
              <span>{transcriptModalRow.front_desk_person_name || "—"}</span>
            </div>
            <div className="transcript-modal-content">
              <p style={{ whiteSpace: "pre-wrap" }}>
                {transcriptModalRow.transcript || "No transcript available."}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── Audio modal ───────────────────────────────────────────────────── */}
      {audioModalRow && (
        <div className="audio-modal-overlay" onClick={closeAudioModal}>
          <div className="audio-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Call Audio</p>
                <h2>{audioModalRow.call_reference || audioModalRow.call_id}</h2>
              </div>
              <div className="audio-modal-actions">
                <button type="button" className="audio-modal-close" onClick={closeAudioModal}>
                  <X size={18} />
                </button>
              </div>
            </div>
            <div className="audio-modal-meta">
              <span>{audioModalRow.front_desk_person_name || "—"}</span>
            </div>
            {audioLoading && (
              <div className="audio-modal-loading">
                <Loader2 size={18} className="spin" /> Loading audio...
              </div>
            )}
            {audioError && <div className="audio-modal-error">{audioError}</div>}
            {audioUrl && (
              <audio
                ref={audioRef}
                className="audio-modal-player"
                controls
                autoPlay
                src={audioUrl}
                onError={() => setAudioError("The selected audio file could not be played.")}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default Business;