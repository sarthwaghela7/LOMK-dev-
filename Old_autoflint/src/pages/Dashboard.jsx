import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { API_BASE_URL, getCallAnalysis, getCalls, getCallContent, updateCallAnalysisFollowUpStatus, updateVoicemailLeft } from "../data/api";
import { useDashboardFilters } from "../hooks/usePersistentFilters";
import {
  BrainCircuit, CalendarRange, FileText, Sparkles, TrendingUp,
  AlertTriangle, CheckCircle2, Clock, BarChart3,
  ShieldAlert, Phone, ChevronDown, ChevronUp,
  DollarSign, UserPlus, UserCheck, CheckSquare, Square, X, Plus,
  Eye, Play, Loader2
} from "lucide-react";
import CallVolumeChart from "../components/charts/CallVolumeChart";
import AnalysisStatusChart from "../components/charts/AnalysisStatusChart";
import DashboardFilters from "../components/Filters/DashboardFilters";
import {
  VALID_AGENT_NAMES, canonicalizeAgentName, isKnownAgent,
  getCallRefCandidates, buildAgentAttribution, resolveAgentForCall,
  isAnalysisRowComplete,
} from "../utils/agentAttribution";
import {
  buildMetrics as buildSharedMetrics,
  isResolvedFollowUp as isResolvedFollowUpStatus,
  scoreColor,
  formatDuration,
  classifySentiment,
  FIXED_LEAD_SOURCES,
  classifyLeadSource,
  parseScore,
  SCORE_MAX,
  scoreBand,
} from "../utils/reportMetrics";
import {
  initDB,
  saveToIndexedDB,
  getFromIndexedDB,
  STORE_NAMES_EXPORT,
} from "../utils/indexedDBCache";

const formatDate = (date) => date.toISOString().slice(0, 10);

// Shared follow-up status helper used by the dashboard tracker UI.
const isResolvedFollowUpStatusValue = (status) => isResolvedFollowUpStatus(status);

// Human-readable label for the applied date range, e.g. "Jul 16, 2026" for a
// single day or "Jun 16 – Jul 16, 2026" for a range — used so KPI cards never
// show a misleading hardcoded "This month" when a custom range is applied.
const formatDateRangeLabel = (startISO, endISO) => {
  if (!startISO || !endISO) return "Selected period";

  const start = new Date(`${startISO}T00:00:00`);
  const end = new Date(`${endISO}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "Selected period";

  const sameDay = startISO === endISO;
  const sameYear = start.getFullYear() === end.getFullYear();

  const dayMonthOpts = { month: "short", day: "numeric" };
  const fullOpts = { month: "short", day: "numeric", year: "numeric" };

  if (sameDay) {
    return start.toLocaleDateString("en-US", fullOpts);
  }

  const startLabel = start.toLocaleDateString("en-US", sameYear ? dayMonthOpts : fullOpts);
  const endLabel = end.toLocaleDateString("en-US", fullOpts);
  return `${startLabel} – ${endLabel}`;
};

// Display a quality score rounded to exactly 1 decimal place.
function formatScore(value) {
  const n = parseScore(value);
  return n === null ? "—" : n.toFixed(1);
}

// Maps scoreBand result to a display label ("Unscored" → "—").
function scoreLabel(score) {
  const band = scoreBand(score);
  return band === "Unscored" ? "—" : band;
}

function ClientTypeDonut({ data }) {
  const newCount = Number(data?.new || 0);
  const existingCount = Number(data?.existing || 0);
  const naCount = Number(data?.na || 0);
  const total = newCount + existingCount + naCount;

  if (total === 0) {
    return (
      <div className="donut-empty">
        <UserPlus size={32} />
        <p>No client type data available</p>
      </div>
    );
  }

  const segments = [
    { label: "New", value: newCount, color: "var(--emerald-400)" },
    { label: "Existing", value: existingCount, color: "var(--sky-400)" },
    { label: "N/A", value: naCount, color: "var(--slate-500)" },
  ].filter((segment) => segment.value > 0);

  const circumference = 2 * Math.PI * 52;
  let offset = 0;

  return (
    <div className="donut-chart-container">
      <div className="donut-chart-wrapper">
        <svg viewBox="0 0 120 120" className="donut-svg">
          <circle cx="60" cy="60" r="52" fill="none" stroke="var(--slate-700)" strokeWidth="10" />
          {segments.map((segment) => {
            const segmentLength = (segment.value / total) * circumference;
            const segmentCircle = (
              <circle
                key={segment.label}
                cx="60"
                cy="60"
                r="52"
                fill="none"
                stroke={segment.color}
                strokeWidth="10"
                strokeDasharray={`${segmentLength} ${circumference}`}
                strokeDashoffset={-offset}
                strokeLinecap="round"
                transform="rotate(-90 60 60)"
                className="donut-segment"
              />
            );
            offset += segmentLength;
            return segmentCircle;
          })}
        </svg>
        <div className="donut-center">
          <span className="donut-total">{total}</span>
          <span className="donut-label">Clients</span>
        </div>
      </div>
      <div className="donut-legend">
        {segments.map((segment) => (
          <div key={segment.label} className="donut-legend-item">
            <span className="donut-legend-dot" style={{ background: segment.color }} />
            <span className="donut-legend-label">{segment.label}</span>
            <span className="donut-legend-value">{segment.value}</span>
            <span className="donut-legend-pct">{Math.round((segment.value / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CallDirectionDonut({ inbound, outbound }) {
  const total = inbound + outbound;

  if (total === 0) {
    return (
      <div className="donut-empty">
        <Phone size={32} />
        <p>No call direction data available</p>
      </div>
    );
  }

  const circumference = 2 * Math.PI * 52;
  const inboundLength = (inbound / total) * circumference;
  const outboundLength = (outbound / total) * circumference;

  return (
    <div className="donut-chart-container">
      <div className="donut-chart-wrapper">
        <svg viewBox="0 0 120 120" className="donut-svg">
          <circle cx="60" cy="60" r="52" fill="none" stroke="var(--slate-700)" strokeWidth="10" />
          <circle
            cx="60"
            cy="60"
            r="52"
            fill="none"
            stroke="var(--sky-400)"
            strokeWidth="10"
            strokeDasharray={`${inboundLength} ${circumference}`}
            strokeDashoffset={0}
            strokeLinecap="round"
            transform="rotate(-90 60 60)"
            className="donut-segment"
          />
          <circle
            cx="60"
            cy="60"
            r="52"
            fill="none"
            stroke="var(--violet-400)"
            strokeWidth="10"
            strokeDasharray={`${outboundLength} ${circumference}`}
            strokeDashoffset={-inboundLength}
            strokeLinecap="round"
            transform="rotate(-90 60 60)"
            className="donut-segment"
          />
        </svg>
        <div className="donut-center">
          <span className="donut-total">{total}</span>
          <span className="donut-label">Calls</span>
        </div>
      </div>
      <div className="donut-legend">
        <div className="donut-legend-item">
          <span className="donut-legend-dot" style={{ background: "var(--sky-400)" }} />
          <span className="donut-legend-label">Inbound</span>
          <span className="donut-legend-value">{inbound}</span>
          <span className="donut-legend-pct">{Math.round((inbound / total) * 100)}%</span>
        </div>
        <div className="donut-legend-item">
          <span className="donut-legend-dot" style={{ background: "var(--violet-400)" }} />
          <span className="donut-legend-label">Outbound</span>
          <span className="donut-legend-value">{outbound}</span>
          <span className="donut-legend-pct">{Math.round((outbound / total) * 100)}%</span>
        </div>
      </div>
    </div>
  );
}

// ─── Voicemail Left Donut (Yes vs No — was a message actually left?) ──────
function VoicemailLeftDonut({ yes, no }) {
  const total = yes + no;
  if (total === 0) {
    return (
      <div className="donut-empty">
        <Phone size={32} />
        <p>No voicemail data available</p>
      </div>
    );
  }

  const yesPct = Math.round((yes / total) * 100);
  const noPct = Math.round((no / total) * 100);
  const circumference = 2 * Math.PI * 52;
  const yesOffset = circumference - (yes / total) * circumference;

  return (
    <div className="donut-chart-container">
      <div className="donut-chart-wrapper">
        <svg viewBox="0 0 120 120" className="donut-svg">
          <circle cx="60" cy="60" r="52" fill="none" stroke="var(--slate-700)" strokeWidth="10" />
          <circle
            cx="60" cy="60" r="52" fill="none"
            stroke="var(--emerald-400)" strokeWidth="10"
            strokeDasharray={circumference}
            strokeDashoffset={yesOffset}
            strokeLinecap="round"
            transform="rotate(-90 60 60)"
            className="donut-segment"
          />
          <circle
            cx="60" cy="60" r="52" fill="none"
            stroke="var(--rose-400)" strokeWidth="10"
            strokeDasharray={circumference}
            strokeDashoffset={0}
            strokeLinecap="round"
            transform="rotate(-90 60 60)"
            className="donut-segment-bg"
            style={{ opacity: 0.3 }}
          />
        </svg>
        <div className="donut-center">
          <span className="donut-total">{total}</span>
          <span className="donut-label">Total</span>
        </div>
      </div>
      <div className="donut-legend">
        <div className="donut-legend-item">
          <span className="donut-legend-dot" style={{ background: "var(--emerald-400)" }} />
          <span className="donut-legend-label">Left (Yes)</span>
          <span className="donut-legend-value">{yes}</span>
          <span className="donut-legend-pct">{yesPct}%</span>
        </div>
        <div className="donut-legend-item">
          <span className="donut-legend-dot" style={{ background: "var(--rose-400)" }} />
          <span className="donut-legend-label">Not Left (No)</span>
          <span className="donut-legend-value">{no}</span>
          <span className="donut-legend-pct">{noPct}%</span>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// KPI CARD COMPONENT — renders a single KPI with optional click-to-drill-down.
// Cards with onClick get a visual "clickable" indicator (subtle arrow / cursor).
// ═══════════════════════════════════════════════════════════════════════════════
function DashboardKpiCard({ card }) {
  const isClickable = Boolean(card.onClick);
  return (
    <article
      className={`dashboard-card${isClickable ? " dashboard-card-clickable" : ""}`}
      onClick={card.onClick}
      role={isClickable ? "button" : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onKeyDown={isClickable ? (e) => { if (e.key === "Enter" || e.key === " ") card.onClick(); } : undefined}
    >
      {isClickable && <span className="dashboard-card-click-hint" title="Click for details">↗</span>}
      <div className="dashboard-card-icon" style={{ color: card.color, backgroundColor: `${card.color}15` }}>{card.icon}</div>
      <div className="dashboard-card-content">
        <div className="dashboard-card-title">{card.title}</div>
        {card.subItems ? (
          <div style={{ marginTop: 6 }}>
            {card.subItems.map((item) => (
              <div
                key={item.label}
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "12px", padding: "2px 0", gap: 8, cursor: item.onClick ? "pointer" : "default" }}
                onClick={(e) => { if (!item.onClick) return; e.stopPropagation(); item.onClick(); }}
              >
                <span style={{ color: "var(--slate-400)" }}>{item.label}</span>
                <span style={{ fontWeight: 600, color: "var(--slate-100)" }}>{item.value}</span>
              </div>
            ))}
          </div>
        ) : (
          <>
            <div className="dashboard-card-value">{card.value}</div>
            <div className="dashboard-card-detail">{card.detail}</div>
          </>
        )}
      </div>
    </article>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN DASHBOARD COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════
function Dashboard() {
  const { user } = useAuth();
  const { dashboardFilters, setDashboardFilters, resetDashboardFilters } = useDashboardFilters();
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good Morning" : hour < 17 ? "Good Afternoon" : "Good Evening";

  // ── Data state ─────────────────────────────────────────────────────────────
  const [callRecords, setCallRecords] = useState([]);
  const [callAnalysis, setCallAnalysis] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [expandedTracker, setExpandedTracker] = useState(true);
  // Tracks ALL in-flight "Mark Done" / "Reopen" requests as a Set, so any
  // number of rows can be updated simultaneously — clicking a second row
  // never blocks or cancels the first one.
  const [updatingFollowUpIds, setUpdatingFollowUpIds] = useState(() => new Set());
  const [updatingVmuIds, setUpdatingVmuIds] = useState(() => new Set());

  // ── Consultation selection state ─────────────────────────────────────────
  const [consultationSelectionMode, setConsultationSelectionMode] = useState(false);

  // Load cached data from IndexedDB on component mount
  useEffect(() => {
    const loadCachedData = async () => {
      try {
        await initDB();
        const cachedRecords = await getFromIndexedDB(STORE_NAMES_EXPORT.CALL_RECORDS);
        const cachedAnalysis = await getFromIndexedDB(STORE_NAMES_EXPORT.CALL_ANALYSIS);
        
        if (cachedRecords) setCallRecords(cachedRecords);
        if (cachedAnalysis) setCallAnalysis(cachedAnalysis);
      } catch (err) {
        console.warn("Failed to load cached data from IndexedDB:", err);
      }
    };
    
    loadCachedData();
  }, []);
  const [selectedConsultationIds, setSelectedConsultationIds] = useState(new Set());
  const [bulkUpdatingConsultation, setBulkUpdatingConsultation] = useState(false);

  // ── Follow-up tracker selection state ────────────────────────────────────
  const [trackerSelectionMode, setTrackerSelectionMode] = useState(false);
  const [selectedTrackerIds, setSelectedTrackerIds] = useState(new Set());
  const [bulkUpdatingTracker, setBulkUpdatingTracker] = useState(false);

  // ── Custom lead sources (user-added, persisted in localStorage) ─────────
  const [customLeadSources, setCustomLeadSources] = useState(() => {
    try { return JSON.parse(localStorage.getItem("custom_lead_sources") || "[]"); } catch { return []; }
  });
  const [addingLeadSource, setAddingLeadSource] = useState(false);
  const [newLeadSourceName, setNewLeadSourceName] = useState("");

  const persistCustomLeadSources = (list) => {
    setCustomLeadSources(list);
    try { localStorage.setItem("custom_lead_sources", JSON.stringify(list)); } catch { /* ignore */ }
  };

  const handleAddLeadSource = () => {
    const name = newLeadSourceName.trim();
    if (!name) return;
    const exists = customLeadSources.some((s) => s.toLowerCase() === name.toLowerCase());
    if (!exists) persistCustomLeadSources([...customLeadSources, name]);
    setNewLeadSourceName("");
    setAddingLeadSource(false);
  };

  const handleRemoveLeadSource = (name) => {
    persistCustomLeadSources(customLeadSources.filter((s) => s !== name));
  };

  // ── Filter draft state ───────────────────────────────────────────────────
  // filterDraft holds the in-progress values shown in the filter form.
  // dashboardFilters (from useDashboardFilters) holds the last *applied*
  // filters that actually drive data fetching / useMemo filtering below.
  const [filterDraft, setFilterDraft] = useState(dashboardFilters);

  // Keep the draft in sync if the applied filters change from elsewhere
  // (e.g. persisted values loading, or resetDashboardFilters()).
  useEffect(() => {
    setFilterDraft(dashboardFilters);
  }, [dashboardFilters]);

  const handleFilterDraftChange = (partialFilters) => {
    setFilterDraft((prev) => ({ ...prev, ...partialFilters }));
  };

  const handleApplyFilters = () => {
    setDashboardFilters(filterDraft);
  };

  // ── Load data based on filters ───────────────────────────────────────────
  const fetchData = async (startDate, endDate) => {
    setLoading(true);
    setLoadError("");
    try {
      const [recordsResp, analysisResp] = await Promise.all([
        getCalls(startDate, endDate),
        getCallAnalysis(startDate, endDate),
      ]);

      const recordsArray = Array.isArray(recordsResp)
        ? recordsResp
        : recordsResp?.calls || recordsResp?.results || [];

      const analysisArray = Array.isArray(analysisResp)
        ? analysisResp
        : analysisResp?.analysis || analysisResp?.results || analysisResp?.items || [];

      setCallRecords(recordsArray);
      setCallAnalysis(analysisArray);
      
      // Cache data to IndexedDB (non-blocking, errors are logged but don't fail the operation)
      saveToIndexedDB(STORE_NAMES_EXPORT.CALL_RECORDS, recordsArray).catch((err) => {
        console.warn("Failed to cache call records to IndexedDB:", err);
      });
      saveToIndexedDB(STORE_NAMES_EXPORT.CALL_ANALYSIS, analysisArray).catch((err) => {
        console.warn("Failed to cache call analysis to IndexedDB:", err);
      });
    } catch (err) {
      console.error("Dashboard data load failed", err);
      setLoadError(err.message || "Dashboard data could not be loaded.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (dashboardFilters.startDate && dashboardFilters.endDate) {
      fetchData(dashboardFilters.startDate, dashboardFilters.endDate);
    }
  }, [dashboardFilters.startDate, dashboardFilters.endDate]);

  const handleResetFilters = () => {
    resetDashboardFilters();
    const now = new Date();
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
    const monthEnd = new Date(now.getFullYear(), now.getMonth() + 1, 0);
    // resetDashboardFilters() may not clear the score range on its own, so
    // make sure the draft (and therefore the visible inputs) reflect a clean
    // slate immediately rather than waiting on the sync effect.
    setFilterDraft((prev) => ({
      ...prev,
      agent: "",
      minQualityScore: "",
      maxQualityScore: "",
      startDate: formatDate(monthStart),
      endDate: formatDate(monthEnd),
    }));
    fetchData(formatDate(monthStart), formatDate(monthEnd));
  };

  const handleToggleFollowUpStatus = async (row) => {
    const nextStatus = isResolvedFollowUpStatusValue(row.follow_up_status) ? "Pending" : "Done";

    // Mark only THIS row as saving — every other row stays clickable, so
    // multiple updates can run in parallel.
    setUpdatingFollowUpIds((prev) => {
      const next = new Set(prev);
      next.add(row.id);
      return next;
    });

    try {
      await updateCallAnalysisFollowUpStatus(row.id, nextStatus);

      // Update local state directly instead of a full refetch: refetching
      // here would race with other in-flight updates and could overwrite
      // their results (which is what made the "previous one stop").
      setCallAnalysis((prev) => {
        const next = prev.map((item) =>
          item.id === row.id ? { ...item, follow_up_status: nextStatus } : item
        );
        try { localStorage.setItem("call_analysis_data", JSON.stringify(next)); } catch { /* storage full/blocked — ignore */ }
        return next;
      });
    } catch (err) {
      console.error("Failed to update follow-up status", err);
    } finally {
      setUpdatingFollowUpIds((prev) => {
        const next = new Set(prev);
        next.delete(row.id);
        return next;
      });
    }
  };

  // Called from the Voicemail Drill-down popup's Yes/No dropdown. Persists
  // the new value to call_analysis_results.voicemail_left_if_no_answer and
  // updates local state directly (no full refetch) so other in-flight edits
  // in the same table aren't clobbered.
  const handleToggleVmuField = async (row, nextValue) => {
    if (!row?.id || (nextValue !== "Yes" && nextValue !== "No")) return;

    setUpdatingVmuIds((prev) => {
      const next = new Set(prev);
      next.add(row.id);
      return next;
    });

    try {
      await updateVoicemailLeft(row.id, nextValue);

      setCallAnalysis((prev) => {
        const next = prev.map((item) =>
          item.id === row.id ? { ...item, voicemail_left_if_no_answer: nextValue } : item
        );
        try { localStorage.setItem("call_analysis_data", JSON.stringify(next)); } catch { /* storage full/blocked — ignore */ }
        return next;
      });
    } catch (err) {
      console.error("Failed to update voicemail left status", err);
    } finally {
      setUpdatingVmuIds((prev) => {
        const next = new Set(prev);
        next.delete(row.id);
        return next;
      });
    }
  };

  // ═══════════════════════════════════════════════════════════════════════════
  // BULK UPDATE HELPERS — Shared logic for both trackers
  // ═══════════════════════════════════════════════════════════════════════════

  const handleBulkMarkDone = async (selectedIds, setBulkUpdating, exitSelectionMode) => {
    if (selectedIds.size === 0) return;
    setBulkUpdating(true);
    try {
      const promises = Array.from(selectedIds).map((id) =>
        updateCallAnalysisFollowUpStatus(id, "Done")
      );
      await Promise.all(promises);
      await fetchData(dashboardFilters.startDate, dashboardFilters.endDate);
      exitSelectionMode();
    } catch (err) {
      console.error("Bulk update failed", err);
    } finally {
      setBulkUpdating(false);
    }
  };

  // ── Consultation selection helpers ─────────────────────────────────────────
  const toggleConsultationSelection = (id) => {
    setSelectedConsultationIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllConsultations = (items) => {
    const unresolvedIds = items
      .filter((row) => !isResolvedFollowUpStatusValue(row.follow_up_status))
      .map((row) => row.id);
    setSelectedConsultationIds(new Set(unresolvedIds));
  };

  const clearConsultationSelection = () => setSelectedConsultationIds(new Set());

  const exitConsultationSelectionMode = () => {
    setConsultationSelectionMode(false);
    setSelectedConsultationIds(new Set());
  };

  // ── Tracker selection helpers ──────────────────────────────────────────────
  const toggleTrackerSelection = (id) => {
    setSelectedTrackerIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllTrackerItems = (items) => {
    const unresolvedIds = items
      .filter((row) => !isResolvedFollowUpStatusValue(row.follow_up_status))
      .map((row) => row.id);
    setSelectedTrackerIds(new Set(unresolvedIds));
  };

  const clearTrackerSelection = () => setSelectedTrackerIds(new Set());

  const exitTrackerSelectionMode = () => {
    setTrackerSelectionMode(false);
    setSelectedTrackerIds(new Set());
  };

  // ── Date-scoped base sets ─────────────────────────────────────────────────
  const dateFilteredRecords = callRecords;

  const validCallIds = useMemo(() => {
    const ids = new Set();
    callRecords.forEach((call) => {
      getCallRefCandidates(call).forEach((c) => ids.add(c));
    });
    return ids;
  }, [callRecords]);

  const dateFilteredAnalysis = useMemo(() => {
    return callAnalysis.filter((row) => {
      const candidates = getCallRefCandidates(row);
      return candidates.some((c) => validCallIds.has(c));
    });
  }, [callAnalysis, validCallIds]);

  // ── Canonical per-call agent resolution ──────────────────────────────────
  // Single resolution path — imported from utils/agentAttribution.js and
  // shared verbatim with ReportAnalysis.jsx — used by the agent DROPDOWN
  // FILTER, the "Agent Calls Attended" KPI, and every other agent-scoped
  // number on this page.
  //
  // POLICY (locked in): call_analysis_results.front_desk_person_name is the
  // ONLY source of truth for a call's agent. There is no extension-based
  // fallback — a call with no analysis row, or an analysis row that doesn't
  // resolve to one of the 4 known agents (e.g. literal "N/A"), is simply
  // not attributed to anyone. This is the same resolver used by
  // ReportAnalysis.jsx, so counts reconcile exactly across both pages.
  const { refToAgent: refToCanonicalAgent } = useMemo(
    () => buildAgentAttribution(callAnalysis),
    [callAnalysis]
  );

  const resolveCallCanonicalAgent = useMemo(() => {
    return (call) => resolveAgentForCall(call, { refToAgent: refToCanonicalAgent });
  }, [refToCanonicalAgent]);

  // Records are scoped to an agent by resolving each call through the same
  // call_analysis-only resolver above. A call whose analysis row isn't
  // tagged to a known agent (or has no analysis row at all) drops out of
  // every agent-filtered view — records, analysis, and the KPI cards alike.
  const agentFilteredRecords = useMemo(() => {
    if (!dashboardFilters.agent) return dateFilteredRecords;
    const target = canonicalizeAgentName(dashboardFilters.agent);

    return dateFilteredRecords.filter((call) => resolveCallCanonicalAgent(call) === target);
  }, [dateFilteredRecords, dashboardFilters.agent, resolveCallCanonicalAgent]);

  const agentFilteredAnalysis = useMemo(() => {
    if (!dashboardFilters.agent) return dateFilteredAnalysis;
    const target = canonicalizeAgentName(dashboardFilters.agent);

    return dateFilteredAnalysis.filter(
      (row) => resolveAgentForCall(row, { refToAgent: refToCanonicalAgent }) === target
    );
  }, [dateFilteredAnalysis, dashboardFilters.agent, refToCanonicalAgent]);

  // Then apply quality score filter (affects BOTH records and analysis)
  const scoreBounds = useMemo(() => {
    const min = dashboardFilters.minQualityScore !== undefined
      && dashboardFilters.minQualityScore !== null
      && dashboardFilters.minQualityScore !== ""
      ? Number(dashboardFilters.minQualityScore)
      : null;
    const max = dashboardFilters.maxQualityScore !== undefined
      && dashboardFilters.maxQualityScore !== null
      && dashboardFilters.maxQualityScore !== ""
      ? Number(dashboardFilters.maxQualityScore)
      : null;
    return {
      min: Number.isFinite(min) ? min : null,
      max: Number.isFinite(max) ? max : null,
    };
  }, [dashboardFilters.minQualityScore, dashboardFilters.maxQualityScore]);

  const scoreFilteredAnalysis = useMemo(() => {
    const { min, max } = scoreBounds;
    if (min === null && max === null) return agentFilteredAnalysis;

    return agentFilteredAnalysis.filter((row) => {
      const score = parseScore(row.overall_call_quality_score);
      if (score === null) return false;
      if (min !== null && score < min) return false;
      if (max !== null && score > max) return false;
      return true;
    });
  }, [agentFilteredAnalysis, scoreBounds]);

  const scoreFilteredRecords = useMemo(() => {
    const { min, max } = scoreBounds;
    if (min === null && max === null) return agentFilteredRecords;

    // Restrict records to those whose analysis row actually falls in range.
    const matchingRefs = new Set();
    scoreFilteredAnalysis.forEach((row) => {
      getCallRefCandidates(row).forEach((ref) => matchingRefs.add(ref));
    });

    return agentFilteredRecords.filter((call) => {
      const candidates = getCallRefCandidates(call);
      return candidates.some((c) => matchingRefs.has(c));
    });
  }, [agentFilteredRecords, scoreFilteredAnalysis, scoreBounds]);

  const filteredRecords = scoreFilteredRecords;
  const filteredAnalysis = scoreFilteredAnalysis;

  // Only these four front desk reps should ever appear in the Agent filter,
  // and ALL FOUR must ALWAYS appear in the list — even if a rep had no calls
  // in the current period (their counts simply show 0). Stray spelling
  // variants / extension numbers in the data can never produce an extra option.
  // (VALID_AGENT_NAMES is defined at module scope, shared with the fuzzy
  // agent-name matcher above.)

  // Always hand the full fixed list to the filter, regardless of whether
  // the current data contains any calls for a given rep.
  const agents = VALID_AGENT_NAMES;


  // ═══════════════════════════════════════════════════════════════════════════
  // SUMMARY STATS — SENTIMENT LOGIC FIXED HERE
  // ═══════════════════════════════════════════════════════════════════════════
  const summary = useMemo(() => {
    return buildSharedMetrics(filteredRecords, filteredAnalysis, { customLeadSources });
  }, [filteredRecords, filteredAnalysis, customLeadSources, resolveCallCanonicalAgent, dashboardFilters.startDate, dashboardFilters.endDate]);

  const displayName = user?.name || user?.full_name || user?.email?.split("@")[0] || "there";
  const displayEmail = user?.email || "No email on file";

  // ── Quality drill-down modal (Below Average / Poor KPI cards) ────────────
  const [qualityModalType, setQualityModalType] = useState(null); // "below_avg" | "poor" | null
  const [audioModalType, setAudioModalType] = useState(null); // "no_audio" | "corrupted" | null
  const [statusModalState, setStatusModalState] = useState({ type: null, label: null });
  const [agentAttendedModalOpen, setAgentAttendedModalOpen] = useState(false);
  const [agentOtherModalOpen, setAgentOtherModalOpen] = useState(false);
  const [transcriptModalRow, setTranscriptModalRow] = useState(null);
  const openTranscript = async (row) => {
    try { setTranscriptModalRow({ ...row, ...await getCallContent(row.call_id) }); }
    catch (error) { setError(error.message || "Unable to load transcript"); }
  };
  const [audioModalRow, setAudioModalRow] = useState(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState("");
  const audioRef = useRef(null);
  const audioCacheRef = useRef(new Map());

  const closeQualityModal = () => setQualityModalType(null);
  const closeTranscriptModal = () => setTranscriptModalRow(null);
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

 const qualityModalConfig = {
    below_avg: { title: "Below Average Quality Calls", items: summary.belowAvgCallItems, color: "var(--amber-400)" },
    poor: { title: "Poor Rated Calls", items: summary.poorCallItems, color: "var(--rose-400)" },
    score_dist: {
      title: "Quality Score Distribution",
      items: [
        { label: "Excellent", count: summary.scoreDistLower.excellent, min: 8, color: "var(--emerald-400)" },
        { label: "Good", count: summary.scoreDistLower.good, min: 6, color: "var(--blue-400)" },
        { label: "Fair", count: summary.scoreDistLower.fair, min: 4, color: "var(--amber-400)" },
        { label: "Poor", count: summary.scoreDistLower.poor, min: 0, color: "var(--rose-400)" },
      ],
      color: "var(--cyan-400)",
    },
  };

  const audioModalConfig = {
    no_audio: { title: "No Audio Files", items: summary.noAudioCallItems, color: "var(--amber-400)" },
    corrupted: { title: "Audio Corrupted", items: summary.audioCorruptedCallItems, color: "var(--rose-400)" },
  };

  const statusModalConfig = {
    voicemail: {
      title: "Voicemail Left — Yes / No",
      kicker: "Voicemail Drill-down",
      color: "var(--violet-400)",
      statusKey: "vmuLabel",
      statusColumnLabel: "Voicemail Left",
      items: summary.voicemailModalItems,
      editable: true,
    },
    vmu_sent: {
      title: "VMU Sent",
      kicker: "VMU Sent Drill-down",
      color: "var(--violet-400)",
      statusKey: "vmuSentLabel",
      statusColumnLabel: "VMU Sent",
      items: summary.vmuSentModalItems,
      editable: true,
    },
    concern: {
      title: "Client Showed Signs of Concern or Anxiety",
      kicker: "VMU Status Drill-down",
      color: "var(--amber-400)",
      statusKey: "concernLabel",
      statusColumnLabel: "Concern / Anxiety",
      items: summary.concernAnxietyModalItems,
      editable: false,
    },
    reassurance: {
      title: "Assurance / Reassurance Provided by Agent",
      kicker: "VMU Status Drill-down",
      color: "var(--emerald-400)",
      statusKey: "reassuranceLabel",
      statusColumnLabel: "Assurance / Reassurance",
      items: summary.reassuranceProvidedModalItems,
      editable: false,
    },
  };

  const openStatusModal = (type, label = null) => setStatusModalState({ type, label });
  const closeStatusModal = () => setStatusModalState({ type: null, label: null });

  const currentStatusModal = statusModalState.type ? statusModalConfig[statusModalState.type] : null;
  const currentStatusItems = currentStatusModal
    ? currentStatusModal.items.filter((row) => {
        if (!statusModalState.label) return true;
        return row[currentStatusModal.statusKey] === statusModalState.label;
      })
    : [];

  const kpiCards = [
    // Belt 1 — Call Volume
    { title: "Call Records", value: summary.callCount, detail: formatDateRangeLabel(dashboardFilters.startDate, dashboardFilters.endDate), icon: <FileText size={18} />, color: "var(--blue-400)" },
    { title: "AI Analysis Done", value: summary.analysisCount, detail: `of ${summary.callCount} calls`, icon: <BrainCircuit size={18} />, color: "var(--emerald-400)" },
    // ── No Audio Files / Audio Corrupted — two standalone cards, sourced
    // from call_records (see the summary useMemo above). These two plus
    // "AI Analysis Done" (its own card, above) always sum to
    // summary.callCount, since every filtered call falls into exactly one bucket.
    { title: "No Audio Files", value: summary.noAudioCallsCount, detail: "Call record has no audio", icon: <CalendarRange size={18} />, color: "var(--amber-400)", onClick: () => setAudioModalType("no_audio") },
    { title: "Audio Corrupted", value: summary.audioCorruptedCount, detail: "Audio present, analysis failed", icon: <AlertTriangle size={18} />, color: "var(--rose-400)", onClick: () => setAudioModalType("corrupted") },
    { title: "Inbound Calls", value: summary.inboundCalls, detail: "Received", icon: <Phone size={18} />, color: "var(--cyan-400)" },
    { title: "Outbound Calls", value: summary.outboundCalls, detail: "Made", icon: <Phone size={18} />, color: "var(--violet-400)" },
    { title: "VMU Sent", icon: <Phone size={18} />, color: "var(--violet-400)", onClick: () => openStatusModal("vmu_sent"), subItems: [
      { label: "Yes", value: summary.vmuSentYes, onClick: () => openStatusModal("vmu_sent", "Yes") },
      { label: "No", value: summary.vmuSentNo, onClick: () => openStatusModal("vmu_sent", "No") },
    ] },
    { title: "Voicemail Calls", value: summary.voicemailLeftYes + summary.voicemailNotLeftCalls, detail: "Reached voicemail (VMU present)", icon: <Phone size={18} />, color: "var(--amber-400)" },
    { title: "Agent Calls Attended", value: summary.agentCallsAttended, detail: dashboardFilters.agent ? `Agent: ${dashboardFilters.agent}` : "All agents", icon: <UserCheck size={18} />, color: "var(--emerald-400)" },
    // Belt 1 tail — Agents Unidentified (blank front-desk name — true N/A)
    { title: "Agents Unidentified", value: summary.agentCallsNAItems.length, detail: "No front desk person name", icon: <UserPlus size={18} />, color: "var(--slate-400)", onClick: () => setAgentAttendedModalOpen(true) },
    // A front-desk name IS present but doesn't match one of the 4 known
    // agents — distinct from the blank/N/A case above.
    { title: "Front Desk Name Not Recognized", value: summary.agentCallsOtherItems.length, detail: "Other / unmatched name", icon: <UserPlus size={18} />, color: "var(--slate-400)", onClick: () => setAgentOtherModalOpen(true) },
    // Belt 3 — Quality & Attention
    { title: "Client Showed Signs of Concern or Anxiety", value: summary.concernAnxietyYes, detail: "Yes", icon: <AlertTriangle size={18} />, color: "var(--amber-400)", onClick: () => openStatusModal("concern", "Yes") },
    { title: "Assurance / Reassurance Provided by Agent", icon: <CheckCircle2 size={18} />, color: "var(--emerald-400)", onClick: () => openStatusModal("reassurance"), subItems: [
      { label: "Yes", value: summary.reassuranceProvidedYes, onClick: () => openStatusModal("reassurance", "Yes") },
      { label: "No", value: summary.reassuranceProvidedNo, onClick: () => openStatusModal("reassurance", "No") },
    ] },
    // Belt 4 — Performance
    { title: "Follow-ups", value: summary.followUps, detail: "Queued actions", icon: <TrendingUp size={18} />, color: "var(--violet-400)" },
    { title: "Escalations", value: summary.escalations, detail: "Requires escalation", icon: <ShieldAlert size={18} />, color: "var(--rose-400)" },
    { title: "Below Average Calls", value: summary.belowAvgCalls, detail: "Score 4 – 5.9", icon: <AlertTriangle size={18} />, color: "var(--amber-400)", onClick: () => setQualityModalType("below_avg") },
    { title: "Poor Rated Calls", value: summary.poorCalls, detail: "Score below 4", icon: <AlertTriangle size={18} />, color: "var(--rose-400)", onClick: () => setQualityModalType("poor") },
    { title: "Avg Call Duration", value: summary.durationCallCount > 0 ? formatDuration(summary.avgDurationSec) : "—", detail: formatDateRangeLabel(dashboardFilters.startDate, dashboardFilters.endDate), icon: <Clock size={18} />, color: "var(--blue-400)" },
    { title: "Total Call Duration", value: summary.durationCallCount > 0 ? formatDuration(summary.totalDurationSec) : "—", detail: dashboardFilters.agent ? `Agent: ${dashboardFilters.agent}` : "All calls", icon: <Clock size={18} />, color: "var(--emerald-400)" },
    { title: "Avg Quality Score", value: formatScore(summary.avgScore), detail: `Out of ${SCORE_MAX}`, icon: <BarChart3 size={18} />, color: "var(--cyan-400)", onClick: () => setQualityModalType("score_dist") },
  ];

  return (
    <div className="dashboard-page">
      <section className="dashboard-hero">
        <div className="dashboard-hero-copy">
          <div className="dashboard-badge"><Sparkles size={14} /><span>Live overview</span></div>
          <p className="greeting">{greeting}</p>
          <h1 className="welcome">Welcome back, {displayName}</h1>
          <p className="welcome-subtext">{displayEmail}</p>
        </div>
        <div className="dashboard-hero-right">
          <div className="dashboard-glance-card">
            <div className="glance-label">Active period</div>
            <div className="glance-value">{formatDateRangeLabel(dashboardFilters.startDate, dashboardFilters.endDate)}</div>
            <div className="glance-meta">{summary.total ?? 0} calls &middot; {summary.analysisCount ?? 0} analysed</div>
          </div>
        </div>
      </section>

      <DashboardFilters
        filters={filterDraft}
        onChange={handleFilterDraftChange}
        agents={agents}
        onReset={handleResetFilters}
        onApply={handleApplyFilters}
      />

      {loadError && !loading && (
        <div
          role="alert"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            padding: "10px 14px",
            marginBottom: 16,
            borderRadius: 10,
            background: "var(--rose-50, #fff1f2)",
          }}
        >
          <span>{loadError}</span>
          <button
            type="button"
            className="dashboard-filter-btn btn-secondary"
            onClick={() => fetchData(dashboardFilters.startDate, dashboardFilters.endDate)}
          >
            Retry
          </button>
        </div>
      )}

      {/* ── KPI Cards grouped by category ─────────────────────────────── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 20, marginBottom: 24 }}>

        {/* Call Volume — Belt 1 */}
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--slate-500)", marginBottom: 10 }}>Call Volume</div>
          <section className="dashboard-grid">
            {[kpiCards[0], kpiCards[1], kpiCards[2], kpiCards[3], kpiCards[8], kpiCards[9]].map((card) => (
              <DashboardKpiCard key={card.title} card={card} />
            ))}
          </section>
        </div>

        {/* Call Types — Belt 2 */}
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--slate-500)", marginBottom: 10 }}>Call Types</div>
          <section className="dashboard-grid">
            <DashboardKpiCard key={kpiCards[4].title} card={{ ...kpiCards[4], title: "Inbound Calls" }} />
            <DashboardKpiCard key={kpiCards[5].title} card={{ ...kpiCards[5], title: "Outbound Calls" }} />
            <DashboardKpiCard key={kpiCards[7].title} card={kpiCards[7]} />
            <DashboardKpiCard key={kpiCards[6].title} card={kpiCards[6]} />
          </section>
        </div>

        {/* Quality & Attention — Belt 3 */}
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--slate-500)", marginBottom: 10 }}>Quality &amp; Attention</div>
          <section className="dashboard-grid">
            {kpiCards.slice(10, 13).map((card) => (
              <DashboardKpiCard key={card.title} card={card} />
            ))}
          </section>
        </div>

        {/* Performance — Belt 4 */}
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--slate-500)", marginBottom: 10 }}>Performance</div>
          <section className="dashboard-grid">
            {kpiCards.slice(13).map((card) => (
              <DashboardKpiCard key={card.title} card={card} />
            ))}
          </section>
        </div>

      </div>

      <section className="dashboard-panels-row">
        <div className="dashboard-panel chart-panel">
          <div className="dashboard-panel-header"><BarChart3 size={18} /><span>Call Volume</span><span className="panel-badge">Firm view</span></div>
          <CallVolumeChart data={filteredRecords} viewMode="firm" />
        </div>
        <div className="dashboard-panel chart-panel">
          <div className="dashboard-panel-header"><BrainCircuit size={18} /><span>Analysis Status</span><span className="panel-badge">{summary.totalAnalysis} total</span></div>
          <AnalysisStatusChart data={filteredAnalysis} />
        </div>
      </section>

      <section className="dashboard-panels-row">
        <div className="dashboard-panel chart-panel">
          <div className="dashboard-panel-header"><Clock size={18} /><span>Call Volume by Hour</span><span className="panel-badge">Firm view</span></div>
          <CallVolumeChart data={filteredRecords} viewMode="hour" />
        </div>
      </section>

      <section className="dashboard-panels-row">
        <div className="dashboard-panel donut-panel">
          <div className="dashboard-panel-header">
            <UserPlus size={18} />
            <span>Client Type Distribution</span>
            <span className="panel-badge">
              {summary.clientTypeStats.new + summary.clientTypeStats.existing + summary.clientTypeStats.na} clients
            </span>
          </div>
          <ClientTypeDonut data={summary.clientTypeStats} />
        </div>
        <div className="dashboard-panel donut-panel">
          <div className="dashboard-panel-header">
            <Phone size={18} />
            <span>Call Direction</span>
            <span className="panel-badge">
              {summary.inboundCalls + summary.outboundCalls} calls
            </span>
          </div>
          <CallDirectionDonut inbound={summary.inboundCalls} outbound={summary.outboundCalls} />
        </div>
        <div className="dashboard-panel sentiment-panel">
          <div className="dashboard-panel-header"><Phone size={18} /><span>Client Sentiment</span><span className="panel-badge">{summary.sentimentDist.positive + summary.sentimentDist.neutral + summary.sentimentDist.negative} calls</span></div>
          <div className="sentiment-bars">
            {[
              { label: "Positive", count: summary.sentimentDist.positive, color: "var(--emerald-400)" },
              { label: "Neutral", count: summary.sentimentDist.neutral, color: "var(--slate-400)" },
              { label: "Negative", count: summary.sentimentDist.negative, color: "var(--rose-400)" },
            ].map((item) => {
              const total = summary.sentimentDist.positive + summary.sentimentDist.neutral + summary.sentimentDist.negative;
              const pct = total > 0 ? Math.round((item.count / total) * 100) : 0;
              return (
                <div key={item.label} className="sentiment-bar-row">
                  <div className="sentiment-bar-info"><span className="sentiment-dot" style={{ backgroundColor: item.color }} /><span className="sentiment-bar-label">{item.label}</span><span className="sentiment-bar-count">{item.count} ({pct}%)</span></div>
                  <div className="sentiment-bar-track"><div className="sentiment-bar-fill" style={{ width: `${pct}%`, backgroundColor: item.color }} /></div>
                </div>
              );
            })}
          </div>
        </div>
      </section>



      {/* ═══════════════════════════════════════════════════════════════════════
          PAID CONSULTATION TRACKER — with Multi-Select
          ═══════════════════════════════════════════════════════════════════════ */}
      <section className="dashboard-panel consultation-panel">
        <div className="dashboard-panel-header" style={{ justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <DollarSign size={18} />
            <span>Paid Consultation Tracker</span>
            <span className="panel-badge consultation-badge">
              {summary.consultationPitched} pitched
            </span>
          </div>
          {!consultationSelectionMode && summary.consultationItems.length > 0 && (
            <button
              type="button"
              className="dashboard-filter-btn btn-secondary"
              onClick={() => setConsultationSelectionMode(true)}
              style={{ padding: "6px 12px", fontSize: "13px" }}
            >
              <CheckSquare size={14} style={{ marginRight: 6 }} />
              Select Multiple
            </button>
          )}
        </div>

        {/* ── Selection Mode Toolbar ── */}
        {consultationSelectionMode && (
          <div className="tracker-selection-toolbar">
            <div className="selection-toolbar-left">
              <span className="selection-count">
                {selectedConsultationIds.size} selected
              </span>
              <button
                type="button"
                className="dashboard-filter-btn btn-apply"
                onClick={() => handleBulkMarkDone(selectedConsultationIds, setBulkUpdatingConsultation, exitConsultationSelectionMode)}
                disabled={selectedConsultationIds.size === 0 || bulkUpdatingConsultation}
                style={{ minWidth: 120 }}
              >
                {bulkUpdatingConsultation ? "Saving..." : (
                  <>
                    <CheckCircle2 size={14} style={{ marginRight: 6 }} />
                    Mark Done
                  </>
                )}
              </button>
            </div>
            <div className="selection-toolbar-right">
              <button
                type="button"
                className="dashboard-filter-btn btn-secondary"
                onClick={() => selectAllConsultations(summary.consultationItems)}
              >
                Select All
              </button>
              <button
                type="button"
                className="dashboard-filter-btn btn-secondary"
                onClick={clearConsultationSelection}
              >
                Clear
              </button>
              <button
                type="button"
                className="dashboard-filter-btn btn-cancel"
                onClick={exitConsultationSelectionMode}
              >
                <X size={14} style={{ marginRight: 4 }} />
                Cancel
              </button>
            </div>
          </div>
        )}

        {summary.consultationItems.length === 0 && (
          <div className="consultation-empty">
            <DollarSign size={32} />
            <p>No paid consultations pitched in this period.</p>
          </div>
        )}

        {summary.consultationItems.length > 0 && (
          <div className="consultation-table-wrapper">
            <table className="consultation-table">
              <thead>
                <tr>
                  {consultationSelectionMode && <th className="select-col"></th>}
                  <th>#</th>
                  <th>Date</th>
                  <th>Client</th>
                  <th>Front Desk</th>
                  <th>Case Type</th>
                  <th>Consultation Pitched</th>
                  <th>Client Response</th>
                  <th>Follow-up Required</th>
                  {!consultationSelectionMode && <th style={{ textAlign: "center" }}>Action</th>}
                </tr>
              </thead>
              <tbody>
                {summary.consultationItems.map((row, idx) => {
                  const isResolved = isResolvedFollowUpStatusValue(row.follow_up_status);
                  const isSelected = selectedConsultationIds.has(row.id);

                  return (
                    <tr
                      key={row.id ?? idx}
                      className={`${isResolved ? "resolved-row" : ""} ${consultationSelectionMode ? "selectable-row" : ""} ${isSelected ? "selected-row" : ""}`}
                      onClick={() => {
                        if (consultationSelectionMode && !isResolved) {
                          toggleConsultationSelection(row.id);
                        }
                      }}
                      style={{ cursor: consultationSelectionMode && !isResolved ? "pointer" : "default" }}
                    >
                      {consultationSelectionMode && (
                        <td className="select-col" onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            className="checkbox-btn"
                            onClick={() => toggleConsultationSelection(row.id)}
                            disabled={isResolved}
                          >
                            {isSelected ? (
                              <CheckSquare size={18} className="checkbox-checked" />
                            ) : (
                              <Square size={18} className="checkbox-unchecked" />
                            )}
                          </button>
                        </td>
                      )}
                      <td>{idx + 1}</td>
                      <td>{(() => { const d = new Date(row.call_recording_date); return row.call_recording_date && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—"; })()}</td>
                      <td>{row.client_name || "—"}</td>
                      <td>{row.front_desk_person_name || "—"}</td>
                      <td><span className="case-badge">{row.case_type || "—"}</span></td>
                      <td>
                        {String(row.paid_consultation_pitched || "").toLowerCase() === "yes" ? (
                          <span className="badge pitched-badge"><DollarSign size={12} /> Yes</span>
                        ) : (
                          <span className="badge none-badge">—</span>
                        )}
                      </td>
                      <td>
                        <span className={`badge response-badge ${String(row.client_response_to_consultation || "").toLowerCase().replace(/\s+/g, "-")}`}>
                          {row.client_response_to_consultation || "No response"}
                        </span>
                      </td>
                      <td>
                        {String(row.follow_up_required || "").toLowerCase() === "yes" ? (
                          <span className="badge followup-badge"><Clock size={12} /> Yes</span>
                        ) : (
                          <span className="badge none-badge">—</span>
                        )}
                      </td>
                      {!consultationSelectionMode && (
                        <td style={{ textAlign: "center" }}>
                          <button
                            type="button"
                            className="dashboard-filter-btn btn-apply"
                            onClick={() => handleToggleFollowUpStatus(row)}
                            disabled={updatingFollowUpIds.has(row.id)}
                            style={{ minWidth: 110, padding: "8px 12px", display: "inline-flex", alignItems: "center", justifyContent: "center", margin: "0 auto" }}
                          >
                            {updatingFollowUpIds.has(row.id)
                              ? "Saving..."
                              : isResolved
                                ? "Reopen"
                                : "Mark Done"}
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="dashboard-panels-row">
        <div className="dashboard-panel quality-panel">
          <div className="dashboard-panel-header"><BarChart3 size={18} /><span>Quality Score Distribution</span><span className="panel-badge">{summary.totalAnalysis} analyzed</span></div>
          <div className="quality-bars">
            {[
              { label: "Excellent", count: summary.scoreDistLower.excellent, min: 8, color: "var(--emerald-400)" },
              { label: "Good", count: summary.scoreDistLower.good, min: 6, color: "var(--blue-400)" },
              { label: "Fair", count: summary.scoreDistLower.fair, min: 4, color: "var(--amber-400)" },
              { label: "Poor", count: summary.scoreDistLower.poor, min: 0, color: "var(--rose-400)" },
            ].map((item) => {
              const pct = summary.totalAnalysis > 0 ? Math.round((item.count / summary.totalAnalysis) * 100) : 0;
              return (
                <div key={item.label} className="quality-bar-row">
                  <div className="quality-bar-info"><span className="quality-bar-label">{item.label}</span><span className="quality-bar-count">{item.count} ({pct}%)</span></div>
                  <div className="quality-bar-track"><div className="quality-bar-fill" style={{ width: `${pct}%`, backgroundColor: item.color }} /></div>
                  <span className="quality-bar-min">≥ {item.min}/{SCORE_MAX}</span>
                </div>
              );
            })}
          </div>
          <div className="quality-avg">
            <span className="quality-avg-label">Average Score</span>
            <span className="quality-avg-value" style={{ color: scoreColor(parseScore(summary.avgScore)) }}>{formatScore(summary.avgScore)}</span>
            <span className="quality-avg-label">{scoreLabel(parseScore(summary.avgScore))}</span>
          </div>
        </div>
        <div className="dashboard-panel breakdown-panel">
          <div className="dashboard-panel-header"><FileText size={18} /><span>Top Case Types</span></div>
          <div className="breakdown-list">
            {summary.topCaseTypes.length > 0 ? summary.topCaseTypes.map(([type, count], i) => (
              <div key={type} className="breakdown-item"><span className="breakdown-rank">{i + 1}</span><span className="breakdown-name">{type}</span><div className="breakdown-track"><div className="breakdown-fill" style={{ width: `${Math.round((count / summary.topCaseTypes[0][1]) * 100)}%` }} /></div><span className="breakdown-count">{count}</span></div>
            )) : <div className="breakdown-empty">No data available</div>}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════════════
          TOP LEAD SOURCES — customisable: user can pin their own source names
          ═══════════════════════════════════════════════════════════════════════ */}
      <section className="dashboard-panel breakdown-panel">
        <div className="dashboard-panel-header" style={{ justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <TrendingUp size={18} />
            <span>Top Lead Sources</span>
            {customLeadSources.length > 0 && (
              <span className="panel-badge">{customLeadSources.length} tracked</span>
            )}
          </div>
          <button
            type="button"
            className="dashboard-filter-btn btn-secondary"
            onClick={() => setAddingLeadSource((p) => !p)}
            style={{ padding: "6px 12px", fontSize: "13px" }}
          >
            <Plus size={14} style={{ marginRight: 6 }} />
            Add Source
          </button>
        </div>

        {addingLeadSource && (
          <div style={{ display: "flex", gap: "8px", margin: "12px 0 4px" }}>
            <input
              type="text"
              value={newLeadSourceName}
              onChange={(e) => setNewLeadSourceName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleAddLeadSource(); }}
              placeholder="Source name, e.g. Google, Referral, JustDial"
              className="dashboard-filter-input"
              style={{ flex: 1 }}
            />
            <button
              type="button"
              className="dashboard-filter-btn btn-apply"
              onClick={handleAddLeadSource}
              style={{ minWidth: 90 }}
            >
              Add
            </button>
            <button
              type="button"
              className="dashboard-filter-btn btn-cancel"
              onClick={() => { setAddingLeadSource(false); setNewLeadSourceName(""); }}
            >
              <X size={14} />
            </button>
          </div>
        )}

        <div className="breakdown-list">
          {summary.topLeadSources.length > 0 ? summary.topLeadSources.map(([source, count, isCustom], i) => {
            const maxCount = Math.max(...summary.topLeadSources.map((row) => row[1]), 0);
            const pct = maxCount > 0 ? Math.round((count / maxCount) * 100) : 0;
            return (
              <div key={source} className="breakdown-item">
                <span className="breakdown-rank">{i + 1}</span>
                <span className="breakdown-name">
                  {source}
                  {isCustom && (
                    <button
                      type="button"
                      title="Remove this source"
                      onClick={() => handleRemoveLeadSource(source)}
                      style={{ marginLeft: 6, background: "none", border: "none", cursor: "pointer", color: "var(--rose-400)", display: "inline-flex", alignItems: "center", padding: 0, verticalAlign: "middle" }}
                    >
                      <X size={12} />
                    </button>
                  )}
                </span>
                <div className="breakdown-track"><div className="breakdown-fill" style={{ width: `${pct}%` }} /></div>
                <span className="breakdown-count">{count}</span>
              </div>
            );
          }) : (
            <div className="breakdown-empty">No lead source data yet — click "Add Source" to track one.</div>
          )}
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════════════
          FOLLOW-UP & ESCALATION TRACKER — with Multi-Select
          ═══════════════════════════════════════════════════════════════════════ */}
      <section className="dashboard-panel tracker-panel">
        <div className="dashboard-panel-header tracker-header" onClick={() => setExpandedTracker((p) => !p)} style={{ cursor: "pointer", justifyContent: "space-between" }}>
          <div className="tracker-header-left">
            <AlertTriangle size={18} />
            <span>Follow-up & Escalation Tracker</span>
            <span className="panel-badge tracker-badge">{summary.followUps} open</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            {!trackerSelectionMode && summary.followUpItems.length > 0 && (
              <button
                type="button"
                className="dashboard-filter-btn btn-secondary"
                onClick={(e) => { e.stopPropagation(); setTrackerSelectionMode(true); }}
                style={{ padding: "6px 12px", fontSize: "13px" }}
              >
                <CheckSquare size={14} style={{ marginRight: 6 }} />
                Select Multiple
              </button>
            )}
            {expandedTracker ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </div>
        </div>

        {expandedTracker && (
          <div className="tracker-body">
            {/* ── Tracker Selection Mode Toolbar ── */}
            {trackerSelectionMode && (
              <div className="tracker-selection-toolbar">
                <div className="selection-toolbar-left">
                  <span className="selection-count">
                    {selectedTrackerIds.size} selected
                  </span>
                  <button
                    type="button"
                    className="dashboard-filter-btn btn-apply"
                    onClick={() => handleBulkMarkDone(selectedTrackerIds, setBulkUpdatingTracker, exitTrackerSelectionMode)}
                    disabled={selectedTrackerIds.size === 0 || bulkUpdatingTracker}
                    style={{ minWidth: 120 }}
                  >
                    {bulkUpdatingTracker ? "Saving..." : (
                      <>
                        <CheckCircle2 size={14} style={{ marginRight: 6 }} />
                        Mark Done
                      </>
                    )}
                  </button>
                </div>
                <div className="selection-toolbar-right">
                  <button
                    type="button"
                    className="dashboard-filter-btn btn-secondary"
                    onClick={() => selectAllTrackerItems(summary.followUpItems)}
                  >
                    Select All
                  </button>
                  <button
                    type="button"
                    className="dashboard-filter-btn btn-secondary"
                    onClick={clearTrackerSelection}
                  >
                    Clear
                  </button>
                  <button
                    type="button"
                    className="dashboard-filter-btn btn-cancel"
                    onClick={exitTrackerSelectionMode}
                  >
                    <X size={14} style={{ marginRight: 4 }} />
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {summary.followUpItems.length > 0 ? (
              <div className="tracker-table-wrapper followup-tracker-wrapper">
                <table className="tracker-table followup-tracker-table">
                  <thead>
                    <tr>
                      {trackerSelectionMode && <th className="select-col"></th>}
                      <th>#</th>
                      <th>Date</th>
                      <th>Client</th>
                      <th>Front Desk</th>
                      <th>Case Type</th>
                      <th>Follow-up</th>
                      <th>Deadline</th>
                      <th>Status</th>
                      <th>Escalation</th>
                      <th>To</th>
                      <th>Score</th>
                      {!trackerSelectionMode && <th style={{ textAlign: "center" }}>Action</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {summary.followUpItems.map((row, idx) => {
                      const isResolved = isResolvedFollowUpStatusValue(row.follow_up_status);
                      const isSelected = selectedTrackerIds.has(row.id);

                      return (
                        <tr
                          key={row.id ?? idx}
                          className={`${isResolved ? "resolved-row" : ""} ${trackerSelectionMode ? "selectable-row" : ""} ${isSelected ? "selected-row" : ""}`}
                          onClick={() => {
                            if (trackerSelectionMode && !isResolved) {
                              toggleTrackerSelection(row.id);
                            }
                          }}
                          style={{ opacity: isResolved ? 0.72 : 1, cursor: trackerSelectionMode && !isResolved ? "pointer" : "default" }}
                        >
                          {trackerSelectionMode && (
                            <td className="select-col" onClick={(e) => e.stopPropagation()}>
                              <button
                                type="button"
                                className="checkbox-btn"
                                onClick={() => toggleTrackerSelection(row.id)}
                                disabled={isResolved}
                              >
                                {isSelected ? (
                                  <CheckSquare size={18} className="checkbox-checked" />
                                ) : (
                                  <Square size={18} className="checkbox-unchecked" />
                                )}
                              </button>
                            </td>
                          )}
                          <td>{idx + 1}</td>
                          <td>{(() => { const d = new Date(row.call_recording_date); return row.call_recording_date && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—"; })()}</td>
                          <td>{row.client_name || "—"}</td>
                          <td>{row.front_desk_person_name || "—"}</td>
                          <td><span className="case-badge">{row.case_type || "—"}</span></td>
                          <td>{String(row.follow_up_required || "").toLowerCase() === "yes" ? <span className="badge followup-badge"><Clock size={12} /> REQ</span> : <span className="badge none-badge">—</span>}</td>
                          <td>{(() => { const d = new Date(row.follow_up_deadline); return row.follow_up_deadline && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—"; })()}</td>
                          <td>
                            <span className={`badge status-badge ${String(row.follow_up_status || "").toLowerCase().replace(/\s+/g, "-")}`}>
                              {row.follow_up_status || "Pending"}
                            </span>
                          </td>
                          <td>{String(row.escalation_required || "").toLowerCase() === "yes" ? <span className="badge escalation-badge"><ShieldAlert size={12} /> Yes</span> : <span className="badge none-badge">—</span>}</td>
                          <td>{row.escalation_to || "—"}</td>
                          <td><span className="score-pill" style={{ color: scoreColor(parseScore(row.overall_call_quality_score)) }}>{row.overall_call_quality_score ?? "—"}</span></td>
                          {!trackerSelectionMode && (
                            <td style={{ textAlign: "center" }}>
                              <button
                                type="button"
                                className="dashboard-filter-btn btn-apply"
                                onClick={() => handleToggleFollowUpStatus(row)}
                                disabled={updatingFollowUpIds.has(row.id)}
                                style={{ maxWidth: "100%", padding: "6px 8px", display: "inline-flex", alignItems: "center", justifyContent: "center", margin: "0 auto", whiteSpace: "nowrap" }}
                              >
                                {updatingFollowUpIds.has(row.id)
                                  ? "Saving..."
                                  : isResolved
                                    ? "Reopen"
                                    : "Mark Done"}
                              </button>
                            </td>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : <div className="tracker-empty"><CheckCircle2 size={32} /><p>No follow-ups or escalations pending. All caught up!</p></div>}
          </div>
        )}
      </section>

      {/* ═══════════════════════════════════════════════════════════════════
          QUALITY DRILL-DOWN MODAL — Below Average / Poor rated calls
          ═══════════════════════════════════════════════════════════════════ */}
      {qualityModalType && (
        <div className="audio-modal-overlay" onClick={closeQualityModal}>
          <div
            className="audio-modal-card quality-modal-card"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 720, width: "92%" }}
          >
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Quality Drill-down</p>
                <h2 style={{ color: qualityModalConfig[qualityModalType].color }}>
                  {qualityModalConfig[qualityModalType].title}
                </h2>
              </div>
              <div className="audio-modal-actions">
                <button type="button" className="audio-modal-close" onClick={closeQualityModal}>
                  <X size={18} />
                </button>
              </div>
            </div>

            {qualityModalType === "score_dist" ? (
              <div style={{ padding: "20px 24px" }}>
                <div className="quality-bars">
                  {qualityModalConfig.score_dist.items.map((item) => {
                    const total = qualityModalConfig.score_dist.items.reduce((s, i) => s + i.count, 0);
                    const pctVal = total > 0 ? Math.round((item.count / total) * 100) : 0;
                    return (
                      <div key={item.label} className="quality-bar-row" style={{ marginBottom: 12 }}>
                        <div className="quality-bar-info">
                          <span className="quality-bar-label">{item.label}</span>
                          <span className="quality-bar-count">{item.count} ({pctVal}%)</span>
                        </div>
                        <div className="quality-bar-track">
                          <div className="quality-bar-fill" style={{ width: `${pctVal}%`, backgroundColor: item.color }} />
                        </div>
                        <span className="quality-bar-min">≥ {item.min}/{SCORE_MAX}</span>
                      </div>
                    );
                  })}
                </div>
                <div className="quality-avg" style={{ marginTop: 16 }}>
                  <span className="quality-avg-label">Average Score</span>
                  <span className="quality-avg-value" style={{ color: scoreColor(parseScore(summary.avgScore)) }}>{formatScore(summary.avgScore)}</span>
                  <span className="quality-avg-label">{scoreLabel(parseScore(summary.avgScore))}</span>
                </div>
              </div>
            ) : qualityModalConfig[qualityModalType].items.length === 0 ? (
              <div className="tracker-empty" style={{ padding: "32px 0" }}>
                <CheckCircle2 size={32} />
                <p>No calls in this category for the current filters.</p>
              </div>
            ) : (
              <div className="tracker-table-wrapper" style={{ maxHeight: "60vh", overflowY: "auto" }}>
                <table className="tracker-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Call ID</th>
                      <th>Front Desk</th>
                      <th>Score</th>
                      <th style={{ textAlign: "center" }}>Transcript</th>
                      <th style={{ textAlign: "center" }}>Audio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {qualityModalConfig[qualityModalType].items.map((row, idx) => (
                      <tr key={row.id ?? row.call_reference ?? idx}>
                        <td>{idx + 1}</td>
                        <td>{row.call_reference || row.call_id || "—"}</td>
                        <td>{row.front_desk_person_name || "—"}</td>
                        <td>
                          <span
                            className="score-pill"
                            style={{ color: scoreColor(parseScore(row.overall_call_quality_score)) }}
                          >
                            {row.overall_call_quality_score ?? "—"}
                          </span>
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <button
                            type="button"
                            className="transcript-view-btn"
                            onClick={() => openTranscript(row)}
                            disabled={!row.call_id}
                            aria-label="View transcript"
                            title="View transcript"
                          >
                            <Eye size={16} />
                          </button>
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
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════════════
          NO AUDIO FILES / AUDIO CORRUPTED DRILL-DOWN MODAL
          Reuses the same popup shell/styling as the Quality drill-down modal
          above (audio-modal-overlay / audio-modal-card / tracker-table) plus
          the shared Transcript and Audio actions. ───────────────────────── */}
      {audioModalType && (
        <div className="audio-modal-overlay" onClick={() => setAudioModalType(null)}>
          <div
            className="audio-modal-card quality-modal-card"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 720, width: "92%" }}
          >
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Audio Drill-down</p>
                <h2 style={{ color: audioModalConfig[audioModalType].color }}>
                  {audioModalConfig[audioModalType].title}
                </h2>
              </div>
              <div className="audio-modal-actions">
                <button type="button" className="audio-modal-close" onClick={() => setAudioModalType(null)}>
                  <X size={18} />
                </button>
              </div>
            </div>

            {audioModalConfig[audioModalType].items.length === 0 ? (
              <div className="tracker-empty" style={{ padding: "32px 0" }}>
                <CheckCircle2 size={32} />
                <p>No calls in this category for the current filters.</p>
              </div>
            ) : (
              <div className="tracker-table-wrapper" style={{ maxHeight: "60vh", overflowY: "auto" }}>
                <table className="tracker-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Call ID</th>
                      <th>Front Desk</th>
                      <th>Call Date</th>
                      <th style={{ textAlign: "center" }}>Transcript</th>
                      <th style={{ textAlign: "center" }}>Audio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audioModalConfig[audioModalType].items.map((row, idx) => (
                      <tr key={row.id ?? row.call_reference ?? idx}>
                        <td>{idx + 1}</td>
                        <td>{row.call_reference || row.call_id || "—"}</td>
                        <td>{row.front_desk_person_name || "N/A"}</td>
                        <td>
                          {(() => {
                            const d = new Date(row.call_recording_date);
                            return row.call_recording_date && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—";
                          })()}
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <button
                            type="button"
                            className="transcript-view-btn"
                            onClick={() => openTranscript(row)}
                            disabled={!row.call_id}
                            aria-label="View transcript"
                            title={audioModalType === "corrupted" ? "Transcript unavailable (analysis failed)" : "View transcript"}
                          >
                            <Eye size={16} />
                          </button>
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <button
                            type="button"
                            className="audio-play-btn"
                            onClick={() => handlePlayAudio(row)}
                            disabled={audioModalType === "no_audio" || audioModalType === "corrupted" || !row.call_id}
                            aria-label="Play audio"
                            title={audioModalType === "corrupted" ? "Audio unavailable (analysis failed)" : "Play audio"}
                          >
                            <Play size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Agent Calls Attended drill-down (Front Desk = N/A) ───────────────
          Reuses the same popup shell/styling as the Quality drill-down modal
          above (audio-modal-overlay / audio-modal-card / tracker-table) plus
          the shared Transcript and Audio actions. ───────────────────────── */}
      {agentAttendedModalOpen && (
        <div className="audio-modal-overlay" onClick={() => setAgentAttendedModalOpen(false)}>
          <div
            className="audio-modal-card quality-modal-card"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 720, width: "92%" }}
          >
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Agent Calls Attended</p>
                <h2 style={{ color: "var(--emerald-400)" }}>Front Desk Not Resolved (N/A)</h2>
              </div>
              <div className="audio-modal-actions">
                <button type="button" className="audio-modal-close" onClick={() => setAgentAttendedModalOpen(false)}>
                  <X size={18} />
                </button>
              </div>
            </div>

            {summary.agentCallsNAItems.length === 0 ? (
              <div className="tracker-empty" style={{ padding: "32px 0" }}>
                <CheckCircle2 size={32} />
                <p>No calls with an unresolved Front Desk for the current filters.</p>
              </div>
            ) : (
              <div className="tracker-table-wrapper" style={{ maxHeight: "60vh", overflowY: "auto" }}>
                <table className="tracker-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Call ID</th>
                      <th>Front Desk</th>
                      <th>Call Date</th>
                      <th style={{ textAlign: "center" }}>Transcript</th>
                      <th style={{ textAlign: "center" }}>Audio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.agentCallsNAItems.map((row, idx) => (
                      <tr key={row.id ?? row.call_reference ?? idx}>
                        <td>{idx + 1}</td>
                        <td>{row.call_reference || row.call_id || "—"}</td>
                        <td>{row.front_desk_person_name || "N/A"}</td>
                        <td>
                          {(() => {
                            const d = new Date(row.call_recording_date);
                            return row.call_recording_date && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—";
                          })()}
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <button
                            type="button"
                            className="transcript-view-btn"
                            onClick={() => openTranscript(row)}
                            disabled={!row.call_id}
                            aria-label="View transcript"
                            title="View transcript"
                          >
                            <Eye size={16} />
                          </button>
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
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {agentOtherModalOpen && (
        <div className="audio-modal-overlay" onClick={() => setAgentOtherModalOpen(false)}>
          <div
            className="audio-modal-card quality-modal-card"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 720, width: "92%" }}
          >
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Agent Calls Attended</p>
                <h2 style={{ color: "var(--slate-400)" }}>Front Desk Name Not Recognized (Other)</h2>
              </div>
              <div className="audio-modal-actions">
                <button type="button" className="audio-modal-close" onClick={() => setAgentOtherModalOpen(false)}>
                  <X size={18} />
                </button>
              </div>
            </div>

            {summary.agentCallsOtherItems.length === 0 ? (
              <div className="tracker-empty" style={{ padding: "32px 0" }}>
                <CheckCircle2 size={32} />
                <p>No calls with an unrecognized Front Desk name for the current filters.</p>
              </div>
            ) : (
              <div className="tracker-table-wrapper" style={{ maxHeight: "60vh", overflowY: "auto" }}>
                <table className="tracker-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Call ID</th>
                      <th>Front Desk</th>
                      <th>Call Date</th>
                      <th style={{ textAlign: "center" }}>Transcript</th>
                      <th style={{ textAlign: "center" }}>Audio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.agentCallsOtherItems.map((row, idx) => (
                      <tr key={row.id ?? row.call_reference ?? idx}>
                        <td>{idx + 1}</td>
                        <td>{row.call_reference || row.call_id || "—"}</td>
                        <td>{row.front_desk_person_name || "—"}</td>
                        <td>
                          {(() => {
                            const d = new Date(row.call_recording_date);
                            return row.call_recording_date && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—";
                          })()}
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <button
                            type="button"
                            className="transcript-view-btn"
                            onClick={() => openTranscript(row)}
                            disabled={!row.call_id}
                            aria-label="View transcript"
                            title="View transcript"
                          >
                            <Eye size={16} />
                          </button>
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
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Shared VMU-style drill-down — reused for Voicemail Left,
          Concern/Anxiety, and Reassurance yes/no KPIs. ─────────────────── */}
      {currentStatusModal && (
        <div className="audio-modal-overlay" onClick={closeStatusModal}>
          <div
            className="audio-modal-card quality-modal-card"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 720, width: "92%" }}
          >
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">{currentStatusModal.kicker}</p>
                <h2 style={{ color: currentStatusModal.color }}>{currentStatusModal.title}</h2>
              </div>
              <div className="audio-modal-actions">
                <button type="button" className="audio-modal-close" onClick={closeStatusModal}>
                  <X size={18} />
                </button>
              </div>
            </div>

            {currentStatusItems.length === 0 ? (
              <div className="tracker-empty" style={{ padding: "32px 0" }}>
                <CheckCircle2 size={32} />
                <p>No calls available for the current filters.</p>
              </div>
            ) : (
              <div className="tracker-table-wrapper" style={{ maxHeight: "60vh", overflowY: "auto" }}>
                <table className="tracker-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Call ID</th>
                      <th>Front Desk</th>
                      <th>{currentStatusModal.statusColumnLabel}</th>
                      <th>Call Date</th>
                      <th style={{ textAlign: "center" }}>Transcript</th>
                      <th style={{ textAlign: "center" }}>Audio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {currentStatusItems.map((row, idx) => (
                      <tr key={row.id ?? row.call_reference ?? idx}>
                        <td>{idx + 1}</td>
                        <td>{row.call_reference || row.call_id || "—"}</td>
                        <td>{row.front_desk_person_name || "N/A"}</td>
                        <td>
                          {currentStatusModal.editable ? (
                            <select
                              className="score-pill vmu-select"
                              value={
                                String(row.voicemail_left_if_no_answer || "").trim().toLowerCase() === "yes"
                                  ? "Yes"
                                  : "No"
                              }
                              onChange={(e) => handleToggleVmuField(row, e.target.value)}
                              disabled={updatingVmuIds.has(row.id)}
                              style={{
                                color: row.vmuLabel === "Yes" ? "var(--emerald-400)" : "var(--rose-400)",
                                background: "transparent",
                                border: "1px solid currentColor",
                                borderRadius: 6,
                                cursor: updatingVmuIds.has(row.id) ? "wait" : "pointer",
                                fontWeight: 600,
                                padding: "2px 6px",
                              }}
                            >
                              <option value="Yes">Yes</option>
                              <option value="No">No</option>
                            </select>
                          ) : (
                            <span
                              className="score-pill"
                              style={{ color: row[currentStatusModal.statusKey] === "Yes" ? "var(--emerald-400)" : "var(--rose-400)" }}
                            >
                              {row[currentStatusModal.statusKey] || "—"}
                            </span>
                          )}
                        </td>
                        <td>
                          {(() => {
                            const d = new Date(row.call_recording_date);
                            return row.call_recording_date && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—";
                          })()}
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <button
                            type="button"
                            className="transcript-view-btn"
                            onClick={() => openTranscript(row)}
                            disabled={!row.call_id}
                            aria-label="View transcript"
                            title="View transcript"
                          >
                            <Eye size={16} />
                          </button>
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
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Transcript modal ─────────────────────────────────────────────── */}
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

      {/* ── Audio modal ──────────────────────────────────────────────────── */}
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

export default Dashboard;
