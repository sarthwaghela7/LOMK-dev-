import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL, getCalls, getCallAnalysis } from "../data/api";
import jsPDF from "jspdf";
import html2canvas from "html2canvas";
import {
  FileText, Sparkles, TrendingUp, TrendingDown, Users, AlertTriangle,
  ShieldAlert, CheckCircle2, Clock, BarChart3, Phone, Calendar,
  Award, Frown, Scale, Loader2, Download, BrainCircuit, Play, X,
} from "lucide-react";
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, PieChart, Pie, Cell,
} from "recharts";
import {
  VALID_AGENT_NAMES, buildAgentAttribution, resolveAgentForCall,
  dedupeAnalysisRowsByCall, computeAiAnalysisDone, getCallRefCandidates,
} from "../utils/agentAttribution";
import {
  buildMetrics as buildSharedMetrics,
  buildDailySeries as buildSharedDailySeries,
  buildWeeklySeries as buildSharedWeeklySeries,
  buildPreviousRange as buildSharedPreviousRange,
  daysBetween as sharedDaysBetween,
  REPORT_AGENT_ORDER,
  SENTIMENT_BUCKETS,
  safeParseJson,
  llmField,
  isYes,
  isNAValue,
  parseScore,
  SCORE_MAX,
  scoreBand,
  scoreColor,
  formatDuration,
  formatDate,
  pct,
  avg,
  topN,
  isResolvedFollowUp,
  vmuStatus,
  concernAnxietyStatus,
  reassuranceProvidedStatus,
} from "../utils/reportMetrics";

/*
  ────────────────────────────────────────────────────────────────────────
  DATA NOTE:
  Fields marked "raw_llm_json.<key>" below are BEST-GUESS key names for
  metrics your prompt schema may not literally use (SOP compliance,
  greetings, empathy, tone, legal flag, voicemail, hold protocol, etc).
  If your `get_analysis_fields()` config uses different key names, update
  the `K` map below — everything downstream reads from it.
  ────────────────────────────────────────────────────────────────────────
*/
// Fixed front-desk roster — per spec 3.4, every agent's subsection must
// appear in the daily report even if they handled zero calls that day.
// Sourced from utils/agentAttribution.js so Reports and Dashboard always
// agree on the canonical spellings and the fuzzy-matching logic.
const KNOWN_AGENTS = VALID_AGENT_NAMES;

const K = {
  // SOP Compliance section — mapped to the actual analysis-record fields
  // (field numbers per spec). These previously pointed at guessed key names
  // that don't exist on the backend rows, so isYes()/llmField() resolved to
  // undefined and every rate collapsed to 0. Fixed to the real field names.
  sopCompliant: "script_sop_followed",              // Field 25
  sopStepsMissed: "sop_steps_missed",              // expected: array or comma string
  empathyShown: "acknowledging_client_requirement_empathy", // Field 27
  startGreeting: "conversation_start_greeting",     // Field 28
  endGreeting: "conversation_end_greeting",         // Field 29
  answerQuality: "answer_quality",                  // e.g. "Clear" | "Partial" | "Unclear"
  nextStepCommunicated: "next_step_communicated_to_client", // Field 32
  callClosedProperly: "call_closed_properly",       // Field 33
  holdProtocolFollowed: "hold_protocol_followed",   // Field 31
  leadSourceAsked: "lead_source_asked",             // Field 37
  paidConsultationPitched: "paid_consultation_pitched", // Field 36
  tone: "tone_conversation_check",                  // Field 39
  clientConcern: "client_concern_or_anxiety",
  reassuranceProvided: "reassurance_provided",
  legalFlag: "legal_flag",
  legalConcern: "legal_concern_summary",
  isVoicemail: "is_voicemail",
  voicemailLeftIfNoAnswer: "voicemail_left_if_no_answer", // "Yes" | "No" — VMU status field
  direction: "call_direction",                      // "Inbound" | "Outbound"
  // Added for daily-report build-out — best-guess key names, update if your
  // get_analysis_fields() config uses different ones.
  callDuration: "call_duration_seconds",            // numeric, seconds
  conversationPreparedness: "conversation_preparedness", // e.g. "High" | "Medium" | "Low" (Field 24)
  callTime: "call_time",                            // "HH:MM[:SS]" or full timestamp
  frontDeskActionPending: "front_desk_action_pending", // Field 15
  clientActionPending: "client_action_pending",        // Field 16
};

const COLORS = {
  emerald: "var(--emerald-400)", blue: "var(--blue-400)", amber: "var(--amber-400)",
  rose: "var(--rose-400)", violet: "var(--violet-400)", cyan: "var(--cyan-400)",
  slate: "var(--slate-400)",
};
const PIE_COLORS = [COLORS.emerald, COLORS.blue, COLORS.rose];

// ─── Helpers ──────────────────────────────────────────────────────────────

// Calls-by-Agent breakdown for "Month-at-a-Glance" must always show the
// same fixed buckets the Dashboard logic resolves to.
function agentCallsBreakdown(callsByAgent) {
  const result = {
    Shrishti: 0,
    Manaswi: 0,
    Satish: 0,
    Gokul: 0,
    Other: 0,
    "N/A": 0,
  };

  Object.entries(callsByAgent || {}).forEach(([name, count]) => {
    if (name in result) {
      result[name] += Number(count) || 0;
    } else if (KNOWN_AGENTS.includes(name)) {
      result[name] += Number(count) || 0;
    } else if (name === "Unassigned") {
      result["N/A"] += Number(count) || 0;
    } else {
      result.Other += Number(count) || 0;
    }
  });

  return result;
}

function bump(map, key) {
  if (!key) return;
  map[key] = (map[key] || 0) + 1;
}

function getTranscriptSerialNumber(row) {
  return (
    row?.transcript_serial_number
    || row?.transcript_sr_no
    || row?.transcript_id
    || row?.call_reference
    || row?.call_id
    || "—"
  );
}

// Thin wrapper kept so the rest of this file (many call sites below) reads
// the same as before, but the actual attribution comes from
// utils/agentAttribution.js — the same logic Dashboard.jsx uses — so a call
// counted for "Shrishti" here is counted for "Shrishti" there too.
//
// POLICY (locked in): call_analysis_results.front_desk_person_name is the
// ONLY source of truth. No extension fallback — a call whose analysis row
// doesn't resolve to a known agent (or has no analysis row) is "Unassigned".
function buildAgentRefMap(analysis) {
  const { refToAgent } = buildAgentAttribution(analysis);
  return { refToAgent };
}

function resolveAgentName(row, refToAgent) {
  return resolveAgentForCall(row, { refToAgent }) || "Unassigned";
}

function scopeAnalysisToFetchedCalls(records, analysis) {
  const validCallRefs = new Set();
  (records || []).forEach((call) => {
    getCallRefCandidates(call).forEach((ref) => validCallRefs.add(ref));
  });

  return (analysis || []).filter((row) => {
    const candidates = getCallRefCandidates(row);
    return candidates.some((c) => validCallRefs.has(c));
  });
}

function buildAgentVolumeTotal(agents) {
  const total = {
    name: "Total",
    calls: 0,
    inbound: 0,
    outbound: 0,
    newClients: 0,
    existingClients: 0,
    voicemail: 0,
    voicemailRate: 0,
    durations: [],
    avgDurationSec: null,
  };
  agents.forEach((a) => {
    total.calls += a.calls;
    total.inbound += a.inbound;
    total.outbound += a.outbound;
    total.newClients += a.newClients;
    total.existingClients += a.existingClients;
    total.voicemail += a.voicemail;
    if (a.avgDurationSec !== null && Number.isFinite(a.avgDurationSec)) {
      total.durations.push(a.avgDurationSec);
    }
  });
  total.voicemailRate = total.outbound > 0 ? pct(total.voicemail, total.outbound) : 0;
  total.avgDurationSec = avg(total.durations);
  return total;
}
function agentDisplayName(name) {
  if (name === "Unassigned") return "N/A";
  return name;
}
// ─── Shared metric wrappers ────────────────────────────────────────────────
const buildMetrics = (records, rawAnalysis) => buildSharedMetrics(records, rawAnalysis);
const buildWeeklySeries = (analysis) => buildSharedWeeklySeries(analysis);
const buildDailySeries = (analysis) => buildSharedDailySeries(analysis);



// ─── Stat card ──────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, icon, color = "var(--blue-400)" }) {
  return (
    <div className="report-stat-card">
      <div className="report-stat-icon" style={{ color, backgroundColor: `${color}15` }}>{icon}</div>
      <div>
        <div className="report-stat-label">{label}</div>
        <div className="report-stat-value">{value}</div>
        {sub != null && <div className="report-stat-sub">{sub}</div>}
      </div>
    </div>
  );
}

// ─── Hero metric — bigger, used for the headline row at the top of a report ─
function HeroStat({ label, value, sub, icon, color = "var(--blue-400)" }) {
  return (
    <div className="report-hero-stat">
      <div className="report-hero-stat-top">
        <span className="report-hero-stat-icon" style={{ color, backgroundColor: `${color}18` }}>{icon}</span>
        <span className="report-hero-stat-label">{label}</span>
      </div>
      <div className="report-hero-stat-value" style={{ color }}>{value}</div>
      {sub != null && <div className="report-hero-stat-sub">{sub}</div>}
    </div>
  );
}

function YesNoKpiCard({ title, icon, color = "var(--blue-400)", yesCount, noCount, onYesClick, onNoClick }) {
  const total = (yesCount || 0) + (noCount || 0);
  const yesPct = total > 0 ? Math.round(((yesCount || 0) / total) * 100) : 0;
  const noPct = total > 0 ? 100 - yesPct : 0;
  return (
    <div className="report-stat-card" style={{ flexDirection: "column", gap: 10, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        <div className="report-stat-icon" style={{ color, backgroundColor: `${color}15`, flexShrink: 0 }}>{icon}</div>
        <div className="report-stat-label" style={{ fontSize: 11, lineHeight: 1.4, whiteSpace: "normal", wordBreak: "break-word", paddingTop: 2 }}>{title}</div>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <div
          role="button" tabIndex={0}
          onClick={onYesClick}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onYesClick(); }}
          style={{ flex: 1, background: `${color}12`, border: `1px solid ${color}30`, borderRadius: 8, padding: "8px 10px", cursor: "pointer", textAlign: "center" }}
        >
          <div style={{ fontSize: 22, fontWeight: 700, color }}>{yesCount ?? 0}</div>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 2 }}>Yes &middot; {yesPct}%</div>
        </div>
        <div
          role="button" tabIndex={0}
          onClick={onNoClick}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onNoClick(); }}
          style={{ flex: 1, background: "rgba(148,163,184,0.07)", border: "1px solid rgba(148,163,184,0.15)", borderRadius: 8, padding: "8px 10px", cursor: "pointer", textAlign: "center" }}
        >
          <div style={{ fontSize: 22, fontWeight: 700, color: "var(--slate-200)" }}>{noCount ?? 0}</div>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 2 }}>No &middot; {noPct}%</div>
        </div>
      </div>
    </div>
  );
}

// ─── Section — collapsible, anchorable container used throughout the report ─
function Section({ id, title, icon, badge, children, defaultOpen = true }) {
  return (
    <section id={id} className="report-section">
      <div className="report-section-header">
        {icon}<span>{title}</span>
        {badge != null && <span className="panel-badge">{badge}</span>}
      </div>
      <div className="report-section-body">{children}</div>
    </section>
  );
}

// ─── Section nav — sticky quick-jump list of section anchors ───────────────
function SectionNav({ items }) {
  const scrollTo = (id) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <nav className="report-nav" aria-label="Report sections">
      <div className="report-nav-label">Jump to</div>
      <div className="report-nav-list">
        {items.map((item) => (
          <button key={item.id} className="report-nav-item" onClick={() => scrollTo(item.id)}>
            {item.icon}<span>{item.label}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}

function RankedList({ items, empty = "No data available", total = null }) {
  if (!items.length) return <div className="breakdown-empty">{empty}</div>;
  const max = items[0][1] || 1;
  return (
    <div className="breakdown-list">
      {items.map(([label, count], i) => (
        <div key={label} className="breakdown-item">
          <span className="breakdown-rank">{i + 1}</span>
          <span className="breakdown-name" title={label}>{label}</span>
          <div className="breakdown-track">
            <div className="breakdown-fill" style={{ width: `${pct(count, max)}%` }} />
          </div>
          <span className="breakdown-count">
            {total != null ? `${count} of ${total}` : count}
          </span>
        </div>
      ))}
    </div>
  );
}

// ─── SOP & Protocol Compliance — card grid with a radial-progress ring per
// step, so this (deliberately called out as the most important quality
// signal) reads as a dashboard rather than a stack of thin bars. Every
// card's count is always expressed as "N of Total analyzed calls".
function ComplianceCard({ label, count, total, value, valueSub, color }) {
  // Plain-value variant: used for count/duration stats (e.g. No. of Calls,
  // Avg/Total Call Duration) that aren't a "X of Y" percentage, so they
  // render as a solid-filled ring showing the raw value instead of a %.
  if (value !== undefined) {
    const tone = color || "var(--blue-400)";
    return (
      <div className="compliance-card">
        <div className="compliance-ring" style={{ background: tone }}>
          <div className="compliance-ring-inner" style={{ color: tone, fontSize: 12, fontWeight: 800, lineHeight: 1.15, padding: "0 2px", textAlign: "center" }}>
            {value}
          </div>
        </div>
        <div className="compliance-card-body">
          <div className="compliance-card-label">{label}</div>
          {valueSub != null && <div className="compliance-card-count">{valueSub}</div>}
        </div>
      </div>
    );
  }

  const ratio = total > 0 ? count / total : 0;
  const barPct = Math.min(100, Math.round(ratio * 100));
  const tone = barPct >= 80 ? "var(--emerald-400)" : barPct >= 50 ? "var(--amber-400)" : "var(--rose-400)";
  return (
    <div className="compliance-card">
      <div
        className="compliance-ring"
        style={{ background: `conic-gradient(${tone} ${barPct * 3.6}deg, rgba(148,163,184,0.15) 0deg)` }}
      >
        <div className="compliance-ring-inner" style={{ color: tone }}>{barPct}%</div>
      </div>
      <div className="compliance-card-body">
        <div className="compliance-card-label">{label}</div>
        <div className="compliance-card-count">{count} of {total} analyzed calls</div>
      </div>
    </div>
  );
}

function ComplianceGrid({ rows }) {
  return (
    <div className="compliance-grid">
      {rows.map((row) => (
        <ComplianceCard
          key={row.label}
          label={row.label}
          count={row.count}
          total={row.total}
          value={row.value}
          valueSub={row.valueSub}
          color={row.color}
        />
      ))}
    </div>
  );
}

// ─── Tone Distribution — pie chart of tone_conversation_check values,
// deliberately kept as a pie (per spec) alongside the SOP compliance grid.
function ToneDistributionChart({ toneDist, dominantTone }) {
  const entries = topN(toneDist, 6);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  if (!entries.length) {
    return <div className="breakdown-empty">No tone data available</div>;
  }
  const toneColors = [COLORS.blue, COLORS.emerald, COLORS.amber, COLORS.rose, COLORS.violet, COLORS.slate];
  return (
    <>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie
            data={entries.map(([name, value]) => ({ name, value }))}
            dataKey="value" nameKey="name" innerRadius={50} outerRadius={80}
          >
            {entries.map((_, i) => <Cell key={i} fill={toneColors[i % toneColors.length]} />)}
          </Pie>
          <Tooltip formatter={(value) => [`${value} of ${total} calls`, ""]} /><Legend />
        </PieChart>
      </ResponsiveContainer>
      <p style={{ marginTop: 6, fontSize: 13, color: "var(--text-secondary)", textAlign: "center" }}>
        Dominant tone: <strong style={{ color: "var(--text-primary)" }}>{dominantTone}</strong>
      </p>
    </>
  );
}



// ─── Agent Comparison — custom tooltip so every bar's "%" is paired with
// the raw "N of Total calls" it was computed from, instead of showing a
// bare percentage with no context (spec: make it clear what's being
// measured and out of what).
const AGENT_COMPARISON_COUNT_KEYS = {
  sopComplianceRate: (a) => a.sopCompliant,
  empathyRate: (a) => a.empathy,
  positiveSentimentRate: (a) => a.sentiment?.positive ?? 0,
  consultationPitchRate: (a) => a.consultationsPitched,
};

function AgentComparisonTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  const agent = payload[0]?.payload;
  const totalCalls = agent?.calls ?? 0;
  return (
    <div style={{ backgroundColor: "#0f172a", border: "1px solid #334155", borderRadius: 8, padding: "10px 12px", fontSize: 13, color: "#e2e8f0", minWidth: 220 }}>
      <div style={{ fontWeight: 700, marginBottom: 6, color: "#f8fafc" }}>
        {agentDisplayName(label)} — {totalCalls} call{totalCalls === 1 ? "" : "s"} analyzed
      </div>
      {totalCalls === 0 ? (
        <div style={{ color: "var(--text-secondary)" }}>No calls handled this period.</div>
      ) : (
        payload.map((entry) => {
          const getCount = AGENT_COMPARISON_COUNT_KEYS[entry.dataKey];
          const count = getCount ? getCount(agent) : null;
          return (
            <div key={entry.dataKey} style={{ display: "flex", justifyContent: "space-between", gap: 16, marginTop: 3 }}>
              <span style={{ color: entry.color }}>{entry.name}</span>
              <span style={{ fontWeight: 600 }}>
                {entry.value}%{count != null ? ` (${count} of ${totalCalls})` : ""}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}

// ─── Printable (PDF) report — plain, white-background, Word-style layout ──
const PRINT_COLORS = {
  ink: "#1a1a1a", sub: "#555555", muted: "#8a8a8a", line: "#dddddd",
  headBg: "#f2f2f2", rowAlt: "#fafafa",
  blue: "#2563eb", emerald: "#059669", amber: "#b45309", rose: "#dc2626",
  violet: "#7c3aed",
};

const pageStyle = {
  fontFamily: "'Calibri','Segoe UI',Arial,sans-serif",
  color: PRINT_COLORS.ink,
  background: "#ffffff",
  width: 780,
  padding: "36px 42px",
  lineHeight: 1.5,
  fontSize: 12,
};

const pTitleStyle = { fontSize: 22, fontWeight: 700, margin: 0, letterSpacing: -0.3 };
const pSubStyle = { fontSize: 12, color: PRINT_COLORS.sub, marginTop: 4 };

function PSection({ title, children }) {
  return (
    <div style={{ marginTop: 26, breakInside: "avoid" }}>
      <div style={{
        fontSize: 14, fontWeight: 700, color: PRINT_COLORS.ink,
        borderBottom: `2px solid ${PRINT_COLORS.ink}`, paddingBottom: 6, marginBottom: 12,
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function PStatGrid({ items }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <tbody>
        {Array.from({ length: Math.ceil(items.length / 3) }).map((_, rowIdx) => (
          <tr key={rowIdx}>
            {items.slice(rowIdx * 3, rowIdx * 3 + 3).map(([label, value, sub], i) => (
              <td key={i} style={{
                width: "33.33%", padding: "10px 12px", border: `1px solid ${PRINT_COLORS.line}`,
                verticalAlign: "top",
              }}>
                <div style={{ fontSize: 10, color: PRINT_COLORS.muted, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
                <div style={{ fontSize: 17, fontWeight: 700, marginTop: 2 }}>{value}</div>
                {sub != null && <div style={{ fontSize: 10, color: PRINT_COLORS.sub, marginTop: 2 }}>{sub}</div>}
              </td>
            ))}
            {items.slice(rowIdx * 3, rowIdx * 3 + 3).length < 3 &&
              Array.from({ length: 3 - items.slice(rowIdx * 3, rowIdx * 3 + 3).length }).map((_, k) => (
                <td key={`pad-${k}`} style={{ border: "none" }} />
              ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PBar({ label, value, max = 100, color = PRINT_COLORS.blue, suffix = "%" }) {
  const width = max ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
        <span>{label}</span><span style={{ color: PRINT_COLORS.sub }}>{value}{suffix}</span>
      </div>
      <div style={{ height: 8, background: "#eeeeee", borderRadius: 4 }}>
        <div style={{ height: 8, width: `${width}%`, background: color, borderRadius: 4 }} />
      </div>
    </div>
  );
}

function PTable({ columns, rows, compact = false }) {
  if (!rows.length) {
    return <div style={{ fontSize: 11, color: PRINT_COLORS.muted, fontStyle: "italic", padding: "8px 0" }}>No data available</div>;
  }
  const cellPad = compact ? "4px 5px" : "7px 10px";
  const headFontSize = compact ? 8.5 : 10;
  const bodyFontSize = compact ? 9.5 : 11;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: bodyFontSize, ...(compact ? { tableLayout: "fixed" } : {}) }}>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c} style={{
              textAlign: "left", padding: cellPad, background: PRINT_COLORS.headBg,
              border: `1px solid ${PRINT_COLORS.line}`, fontSize: headFontSize, textTransform: "uppercase",
              letterSpacing: 0.4, color: PRINT_COLORS.sub, ...(compact ? { wordBreak: "break-word" } : {}),
            }}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} style={{ background: i % 2 ? PRINT_COLORS.rowAlt : "#ffffff" }}>
            {row.map((cell, j) => (
              <td key={j} style={{ padding: cellPad, border: `1px solid ${PRINT_COLORS.line}`, ...(compact ? { wordBreak: "break-word" } : {}) }}>{cell}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function scoreHex(score) {
  if (score === null || score === undefined) return PRINT_COLORS.muted;
  if (score >= 8) return PRINT_COLORS.emerald;
  if (score >= 6) return PRINT_COLORS.blue;
  if (score >= 4) return PRINT_COLORS.amber;
  return PRINT_COLORS.rose;
}

function PrintableReport({ report, reportType, narrative }) {
  const m = report.current;
  const p = report.previous;
  const today = new Date().toLocaleDateString("en", { day: "2-digit", month: "short", year: "numeric" });

  return (
    <div style={pageStyle}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", borderBottom: `3px solid ${PRINT_COLORS.ink}`, paddingBottom: 14 }}>
        <div>
          <h1 style={pTitleStyle}>AutoFlint</h1>
          <p style={pSubStyle}>{reportType === "daily" ? "Daily" : "Monthly"} Performance Report</p>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 12, fontWeight: 700 }}>{report.range.start} – {report.range.end}</div>
          <div style={{ fontSize: 10, color: PRINT_COLORS.muted, marginTop: 2 }}>Generated {today}</div>
        </div>
      </div>

      {/* 1. Executive Summary */}
      <PSection title="1. Executive Summary">
        <PStatGrid items={[
          ["Total Calls", m.callCount],
          ["Inbound / Outbound", `${m.inbound} / ${m.outbound}`],
          ["New / Existing Clients", `${m.clientType.new} / ${m.clientType.existing}`],
          ["Avg Quality Score", m.avgScore != null ? m.avgScore.toFixed(1) : "—", scoreBand(m.avgScore)],
          ["Best Performing Agent", m.bestAgent?.name || "—", m.bestAgent?.avgScore != null ? `${m.bestAgent.avgScore.toFixed(1)} avg score` : null],
          ["Agent Requiring Attention", m.worstAgent?.name || "—", m.worstAgent?.avgScore != null ? `${m.worstAgent.avgScore.toFixed(1)} avg score` : null],
          ["Open Follow-ups", m.openFollowUps.length, `${m.overdueFollowUps.length} overdue`],
          ["Total Escalations", m.escalationItems.length],
          ["Calls Requiring Attorney Attention", m.attorneyCalls.length],
          ["Positive / Negative Sentiment", `${m.rates.positiveSentiment}% / ${m.rates.negativeSentiment}%`],
          ["Top SOP Gap", topN(m.sopMissCounts, 1)[0]?.[0] || "None recorded"],
          ["Paid Consultation Pitch Rate", `${m.rates.consultationPitch}%`],
        ]} />
      </PSection>

      {/* AI Summary — only appears if the user generated one before exporting */}
      {narrative && (
        <PSection title="AI Summary">
          {narrative.executive_summary && (
            <p style={{ fontSize: 11.5, lineHeight: 1.7, margin: "0 0 10px" }}>{narrative.executive_summary}</p>
          )}

          {Array.isArray(narrative.key_findings) && narrative.key_findings.length > 0 && (
            <>
              <div style={{ fontSize: 11, fontWeight: 700, margin: "10px 0 6px", color: PRINT_COLORS.sub }}>Key Findings</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, lineHeight: 1.8 }}>
                {narrative.key_findings.map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </>
          )}

          {narrative.agent_commentary && (
            <p style={{ fontSize: 11.5, lineHeight: 1.7, margin: "10px 0 0" }}>
              <strong>Agent comparison: </strong>{narrative.agent_commentary}
            </p>
          )}

          {narrative.sentiment_commentary && (
            <p style={{ fontSize: 11.5, lineHeight: 1.7, margin: "8px 0 0" }}>
              <strong>Sentiment: </strong>{narrative.sentiment_commentary}
            </p>
          )}

          {Array.isArray(narrative.additional_insights) && narrative.additional_insights.length > 0 && (
            <>
              <div style={{ fontSize: 11, fontWeight: 700, margin: "14px 0 6px", color: PRINT_COLORS.sub }}>Additional Insights</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, lineHeight: 1.8 }}>
                {narrative.additional_insights.map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </>
          )}

          {Array.isArray(narrative.priority_recommendations) && narrative.priority_recommendations.length > 0 && (
            <>
              <div style={{ fontSize: 11, fontWeight: 700, margin: "14px 0 6px", color: PRINT_COLORS.sub }}>Priority Recommendations</div>
              <PTable
                columns={["Priority", "Finding", "Recommended Action"]}
                rows={narrative.priority_recommendations.map((r) => [r.priority || "—", r.finding || "—", r.action || "—"])}
              />
            </>
          )}
        </PSection>
      )}

      {/* 2. Call Volume & Distribution */}
      <PSection title={reportType === "daily" ? "2. Call Volume & Distribution" : "2. Month-at-a-Glance"}>
        <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 8, color: PRINT_COLORS.sub }}>Calls by Agent</div>
        <PTable
          columns={["Agent", "Calls"]}
          rows={topN(agentCallsBreakdown(m.callsByAgent), 6).map(([name, count]) => [name, count])}
        />
        <div style={{ fontSize: 11, fontWeight: 700, margin: "16px 0 8px", color: PRINT_COLORS.sub }}>Client Sentiment</div>
        <PBar label="Positive" value={m.rates.positiveSentiment} color={PRINT_COLORS.emerald} />
        <PBar label="Negative" value={m.rates.negativeSentiment} color={PRINT_COLORS.rose} />
        <PBar label="Neutral" value={100 - m.rates.positiveSentiment - m.rates.negativeSentiment} color={PRINT_COLORS.muted} />

        <div style={{ fontSize: 11, fontWeight: 700, margin: "16px 0 8px", color: PRINT_COLORS.sub }}>Call Volume by Agent</div>
       <PTable
          columns={["Agent", "Total", "Inbound", "Outbound", "New", "Existing", "Voicemail %", "Avg Duration"]}
          rows={[
            ...m.agentVolumeRows.filter((a) => !(a.name === "N/A" && a.calls === 0)).map((a) => [
              a.name, a.calls, a.inbound, a.outbound, a.newClients, a.existingClients,
              `${a.voicemailRate}%`, formatDuration(a.avgDurationSec),
            ]),
            (() => { const t = buildAgentVolumeTotal(m.agentVolumeRows); return [
              t.name, t.calls, t.inbound, t.outbound, t.newClients, t.existingClients,
              `${t.voicemailRate}%`, formatDuration(t.avgDurationSec),
            ]; })(),
          ]}
        />
        <div style={{ fontSize: 11, fontWeight: 700, margin: "16px 0 8px", color: PRINT_COLORS.sub }}>
          Average Call Duration (Overall): {formatDuration(m.avgDurationSec)} &nbsp;|&nbsp; Total Call Duration (Overall): {formatDuration(m.totalDurationSec)}
        </div>
        <div style={{ fontSize: 11, fontWeight: 700, margin: "16px 0 8px", color: PRINT_COLORS.sub }}>Call Volume by Hour</div>
        <PTable
          columns={Object.keys(m.callsByHour).map((h) => `${h}:00`)}
          rows={[Object.values(m.callsByHour)]}
        />
      </PSection>

      {/* 3. Call Quality Performance */}
      <PSection title="3. Call Quality Performance">
        <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 8, color: PRINT_COLORS.sub }}>Quality Band Distribution</div>
        {Object.entries(m.scoreDist).map(([label, count]) => (
          <PBar key={label} label={label} value={pct(count, m.analysisCount)} color={scoreHex(label === "Excellent" ? 9 : label === "Good" ? 7 : label === "Fair" ? 5 : 2)} />
        ))}
        <div style={{ fontSize: 11, fontWeight: 700, margin: "16px 0 8px", color: PRINT_COLORS.sub }}>SOP &amp; Protocol Compliance</div>
        <PBar label="SOP Scripts" value={m.sopCompliant} max={m.analysisCount} suffix={` of ${m.analysisCount} calls`} color={PRINT_COLORS.blue} />
        <PBar label="Start Greeting" value={m.startGreeting} max={m.analysisCount} suffix={` of ${m.analysisCount} calls`} color={PRINT_COLORS.blue} />
        <PBar label="End Greeting" value={m.endGreeting} max={m.analysisCount} suffix={` of ${m.analysisCount} calls`} color={PRINT_COLORS.blue} />
        <PBar label="Empathy Shown" value={m.empathy} max={m.analysisCount} suffix={` of ${m.analysisCount} calls`} color={PRINT_COLORS.blue} />
        <PBar label="Next Step Communicated" value={m.nextStep} max={m.analysisCount} suffix={` of ${m.analysisCount} calls`} color={PRINT_COLORS.blue} />
        <PBar label="Call Closed Properly" value={m.closedProperly} max={m.analysisCount} suffix={` of ${m.analysisCount} calls`} color={PRINT_COLORS.blue} />
        <PBar label="Hold Protocol Followed" value={m.holdProtocol} max={m.analysisCount} suffix={` of ${m.analysisCount} calls`} color={PRINT_COLORS.blue} />
        <div style={{ fontSize: 11, fontWeight: 700, margin: "16px 0 8px", color: PRINT_COLORS.sub }}>Top 5 SOP Steps Missed</div>
        <PTable columns={["SOP Step", "Calls Affected"]} rows={topN(m.sopMissCounts, 5).map(([k, v]) => [k, `${v} of ${m.analysisCount}`])} />
        <div style={{ fontSize: 11, fontWeight: 700, margin: "16px 0 8px", color: PRINT_COLORS.sub }}>Answer Quality Breakdown</div>
        <PTable columns={["Answer Quality", "Calls"]} rows={topN(m.answerQualityDist, 5).map(([k, v]) => [k, `${v} of ${m.analysisCount}`])} />
      </PSection>

      {/* 4. Agent Performance / Scorecards */}
      <PSection title={reportType === "daily" ? "4. Agent Performance" : "4. Agent Scorecards"}>
        <PTable
          compact
          columns={["Agent", "Calls", "Avg Score", "SOP %", "Empathy %", "Dominant Tone", "+Sentiment %", "Follow-ups", "Escalations", "Avg Duration", "Total Duration"]}
          rows={m.agents.filter((a) => !(a.name === "Unassigned" && a.calls === 0)).map((a) => [
            agentDisplayName(a.name), a.calls, a.avgScore != null ? a.avgScore.toFixed(1) : "—",
            `${a.sopComplianceRate}%`, `${a.empathyRate}%`, a.dominantTone,
            `${a.positiveSentimentRate}%`, a.followUps, a.escalations,
            formatDuration(a.avgDurationSec), formatDuration(a.totalDurationSec),
          ])}
        />
        {m.agents.map((a) => (
          <div key={a.name} style={{ marginTop: 16, breakInside: "avoid" }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, marginBottom: 6 }}>
              {agentDisplayName(a.name)}{a.calls === 0
                ? (reportType === "daily" ? " — No calls handled on this date" : " — No calls handled this month")
                : ` — ${a.avgScore != null ? a.avgScore.toFixed(1) : "—"} avg (${scoreBand(a.avgScore)})`}
            </div>
            {a.calls > 0 && (
              <>
                <div style={{ fontSize: 10.5, color: PRINT_COLORS.sub, marginBottom: 8 }}>
                  No. of Calls: {a.calls} &nbsp;|&nbsp; Avg Call Duration: {formatDuration(a.avgDurationSec)} &nbsp;|&nbsp; Total Call Duration: {formatDuration(a.totalDurationSec)}
                </div>
                <PTable
                  columns={["Quality Band", "Count"]}
                  rows={Object.entries(a.scoreDist).filter(([, c]) => c > 0)}
                />
                <PTable
                  columns={["Top SOP Misses", "Count"]}
                  rows={a.topSopMisses.length ? a.topSopMisses : [["None recorded", ""]]}
                />
                {a.observations.map((o, i) => (
                  <div key={i} style={{ fontSize: 10.5, color: PRINT_COLORS.sub, marginTop: 4 }}>{o}</div>
                ))}
              </>
            )}
          </div>
        ))}
      </PSection>

      {/* Month-over-Month (monthly only) */}
      {reportType === "monthly" && p && (
        <PSection title="5. Month-over-Month Comparison">
          <PTable
            columns={["Metric", "This Period", "Previous Period"]}
            rows={[
              ["Avg Quality Score", m.avgScore?.toFixed(1) ?? "—", p.avgScore?.toFixed(1) ?? "—"],
              ["SOP Scripts %", `${m.rates.sopCompliance}%`, `${p.rates.sopCompliance}%`],
              ["Empathy %", `${m.rates.empathy}%`, `${p.rates.empathy}%`],
              ["Positive Sentiment %", `${m.rates.positiveSentiment}%`, `${p.rates.positiveSentiment}%`],
              ["Escalation Rate %", `${m.rates.escalation}%`, `${p.rates.escalation}%`],
              ["Follow-up Open Rate %", `${m.rates.followUpOpen}%`, `${p.rates.followUpOpen}%`],
            ]}
          />
        </PSection>
      )}

      {/* Follow-up & Escalation */}
      <PSection title={reportType === "daily" ? "5. Follow-Up & Escalation Summary" : "6. Follow-Up & Escalation Summary"}>
        <PStatGrid items={[
          ["Total Follow-ups", m.followUpItems.length],
          ["Open", m.openFollowUps.length],
          ["Overdue", m.overdueFollowUps.length],
          ["No Deadline Set", m.followUpsNoDeadline.length],
          ["Total Escalations", m.escalationItems.length],
          ["Front Desk Actions Pending", m.frontDeskActionsPending],
        ]} />
        <div style={{ fontSize: 11, fontWeight: 700, margin: "16px 0 8px", color: PRINT_COLORS.sub }}>Follow-up Status</div>
        <PTable
          columns={["Status", "Count"]}
          rows={Object.entries(m.followUpStatusDist).filter(([, c]) => c > 0)}
        />
      </PSection>


      {/* Attorney attention */}
      <PSection title={reportType === "daily" ? "6. Calls Requiring Attorney Attention" : "7. Calls Requiring Attorney Attention"}>
        <PTable
          columns={["Date", "Agent", "Client", "Case Type", "Escalation Target"]}
          rows={m.attorneyCalls.map((row) => [
            row.call_recording_date || "—", row.front_desk_person_name || "—",
            row.client_name || "—", row.case_type || "—", row.escalation_to || "—",
          ])}
        />
      </PSection>

      {/* Gap analysis */}
      <PSection title={reportType === "daily" ? "7. Gap Analysis & Observations" : "8. Recurring Gaps & Recommendations"}>
        {reportType === "daily" && narrative?.gap_analysis ? (
          <>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, lineHeight: 1.9 }}>
              {narrative.gap_analysis.top_gaps?.map((g, i) => <li key={i}>{g}</li>)}
            </ul>
            {narrative.gap_analysis.recurring_pattern && <p style={{ fontSize: 11.5, margin: "10px 0 0" }}><strong>Recurring pattern: </strong>{narrative.gap_analysis.recurring_pattern}</p>}
            {narrative.gap_analysis.positive_standout && <p style={{ fontSize: 11.5, margin: "8px 0 0" }}><strong>Positive standout: </strong>{narrative.gap_analysis.positive_standout}</p>}
            {narrative.gap_analysis.process_gap && <p style={{ fontSize: 11.5, margin: "8px 0 0" }}><strong>Process gap: </strong>{narrative.gap_analysis.process_gap}</p>}
            {narrative.gap_analysis.recommendations?.length > 0 && (
              <>
                <div style={{ fontSize: 11, fontWeight: 700, margin: "12px 0 6px", color: PRINT_COLORS.sub }}>Recommendations</div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, lineHeight: 1.9 }}>
                  {narrative.gap_analysis.recommendations.map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              </>
            )}
          </>
        ) : reportType === "monthly" && narrative?.recurring_gaps ? (
          <>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, lineHeight: 1.9 }}>
              {narrative.recurring_gaps.top_gaps?.map((g, i) => <li key={i}>{g}</li>)}
            </ul>
            {narrative.recurring_gaps.persisted_patterns && <p style={{ fontSize: 11.5, margin: "10px 0 0" }}><strong>Persisted from prior period: </strong>{narrative.recurring_gaps.persisted_patterns}</p>}
            {narrative.recurring_gaps.positive_trends && <p style={{ fontSize: 11.5, margin: "8px 0 0" }}><strong>Positive trends: </strong>{narrative.recurring_gaps.positive_trends}</p>}
            {narrative.recurring_gaps.structural_issues && <p style={{ fontSize: 11.5, margin: "8px 0 0" }}><strong>Structural issues: </strong>{narrative.recurring_gaps.structural_issues}</p>}
            {narrative.recurring_gaps.recommendations?.length > 0 && (
              <>
                <div style={{ fontSize: 11, fontWeight: 700, margin: "12px 0 6px", color: PRINT_COLORS.sub }}>Prioritised Recommendations</div>
                <PTable
                  columns={["Priority", "Action", "Owner"]}
                  rows={narrative.recurring_gaps.recommendations.map((r) => [r.priority || "—", r.action || "—", r.owner || "—"])}
                />
              </>
            )}
          </>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, lineHeight: 1.9 }}>
            <li><strong>Top SOP gap:</strong> {topN(m.sopMissCounts, 1)[0]?.[0] || "None recorded"}</li>
            <li><strong>Weakest compliance area:</strong> {
              Object.entries({
                "SOP Scripts": m.rates.sopCompliance, "Start Greeting": m.rates.startGreeting,
                "End Greeting": m.rates.endGreeting, "Empathy": m.rates.empathy,
                "Next Step Communicated": m.rates.nextStep, "Call Closed Properly": m.rates.closedProperly,
                "Hold Protocol": m.rates.holdProtocol,
              }).sort((a, b) => a[1] - b[1])[0]?.[0] || "—"
            }</li>
            <li><strong>Positive standout:</strong> {m.bestAgent ? `${m.bestAgent.name} — ${m.bestAgent.avgScore?.toFixed(1)} avg quality score` : "—"}</li>
            <li><strong>Escalation rate:</strong> {m.rates.escalation}% of analyzed calls</li>
            <li><strong>Overdue follow-ups needing action:</strong> {m.overdueFollowUps.length}</li>
          </ul>
        )}
      </PSection>

      <div style={{ marginTop: 30, paddingTop: 12, borderTop: `1px solid ${PRINT_COLORS.line}`, fontSize: 9, color: PRINT_COLORS.muted, textAlign: "center" }}>
        AutoFlint Call Analytics — Confidential — Generated {today}
      </div>
    </div>
  );
}




// Anchors used by the sticky "Jump to" nav — keep ids in sync with <Section id="...">
const NAV_ITEMS = (reportType) => [
  { id: "sec-summary", label: "Summary", icon: <Sparkles size={13} /> },
  { id: "sec-volume", label: reportType === "daily" ? "Volume" : "Month-at-a-Glance", icon: <Phone size={13} /> },
  { id: "sec-quality", label: "Quality", icon: <BarChart3 size={13} /> },
  { id: "sec-agents", label: "Agents", icon: <Users size={13} /> },
  { id: "sec-sentiment", label: "Sentiment", icon: <Frown size={13} /> },
  { id: "sec-followups", label: "Follow-ups", icon: <Clock size={13} /> },
  { id: "sec-legal", label: "Legal", icon: <Scale size={13} /> },
  { id: "sec-gaps", label: "Gaps", icon: <AlertTriangle size={13} /> },
];

// ─── Report component ──────────────────────────────────────────────────────
// Threshold: ranges longer than this many days get the Monthly (strategic/
// trend) report treatment; ranges at or under it get the Daily (tactical)
// report treatment. Kept as a constant so it's easy to tune in one place.
const MONTHLY_THRESHOLD_DAYS = 7;

// Inclusive day count between two YYYY-MM-DD strings.
function daysBetween(start, end) {
  const s = new Date(`${start}T00:00:00`);
  const e = new Date(`${end}T00:00:00`);
  if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime())) return 0;
  return Math.round((e - s) / 86400000) + 1;
}

// Given a start/end range, build the equal-length immediately-preceding
// period — used as the "previous" comparison window for monthly-style
// (trend / MoM) reports.
function buildPreviousRange(start, end) {
  const dayCount = daysBetween(start, end);
  const s = new Date(`${start}T00:00:00`);
  const prevEnd = new Date(s);
  prevEnd.setDate(prevEnd.getDate() - 1);
  const prevStart = new Date(prevEnd);
  prevStart.setDate(prevStart.getDate() - (dayCount - 1));
  return { prevStart: formatDate(prevStart), prevEnd: formatDate(prevEnd) };
}

function ReportAnalysis() {
  // Single date-range selector — no more manual daily/monthly toggle.
  // Report type is derived automatically: more than MONTHLY_THRESHOLD_DAYS
  // days selected => monthly (strategic/trend) report; otherwise => daily
  // (tactical) report.
  const [rangeStart, setRangeStart] = useState(() => formatDate(new Date()));
  const [rangeEnd, setRangeEnd] = useState(() => formatDate(new Date()));
  const [loading, setLoading] = useState(false);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [error, setError] = useState(null);
  const [report, setReport] = useState(null); // { current, previous, range, prevRange }
  const [narrative, setNarrative] = useState(null); // AI-generated summary/findings
  const [narrativeLoading, setNarrativeLoading] = useState(false);
  const [narrativeError, setNarrativeError] = useState(null);
  const reportRef = useRef(null);
  const printRef = useRef(null);
  const [statusModalState, setStatusModalState] = useState({ type: null, label: null });
  const [audioModalRow, setAudioModalRow] = useState(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState("");
  const audioRef = useRef(null);
  const audioCacheRef = useRef(new Map());

  const selectedDayCount = useMemo(() => sharedDaysBetween(rangeStart, rangeEnd), [rangeStart, rangeEnd]);
  const reportType = selectedDayCount > MONTHLY_THRESHOLD_DAYS ? "monthly" : "daily";

  const selectedRange = useMemo(() => {
    const { prevStart, prevEnd } = buildSharedPreviousRange(rangeStart, rangeEnd);
    return { start: rangeStart, end: rangeEnd, prevStart, prevEnd };
  }, [rangeStart, rangeEnd]);

  const fetchRange = async (start, end) => {
    const [recordsResp, analysisResp] = await Promise.all([
      getCalls(start, end),
      getCallAnalysis(start, end),
    ]);
    const records = Array.isArray(recordsResp) ? recordsResp : recordsResp?.calls || recordsResp?.results || [];
    const analysisRaw = Array.isArray(analysisResp) ? analysisResp : analysisResp?.analysis || analysisResp?.results || analysisResp?.items || [];
    const analysis = scopeAnalysisToFetchedCalls(records, analysisRaw);
    return { records, analysis };
  };

  const handleGenerate = async () => {
    if (daysBetween(rangeStart, rangeEnd) < 1) {
      setError("Please choose a valid date range (start date must be on or before the end date).");
      return;
    }
    setLoading(true);
    setError(null);
    setNarrative(null);
    setNarrativeError(null);
    try {
      if (reportType === "daily") {
        // 7 days or fewer selected — tactical daily-style report, no prior-period comparison.
        const { records, analysis } = await fetchRange(selectedRange.start, selectedRange.end);
        setReport({ current: buildSharedMetrics(records, analysis), previous: null, range: selectedRange });
      } else {
        // More than 7 days selected — strategic monthly-style report, compared
        // against the equal-length immediately preceding period.
     const [curr, prev] = await Promise.all([
          fetchRange(selectedRange.start, selectedRange.end),
          fetchRange(selectedRange.prevStart, selectedRange.prevEnd),
        ]);
        // Trend series uses the same scoped analysis dataset used by the rest
        // of the report so totals and trend views reconcile.
        const scopedCurrAnalysis = curr.analysis;
        setReport({
          current: buildSharedMetrics(curr.records, curr.analysis),
          previous: buildSharedMetrics(prev.records, prev.analysis),
          range: selectedRange,
          dailySeries: buildSharedDailySeries(scopedCurrAnalysis),
          weeklySeries: buildSharedWeeklySeries(scopedCurrAnalysis),
        });
      }
    } catch (err) {
      console.error("Report generation failed", err);
      setError("Failed to generate report. Please check the date range and try again.");
    } finally {
      setLoading(false);
    }
  };

  // Calls the backend's LLM proxy — the actual provider API key never
  // reaches the browser; the backend attaches it server-side.
  const handleGenerateNarrative = async () => {
    if (!report) return;
    setNarrativeLoading(true);
    setNarrativeError(null);
    try {

      const m = report.current;
      // Pre-aggregated rollups (fast for the model to quote directly).
      // This is the ONLY numeric payload sent to the model — its size is
      // flat regardless of date range (it's already bucketed/counted data),
      // so it never blows the context window on large monthly ranges the
      // way a raw per-call dump used to.
      const {
        agents, rawAnalysis,
        // These are full arrays of raw call records (analysis.filter(...)),
        // not pre-aggregated data — spreading them into `rest` below would
        // dump hundreds of complete records into the LLM payload, which is
        // what blew the request past the TPM limit on larger date ranges.
        // The model only ever needs their counts; individual examples come
        // from the separately-capped `notableCalls` sample instead.
        escalationItems, followUpItems, openFollowUps, overdueFollowUps,
        followUpsNoDeadline, consultationItems, attorneyCalls,
        ...rest
      } = m;
      const trimmedAgents = agents.map(({ scores, toneDist, sopMisses, ...a }) => a);
      const metrics = {
        ...rest,
        agents: trimmedAgents,
        escalationCount: escalationItems.length,
        followUpCount: followUpItems.length,
        openFollowUpCount: openFollowUps.length,
        overdueFollowUpCount: overdueFollowUps.length,
        followUpsNoDeadlineCount: followUpsNoDeadline.length,
        consultationPitchedCount: consultationItems.length,
        attorneyAttentionCount: attorneyCalls.length,
      };

      // A small, bounded sample of the most noteworthy individual calls —
      // NOT a raw dump of the full dataset — so the model can still cite
      // specific examples beyond the pre-built aggregates, without payload
      // size scaling with the date range. Picks: lowest-scoring calls,
      // legal-flagged calls, and escalations, deduped, capped at
      // NOTABLE_CALL_CAP total.
      const NOTABLE_CALL_CAP = 40;
      const toNotableRow = (r) => ({
        call_id: r.call_id ?? r.id ?? null,
        agent: r.agent_name || "Unassigned",
        case_type: r.case_type || "Unknown",
        overall_call_quality_score: parseScore(r.overall_call_quality_score),
        client_sentiment: r.client_sentiment || null,
        sop_compliant: llmField(r, K.sopCompliant) ?? null,
        sop_steps_missed: llmField(r, K.sopStepsMissed) ?? null,
        legal_flag: llmField(r, K.legalFlag) ?? null,
        legal_concern_summary: llmField(r, K.legalConcern) ?? null,
        escalation_required: r.escalation_required ?? null,
        escalation_to: r.escalation_to ?? null,
        call_date: r.call_date || r.created_at || null,
      });

      const allAnalysis = rawAnalysis || [];
      const seen = new Set();
      const notableCalls = [];
      const addRows = (rows) => {
        for (const r of rows) {
          if (notableCalls.length >= NOTABLE_CALL_CAP) break;
          const key = r.call_id ?? r.id;
          if (key != null && seen.has(key)) continue;
          if (key != null) seen.add(key);
          notableCalls.push(toNotableRow(r));
        }
      };
      const byScoreAsc = [...allAnalysis]
        .filter((r) => parseScore(r.overall_call_quality_score) !== null)
        .sort((a, b) => parseScore(a.overall_call_quality_score) - parseScore(b.overall_call_quality_score));
      addRows(byScoreAsc.slice(0, 15)); // worst-scoring calls
      addRows(allAnalysis.filter((r) => isYes(llmField(r, K.legalFlag)))); // legal-flagged
      addRows(allAnalysis.filter((r) => isYes(r.escalation_required))); // escalations
      const notableCallsTruncated = notableCalls.length >= NOTABLE_CALL_CAP;

      const gapSectionSpec = reportType === "daily"
        ? `"gap_analysis": {
    "top_gaps": ["3 specific, factual operational gaps from today's data, each with a number"],
    "recurring_pattern": "1-2 sentences on any pattern across multiple agents that warrants attention, or empty string if none",
    "positive_standout": "1 sentence acknowledging a specific agent or call that stood out positively",
    "process_gap": "1-2 sentences on any process/workflow gap that hurt call quality today (e.g. caseworker non-response, missed client update), or empty string if none",
    "recommendations": ["2-3 short, specific, actionable recommendations for the next day or near term"]
  }`
        : `"recurring_gaps": {
    "top_gaps": ["up to 5 specific recurring operational gaps this month, each stated with a supporting data point, e.g. 'End greeting compliance averaged 54%, concentrated in X and Y calls'"],
    "persisted_patterns": "1-2 sentences on any pattern that has not improved (say 'No prior period data available for comparison' if metrics.previous is absent)",
    "positive_trends": "1-2 sentences on what improved this month and which agent(s) drove it",
    "recommendations": [{"priority": "Critical|High|Medium", "action": "specific action", "owner": "agent name, Operations, or Management"}],
    "structural_issues": "1-2 sentences on any process-level issue beyond individual agent performance, or empty string if none"
  }`;

      const systemPrompt = `You are an operations analyst writing a front-desk call quality report for a law firm's leadership team.

You will be given two JSON payloads:
1. "metrics" — pre-aggregated rollups for the FULL period (totals, rates, per-agent stats, breakdowns by case type/lead source/hour/etc). This covers the entire date range regardless of how large it is — treat every number in it as authoritative for the whole period.
2. "notable_calls" — a small, hand-picked sample of individual calls (lowest-scoring, legal-flagged, and escalated) from within that same period, included so you can cite concrete examples. This is NOT the full dataset and must never be used to compute rates, percentages, or totals — only use it for illustrative examples and quotes to support a finding that's already grounded in "metrics".

Base every number and rate in your report on "metrics". Use "notable_calls" only to name specific example calls/agents that illustrate a pattern already visible in "metrics". Write in the tone of a sharp, data-driven internal report: direct, specific, uses real numbers, calls out patterns rather than restating every stat. Every gap or recommendation must be traceable to a specific finding in the data — no generic advice.

Return ONLY valid JSON, no markdown fences, no preamble, matching exactly this shape:
{
  "executive_summary": "2-4 sentence paragraph summarizing overall performance",
  "key_findings": ["short bullet with a concrete number", "short bullet", ...],
  "agent_commentary": "1-2 sentence paragraph comparing agents by name using their actual metrics",
  "sentiment_commentary": "1-2 sentence paragraph interpreting the sentiment distribution",
  "additional_insights": ["short bullet surfacing a secondary pattern from metrics, optionally naming a specific example from notable_calls, with a number", ...],
  "priority_recommendations": [
    {"priority": "Critical|High|Medium", "finding": "...", "action": "..."}
  ],
  ${gapSectionSpec}
}`;

      const userContent = `Report type: ${reportType}\nDate range: ${report.range.start} to ${report.range.end}\n\nmetrics JSON (covers the FULL period — use this for every number/rate):\n${JSON.stringify(metrics)}\n\nnotable_calls JSON (${notableCalls.length} hand-picked example calls${notableCallsTruncated ? ", capped — there may be more matching calls than shown" : ""} — for illustrative citation only, NOT for computing totals or rates):\n${JSON.stringify(notableCalls)}`;

      const res = await fetch(`${API_BASE_URL}/ai/narrative`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(localStorage.getItem("autoflint_token")
            ? { Authorization: `Bearer ${localStorage.getItem("autoflint_token")}` }
            : {}),
        },
        body: JSON.stringify({
          system_prompt: systemPrompt,
          user_content: userContent,
        }),
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(`LLM request failed (${res.status}): ${errText.slice(0, 200)}`);
      }

      const data = await res.json();
      const textBlock = data.content;
      if (!textBlock) throw new Error("LLM returned no text content");

      const cleaned = textBlock.trim().replace(/^```json/, "").replace(/^```/, "").replace(/```$/, "").trim();
      const parsed = JSON.parse(cleaned);
      setNarrative(parsed);
    } catch (err) {
      console.error("Narrative generation failed", err);
      setNarrativeError(err.message || "Couldn't generate the AI summary. Please try again.");
    } finally {
      setNarrativeLoading(false);
    }
  };

  const handleDownloadPdf = async () => {
    if (!printRef.current || !report) return;
    setPdfLoading(true);
    try {
      // The printable report is a separate, light/white-themed DOM tree
      // (rendered off-screen) built specifically for export — plain tables,
      // black-on-white text, no dark dashboard styling — so the PDF reads
      // like an actual document rather than a screenshot of the app.
      const node = printRef.current;
      const canvas = await html2canvas(node, {
        scale: 2,
        backgroundColor: "#ffffff",
        useCORS: true,
        windowWidth: node.scrollWidth,
      });

      const pdf = new jsPDF({ orientation: "portrait", unit: "pt", format: "a4" });
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const marginPt = 24;
      const usableWidth = pageWidth - marginPt * 2;
      const usableHeight = pageHeight - marginPt * 2;

      const imgWidth = usableWidth;

      // Slice the tall canvas into page-sized chunks (in canvas-pixel space)
      // so rows/cards don't get sliced mid-content across page breaks.
      const pageCanvasHeightPx = Math.floor((usableHeight * canvas.width) / imgWidth);
      let renderedHeightPx = 0;
      let pageIndex = 0;

      while (renderedHeightPx < canvas.height) {
        const sliceHeightPx = Math.min(pageCanvasHeightPx, canvas.height - renderedHeightPx);

        const pageCanvas = document.createElement("canvas");
        pageCanvas.width = canvas.width;
        pageCanvas.height = sliceHeightPx;
        const ctx = pageCanvas.getContext("2d");
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, pageCanvas.width, pageCanvas.height);
        ctx.drawImage(
          canvas,
          0, renderedHeightPx, canvas.width, sliceHeightPx,
          0, 0, canvas.width, sliceHeightPx
        );

        const sliceImg = pageCanvas.toDataURL("image/jpeg", 0.95);
        const sliceHeightPt = (sliceHeightPx * imgWidth) / canvas.width;

        if (pageIndex > 0) pdf.addPage();
        pdf.addImage(sliceImg, "JPEG", marginPt, marginPt, imgWidth, sliceHeightPt);

        renderedHeightPx += sliceHeightPx;
        pageIndex++;
      }

      const label = reportType === "daily"
        ? `Daily-Report-${report.range.start}${report.range.start !== report.range.end ? `_to_${report.range.end}` : ""}`
        : `Monthly-Report-${report.range.start}_to_${report.range.end}`;
      pdf.save(`${label}.pdf`);
    } catch (err) {
      console.error("PDF export failed", err);
      setError("Failed to generate PDF. Please try again.");
    } finally {
      setPdfLoading(false);
    }
  };

  const m = report?.current;
  const p = report?.previous;

  const momDelta = (curr, prev) => {
    if (curr == null || prev == null) return null;
    return Math.round((curr - prev) * 10) / 10;
  };

  const openStatusModal = (type, label = null) => setStatusModalState({ type, label });
  const closeStatusModal = () => setStatusModalState({ type: null, label: null });

  const statusModalConfig = m ? {
    vmu: {
      title: "Voicemail Left — Yes / No",
      kicker: "VMU Status Drill-down",
      color: COLORS.violet,
      items: m.vmuStatusItems,
    },
    concern: {
      title: "Client Showed Signs of Concern or Anxiety",
      kicker: "VMU Status Drill-down",
      color: COLORS.amber,
      items: m.concernAnxietyItems,
    },
    reassurance: {
      title: "Assurance / Reassurance Provided by Agent",
      kicker: "VMU Status Drill-down",
      color: COLORS.emerald,
      items: m.reassuranceProvidedItems,
    },
  } : null;

  const currentStatusModal = statusModalState.type && statusModalConfig
    ? statusModalConfig[statusModalState.type]
    : null;

  const currentStatusItems = currentStatusModal
    ? currentStatusModal.items.filter((row) => {
        if (!statusModalState.label) return true;
        return row.statusLabel === statusModalState.label;
      })
    : [];

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
    <div className="report-page">
      <section className="report-hero">
        <div className="dashboard-badge"><Sparkles size={14} /><span>Call Analytics</span></div>
        <h1 className="welcome">Report Analysis</h1>
        <p className="welcome-subtext">Pick a date range — 7 days or fewer generates a Daily report, more than 7 days generates a Monthly report.</p>
      </section>

      <section className="report-controls">
        <div className="report-date-input">
          <label>From</label>
          <input
            type="date"
            value={rangeStart}
            max={rangeEnd}
            onChange={(e) => {
              const next = e.target.value;
              setRangeStart(next);
              if (rangeEnd < next) setRangeEnd(next);
            }}
          />
        </div>
        <div className="report-date-input">
          <label>To</label>
          <input
            type="date"
            value={rangeEnd}
            min={rangeStart}
            onChange={(e) => setRangeEnd(e.target.value)}
          />
        </div>

        <div className={`report-type-badge report-type-badge-${reportType}`} title={`${selectedDayCount} day${selectedDayCount === 1 ? "" : "s"} selected — more than ${MONTHLY_THRESHOLD_DAYS} switches to a Monthly report`}>
          {reportType === "daily" ? <Calendar size={14} /> : <BarChart3 size={14} />}
          <span>{reportType === "daily" ? "Daily Analysis" : "Monthly Analysis"}</span>
          <span className="report-type-badge-days">{selectedDayCount} day{selectedDayCount === 1 ? "" : "s"}</span>
        </div>

        <div className="report-controls-spacer" />

        <div className="report-btn-group">
          <button className="dashboard-filter-btn btn-apply" onClick={handleGenerate} disabled={loading}>
            {loading ? <><Loader2 size={16} className="spin" /> Generating...</> : "Generate Report"}
          </button>
          <button
            className="report-download-btn"
            onClick={handleGenerateNarrative}
            disabled={!report || narrativeLoading}
          >
            {narrativeLoading ? <><Loader2 size={16} className="spin" /> Analyzing...</> : <><Sparkles size={16} /> Generate AI Summary</>}
          </button>
          <button
            className="report-download-btn"
            onClick={handleDownloadPdf}
            disabled={!report || pdfLoading}
          >
            {pdfLoading ? <><Loader2 size={16} className="spin" /> Preparing PDF...</> : <><Download size={16} /> Download PDF</>}
          </button>
        </div>
      </section>

      {error && <div className="report-error">{error}</div>}

      {!report && !loading && (
        <div className="report-empty-state">
          <FileText size={40} />
          <p>Choose a date range and generate a report to see the full breakdown.</p>
        </div>
      )}

      {m && (
        <>
          {/* Headline row — the handful of numbers that matter most at a glance */}
          <section className="report-hero-row">
            <HeroStat label="Total Calls" value={m.callCount} sub={`${m.inbound} in / ${m.outbound} out`} icon={<Phone size={16} />} color={COLORS.blue} />
            <HeroStat label="Avg Quality Score" value={m.avgScore != null ? m.avgScore.toFixed(1) : "—"} sub={scoreBand(m.avgScore)} icon={<BarChart3 size={16} />} color={scoreColor(m.avgScore)} />
            <HeroStat label="Escalations" value={m.escalationItems.length} sub={`${m.rates.escalation}% of calls`} icon={<ShieldAlert size={16} />} color={COLORS.rose} />
            <HeroStat label="Open Follow-ups" value={m.openFollowUps.length} sub={`${m.overdueFollowUps.length} overdue`} icon={<Clock size={16} />} color={COLORS.amber} />
          </section>

          <div className="report-body-layout">
            <SectionNav items={NAV_ITEMS(reportType)} />

            <div className="report-print-area" ref={reportRef}>
              {/* 1. Executive Summary */}
              <Section id="sec-summary" title="Executive Summary" icon={<Sparkles size={18} />} badge={`${report.range.start} – ${report.range.end}`}>
                <div className="report-stat-grid">
                  <StatCard label="AI Analysis Done" value={m.analysisCount} sub={`of ${m.callCount} calls`} icon={<BrainCircuit size={18} />} color={COLORS.emerald} />
                  <StatCard label="New / Existing" value={`${m.clientType.new} / ${m.clientType.existing}`} icon={<Users size={18} />} color={COLORS.cyan} />
                  <StatCard label="Best Agent" value={m.bestAgent?.name || "—"} sub={m.bestAgent?.avgScore != null ? `${m.bestAgent.avgScore.toFixed(1)} avg` : null} icon={<Award size={18} />} color={COLORS.emerald} />
                  <StatCard label="Needs Attention" value={m.worstAgent?.name || "—"} sub={m.worstAgent?.avgScore != null ? `${m.worstAgent.avgScore.toFixed(1)} avg` : null} icon={<Frown size={18} />} color={COLORS.rose} />
                  <StatCard label="Attorney Attention" value={m.attorneyCalls.length} icon={<Scale size={18} />} color={COLORS.violet} />
                  <StatCard label="Positive Sentiment" value={`${m.rates.positiveSentiment}%`} sub={`${m.rates.negativeSentiment}% negative`} icon={<CheckCircle2 size={18} />} color={COLORS.emerald} />
                  <StatCard label="Total Escalations" value={m.escalationItems.length} icon={<ShieldAlert size={18} />} color={COLORS.rose} />
                </div>

                <div className="report-stat-grid" style={{ marginTop: 12 }}>
                  <YesNoKpiCard
                    title="Client Showed Signs of Concern or Anxiety"
                    icon={<AlertTriangle size={18} />}
                    color={COLORS.amber}
                    yesCount={m.concernAnxietyYesCount}
                    noCount={m.concernAnxietyNoCount}
                    onYesClick={() => openStatusModal("concern", "Yes")}
                    onNoClick={() => openStatusModal("concern", "No")}
                  />
                  <YesNoKpiCard
                    title="Assurance / Reassurance Provided by Agent"
                    icon={<CheckCircle2 size={18} />}
                    color={COLORS.emerald}
                    yesCount={m.reassuranceProvidedYesCount}
                    noCount={m.reassuranceProvidedNoCount}
                    onYesClick={() => openStatusModal("reassurance", "Yes")}
                    onNoClick={() => openStatusModal("reassurance", "No")}
                  />
                </div>
                <div className="report-obs">
                  <strong>Top SOP Gap:</strong> {topN(m.sopMissCounts, 1)[0]?.[0] || "No SOP misses recorded"}
                </div>

                {narrativeError && <div className="report-obs" style={{ color: COLORS.rose }}>{narrativeError}</div>}

                {narrative && (
                  <div className="report-ai-narrative" style={{ marginTop: 16 }}>
                    <div className="report-subhead" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <Sparkles size={14} /> AI Summary
                    </div>
                    <p style={{ lineHeight: 1.6 }}>{narrative.executive_summary}</p>

                    {Array.isArray(narrative.key_findings) && narrative.key_findings.length > 0 && (
                      <ul style={{ marginTop: 8, paddingLeft: 20 }}>
                        {narrative.key_findings.map((f, i) => <li key={i} style={{ marginBottom: 4 }}>{f}</li>)}
                      </ul>
                    )}

                    {narrative.agent_commentary && (
                      <p style={{ marginTop: 12, lineHeight: 1.6 }}><strong>Agent comparison:</strong> {narrative.agent_commentary}</p>
                    )}

                    {narrative.sentiment_commentary && (
                      <p style={{ marginTop: 8, lineHeight: 1.6 }}><strong>Sentiment:</strong> {narrative.sentiment_commentary}</p>
                    )}

                    {Array.isArray(narrative.priority_recommendations) && narrative.priority_recommendations.length > 0 && (
                      <div className="report-table-wrapper" style={{ marginTop: 12 }}>
                        <table className="tracker-table">
                          <thead><tr><th>Priority</th><th>Finding</th><th>Recommended Action</th></tr></thead>
                          <tbody>
                            {narrative.priority_recommendations.map((r, i) => (
                              <tr key={i}>
                                <td>
                                  <span style={{
                                    color: r.priority === "Critical" ? COLORS.rose : r.priority === "High" ? COLORS.amber : COLORS.slate,
                                    fontWeight: 700,
                                  }}>{r.priority}</span>
                                </td>
                                <td>{r.finding}</td>
                                <td>{r.action}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </Section>

              {/* 2. Call Volume & Distribution */}
              <Section id="sec-volume" title={reportType === "daily" ? "Call Volume & Distribution" : "Month-at-a-Glance"} icon={<Phone size={18} />}>
                <div className="report-two-col">
                  <div>
                    <div className="report-subhead">Calls by Agent</div>
                    <RankedList items={topN(agentCallsBreakdown(m.callsByAgent), 6)} total={m.analysisCount} />
                  </div>
                  <div>
                    <div className="report-subhead">Client Sentiment</div>
                    <ResponsiveContainer width="100%" height={220}>
                      <PieChart>
                        <Pie
                          data={[
                            { name: "Positive", value: m.sentiment.positive },
                            { name: "Neutral", value: m.sentiment.neutral },
                            { name: "Negative", value: m.sentiment.negative },
                          ]}
                          dataKey="value" nameKey="name" innerRadius={50} outerRadius={80}
                        >
                          {PIE_COLORS.map((c, i) => <Cell key={i} fill={c} />)}
                        </Pie>
                        <Tooltip /><Legend />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                </div>
                <div className="report-stat-grid" style={{ marginTop: 16 }}>
                  <StatCard label="Avg Call Duration" value={formatDuration(m.avgDurationSec)} icon={<Clock size={18} />} color={COLORS.violet} />
                  <StatCard label="Total Call Duration" value={formatDuration(m.totalDurationSec)} icon={<Clock size={18} />} color={COLORS.blue} />
                  <StatCard label="Voicemail Rate" value={`${m.rates.voicemail}%`} icon={<Phone size={18} />} color={COLORS.slate} />
                  <StatCard label="Lead Source Asked" value={`${m.rates.leadSourceAsked}%`} icon={<CheckCircle2 size={18} />} color={COLORS.blue} />
                  <StatCard label="Paid Consultation Pitch Rate" value={`${m.rates.consultationPitch}%`} icon={<TrendingUp size={18} />} color={COLORS.emerald} />
                </div>

                <div className="report-subhead" style={{ marginTop: 20 }}>Call Volume by Agent</div>
                <div className="report-table-wrapper">
                  <table className="tracker-table">
                    <thead>
                      <tr>
                        <th>Agent</th><th>Total</th><th>Inbound</th><th>Outbound</th>
                        <th>New</th><th>Existing</th><th>Voicemail %</th><th>Avg Duration</th>
                      </tr>
                    </thead>
                   <tbody>
                      {m.agentVolumeRows.filter((a) => !(a.name === "N/A" && a.calls === 0)).map((a) => (
                        <tr key={a.name}>
                          <td>{a.name}</td><td>{a.calls}</td><td>{a.inbound}</td><td>{a.outbound}</td>
                          <td>{a.newClients}</td><td>{a.existingClients}</td>
                          <td>{a.voicemailRate}%</td><td>{formatDuration(a.avgDurationSec)}</td>
                        </tr>
                      ))}
                      {(() => { const t = buildAgentVolumeTotal(m.agentVolumeRows); return (
                        <tr key="agent-volume-total" style={{ fontWeight: 700, borderTop: "2px solid var(--slate-600)" }}>
                          <td>{t.name}</td><td>{t.calls}</td><td>{t.inbound}</td><td>{t.outbound}</td>
                          <td>{t.newClients}</td><td>{t.existingClients}</td>
                          <td>{t.voicemailRate}%</td><td>{formatDuration(t.avgDurationSec)}</td>
                        </tr>
                      ); })()}
                    </tbody>
                  </table>
                </div>

                <div className="report-subhead" style={{ marginTop: 20 }}>Call Volume by Hour</div>
                <div style={{ width: "100%", height: 220 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={Object.entries(m.callsByHour).map(([h, calls]) => ({ hour: `${h}:00`, calls }))}
                      margin={{ top: 10, right: 10, left: -10, bottom: 0 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                      <XAxis dataKey="hour" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={{ stroke: "#334155" }} tickLine={false} />
                      <YAxis tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                      <Tooltip contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #334155", borderRadius: 8, color: "#e2e8f0", fontSize: 13 }} cursor={{ fill: "#1e293b" }} />
                      <Bar dataKey="calls" fill="#3b82f6" radius={[4, 4, 0, 0]} maxBarSize={40} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="report-table-wrapper">
                  <table className="tracker-table">
                    <thead>
                      <tr>
                        {Object.keys(m.callsByHour).map((h) => <th key={h}>{h}:00</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        {Object.values(m.callsByHour).map((c, i) => <td key={i}>{c}</td>)}
                      </tr>
                    </tbody>
                  </table>
                </div>
              </Section>

              {/* 3. Call Quality Performance */}
              <Section id="sec-quality" title="Call Quality Performance" icon={<BarChart3 size={18} />} badge={`${m.analysisCount} analyzed`}>
                <div className="report-two-col">
                  <div>
                    <div className="report-subhead">Quality Band Distribution</div>
                    <div className="quality-bars">
                      {Object.entries(m.scoreDist).map(([label, count]) => (
                        <div key={label} className="quality-bar-row">
                          <div className="quality-bar-info"><span className="quality-bar-label">{label}</span><span className="quality-bar-count">{count} of {m.analysisCount} calls ({pct(count, m.analysisCount)}%)</span></div>
                          <div className="quality-bar-track"><div className="quality-bar-fill" style={{ width: `${pct(count, m.analysisCount)}%`, backgroundColor: scoreColor(label === "Excellent" ? 9 : label === "Good" ? 7 : label === "Fair" ? 5 : 2) }} /></div>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="report-subhead">Tone Distribution</div>
                    <ToneDistributionChart toneDist={m.toneDist} dominantTone={m.dominantTone} />
                  </div>
                </div>

                <div className="report-subhead" style={{ marginTop: 20 }}>SOP &amp; Protocol Compliance</div>
                <ComplianceGrid
                  rows={[
                    { label: "SOP Scripts", count: m.sopCompliant, total: m.analysisCount },
                    { label: "Start Greeting", count: m.startGreeting, total: m.analysisCount },
                    { label: "End Greeting", count: m.endGreeting, total: m.analysisCount },
                    { label: "Empathy Shown", count: m.empathy, total: m.analysisCount },
                    { label: "Next Step Communicated", count: m.nextStep, total: m.analysisCount },
                    { label: "Call Closed Properly", count: m.closedProperly, total: m.analysisCount },
                    { label: "Hold Protocol", count: m.holdProtocol, total: m.analysisCount },
                  ]}
                />

                <div className="report-two-col" style={{ marginTop: 16 }}>
                  <div>
                    <div className="report-subhead">Top 5 SOP Steps Missed</div>
                    <RankedList items={topN(m.sopMissCounts, 5)} empty="No SOP misses recorded" total={m.analysisCount} />
                  </div>
                  <div>
                    <div className="report-subhead">Answer Quality Breakdown</div>
                    <RankedList items={topN(m.answerQualityDist, 5)} total={m.analysisCount} />
                  </div>
                </div>
              </Section>

              {/* 4. Agent Performance / Scorecards */}
              <Section id="sec-agents" title={reportType === "daily" ? "Agent Performance" : "Agent Scorecards"} icon={<Users size={18} />} badge={`${m.agents.length} agents`}>
                <div className="report-table-wrapper">
                  <table className="tracker-table">
                    <thead>
                      <tr>
                        <th>Agent</th><th>Calls</th><th>Avg Score</th><th>SOP %</th>
                        <th>Empathy %</th><th>Dominant Tone</th><th>+ Sentiment %</th>
                        <th>Follow-ups</th><th>Escalations</th><th>Consult Pitch %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {m.agents.filter((a) => !(a.name === "Unassigned" && a.calls === 0)).map((a) => (
                        <tr key={a.name}>
                          <td>{agentDisplayName(a.name)}</td>
                          <td>{a.calls}</td>
                          <td><span className="score-pill" style={{ color: scoreColor(a.avgScore) }}>{a.avgScore != null ? a.avgScore.toFixed(1) : "—"}</span></td>
                          <td>{a.sopComplianceRate}%</td>
                          <td>{a.empathyRate}%</td>
                          <td>{a.dominantTone}</td>
                          <td>{a.positiveSentimentRate}%</td>
                          <td>{a.followUps}</td>
                          <td>{a.escalations}</td>
                          <td>{a.consultationPitchRate}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Per-agent subsections — fixed template, always present even
                    for agents with zero calls, per spec 3.4. */}
                <div style={{ marginTop: 20, display: "flex", flexDirection: "column", gap: 16 }}>
                  {m.agents.filter((a) => !["Unassigned", "N/A", "Other"].includes(a.name)).map((a) => {
                    const prevAgent = reportType === "monthly" && p ? p.agents.find((pa) => pa.name === a.name) : null;
                    const scoreDelta = prevAgent && a.avgScore != null && prevAgent.avgScore != null
                      ? Math.round((a.avgScore - prevAgent.avgScore) * 10) / 10 : null;
                    return (
                      <div key={a.name} className="report-agent-card">
                        <div className="report-agent-card-head">
                          <span className="report-agent-card-name">{agentDisplayName(a.name)}</span>
                          {a.calls === 0 ? (
                            <span className="panel-badge">{reportType === "daily" ? "No calls handled on this date" : "No calls handled this month"}</span>
                          ) : (
                            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <span className="score-pill" style={{ color: scoreColor(a.avgScore) }}>
                                {a.avgScore != null ? a.avgScore.toFixed(1) : "—"} avg ({scoreBand(a.avgScore)})
                              </span>
                              {scoreDelta != null && (
                                <span style={{ fontSize: 12, fontWeight: 700, color: scoreDelta > 0 ? COLORS.emerald : scoreDelta < 0 ? COLORS.rose : COLORS.slate, display: "inline-flex", alignItems: "center", gap: 2 }}>
                                  {scoreDelta > 0 ? <TrendingUp size={13} /> : scoreDelta < 0 ? <TrendingDown size={13} /> : null}
                                  {scoreDelta > 0 ? "+" : ""}{scoreDelta} vs prior month
                                </span>
                              )}
                            </span>
                          )}
                        </div>
                        {a.calls > 0 && reportType === "monthly" && a.calls < 5 && (
                          <div className="report-obs" style={{ marginTop: 8, color: COLORS.amber }}>
                            Data sufficiency note: only {a.calls} call{a.calls === 1 ? "" : "s"} handled this month — metrics below may not be statistically reliable.
                          </div>
                        )}
                        {a.calls > 0 && (
                          <div className="report-two-col" style={{ marginTop: 10 }}>
                            <div>
                              <div className="report-subhead">Quality Band Distribution</div>
                              <RankedList items={Object.entries(a.scoreDist).filter(([, c]) => c > 0)} total={a.calls} />
                              <div className="report-subhead" style={{ marginTop: 12 }}>Top SOP Steps Missed</div>
                              <RankedList items={a.topSopMisses} empty="No SOP misses recorded" total={a.calls} />
                              {reportType === "monthly" && (
                                <>
                                  <div className="report-subhead" style={{ marginTop: 12 }}>Conversation Preparedness</div>
                                  <RankedList items={topN(a.preparednessDist, 5)} empty="No data" total={a.calls} />
                                </>
                              )}
                            </div>
                            <div>
                              <div className="report-subhead">SOP &amp; Protocol Compliance</div>
                              <ComplianceGrid
                                rows={[
                                  { label: "SOP Scripts", count: a.sopCompliant, total: a.calls },
                                  { label: "Start Greeting", count: a.startGreeting, total: a.calls },
                                  { label: "End Greeting", count: a.endGreeting, total: a.calls },
                                  { label: "Empathy Shown", count: a.empathy, total: a.calls },
                                  { label: "No. of Calls", value: a.calls, color: COLORS.cyan },
                                  { label: "Avg Call Duration", value: formatDuration(a.avgDurationSec), color: COLORS.violet },
                                  { label: "Total Call Duration", value: formatDuration(a.totalDurationSec), color: COLORS.blue },
                                ]}
                              />
                              <div className="report-subhead" style={{ marginTop: 12 }}>Answer Quality</div>
                              <RankedList items={topN(a.answerQualityDist, 5)} total={a.calls} />
                              {reportType === "monthly" && (
                                <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                                  Dominant tone: <strong style={{ color: "var(--text-primary)" }}>{a.dominantTone}</strong> · Consult pitch rate: <strong style={{ color: "var(--text-primary)" }}>{a.consultationPitchRate}% ({a.consultationsPitched} of {a.calls})</strong>
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                        {a.calls > 0 && a.observations.length > 0 && (
                          <div className="report-obs" style={{ marginTop: 10 }}>
                            {a.observations.map((o, i) => <div key={i}>{o}</div>)}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </Section>

              {/* Agent Comparison — multi-metric, all report types */}
              <Section title="Agent Comparison" icon={<Award size={18} />}>
                <p style={{ margin: "-4px 0 14px", fontSize: 13, color: "var(--text-secondary)" }}>
                  Each bar shows the % of that agent's own analyzed calls meeting the criterion — e.g. an SOP Scripts bar at 45% means the agent followed the SOP script on 45% of their calls (hover a bar to see the exact "N of Total calls" behind each %). Agents with no calls this period show no bars.
                </p>
                <ResponsiveContainer width="100%" height={Math.max(300, m.agents.length * 60) + 40}>
                  <BarChart data={m.agents.filter((a) => !["Unassigned", "N/A", "Other"].includes(a.name))} layout="vertical" margin={{ left: 24, right: 24, bottom: 28 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" domain={[0, 100]} label={{ value: "% of that agent's analyzed calls", position: "bottom", offset: 4, fill: "var(--text-secondary)", fontSize: 12 }} />
                    <YAxis type="category" dataKey="name" width={110} tickFormatter={(name) => agentDisplayName(name)} />
                    <Tooltip content={<AgentComparisonTooltip />} />
                    <Legend wrapperStyle={{ paddingTop: 20 }} />
                    <Bar dataKey="sopComplianceRate" name="SOP Scripts Followed %" fill={COLORS.blue} radius={[0, 4, 4, 0]} />
                    <Bar dataKey="empathyRate" name="Empathy Shown %" fill={COLORS.amber} radius={[0, 4, 4, 0]} />
                    <Bar dataKey="positiveSentimentRate" name="Positive Sentiment %" fill={COLORS.emerald} radius={[0, 4, 4, 0]} />
                    <Bar dataKey="consultationPitchRate" name="Consultation Pitched %" fill={COLORS.rose} radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </Section>

              {/* Agent Head-to-Head (monthly only) */}
              {reportType === "monthly" && (
                <Section title="Agent Head-to-Head Ranking" icon={<Award size={18} />}>
                  <ResponsiveContainer width="100%" height={Math.max(220, m.agents.length * 46)}>
                    <BarChart data={m.agents.filter((a) => !["Unassigned", "N/A", "Other"].includes(a.name))} layout="vertical" margin={{ left: 24 }}>
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" domain={[0, 10]} />
                      <YAxis type="category" dataKey="name" width={110} />
                      <Tooltip />
                      <Bar dataKey="avgScore" name="Avg Quality Score" radius={[0, 4, 4, 0]}>
                        {m.agents.map((a, i) => <Cell key={i} fill={scoreColor(a.avgScore)} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>

                  {(() => {
                    const rankMetrics = [
                      ["Avg Quality Score", (a) => a.avgScore ?? -1, (a) => a.avgScore != null ? a.avgScore.toFixed(1) : "—"],
                      ["SOP Scripts %", (a) => a.sopComplianceRate, (a) => `${a.sopComplianceRate}%`],
                      ["Empathy %", (a) => a.empathyRate, (a) => `${a.empathyRate}%`],
                      ["Start Greeting %", (a) => a.startGreetingRate, (a) => `${a.startGreetingRate}%`],
                      ["End Greeting %", (a) => a.endGreetingRate, (a) => `${a.endGreetingRate}%`],
                      ["+ Sentiment %", (a) => a.positiveSentimentRate, (a) => `${a.positiveSentimentRate}%`],
                      ["Consult Pitch %", (a) => a.consultationPitchRate, (a) => `${a.consultationPitchRate}%`],
                      ["Escalations", (a) => -a.escalations, (a) => a.escalations],
                      ["Follow-ups Raised", (a) => a.followUps, (a) => a.followUps],
                    ];
                    const topAgentName = (getVal) => [...m.agents].sort((x, y) => getVal(y) - getVal(x))[0]?.name;
                    const bottomAgentName = (getVal) => [...m.agents].sort((x, y) => getVal(x) - getVal(y))[0]?.name;
                    const bestOnEmpathy = topAgentName((a) => a.empathyRate);
                    const bestOnSop = topAgentName((a) => a.sopComplianceRate);
                    const mostConsistent = [...m.agents].sort((x, y) => (y.avgScore ?? 0) - (x.avgScore ?? 0))[0];
                    return (
                      <>
                                            <div className="report-table-wrapper" style={{ marginTop: 18 }}>
                      <table className="tracker-table">
                        <thead>
                          <tr><th>Metric</th>{m.agents.filter((a) => !["Unassigned", "N/A", "Other"].includes(a.name)).map((a) => <th key={a.name}>{agentDisplayName(a.name)}</th>)}</tr>
                            </thead>
                            <tbody>
                              {rankMetrics.map(([label, getVal, fmt]) => {
                                const top = topAgentName(getVal);
                                const bottom = bottomAgentName(getVal);
                                return (
                                  <tr key={label}>
                                    <td>{label}</td>
                                                                        {m.agents.filter((a) => !["Unassigned", "N/A", "Other"].includes(a.name)).map((a) => (
                                      <td key={a.name} style={{
                                        color: a.name === top ? COLORS.emerald : a.name === bottom ? COLORS.amber : undefined,
                                        fontWeight: a.name === top || a.name === bottom ? 700 : 400,
                                      }}>
                                        {fmt(a)}
                                      </td>
                                    ))}
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                        <div className="report-obs" style={{ marginTop: 12 }}>
                          {bestOnEmpathy} leads on empathy ({m.agents.find((a) => a.name === bestOnEmpathy)?.empathyRate}%)
                          {bestOnSop && bestOnSop !== bestOnEmpathy ? `, while ${bestOnSop} leads on SOP scripts (${m.agents.find((a) => a.name === bestOnSop)?.sopComplianceRate}%).` : "."}
                          {" "}{mostConsistent?.name} posts the highest overall average quality score this month at {mostConsistent?.avgScore != null ? mostConsistent.avgScore.toFixed(1) : "—"}.
                        </div>
                      </>
                    );
                  })()}
                </Section>
              )}

              {/* Quality Trend (monthly only) */}
              {reportType === "monthly" && report.dailySeries && (
                <Section title="Quality Trend Analysis" icon={<TrendingUp size={18} />}>
                  <div className="report-subhead">Daily Average Quality Score</div>
                  <ResponsiveContainer width="100%" height={240}>
                    <LineChart data={report.dailySeries}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="date" />
                      <YAxis domain={[0, 10]} />
                      <Tooltip />
                      <Line type="monotone" dataKey="avgScore" name="Avg Score" stroke={COLORS.blue} strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>

                  {(() => {
                    const withScores = report.dailySeries.filter((d) => d.callCount > 0);
                    const best = withScores.length ? [...withScores].sort((a, b) => b.avgScore - a.avgScore)[0] : null;
                    const worst = withScores.length ? [...withScores].sort((a, b) => a.avgScore - b.avgScore)[0] : null;
                    return (
                      <div className="report-stat-grid" style={{ marginTop: 14 }}>
                        <StatCard label="Best Performing Day" value={best ? best.date : "—"} sub={best ? `${best.avgScore.toFixed(1)} avg` : null} icon={<TrendingUp size={18} />} color={COLORS.emerald} />
                        <StatCard label="Worst Performing Day" value={worst ? worst.date : "—"} sub={worst ? `${worst.avgScore.toFixed(1)} avg` : null} icon={<TrendingDown size={18} />} color={COLORS.rose} />
                      </div>
                    );
                  })()}

                  {report.weeklySeries && report.weeklySeries.length > 0 && (
                    <>
                      <div className="report-subhead" style={{ marginTop: 20 }}>Weekly Average Quality Scores</div>
                      <div className="report-table-wrapper">
                        <table className="tracker-table">
                          <thead><tr><th>Week</th><th>Calls</th><th>Avg Score</th><th>Good/Excellent %</th><th>Fair/Poor %</th><th>Dominant Tone</th></tr></thead>
                          <tbody>
                            {report.weeklySeries.map((w) => (
                              <tr key={w.label}>
                                <td>{w.label}</td>
                                <td>{w.count}</td>
                                <td>{w.avgScore != null ? w.avgScore.toFixed(1) : "—"}</td>
                                <td>{w.goodExcellentRate}%</td>
                                <td>{w.poorBelowRate}%</td>
                                <td>{w.dominantTone}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      <div className="report-subhead" style={{ marginTop: 20 }}>SOP Step Miss Frequency by Week</div>
                      <div className="report-table-wrapper">
                        <table className="tracker-table">
                          <thead><tr><th>Week</th><th>Top SOP Misses</th></tr></thead>
                          <tbody>
                            {report.weeklySeries.map((w) => (
                              <tr key={w.label}>
                                <td>{w.label}</td>
                                <td>{w.topSopMisses.length ? w.topSopMisses.map(([step, c]) => `${step} (${c})`).join(", ") : "None recorded"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      <div className="report-subhead" style={{ marginTop: 20 }}>Tone Trend by Week</div>
                      <div className="report-table-wrapper">
                        <table className="tracker-table">
                          <thead><tr><th>Week</th>{topN(m.toneDist, 5).map(([t]) => <th key={t}>{t}</th>)}</tr></thead>
                          <tbody>
                            {report.weeklySeries.map((w) => (
                              <tr key={w.label}>
                                <td>{w.label}</td>
                                {topN(m.toneDist, 5).map(([t]) => <td key={t}>{pct(w.toneDist[t] || 0, w.count)}%</td>)}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </Section>
              )}

              {/* Month-over-Month Comparison */}
              {reportType === "monthly" && p && (
                <Section title="Month-over-Month Comparison" icon={<TrendingUp size={18} />}>
                  <div className="report-table-wrapper">
                    <table className="tracker-table">
                      <thead><tr><th>Metric</th><th>This Month</th><th>Last Month</th><th>Δ</th></tr></thead>
                      <tbody>
                        {[
                          ["Avg Quality Score", m.avgScore?.toFixed(1) ?? "—", p.avgScore?.toFixed(1) ?? "—", momDelta(m.avgScore, p.avgScore)],
                          ["SOP Scripts %", m.rates.sopCompliance, p.rates.sopCompliance, momDelta(m.rates.sopCompliance, p.rates.sopCompliance)],
                          ["Start Greeting %", m.rates.startGreeting, p.rates.startGreeting, momDelta(m.rates.startGreeting, p.rates.startGreeting)],
                          ["End Greeting %", m.rates.endGreeting, p.rates.endGreeting, momDelta(m.rates.endGreeting, p.rates.endGreeting)],
                          ["Empathy %", m.rates.empathy, p.rates.empathy, momDelta(m.rates.empathy, p.rates.empathy)],
                          ["Next Step Communicated %", m.rates.nextStep, p.rates.nextStep, momDelta(m.rates.nextStep, p.rates.nextStep)],
                          ["Call Closed Properly %", m.rates.closedProperly, p.rates.closedProperly, momDelta(m.rates.closedProperly, p.rates.closedProperly)],
                          ["Positive Sentiment %", m.rates.positiveSentiment, p.rates.positiveSentiment, momDelta(m.rates.positiveSentiment, p.rates.positiveSentiment)],
                          ["Negative Sentiment %", m.rates.negativeSentiment, p.rates.negativeSentiment, momDelta(m.rates.negativeSentiment, p.rates.negativeSentiment)],
                          ["Escalation Rate %", m.rates.escalation, p.rates.escalation, momDelta(m.rates.escalation, p.rates.escalation)],
                          ["Follow-up Open Rate %", m.rates.followUpOpen, p.rates.followUpOpen, momDelta(m.rates.followUpOpen, p.rates.followUpOpen)],
                        ].map(([label, curr, prev, delta]) => (
                          <tr key={label}>
                            <td>{label}</td><td>{curr}</td><td>{prev}</td>
                            <td>
                              {delta == null ? "—" : (
                                <span style={{ color: delta > 0 ? COLORS.emerald : delta < 0 ? COLORS.rose : COLORS.slate, display: "inline-flex", alignItems: "center", gap: 4 }}>
                                  {delta > 0 ? <TrendingUp size={14} /> : delta < 0 ? <TrendingDown size={14} /> : null}
                                  {delta > 0 ? "+" : ""}{delta}
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Section>
              )}

              {/* 5. Client Sentiment */}
                                         <Section id="sec-sentiment" title="Client Sentiment" icon={<Phone size={18} />} badge={`${m.positiveRate}% positive`}>
                <div className="report-stat-grid">
                  <StatCard label="Positive Rate" value={`${m.positiveRate}%`} sub="Satisfied + Relieved" icon={<CheckCircle2 size={18} />} color={COLORS.emerald} />
                  <StatCard
                    label="Negative Rate" value={`${m.negativeRate}%`} sub="Frustrated + Angry"
                    icon={<AlertTriangle size={18} />} color={m.negativeRate > 20 ? COLORS.rose : COLORS.amber}
                  />
                </div>
                {m.negativeRate > 20 && (
                  <div className="report-obs" style={{ marginTop: 10, color: COLORS.rose }}>
                    Flagged: negative sentiment rate is above the 20% threshold.
                  </div>
                )}

                <div className="report-subhead" style={{ marginTop: 18 }}>Sentiment by Agent</div>
                <div className="report-table-wrapper">
                  <table className="tracker-table">
                    <thead><tr><th>Agent</th>{SENTIMENT_BUCKETS.map((b) => <th key={b}>{b}</th>)}</tr></thead>
                    <tbody>
                      {m.agents.filter((a) => !["Unassigned", "N/A", "Other"].includes(a.name)).map((a) => (
                        <tr key={a.name}>
                          <td>{agentDisplayName(a.name)}</td>
                          {SENTIMENT_BUCKETS.map((b) => <td key={b}>{a.sentiment6[b] || 0}</td>)}
                        </tr>
                      ))}
                      <tr style={{ fontWeight: 700, borderTop: "2px solid var(--slate-600)" }}>
                        <td>Total</td>
                        {SENTIMENT_BUCKETS.map((b) => (
                          <td key={b}>{m.sentiment6[b] || 0}</td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>

                {reportType === "monthly" && report.weeklySeries && report.weeklySeries.length > 0 && (
                  <>
                    <div className="report-subhead" style={{ marginTop: 18 }}>Week-by-Week Sentiment Trend</div>
                    <div className="report-table-wrapper">
                      <table className="tracker-table">
                        <thead><tr><th>Week</th><th>Calls</th><th>Positive %</th><th>Negative %</th></tr></thead>
                        <tbody>
                          {report.weeklySeries.map((w) => (
                            <tr key={w.label}>
                              <td>{w.label}</td><td>{w.count}</td><td>{w.positiveRate}%</td><td>{w.negativeRate}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </Section>

              {/* 6. Follow-Up & Escalation */}
              <Section id="sec-followups" title="Follow-Up & Escalation Summary" icon={<Clock size={18} />} badge={`${m.followUpItems.length} total`}>
                <div className="report-stat-grid">
                  <StatCard label="Total Follow-ups" value={m.followUpItems.length} icon={<Clock size={18} />} color={COLORS.amber} />
                  <StatCard label="Open" value={m.openFollowUps.length} icon={<Clock size={18} />} color={COLORS.blue} />
                  <StatCard label="Overdue" value={m.overdueFollowUps.length} icon={<AlertTriangle size={18} />} color={COLORS.rose} />
                  <StatCard label="No Deadline Set" value={m.followUpsNoDeadline.length} sub={`${m.followUpsNoDeadlinePct}%`} icon={<Clock size={18} />} color={COLORS.slate} />
                  <StatCard label="Total Escalations" value={m.escalationItems.length} icon={<ShieldAlert size={18} />} color={COLORS.rose} />
                  <StatCard label="Follow-up Completion Rate" value={`${m.followUpCompletionRate}%`} icon={<CheckCircle2 size={18} />} color={COLORS.emerald} />
                </div>
                <div className="report-two-col" style={{ marginTop: 16 }}>
                  <div>
                    <div className="report-subhead">Follow-up Status</div>
                    <RankedList items={Object.entries(m.followUpStatusDist).filter(([, c]) => c > 0)} empty="No follow-ups recorded" total={m.followUpItems.length} />
                  </div>
                  <div>
                    <div className="report-subhead">Pending Actions</div>
                    <div className="report-stat-grid">
                      <StatCard label="Front Desk Actions Pending" value={m.frontDeskActionsPending} icon={<Clock size={18} />} color={COLORS.blue} />
                    </div>
                  </div>
                </div>
                <div className="report-two-col" style={{ marginTop: 16 }}>
                  <div>
                    <div className="report-subhead">Follow-ups by Agent</div>
                    <RankedList items={topN(m.agents.filter((a) => !["Unassigned", "N/A", "Other"].includes(a.name)).reduce((o, a) => ({ ...o, [a.name]: a.followUps }), {}), 8)} total={m.followUpItems.length} />
                  </div>
                  <div>
                    <div className="report-subhead">Escalation Target Breakdown</div>
                    <RankedList items={topN(m.escalationTargetCounts, 8)} empty="No escalations" total={m.escalationItems.length} />
                  </div>
                </div>
                <div className="report-two-col" style={{ marginTop: 16 }}>
                  <div>
                    <div className="report-subhead">Escalation Rate by Agent</div>
                    <div className="report-table-wrapper">
                      <table className="tracker-table">
                        <thead><tr><th>Agent</th><th>Escalations</th><th>Rate</th></tr></thead>
                        <tbody>
                          {[...m.escalationRateByAgent].sort((a, b) => b[2] - a[2]).map(([name, rate, count]) => (
                            <tr key={name}><td>{name}</td><td>{count}</td><td>{rate}%</td></tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <div>
                    <div className="report-subhead">Escalation Rate by Case Type</div>
                    <div className="report-table-wrapper">
                      <table className="tracker-table">
                        <thead><tr><th>Case Type</th><th>Escalations</th><th>Rate</th></tr></thead>
                        <tbody>
                          {m.escalationRateByCaseType.slice(0, 8).map(([ct, rate, count]) => (
                            <tr key={ct}><td>{ct}</td><td>{m.escalationsByCaseType[ct] || 0}</td><td>{rate}%</td></tr>
                          ))}
                          {!m.escalationRateByCaseType.length && (
                            <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)" }}>No case type data</td></tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </Section>


              {/* 7. Calls Requiring Attorney Attention */}
              <Section id="sec-legal" title="Calls Requiring Attorney Attention" icon={<Scale size={18} />} badge={`${m.attorneyCalls.length} calls`}>
                {m.attorneyCalls.length > 0 ? (
                  <div className="report-table-wrapper">
                    <table className="tracker-table">
                      <thead><tr><th>Date</th><th>Agent</th><th>Client</th><th>Case Type</th><th>Legal Concern</th><th>Escalation Target</th></tr></thead>
                      <tbody>
                        {m.attorneyCalls.map((row, idx) => (
                          <tr key={row.id ?? idx}>
                            <td>{row.call_recording_date || "—"}</td>
                            <td>{row.front_desk_person_name || "—"}</td>
                            <td>{row.client_name || "—"}</td>
                            <td><span className="case-badge">{row.case_type || "—"}</span></td>
                            <td>{llmField(row, K.legalConcern) || "—"}</td>
                            <td>{row.escalation_to || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <div className="tracker-empty"><CheckCircle2 size={32} /><p>No calls flagged for attorney attention.</p></div>}

                {reportType === "monthly" && (
                  <>
                    <div className="report-two-col" style={{ marginTop: 18 }}>
                      <div>
                        <div className="report-subhead">Breakdown by Case Type</div>
                        <RankedList items={topN(m.attorneyByCaseType, 8)} empty="No calls flagged" total={m.attorneyCalls.length} />
                      </div>
                      <div>
                        <div className="report-subhead">Breakdown by Agent</div>
                        <RankedList items={topN(m.attorneyByAgent, 8)} empty="No calls flagged" total={m.attorneyCalls.length} />
                      </div>
                    </div>

                    {m.attorneyByWeek && m.attorneyByWeek.length > 0 && (
                      <>
                        <div className="report-subhead" style={{ marginTop: 18 }}>Breakdown by Week</div>
                        <div className="report-table-wrapper">
                          <table className="tracker-table">
                            <thead><tr>{m.attorneyByWeek.map((w) => <th key={w.label}>{w.label}</th>)}</tr></thead>
                            <tbody>
                              <tr>{m.attorneyByWeek.map((w) => <td key={w.label}>{w.count}</td>)}</tr>
                            </tbody>
                          </table>
                        </div>
                      </>
                    )}

                    <div className="report-subhead" style={{ marginTop: 18 }}>Recurring Legal Issue Themes</div>
                    <RankedList items={topN(m.attorneyConcernThemes, 6)} empty="No legal concern notes recorded" total={m.attorneyCalls.length} />
                  </>
                )}
              </Section>

              {/* Case Type & Enquiry Insights (monthly) */}
              {reportType === "monthly" && (
                <Section title="Case Type & Enquiry Insights" icon={<FileText size={18} />}>
                  <div className="report-two-col">
                    <div>
                      <div className="report-subhead">Top 10 Case Types by Volume</div>
                      <RankedList items={topN(m.caseTypeCounts, 10)} total={m.analysisCount} />
                    </div>
                    <div>
                      <div className="report-subhead">Lead Source Breakdown</div>
                      <RankedList items={topN(m.leadSourceCounts, 10)} empty="No lead source data" total={m.clientType.new} />
                    </div>
                  </div>

                  <div className="report-two-col" style={{ marginTop: 20 }}>
                    <div>
                      <div className="report-subhead">Case Types by Most Escalations</div>
                      <RankedList items={topN(m.escalationsByCaseType, 8)} empty="No escalations recorded" total={m.escalationItems.length} />
                    </div>
                    <div>
                      <div className="report-subhead">New Client Enquiry Volume by Case Type</div>
                      <RankedList items={topN(m.newClientCaseTypeCounts, 8)} empty="No new-client calls recorded" total={m.clientType.new} />
                    </div>
                  </div>

                  <div className="report-subhead" style={{ marginTop: 20 }}>Case Types with Lowest Quality Scores</div>
                  <div className="report-table-wrapper">
                    <table className="tracker-table">
                      <thead><tr><th>Case Type</th><th>Avg Score</th><th>Calls</th></tr></thead>
                      <tbody>
                        {m.avgScoreByCaseType.slice(0, 8).map(([ct, avgS, count]) => (
                          <tr key={ct}>
                            <td>{ct}</td>
                            <td><span className="score-pill" style={{ color: scoreColor(avgS) }}>{avgS.toFixed(1)}</span></td>
                            <td>{count}</td>
                          </tr>
                        ))}
                        {!m.avgScoreByCaseType.length && (
                          <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)" }}>No scored calls</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>

                  <div className="report-subhead" style={{ marginTop: 20 }}>Case Type by Agent</div>
                  <div className="report-table-wrapper">
                    <table className="tracker-table">
                      <thead><tr><th>Agent</th>{topN(m.caseTypeCounts, 6).map(([ct]) => <th key={ct}>{ct}</th>)}</tr></thead>
                                            <tbody>
                        {m.agents.filter((a) => !["Unassigned", "N/A", "Other"].includes(a.name)).map((a) => (
                          <tr key={a.name}>
                            <td>{agentDisplayName(a.name)}</td>
                            {topN(m.caseTypeCounts, 6).map(([ct]) => <td key={ct}>{a.caseTypes[ct] || 0}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="report-subhead" style={{ marginTop: 20 }}>Lead Source by Agent</div>
                  <div className="report-table-wrapper">
                    <table className="tracker-table">
                      <thead><tr><th>Agent</th>{topN(m.leadSourceCounts, 6).map(([ls]) => <th key={ls}>{ls}</th>)}</tr></thead>
                                            <tbody>
                        {m.agents.filter((a) => !["Unassigned", "N/A", "Other"].includes(a.name)).map((a) => (
                          <tr key={a.name}>
                            <td>{agentDisplayName(a.name)}</td>
                            {topN(m.leadSourceCounts, 6).map(([ls]) => <td key={ls}>{a.leadSources[ls] || 0}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Section>
              )}

              {/* 8. Gap Analysis & Recommendations — AI-generated narrative
                  (spec 3.8 / 4.11) when "Generate AI Summary" has been run;
                  falls back to static data-derived bullets otherwise so the
                  section is never empty before the AI call is made. */}
              <Section id="sec-gaps" title={reportType === "daily" ? "Gap Analysis & Observations" : "Recurring Gaps & Recommendations"} icon={<AlertTriangle size={18} />}>
                {reportType === "daily" && narrative?.gap_analysis ? (
                  <>
                    <div className="report-subhead">Top Operational Gaps</div>
                    <ul className="report-obs-list">
                      {narrative.gap_analysis.top_gaps?.map((g, i) => <li key={i}>{g}</li>)}
                    </ul>
                    {narrative.gap_analysis.recurring_pattern && (
                      <p style={{ marginTop: 14, fontSize: 13, lineHeight: 1.6, color: "var(--text-secondary)" }}>
                        <strong style={{ color: "var(--text-primary)" }}>Recurring pattern: </strong>{narrative.gap_analysis.recurring_pattern}
                      </p>
                    )}
                    {narrative.gap_analysis.positive_standout && (
                      <p style={{ marginTop: 8, fontSize: 13, lineHeight: 1.6, color: "var(--text-secondary)" }}>
                        <strong style={{ color: "var(--text-primary)" }}>Positive standout: </strong>{narrative.gap_analysis.positive_standout}
                      </p>
                    )}
                    {narrative.gap_analysis.process_gap && (
                      <p style={{ marginTop: 8, fontSize: 13, lineHeight: 1.6, color: "var(--text-secondary)" }}>
                        <strong style={{ color: "var(--text-primary)" }}>Process/workflow gap: </strong>{narrative.gap_analysis.process_gap}
                      </p>
                    )}
                    {narrative.gap_analysis.recommendations?.length > 0 && (
                      <>
                        <div className="report-subhead" style={{ marginTop: 16 }}>Recommendations</div>
                        <ul className="report-obs-list">
                          {narrative.gap_analysis.recommendations.map((r, i) => <li key={i}>{r}</li>)}
                        </ul>
                      </>
                    )}
                  </>
                ) : reportType === "monthly" && narrative?.recurring_gaps ? (
                  <>
                    <div className="report-subhead">Top Recurring Operational Gaps</div>
                    <ul className="report-obs-list">
                      {narrative.recurring_gaps.top_gaps?.map((g, i) => <li key={i}>{g}</li>)}
                    </ul>
                    {narrative.recurring_gaps.persisted_patterns && (
                      <p style={{ marginTop: 14, fontSize: 13, lineHeight: 1.6, color: "var(--text-secondary)" }}>
                        <strong style={{ color: "var(--text-primary)" }}>Persisted from prior period: </strong>{narrative.recurring_gaps.persisted_patterns}
                      </p>
                    )}
                    {narrative.recurring_gaps.positive_trends && (
                      <p style={{ marginTop: 8, fontSize: 13, lineHeight: 1.6, color: "var(--text-secondary)" }}>
                        <strong style={{ color: "var(--text-primary)" }}>Positive trends: </strong>{narrative.recurring_gaps.positive_trends}
                      </p>
                    )}
                    {narrative.recurring_gaps.structural_issues && (
                      <p style={{ marginTop: 8, fontSize: 13, lineHeight: 1.6, color: "var(--text-secondary)" }}>
                        <strong style={{ color: "var(--text-primary)" }}>Structural/process issues: </strong>{narrative.recurring_gaps.structural_issues}
                      </p>
                    )}
                    {narrative.recurring_gaps.recommendations?.length > 0 && (
                      <>
                        <div className="report-subhead" style={{ marginTop: 16 }}>Prioritised Recommendations</div>
                        <div className="report-table-wrapper">
                          <table className="tracker-table">
                            <thead><tr><th>Priority</th><th>Action</th><th>Owner</th></tr></thead>
                            <tbody>
                              {narrative.recurring_gaps.recommendations.map((r, i) => (
                                <tr key={i}>
                                  <td>
                                    <span style={{ color: r.priority === "Critical" ? COLORS.rose : r.priority === "High" ? COLORS.amber : COLORS.slate, fontWeight: 700 }}>
                                      {r.priority}
                                    </span>
                                  </td>
                                  <td>{r.action}</td>
                                  <td>{r.owner}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </>
                    )}
                  </>
                ) : (
                  <>
                    <div className="report-obs" style={{ marginBottom: 14 }}>
                      This section is AI-generated narrative per spec. Click "Generate AI Summary" above to populate it — showing data-derived defaults below in the meantime.
                    </div>
                    <ul className="report-obs-list">
                      <li><strong>Top SOP gap:</strong> {topN(m.sopMissCounts, 1)[0]?.[0] || "None recorded"}</li>
                      <li><strong>Weakest compliance area:</strong> {
                        Object.entries({
                          "SOP Scripts": m.rates.sopCompliance, "Start Greeting": m.rates.startGreeting,
                          "End Greeting": m.rates.endGreeting, "Empathy": m.rates.empathy,
                          "Next Step Communicated": m.rates.nextStep, "Call Closed Properly": m.rates.closedProperly,
                          "Hold Protocol": m.rates.holdProtocol,
                        }).sort((a, b) => a[1] - b[1])[0]?.[0] || "—"
                      }</li>
                      <li><strong>Positive standout:</strong> {m.bestAgent ? `${m.bestAgent.name} — ${m.bestAgent.avgScore?.toFixed(1)} avg quality score` : "—"}</li>
                      <li><strong>Escalation rate:</strong> {m.rates.escalation}% of analyzed calls</li>
                      <li><strong>Overdue follow-ups needing action:</strong> {m.overdueFollowUps.length}</li>
                    </ul>
                  </>
                )}
              </Section>
            </div>
          </div>
        </>
      )}

      {currentStatusModal && (
        <div className="audio-modal-overlay" onClick={closeStatusModal}>
          <div
            className="audio-modal-card quality-modal-card"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 760, width: "92%" }}
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
                      <th>Call Date</th>
                      <th>Front Desk Person</th>
                      <th>Transcript Serial Number</th>
                      <th style={{ textAlign: "center" }}>Audio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {currentStatusItems.map((row, idx) => (
                      <tr key={row.id ?? row.call_reference ?? idx}>
                        <td>{idx + 1}</td>
                        <td>{row.call_reference || row.call_id || "—"}</td>
                        <td>
                          {(() => {
                            const d = new Date(row.call_recording_date);
                            return row.call_recording_date && !Number.isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : "—";
                          })()}
                        </td>
                        <td>{row.front_desk_person_name || "N/A"}</td>
                        <td>{getTranscriptSerialNumber(row)}</td>
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

      {/* Off-screen, white/print-themed version used only for PDF export.
          Kept out of normal layout flow (fixed + pushed off-viewport) but
          still rendered so html2canvas can capture real content/measurements. */}
      {report && (
        <div style={{ position: "fixed", top: 0, left: -10000, zIndex: -1 }}>
          <div ref={printRef}>
            <PrintableReport report={report} reportType={reportType} narrative={narrative} />
          </div>
        </div>
      )}
    </div>
  );
}

export default ReportAnalysis;