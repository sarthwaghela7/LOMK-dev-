// ─── Shared agent attribution logic ────────────────────────────────────────
// Single source of truth for "which of the firm's known front-desk agents
// handled this call", used by Dashboard.jsx, ReportAnalysis.jsx, and any
// future page that needs agent-wise call counts.
//
// WHY THIS FILE EXISTS
// Dashboard and Reports used to each keep their own copy of this fuzzy name
// matcher, and — more importantly — each walked a DIFFERENT base collection
// to build their per-agent counts:
//   - Dashboard looped over raw call records, and fell back to a call's own
//     (non-analysis) front_desk_person_name / agent_name / user_name field
//     whenever no call_analysis row could be matched by reference.
//   - Reports looped over call_analysis rows directly. Since the
//     call_analysis table has no uniqueness constraint on call_id, a
//     re-processed call could appear as more than one analysis row and get
//     counted more than once.
// Both problems produced different per-agent totals for the same date range
// even on days where "Total Calls" happened to match.
//
// The fix: call_analysis (the Call Analysis page's data source) is the ONLY
// source of truth for agent names — a call's own raw fields are never used
// as a fallback — and every call is deduplicated to at most one attributed
// analysis row before anything is counted.
//
// POLICY (locked in): "Calls Attended by Agent" is based purely on the
// front_desk_person_name column of call_analysis_results, fuzzy-matched to
// the 4 known agents. There is NO extension-based fallback/rescue for calls
// that have no analysis row, or whose analysis row doesn't resolve to a
// known agent. Those calls simply aren't attributed to anyone until the
// upstream pipeline tags them correctly — that's a data problem, not
// something this file should paper over.
//
// N/A vs Other (both are "not one of the 4 known agents", but distinct):
//   - N/A   = front_desk_person_name is blank/missing (no analysis row, or
//             an analysis row with an empty name).
//   - Other = front_desk_person_name IS present, but the value doesn't
//             fuzzy-match any of the 4 known agents (e.g. a typo'd/unknown
//             name, or literal "N/A" text entered by the pipeline).
// refToAgent below stores the raw resolved name for BOTH cases (never just
// silently drops "Other" names) so downstream code can tell them apart via
// isKnownAgent(resolved) — resolved is null only for the true-N/A case.

export const VALID_AGENT_NAMES = ["Shrishti", "Satish", "Gokul", "Manaswi"];

// Names within ~40% edit distance of a known agent's name snap to that
// agent (catches typos like "Sathish"→"Satish", "Shishthi"→"Shrishti").
// Anything further away is left alone and treated as NOT one of the 4.
const AGENT_MATCH_THRESHOLD = 0.4;

// NOTE: deliberately does NOT include "id". For a call_analysis row, "id"
// is that table's own surrogate primary key — an unrelated sequential
// integer — NOT a reference to the physical call. Since call_number is
// also a small sequential integer, including "id" here let an analysis
// row's PK collide with a completely unrelated call's call_number (e.g.
// analysis row id=42 could get treated as a candidate ref "42", which then
// matched any call whose call_number happened to also be 42), silently
// attributing that unrelated — often no-audio/unanalyzed — call to
// whichever agent the id=42 analysis row belonged to. CallRecords.jsx's own
// ref resolver never used "id" either, so dropping it here restores exact
// parity between the two.
const REF_KEYS = ["call_id", "formatted_call_id", "call_number", "call_reference"];

function levenshteinDistance(a, b) {
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1]
        : 1 + Math.min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1]);
    }
  }
  return dp[m][n];
}

export function canonicalizeAgentName(name) {
  const trimmed = String(name || "").trim();
  if (!trimmed) return "";

  const lower = trimmed.toLowerCase();
  const exact = VALID_AGENT_NAMES.find((agent) => agent.toLowerCase() === lower);
  if (exact) return exact;

  let bestAgent = null;
  let bestScore = Infinity;
  VALID_AGENT_NAMES.forEach((agent) => {
    const agentLower = agent.toLowerCase();
    const distance = levenshteinDistance(lower, agentLower);
    const normalized = distance / Math.max(agentLower.length, lower.length);
    if (normalized < bestScore) {
      bestScore = normalized;
      bestAgent = agent;
    }
  });

  return bestScore <= AGENT_MATCH_THRESHOLD ? bestAgent : trimmed;
}

// A call counts as "attended" only if its resolved name canonicalizes to one
// of the 4 known agents. Anything else — empty, literal N/A, "Unassigned",
// or a name that doesn't fuzzy-match any of the 4 — is NOT attended.
export function isKnownAgent(name) {
  return VALID_AGENT_NAMES.includes(canonicalizeAgentName(name));
}

function safeParseJson(value) {
  if (!value) return null;
  if (typeof value === "object") return value;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

// Every string a call/analysis row could be referenced by (call_id,
// call_number, call_reference, formatted id), lower-cased and trimmed. Used
// to match a call record to its call_analysis row(s). Deliberately excludes
// "id" and any digits-only variant — see REF_KEYS comment above.
export function getCallRefCandidates(obj) {
  if (!obj) return [];
  const nested = safeParseJson(obj.raw_llm_json);
  const source = nested && typeof nested === "object" ? { ...nested, ...obj } : obj;

  const candidates = new Set();
  REF_KEYS.forEach((key) => {
    const v = source[key];
    if (v === undefined || v === null) return;
    const str = String(v).trim().toLowerCase();
    if (!str) return;
    candidates.add(str);
  });
  return Array.from(candidates);
}

function rawAgentNameFromAnalysisRow(row) {
  return row.front_desk_person_name || null;
}

// Collapses call_analysis rows down to at most ONE row per physical call, so
// a re-processed/re-analyzed call is never counted twice.
//
// Resolution rule: the FIRST row encountered per call wins — no other
// tie-breaking. analysisRows arrives pre-sorted by call_recording_date
// descending (see /calls/analysis), so "first encountered" is simply "most
// recent analysis for this call". This mirrors EXACTLY how CallRecords.jsx
// resolves a call's analysis row (its analysisByRef map is also a plain
// first-wins map over the same pre-sorted array) — so Dashboard/Reports and
// Call Records always agree on which analysis row represents a call, and
// therefore always agree on its front_desk_person_name / agent.
//
// A previous version of this function preferred whichever duplicate row
// resolved to one of the 4 known agents, even if that row was a STALE
// re-processing artifact and not the current/most-recent analysis. That
// caused calls with a blank/unattributed front_desk_person_name in Call
// Records to still show up as "attended" by an agent on the Dashboard —
// which is exactly the mismatch this rule now prevents.
export function dedupeAnalysisRowsByCall(analysisRows) {
  const byRef = new Map();
  const order = [];

  (analysisRows || []).forEach((row, index) => {
    const refs = getCallRefCandidates(row);
    const key = refs[0] || `__norow_${index}`;
    if (!byRef.has(key)) {
      byRef.set(key, row);
      order.push(key);
    }
  });

  return order.map((key) => byRef.get(key));
}

// Builds the lookup structure every page needs to resolve "which agent
// handled this call": a call-ref -> agent map. The AGENT NAME comes
// EXCLUSIVELY from call_analysis_results.front_desk_person_name — there is
// no extension fallback and the calls table is never consulted here.
export function buildAgentAttribution(analysisRows) {
  const deduped = dedupeAnalysisRowsByCall(analysisRows);
  const refToAgent = {};

  deduped.forEach((row) => {
    // STRICT RULE: a call can only be attributed to an agent once its AI
    // analysis has actually completed. A row whose processing hasn't
    // finished (failed / pending / not_processed / queued / etc.) must
    // NEVER be mapped to an agent, even if front_desk_person_name happens
    // to already carry a (possibly stale/partial) value.
    if (!isAnalysisRowComplete(row)) return;

    const rawName = rawAgentNameFromAnalysisRow(row);
    const canonical = rawName ? canonicalizeAgentName(rawName) : null;
    // Only a truly blank name is dropped here (that's the N/A case). A
    // present-but-unrecognized name (the Other case) is still recorded so
    // callers can tell "no name" apart from "a name, just not one of the 4".
    if (!canonical) return;

    getCallRefCandidates(row).forEach((ref) => {
      const current = refToAgent[ref];
      if (!current || (!isKnownAgent(current) && isKnownAgent(canonical))) {
        refToAgent[ref] = canonical;
      }
    });
  });

  return { deduped, refToAgent };
}

// Resolves the agent for any call-like object using call_analysis (via
// refToAgent) as the ONLY source of truth. Returns null when nothing in
// call_analysis can be matched OR the matched row's name is blank — this
// is the true "N/A" case. Otherwise returns the resolved name, which may
// be one of the 4 known agents OR a present-but-unrecognized name (the
// "Other" case) — use isKnownAgent(result) to tell those two apart.
export function resolveAgentForCall(obj, { refToAgent }) {
  const refs = getCallRefCandidates(obj);
  for (const ref of refs) {
    if (refToAgent[ref]) return refToAgent[ref];
  }
  return null;
}

// ── "AI Analysis Done" — single shared definition ──────────────────────────
// A call_analysis row counts as AI-analysis-complete when its processing
// status EXPLICITLY says so. This is the single source of truth used by
// Dashboard.jsx and ReportAnalysis.jsx, so both pages always report the
// identical number for the same backend data.
//
// IMPORTANT: an explicit failure/pending status always wins, even if the
// row happens to carry a non-empty call_summary (e.g. a stale/partial
// summary left over from a prior attempt, or written before the pipeline
// errored out). Previously `hasSummary` was OR'd in unconditionally, which
// meant a row explicitly marked "failed" or "not_processed" could still be
// counted as complete purely because call_summary wasn't empty — silently
// inflating "AI Analysis Done" at the expense of "Audio Corrupted" /
// "Not Processed". `call_summary` presence is now used ONLY as a fallback
// when there is no usable status value to check at all.
const ANALYSIS_FAILURE_STATUSES = [
  "failed",
  "not_processed",
  "not processed",
  "error",
  "pending",
  "processing",
  "in_progress",
  "in progress",
  "queued",
];

const ANALYSIS_SUCCESS_STATUSES = ["processed", "complete", "completed", "true", "success"];

export function isAnalysisRowComplete(row) {
  const status = String(row?.processing_status || row?.status || "").toLowerCase().trim();

  // An explicit failure/pending status is authoritative — never overridden
  // by a leftover/partial call_summary.
  if (ANALYSIS_FAILURE_STATUSES.includes(status)) return false;

  if (ANALYSIS_SUCCESS_STATUSES.includes(status)) return true;

  // No usable status at all (empty, or a value we don't recognize either
  // way) — fall back to summary presence as the only remaining signal.
  const hasSummary = Boolean(row?.call_summary && String(row.call_summary).trim());
  return hasSummary;
}

export function computeAiAnalysisDone(analysisRows) {
  return (analysisRows || []).filter(isAnalysisRowComplete).length;
}