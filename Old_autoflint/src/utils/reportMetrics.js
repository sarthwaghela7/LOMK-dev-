import {
  VALID_AGENT_NAMES,
  buildAgentAttribution,
  resolveAgentForCall,
  isKnownAgent,
  computeAiAnalysisDone,
  isAnalysisRowComplete,
  getCallRefCandidates,
} from "./agentAttribution";

export const REPORT_AGENT_ORDER = ["Shrishti", "Manaswi", "Satish", "Gokul"];

export const SENTIMENT_BUCKETS = ["Satisfied", "Relieved", "Neutral", "Confused", "Frustrated", "Angry"];

export const FIXED_LEAD_SOURCES = [
  "Client Referral",
  "Ads/Brevo",
  "LOMK seminar/ workshops",
  "Google Search",
  "Youtube",
  "Instagram",
  "Website",
  "Friends",
  "Family",
  "Tamil Sangam",
  "Email",
  "Paid Consultation Client",
  "Not Applicable",
  "Other",
];

export const SCORE_MAX = 10;

export function safeParseJson(value) {
  if (!value) return null;
  if (typeof value === "object") return value;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

export function llmField(row, key) {
  const raw = safeParseJson(row?.raw_llm_json);
  if (raw && key in raw) return raw[key];
  return row?.[key];
}

export function isYes(value) {
  const v = String(value ?? "").trim().toLowerCase();
  return v === "yes" || v === "true" || v === "compliant" || v === "followed" || v === "complete";
}

export function isNAValue(value) {
  const s = String(value ?? "").trim().toLowerCase();
  return !s || s === "n/a" || s === "na" || s === "not applicable" || s === "none";
}

export function pct(numerator, denominator) {
  if (!denominator) return 0;
  return Math.round((numerator / denominator) * 100);
}

export function avg(nums) {
  const valid = (nums || []).filter((n) => Number.isFinite(n));
  if (!valid.length) return null;
  return valid.reduce((a, b) => a + b, 0) / valid.length;
}

export function parseScore(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function scoreBand(score) {
  if (score === null) return "Unscored";
  if (score >= 8) return "Excellent";
  if (score >= 6) return "Good";
  if (score >= 4) return "Fair";
  return "Poor";
}

export function scoreColor(score) {
  if (score === null) return "var(--slate-400)";
  if (score >= 8) return "var(--emerald-400)";
  if (score >= 6) return "var(--blue-400)";
  if (score >= 4) return "var(--amber-400)";
  return "var(--rose-400)";
}

export function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return secs > 0 ? `${minutes}m ${secs}s` : `${minutes}m`;
  return `${secs}s`;
}

const SENTIMENT_NEGATIVE = ["frustrated", "confused", "angry", "dissatisfied", "upset", "annoyed", "displeased", "unhappy", "disappointed", "worried", "concerned", "stressed", "irritated"];
const SENTIMENT_POSITIVE = ["relieved", "satisfied", "happy", "pleased", "delighted", "grateful", "content", "comfortable", "confident", "hopeful", "positive", "good", "great", "excellent"];

export function classifySentiment(rawSentiment) {
  if (!rawSentiment) return "neutral";
  const s = String(rawSentiment).toLowerCase().trim();
  if (SENTIMENT_NEGATIVE.some((word) => s.includes(word))) return "negative";
  if (SENTIMENT_POSITIVE.some((word) => s.includes(word))) return "positive";
  if (s.includes("neutral")) return "neutral";
  return "neutral";
}

export function sentimentBucket(raw) {
  const s = String(raw || "").trim().toLowerCase();
  const match = SENTIMENT_BUCKETS.find((bucket) => s === bucket.toLowerCase() || s.includes(bucket.toLowerCase()));
  return match || "Other";
}

export function emptySentiment6() {
  return { Satisfied: 0, Relieved: 0, Neutral: 0, Confused: 0, Frustrated: 0, Angry: 0, Other: 0 };
}

export function topN(counts, n = 5) {
  return Object.entries(counts || {}).sort((a, b) => b[1] - a[1]).slice(0, n);
}

// Direction-identifying numbers used across the project.
// If any of these are present in `src` → outbound.
// If any of these are present in `dst` → inbound.
const DIRECTION_NUMBERS = ["2243374447", "3128781255", "101", "102"];

function normalizePhoneForDirection(value) {
  const digits = String(value || "").replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("1")) return digits.slice(1);
  return digits;
}

const DIRECTION_NUMBERS_NORMALIZED = DIRECTION_NUMBERS.map(normalizePhoneForDirection);

function matchesDirectionNumber(field) {
  if (field == null || field === "") return false;
  const raw = String(field).trim();
  const normalized = normalizePhoneForDirection(raw);

  // Exact/normalized number match for phone-like values.
  if (normalized && DIRECTION_NUMBERS_NORMALIZED.includes(normalized)) return true;

  // SIP/PBX style extension match, e.g. 101@domain or trunk/102.
  return ["101", "102"].some((ext) => raw === ext || raw.startsWith(ext + "@") || raw.endsWith("/" + ext));
}

export function getCallDirection(obj) {
  if (!obj) return null;

  // Extension-based heuristic: checked first so it takes priority over
  // ambiguous text-field values. If the calling party (src) is an agent
  // extension, the call was dialled outward by the agent → outbound.
  // If the receiving party (dst) is an agent extension, an external caller
  // rang the office and the agent answered → inbound.
  if (matchesDirectionNumber(obj.src)) return "outbound";
  if (matchesDirectionNumber(obj.dst)) return "inbound";

  const possibleKeys = [
    "call_type", "direction", "call_direction", "type", "call_type_inbound_outbound",
    "inbound_outbound", "direction_type", "call_category", "type_of_call",
    "call_direction_type", "is_inbound", "is_outbound", "call_type_id",
  ];

  for (const key of possibleKeys) {
    if (obj[key] != null && obj[key] !== "") {
      const val = obj[key];
      if (typeof val === "boolean") {
        if (key === "is_inbound") return val ? "inbound" : "outbound";
        if (key === "is_outbound") return val ? "outbound" : "inbound";
      }
      const s = String(val).toLowerCase();
      if (s.includes("inbound") || s.includes("incoming") || s === "in") return "inbound";
      if (s.includes("outbound") || s.includes("outgoing") || s === "out") return "outbound";
    }
  }

  const raw = safeParseJson(obj.raw_llm_json);
  if (raw && typeof raw === "object") {
    for (const key of possibleKeys) {
      if (raw[key] != null && raw[key] !== "") {
        const val = raw[key];
        if (typeof val === "boolean") {
          if (key === "is_inbound") return val ? "inbound" : "outbound";
          if (key === "is_outbound") return val ? "outbound" : "inbound";
        }
        const s = String(val).toLowerCase();
        if (s.includes("inbound") || s.includes("incoming") || s === "in") return "inbound";
        if (s.includes("outbound") || s.includes("outgoing") || s === "out") return "outbound";
      }
    }
  }

  const fallback = llmField(obj, "call_direction");
  if (fallback != null && fallback !== "") {
    const s = String(fallback).toLowerCase();
    if (s.includes("inbound") || s.includes("incoming")) return "inbound";
    if (s.includes("outbound") || s.includes("outgoing")) return "outbound";
  }

  return null;
}

export function getClientPhoneNumber(obj) {
  if (!obj) return null;
  const src = String(obj.src || "").trim();
  const dst = String(obj.dst || "").trim();
  const srcIsSystem = matchesDirectionNumber(src);
  const dstIsSystem = matchesDirectionNumber(dst);
  const srcUnknown = !src || src.toLowerCase() === "unknown";
  const dstUnknown = !dst || dst.toLowerCase() === "unknown";

  if (dstIsSystem && !srcUnknown) return src;
  if (srcIsSystem && !dstUnknown) return dst;
  return obj.client_phone_number || obj.phone_number || null;
}

const DURATION_KEYS = ["duration", "call_duration", "talk_duration", "talk_time", "call_length", "billsec", "duration_seconds", "call_duration_seconds", "recording_duration", "call_time"];

function parseDurationSeconds(value) {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const s = String(value).trim();
  if (!s) return null;
  if (/^\d{1,3}:\d{1,2}(:\d{1,2})?$/.test(s)) {
    const parts = s.split(":").map(Number);
    if (parts.some((part) => !Number.isFinite(part))) return null;
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return parts[0] * 3600 + parts[1] * 60 + parts[2];
  }
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

export function getDurationSeconds(obj) {
  if (!obj) return null;
  for (const key of DURATION_KEYS) {
    const value = parseDurationSeconds(obj[key]);
    if (value !== null) return value;
  }
  const nested = safeParseJson(obj.raw_llm_json);
  if (nested && typeof nested === "object") {
    for (const key of DURATION_KEYS) {
      const value = parseDurationSeconds(nested[key]);
      if (value !== null) return value;
    }
  }
  return null;
}

export function extractHour(call) {
  const raw = call?.call_time || call?.call_recording_time || call?.call_start_time || llmField(call, "call_time");
  if (!raw) return null;
  const str = String(raw).trim();
  const hm = str.match(/(\d{1,2}):(\d{2})/);
  if (hm) {
    const h = Number(hm[1]);
    return Number.isFinite(h) && h >= 0 && h <= 23 ? h : null;
  }
  const d = new Date(str);
  return Number.isNaN(d.getTime()) ? null : d.getHours();
}

export function isResolvedFollowUp(status) {
  return ["done", "finished", "complete", "completed", "resolved", "closed"].includes(String(status || "").trim().toLowerCase());
}

const COMPLIANCE_FIELD_MAP = {
  sopCompliant: "script_sop_followed",
  startGreeting: "conversation_start_greeting",
  endGreeting: "conversation_end_greeting",
  empathyShown: "acknowledging_client_requirement_empathy",
  nextStepCommunicated: "next_step_communicated_to_client",
  callClosedProperly: "call_closed_properly",
  holdProtocolFollowed: "hold_protocol_followed",
  leadSourceAsked: "lead_source_asked",
  paidConsultationPitched: "paid_consultation_pitched",
};

function complianceRate(rows, key) {
  const field = COMPLIANCE_FIELD_MAP[key];
  if (!field) return { rate: 0, compliant: 0, eligible: 0 };
  const eligible = (rows || []).filter((row) => !isNAValue(llmField(row, field)));
  const compliant = eligible.filter((row) => isYes(llmField(row, field))).length;
  return { rate: pct(compliant, eligible.length), compliant, eligible: eligible.length };
}

export function vmuStatus(row) {
  const raw = llmField(row, "voicemail_left_if_no_answer");
  const value = String(raw || "").trim().toLowerCase();
  if (value === "yes") return "yes";
  if (value === "no") return "no";
  return null;
}

function normalizeYesNo(value) {
  const v = String(value || "").trim().toLowerCase();
  if (v === "yes" || v === "true" || v === "y") return "Yes";
  if (v === "no" || v === "false" || v === "n") return "No";
  return null;
}

export function concernAnxietyStatus(row) {
  const raw = row?.client_showed_signs_of_concern_or_anxiety
    ?? llmField(row, "client_showed_signs_of_concern_or_anxiety")
    ?? row?.client_concern_or_anxiety
    ?? llmField(row, "client_concern_or_anxiety");
  return normalizeYesNo(raw);
}

export function reassuranceProvidedStatus(row) {
  const raw = row?.assurance_reassurance_provided_by_agent
    ?? llmField(row, "assurance_reassurance_provided_by_agent")
    ?? row?.reassurance_provided
    ?? llmField(row, "reassurance_provided");
  return normalizeYesNo(raw);
}

export function classifyLeadSource(raw) {
  const s = String(raw || "").trim().toLowerCase();
  if (!s || s === "n/a" || s === "na" || s === "not applicable" || s === "unknown") return "Not Applicable";
  if (s.includes("paid consultation")) return "Paid Consultation Client";
  if (s.includes("referral")) return "Client Referral";
  if (s.includes("brevo") || s.includes("ads") || s.includes("advert")) return "Ads/Brevo";
  if (s.includes("lomk") || s.includes("seminar") || s.includes("workshop")) return "LOMK seminar/ workshops";
  if (s.includes("google")) return "Google Search";
  if (s.includes("youtube")) return "Youtube";
  if (s.includes("instagram")) return "Instagram";
  if (s.includes("website")) return "Website";
  if (s.includes("friend")) return "Friends";
  if (s.includes("family")) return "Family";
  if (s.includes("tamil sangam") || s.includes("sangam")) return "Tamil Sangam";
  if (s.includes("email")) return "Email";
  return "Other";
}

export function buildAnalysisStatusSummary(rows) {
  const analysisCount = computeAiAnalysisDone(rows);
  return {
    processed: analysisCount,
    pending: Math.max(0, (rows || []).length - analysisCount),
    total: (rows || []).length,
  };
}

export function buildDailySeries(analysisRows) {
  const byDay = {};
  (analysisRows || []).forEach((row) => {
    const date = row.call_recording_date;
    if (!date) return;
    if (!byDay[date]) byDay[date] = { scores: [], count: 0 };
    byDay[date].count++;
    const score = parseScore(row.overall_call_quality_score);
    if (score !== null) byDay[date].scores.push(score);
  });

  return Object.entries(byDay)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, d]) => ({ date: date.slice(5), avgScore: avg(d.scores) ?? 0, callCount: d.count }));
}

export function buildWeeklySeries(analysisRows) {
  const dated = (analysisRows || []).filter((row) => row.call_recording_date);
  if (!dated.length) return [];
  const firstDate = dated.map((row) => row.call_recording_date).sort()[0];
  const start = new Date(`${firstDate}T00:00:00`);

  const weeks = {};
  dated.forEach((row) => {
    const d = new Date(`${row.call_recording_date}T00:00:00`);
    const dayOffset = Math.floor((d - start) / 86400000);
    const weekIndex = Math.max(0, Math.floor(dayOffset / 7));
    if (!weeks[weekIndex]) {
      weeks[weekIndex] = {
        weekIndex,
        count: 0,
        scores: [],
        sentimentPos: 0,
        sentimentNeg: 0,
        sopMisses: {},
        toneDist: {},
        scoreBandCounts: { Excellent: 0, Good: 0, Fair: 0, Poor: 0 },
      };
    }
    const week = weeks[weekIndex];
    week.count++;
    const score = parseScore(row.overall_call_quality_score);
    if (score !== null) {
      week.scores.push(score);
      week.scoreBandCounts[scoreBand(score)]++;
    }
    const bucket = sentimentBucket(row.client_sentiment);
    if (bucket === "Satisfied" || bucket === "Relieved") week.sentimentPos++;
    if (bucket === "Frustrated" || bucket === "Angry") week.sentimentNeg++;
    const missed = llmField(row, "sop_steps_missed");
    if (Array.isArray(missed)) missed.forEach((item) => { if (!isNAValue(item)) week.sopMisses[item] = (week.sopMisses[item] || 0) + 1; });
    const tone = llmField(row, "tone_conversation_check");
    if (!isNAValue(tone)) week.toneDist[tone] = (week.toneDist[tone] || 0) + 1;
  });

  return Object.values(weeks)
    .sort((a, b) => a.weekIndex - b.weekIndex)
    .map((week) => ({
      label: `Week ${week.weekIndex + 1}`,
      count: week.count,
      avgScore: avg(week.scores),
      positiveRate: pct(week.sentimentPos, week.count),
      negativeRate: pct(week.sentimentNeg, week.count),
      goodExcellentRate: pct(week.scoreBandCounts.Excellent + week.scoreBandCounts.Good, week.count),
      poorBelowRate: pct(week.scoreBandCounts.Poor + week.scoreBandCounts.Fair, week.count),
      topSopMisses: topN(week.sopMisses, 3),
      toneDist: week.toneDist,
      dominantTone: topN(week.toneDist, 1)[0]?.[0] || "—",
    }));
}

export function buildPreviousRange(start, end) {
  const dayCount = daysBetween(start, end);
  const s = new Date(`${start}T00:00:00`);
  const prevEnd = new Date(s);
  prevEnd.setDate(prevEnd.getDate() - 1);
  const prevStart = new Date(prevEnd);
  prevStart.setDate(prevStart.getDate() - (dayCount - 1));
  return { prevStart: formatDate(prevStart), prevEnd: formatDate(prevEnd) };
}

export function daysBetween(start, end) {
  const s = new Date(`${start}T00:00:00`);
  const e = new Date(`${end}T00:00:00`);
  if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime())) return 0;
  return Math.round((e - s) / 86400000) + 1;
}

export function formatDate(date) {
  return date.toISOString().slice(0, 10);
}

function deriveMonthLabel(records) {
  const dated = (records || [])
    .map((row) => row?.call_recording_date || row?.call_date || row?.date || row?.created_at)
    .map((value) => {
      if (!value) return null;
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? null : date;
    })
    .filter(Boolean);

  const anchor = dated.length ? dated.sort((a, b) => b - a)[0] : new Date();
  return anchor.toLocaleString("en-US", { month: "long", year: "numeric" });
}

function hasAudioOnCall(call) {
  const audioRef = call?.audio_s3_uri || call?.audio_s3_key || call?.audio_url || call?.recording_url || call?.audio_path;
  return Boolean(String(audioRef || "").trim());
}

function enrichUiRow(baseRow, recordRow, fallbackAgent) {
  const base = baseRow || {};
  const record = recordRow || {};
  return {
    ...base,
    call_reference: base.call_reference || record.call_reference || record.formatted_call_id || base.call_id || record.call_id || null,
    call_id: base.call_id || record.call_id || record.id || null,
    call_recording_date: base.call_recording_date || base.call_date || record.call_recording_date || record.call_date || record.date || base.created_at || record.created_at || null,
    front_desk_person_name: base.front_desk_person_name || base.agent_name || record.front_desk_person_name || fallbackAgent || "Unassigned",
    transcript: base.transcript || record.transcript || "",
    audio_s3_uri: base.audio_s3_uri || record.audio_s3_uri || record.audio_s3_key || "",
  };
}

function createAgentVolumeRow(name) {
  return {
    name,
    calls: 0,
    inbound: 0,
    outbound: 0,
    newClients: 0,
    existingClients: 0,
    voicemail: 0,
    durations: [],
    avgDurationSec: null,
    voicemailRate: 0,
  };
}

function volumeBucketForAgent(name) {
  if (!name || name === "Unassigned" || isNAValue(name)) return "N/A";
  if (VALID_AGENT_NAMES.includes(name)) return name;
  return "Other";
}

function resolveAnalysisByRef(records, analysis) {
  const recordByRef = {};
  const recordById = {};
  (records || []).forEach((call) => {
    getCallRefCandidates(call).forEach((ref) => {
      if (!recordByRef[ref]) recordByRef[ref] = call;
    });
    const cid = call.call_id ?? call.id ?? null;
    if (cid != null) recordById[cid] = call;
  });

  const analysisByRef = {};
  const analysisById = {};
  (analysis || []).forEach((row) => {
    getCallRefCandidates(row).forEach((ref) => {
      if (!analysisByRef[ref]) analysisByRef[ref] = row;
    });
    const cid = row.call_id ?? row.id ?? null;
    if (cid != null && !analysisById[cid]) analysisById[cid] = row;
  });

  return { recordByRef, recordById, analysisByRef, analysisById };
}

export function scopeAnalysisToRecords(records, analysisRows) {
  const validCallRefs = new Set();
  (records || []).forEach((call) => {
    getCallRefCandidates(call).forEach((ref) => validCallRefs.add(ref));
  });

  return (analysisRows || []).filter((row) => {
    const candidates = getCallRefCandidates(row);
    return candidates.some((candidate) => validCallRefs.has(candidate));
  });
}

export function buildMetrics(recordsInput, rawAnalysisInput, options = {}) {
  const records = Array.isArray(recordsInput) ? recordsInput : [];
  const { deduped: analysis, refToAgent } = buildAgentAttribution(rawAnalysisInput);
  const analysisCompleted = analysis.filter(isAnalysisRowComplete);
  const total = records.length;
  const callCount = total;
  const customLeadSources = Array.isArray(options?.customLeadSources)
    ? options.customLeadSources.filter((name) => String(name || "").trim())
    : [];
  const { recordByRef, recordById, analysisByRef, analysisById } = resolveAnalysisByRef(records, analysis);

  const findAnalysisForRecord = (call) => {
    for (const ref of getCallRefCandidates(call)) {
      if (analysisByRef[ref]) return analysisByRef[ref];
    }
    const cid = call?.call_id ?? call?.id ?? null;
    if (cid != null && analysisById[cid]) return analysisById[cid];
    return null;
  };

  const agentVolumeMap = {};
  analysis.forEach((row) => {
    const resolved = resolveAgentForCall(row, { refToAgent }) || "Unassigned";
    const bucket = volumeBucketForAgent(resolved);
    if (!agentVolumeMap[bucket]) agentVolumeMap[bucket] = createAgentVolumeRow(bucket);

    const volumeRow = agentVolumeMap[bucket];
    volumeRow.calls += 1;

    const refs = getCallRefCandidates(row);
    const linkedRecord = refs.map((ref) => recordByRef[ref]).find(Boolean)
      || (row.call_id != null ? recordById[row.call_id] || null : null);

    const direction = linkedRecord ? getCallDirection(linkedRecord) : getCallDirection(row);
    if (direction === "inbound") volumeRow.inbound += 1;
    else if (direction === "outbound") volumeRow.outbound += 1;

    const clientType = String(row.new_client_existing_client || "").toLowerCase();
    if (clientType.includes("new")) volumeRow.newClients += 1;
    else if (clientType.includes("existing")) volumeRow.existingClients += 1;

    if (vmuStatus(row) !== null) volumeRow.voicemail += 1;

    let duration = linkedRecord ? getDurationSeconds(linkedRecord) : null;
    if (duration === null) duration = getDurationSeconds(row);
    if (duration !== null) volumeRow.durations.push(duration);
  });

  const agentVolumeRows = [...REPORT_AGENT_ORDER, "Other", "N/A"].map((name) => {
    const row = agentVolumeMap[name] || createAgentVolumeRow(name);
    const avgDurationSec = avg(row.durations);
    return {
      ...row,
      avgDurationSec,
      voicemailRate: row.outbound > 0 ? pct(row.voicemail, row.outbound) : 0,
    };
  });

  const callsByAgent = agentVolumeRows.reduce((acc, row) => {
    acc[row.name] = row.calls;
    return acc;
  }, {});

  let inbound = records.filter((record) => getCallDirection(record) === "inbound").length;
  let outbound = records.filter((record) => getCallDirection(record) === "outbound").length;
  if (inbound === 0 && outbound === 0 && records.length > 0) {
    inbound = analysis.filter((row) => getCallDirection(row) === "inbound").length;
    outbound = analysis.filter((row) => getCallDirection(row) === "outbound").length;
  }

  const vmuRows = analysis
    .map((row) => {
      const status = vmuStatus(row);
      if (!status) return null;
      const refs = getCallRefCandidates(row);
      const record = refs.map((ref) => recordByRef[ref]).find(Boolean)
        || (row.call_id != null ? recordById[row.call_id] || null : null);
      return {
        ...enrichUiRow(row, record, resolveAgentForCall(row, { refToAgent }) || "Unassigned"),
        statusLabel: status === "yes" ? "Yes" : "No",
      };
    })
    .filter(Boolean);

  const noAudioCallItems = [];
  const audioCorruptedCallItems = [];
  let analysisCount = 0;
  records.forEach((call) => {
    const linkedAnalysis = findAnalysisForRecord(call);
    const resolvedAgent = resolveAgentForCall(call, { refToAgent }) || "Unassigned";
    if (linkedAnalysis && isAnalysisRowComplete(linkedAnalysis)) {
      analysisCount += 1;
      return;
    }

    const uiRow = enrichUiRow(linkedAnalysis, call, resolvedAgent);
    if (!hasAudioOnCall(call)) noAudioCallItems.push(uiRow);
    else audioCorruptedCallItems.push(uiRow);
  });

  const vmuYesCount = vmuRows.filter((row) => row.statusLabel === "Yes").length;
  const vmuNoCount = vmuRows.filter((row) => row.statusLabel === "No").length;
  const voicemail = vmuRows.length;

  const clientType = { new: 0, existing: 0, na: 0 };
  analysisCompleted.forEach((row) => {
    const type = String(row.new_client_existing_client || "").toLowerCase();
    if (type.includes("new")) clientType.new += 1;
    else if (type.includes("existing")) clientType.existing += 1;
    else clientType.na += 1;
  });

  const scores = analysisCompleted.map((row) => parseScore(row.overall_call_quality_score)).filter((score) => score !== null);
  const avgScore = avg(scores);
  const scoreDist = { Excellent: 0, Good: 0, Fair: 0, Poor: 0 };
  scores.forEach((score) => {
    scoreDist[scoreBand(score)] += 1;
  });

  const sentiment = { positive: 0, neutral: 0, negative: 0 };
  const sentiment6 = emptySentiment6();
  analysisCompleted.forEach((row) => {
    const classification = classifySentiment(row.client_sentiment);
    sentiment[classification] += 1;
    sentiment6[sentimentBucket(row.client_sentiment)] += 1;
  });
  const positiveRate = pct(sentiment.positive, analysisCompleted.length || total);
  const negativeRate = pct(sentiment.negative, analysisCompleted.length || total);

  const callsByHour = {};
  for (let hour = 0; hour < 24; hour += 1) callsByHour[hour] = 0;
  records.forEach((record) => {
    const hour = extractHour(record);
    if (hour !== null) callsByHour[hour] = (callsByHour[hour] || 0) + 1;
  });

  let durations = records.map((record) => getDurationSeconds(record)).filter((duration) => duration !== null);
  if (durations.length === 0) durations = analysis.map((row) => getDurationSeconds(row)).filter((duration) => duration !== null);
  const avgDurationSec = avg(durations);
  const totalDurationSec = durations.reduce((sum, duration) => sum + duration, 0);

  const sopComplianceStat = complianceRate(analysis, "sopCompliant");
  const startGreetingStat = complianceRate(analysis, "startGreeting");
  const endGreetingStat = complianceRate(analysis, "endGreeting");
  const empathyStat = complianceRate(analysis, "empathyShown");
  const nextStepStat = complianceRate(analysis, "nextStepCommunicated");
  const closedProperlyStat = complianceRate(analysis, "callClosedProperly");
  const holdProtocolStat = complianceRate(analysis, "holdProtocolFollowed");
  const leadSourceAskedStat = complianceRate(analysis, "leadSourceAsked");
  const paidConsultationStat = complianceRate(analysis, "paidConsultationPitched");

  const sopCompliant = sopComplianceStat.compliant;
  const startGreeting = startGreetingStat.compliant;
  const endGreeting = endGreetingStat.compliant;
  const empathy = empathyStat.compliant;
  const nextStep = nextStepStat.compliant;
  const closedProperly = closedProperlyStat.compliant;
  const holdProtocol = holdProtocolStat.compliant;
  const leadSourceAsked = leadSourceAskedStat.compliant;

  const answerQualityDist = {};
  analysis.forEach((row) => {
    const answerQuality = llmField(row, "answer_quality");
    if (!isNAValue(answerQuality)) answerQualityDist[answerQuality] = (answerQualityDist[answerQuality] || 0) + 1;
  });

  const toneDist = {};
  analysis.forEach((row) => {
    const tone = llmField(row, "tone_conversation_check");
    if (!isNAValue(tone)) toneDist[tone] = (toneDist[tone] || 0) + 1;
  });
  const dominantTone = topN(toneDist, 1)[0]?.[0] || "—";

  const sopMissCounts = {};
  analysis.forEach((row) => {
    const missed = llmField(row, "sop_steps_missed");
    if (Array.isArray(missed)) {
      missed.forEach((item) => { if (!isNAValue(item)) sopMissCounts[item] = (sopMissCounts[item] || 0) + 1; });
    } else if (typeof missed === "string" && missed.trim()) {
      missed.split(",").map((item) => item.trim()).filter((item) => !isNAValue(item)).forEach((item) => {
        sopMissCounts[item] = (sopMissCounts[item] || 0) + 1;
      });
    }
  });

  const caseTypeCounts = {};
  analysisCompleted.forEach((row) => {
    const key = row.case_type || "Unknown";
    caseTypeCounts[key] = (caseTypeCounts[key] || 0) + 1;
  });

  const leadSourceCounts = {};
  FIXED_LEAD_SOURCES.forEach((name) => { leadSourceCounts[name] = 0; });
  analysisCompleted.forEach((row) => {
    const raw = row.source_if_new_client || row.lead_source;
    const bucket = classifyLeadSource(raw);
    leadSourceCounts[bucket] = (leadSourceCounts[bucket] || 0) + 1;
  });

  const topCaseTypes = topN(caseTypeCounts, 5);

  const baseLeadSources = topN(leadSourceCounts, 10)
    .filter(([, count]) => count > 0)
    .map(([name, count]) => [name, count, false]);
  const existingLeadSourceNames = new Set(baseLeadSources.map(([name]) => String(name).toLowerCase()));
  const customLeadSourceTuples = customLeadSources
    .filter((name) => !existingLeadSourceNames.has(String(name).toLowerCase()))
    .map((name) => {
      const customCount = analysis.filter((row) => String(row.source_if_new_client || row.lead_source || "").trim().toLowerCase() === String(name).trim().toLowerCase()).length;
      return [name, customCount, true];
    });
  const topLeadSources = [...baseLeadSources, ...customLeadSourceTuples].sort((a, b) => b[1] - a[1]);

  const consultationItems = analysis.filter((row) => isYes(llmField(row, "paid_consultation_pitched")));
  const consultationConverted = consultationItems.filter((row) => {
    const response = String(row.client_response_to_consultation || "").toLowerCase();
    return response.includes("booked") || response.includes("converted") || response.includes("paid") || response.includes("yes");
  }).length;

  const followUpItems = analysis.filter((row) => isYes(row.follow_up_required));
  const openFollowUps = followUpItems.filter((row) => !isResolvedFollowUp(row.follow_up_status));
  const overdueFollowUps = openFollowUps.filter((row) => {
    if (!row.follow_up_deadline) return false;
    const date = new Date(row.follow_up_deadline);
    return !Number.isNaN(date.getTime()) && date < new Date();
  });
  const followUpsNoDeadline = followUpItems.filter((row) => !row.follow_up_deadline);

  const followUpStatusDist = { Open: 0, "In Progress": 0, Completed: 0 };
  followUpItems.forEach((row) => {
    const status = String(row.follow_up_status || "").trim().toLowerCase();
    if (isResolvedFollowUp(status)) followUpStatusDist.Completed += 1;
    else if (status.includes("progress")) followUpStatusDist["In Progress"] += 1;
    else followUpStatusDist.Open += 1;
  });

  const frontDeskActionsPending = analysis.filter((row) => isYes(row.follow_up_required) && !isResolvedFollowUp(row.follow_up_status)).length;
  const clientActionsPending = analysis.filter((row) => isYes(row.client_action_pending)).length;

  const escalationItems = analysis.filter((row) => isYes(row.escalation_required));
  const escalationTargetCounts = {};
  escalationItems.forEach((row) => {
    const key = row.escalation_to || "Unassigned";
    escalationTargetCounts[key] = (escalationTargetCounts[key] || 0) + 1;
  });

  const attorneyCalls = analysis
    .filter((row) => isYes(llmField(row, "legal_flag")))
    .map((row) => ({ ...row, front_desk_person_name: resolveAgentForCall(row, { refToAgent }) || "Unassigned" }));

  const agentMap = {};
  analysis.forEach((row) => {
    // resolveAgentForCall now returns null ONLY for the true-blank/N/A case;
    // a present-but-unrecognized name (including literal "N/A"/"n/a" text)
    // comes back as that raw string. Collapse both "truly blank" AND
    // "literal N/A text" down to the single "Unassigned" bucket (displayed
    // as "N/A" via agentDisplayName) so they don't create two separate N/A
    // rows in the agent tables — everything else that isn't a known agent
    // is "Other".
    let name = resolveAgentForCall(row, { refToAgent });
    if (!name || isNAValue(name)) {
      name = "Unassigned";
    } else if (!isKnownAgent(name)) {
      name = "Other";
    }
    if (!agentMap[name]) {
      agentMap[name] = {
        name,
        calls: 0,
        scores: [],
        sopCompliant: 0,
        startGreeting: 0,
        endGreeting: 0,
        empathy: 0,
        followUps: 0,
        escalations: 0,
        consultationsPitched: 0,
        sentiment: { positive: 0, neutral: 0, negative: 0 },
        toneDist: {},
        sopMisses: {},
        inbound: 0,
        outbound: 0,
        newClients: 0,
        existingClients: 0,
        voicemail: 0,
        durations: [],
        scoreDist: { Excellent: 0, Good: 0, Fair: 0, Poor: 0 },
        answerQualityDist: {},
        sentiment6: emptySentiment6(),
        caseTypes: {},
        leadSources: {},
        preparednessDist: {},
      };
    }

    const agent = agentMap[name];
    agent.calls += 1;

    const score = parseScore(row.overall_call_quality_score);
    if (score !== null) {
      agent.scores.push(score);
      agent.scoreDist[scoreBand(score)] += 1;
    }

    if (isYes(llmField(row, "script_sop_followed"))) agent.sopCompliant += 1;
    if (isYes(llmField(row, "conversation_start_greeting"))) agent.startGreeting += 1;
    if (isYes(llmField(row, "conversation_end_greeting"))) agent.endGreeting += 1;
    if (isYes(llmField(row, "acknowledging_client_requirement_empathy"))) agent.empathy += 1;
    if (isYes(row.follow_up_required)) agent.followUps += 1;
    if (isYes(row.escalation_required)) agent.escalations += 1;
    if (isYes(row.paid_consultation_pitched)) agent.consultationsPitched += 1;

    let direction = null;
    for (const ref of getCallRefCandidates(row)) {
      if (recordByRef[ref]) {
        direction = getCallDirection(recordByRef[ref]);
        if (direction) break;
      }
    }
    if (!direction) {
      const cid = row.call_id ?? row.id ?? null;
      if (cid != null && recordById[cid]) direction = getCallDirection(recordById[cid]);
    }
    if (!direction) direction = getCallDirection(row);
    if (direction === "inbound") agent.inbound += 1;
    else if (direction === "outbound") agent.outbound += 1;

    const clientTypeLabel = String(row.new_client_existing_client || "").toLowerCase();
    if (clientTypeLabel.includes("new")) agent.newClients += 1;
    else if (clientTypeLabel.includes("existing")) agent.existingClients += 1;

    if (vmuStatus(row) !== null) agent.voicemail += 1;

    let duration = getDurationSeconds(row);
    if (duration === null) {
      for (const ref of getCallRefCandidates(row)) {
        if (recordByRef[ref]) {
          duration = getDurationSeconds(recordByRef[ref]);
          if (duration !== null) break;
        }
      }
    }
    if (duration === null) {
      const cid = row.call_id ?? row.id ?? null;
      if (cid != null && recordById[cid]) duration = getDurationSeconds(recordById[cid]);
    }
    if (duration !== null) agent.durations.push(duration);

    const answerQuality = llmField(row, "answer_quality");
    if (!isNAValue(answerQuality)) {
      agent.answerQualityDist[answerQuality] = (agent.answerQualityDist[answerQuality] || 0) + 1;
    }

    agent.sentiment6[sentimentBucket(row.client_sentiment)] += 1;
    const sentimentLabel = classifySentiment(row.client_sentiment);
    agent.sentiment[sentimentLabel] += 1;

    const tone = llmField(row, "tone_conversation_check");
    if (!isNAValue(tone)) agent.toneDist[tone] = (agent.toneDist[tone] || 0) + 1;

    const missed = llmField(row, "sop_steps_missed");
    if (Array.isArray(missed)) {
      missed.forEach((item) => { if (!isNAValue(item)) agent.sopMisses[item] = (agent.sopMisses[item] || 0) + 1; });
    } else if (typeof missed === "string" && missed.trim()) {
      missed.split(",").map((item) => item.trim()).filter((item) => !isNAValue(item)).forEach((item) => {
        agent.sopMisses[item] = (agent.sopMisses[item] || 0) + 1;
      });
    }

    const caseType = row.case_type || "Unknown";
    agent.caseTypes[caseType] = (agent.caseTypes[caseType] || 0) + 1;

    const leadSource = row.source_if_new_client;
    if (leadSource && !["n/a", "not applicable"].includes(String(leadSource).toLowerCase())) {
      agent.leadSources[leadSource] = (agent.leadSources[leadSource] || 0) + 1;
    }

    const preparedness = llmField(row, "conversation_preparedness");
    const preparednessLabel = String(preparedness || "").trim();
    if (preparednessLabel && !isNAValue(preparednessLabel) && preparednessLabel.toLowerCase() !== "unrated") {
      agent.preparednessDist[preparednessLabel] = (agent.preparednessDist[preparednessLabel] || 0) + 1;
    }
  });

  const agents = Object.values(agentMap)
    .map((agent) => {
      const averageScore = avg(agent.scores);
      const averageDuration = avg(agent.durations);
      const totalDuration = agent.durations.reduce((sum, duration) => sum + duration, 0);
      const topMiss = topN(agent.sopMisses, 3)[0]?.[0] || null;
      const weakest = Object.entries({
        "SOP scripts": pct(agent.sopCompliant, agent.calls),
        "start greeting": pct(agent.startGreeting, agent.calls),
        "end greeting": pct(agent.endGreeting, agent.calls),
        "empathy": pct(agent.empathy, agent.calls),
      }).sort((a, b) => a[1] - b[1])[0];
      const observations = [];
      if (averageScore !== null) observations.push(`Averaged ${averageScore.toFixed(1)}/${SCORE_MAX} (${scoreBand(averageScore)}) across ${agent.calls} call${agent.calls === 1 ? "" : "s"}.`);
      if (topMiss) observations.push(`Most frequently missed SOP step: "${topMiss}".`);
      else if (weakest && weakest[1] < 100) observations.push(`Weakest area: ${weakest[0]} at ${weakest[1]}%.`);

      return {
        ...agent,
        avgScore: averageScore,
        avgDurationSec: averageDuration,
        totalDurationSec: totalDuration,
        sopComplianceRate: pct(agent.sopCompliant, agent.calls),
        startGreetingRate: pct(agent.startGreeting, agent.calls),
        endGreetingRate: pct(agent.endGreeting, agent.calls),
        empathyRate: pct(agent.empathy, agent.calls),
        positiveSentimentRate: pct(agent.sentiment.positive, agent.calls),
        negativeSentimentRate: pct(agent.sentiment.negative, agent.calls),
        consultationPitchRate: pct(agent.consultationsPitched, agent.calls),
        voicemailRate: pct(agent.voicemail, agent.outbound),
        dominantTone: topN(Object.entries(agent.toneDist).reduce((acc, [key, value]) => ({ ...acc, [key]: value }), {}))[0]?.[0] || "—",
        topSopMisses: topN(agent.sopMisses, 3),
        topCaseTypes: topN(agent.caseTypes, 5),
        topLeadSources: topN(agent.leadSources, 5),
        observations,
      };
    })
    .sort((a, b) => (b.avgScore ?? -1) - (a.avgScore ?? -1));

  VALID_AGENT_NAMES.forEach((name) => {
    if (!agents.some((agent) => agent.name === name)) {
      agents.push({
        name,
        calls: 0,
        scores: [],
        avgScore: null,
        avgDurationSec: null,
        totalDurationSec: null,
        sopComplianceRate: 0,
        startGreetingRate: 0,
        endGreetingRate: 0,
        empathyRate: 0,
        positiveSentimentRate: 0,
        negativeSentimentRate: 0,
        consultationPitchRate: 0,
        voicemailRate: 0,
        dominantTone: "—",
        topSopMisses: [],
        observations: [],
        caseTypes: {},
        leadSources: {},
        topCaseTypes: [],
        topLeadSources: [],
        preparednessDist: {},
        followUps: 0,
        escalations: 0,
        inbound: 0,
        outbound: 0,
        newClients: 0,
        existingClients: 0,
        scoreDist: { Excellent: 0, Good: 0, Fair: 0, Poor: 0 },
        answerQualityDist: {},
        sentiment6: emptySentiment6(),
      });
    }
  });

  // Pad N/A and Other so they always appear in the agent tables, even at 0.
  // NOTE: the blank/N/A bucket's real key in agentMap is "Unassigned" (see
  // above) — it's only ever displayed as "N/A" via agentDisplayName. Padding
  // must check for "Unassigned" here too, otherwise this would push a
  // SECOND, separate "N/A"-named row alongside the real "Unassigned" one,
  // and both would render as duplicate "N/A" rows in the agent tables.
  ["Unassigned", "Other"].forEach((bucket) => {
    if (!agents.some((agent) => agent.name === bucket)) {
      agents.push({
        name: bucket,
        calls: 0,
        scores: [],
        avgScore: null,
        avgDurationSec: null,
        totalDurationSec: null,
        sopComplianceRate: 0,
        startGreetingRate: 0,
        endGreetingRate: 0,
        empathyRate: 0,
        positiveSentimentRate: 0,
        negativeSentimentRate: 0,
        consultationPitchRate: 0,
        voicemailRate: 0,
        dominantTone: "—",
        topSopMisses: [],
        observations: [],
        caseTypes: {},
        leadSources: {},
        topCaseTypes: [],
        topLeadSources: [],
        preparednessDist: {},
        followUps: 0,
        escalations: 0,
        inbound: 0,
        outbound: 0,
        newClients: 0,
        existingClients: 0,
        scoreDist: { Excellent: 0, Good: 0, Fair: 0, Poor: 0 },
        answerQualityDist: {},
        sentiment6: emptySentiment6(),
      });
    }
  });

  const rankableAgents = agents.filter((agent) => VALID_AGENT_NAMES.includes(agent.name));
  const bestAgent = rankableAgents.length ? [...rankableAgents].sort((a, b) => (b.avgScore ?? -1) - (a.avgScore ?? -1))[0] : null;
  const worstAgent = rankableAgents.length ? [...rankableAgents].sort((a, b) => (a.avgScore ?? 11) - (b.avgScore ?? 11))[0] : null;

  const escalationsByCaseType = {};
  escalationItems.forEach((row) => {
    const key = row.case_type || "Unknown";
    escalationsByCaseType[key] = (escalationsByCaseType[key] || 0) + 1;
  });

  const scoresByCaseType = {};
  analysis.forEach((row) => {
    const key = row.case_type || "Unknown";
    const score = parseScore(row.overall_call_quality_score);
    if (score === null) return;
    if (!scoresByCaseType[key]) scoresByCaseType[key] = [];
    scoresByCaseType[key].push(score);
  });
  const avgScoreByCaseType = Object.entries(scoresByCaseType)
    .map(([caseType, scoresForType]) => [caseType, avg(scoresForType), scoresForType.length])
    .filter(([, score]) => score !== null)
    .sort((a, b) => a[1] - b[1]);

  const newClientCaseTypeCounts = {};
  analysis.forEach((row) => {
    if (String(row.new_client_existing_client || "").toLowerCase().includes("new")) {
      const key = row.case_type || "Unknown";
      newClientCaseTypeCounts[key] = (newClientCaseTypeCounts[key] || 0) + 1;
    }
  });

  const escalationRateByCaseType = Object.entries(caseTypeCounts)
    .map(([caseType, count]) => [caseType, pct(escalationsByCaseType[caseType] || 0, count), count])
    .sort((a, b) => b[1] - a[1]);

  const followUpCompletionRate = pct(followUpStatusDist.Completed, followUpItems.length);
  const followUpsNoDeadlinePct = pct(followUpsNoDeadline.length, followUpItems.length);
  const escalationRateByAgent = agents.map((agent) => [agent.name, pct(agent.escalations, agent.calls), agent.escalations]);

  const attorneyByCaseType = {};
  const attorneyByAgent = {};
  const attorneyConcernThemes = {};
  attorneyCalls.forEach((row) => {
    const caseType = row.case_type || "Unknown";
    attorneyByCaseType[caseType] = (attorneyByCaseType[caseType] || 0) + 1;
    const agentName = row.front_desk_person_name || "Unassigned";
    attorneyByAgent[agentName] = (attorneyByAgent[agentName] || 0) + 1;
    const concern = llmField(row, "legal_concern_summary");
    if (concern && String(concern).trim()) attorneyConcernThemes[String(concern).trim()] = (attorneyConcernThemes[String(concern).trim()] || 0) + 1;
  });

  let attorneyByWeek = [];
  const attorneyDated = attorneyCalls.filter((row) => row.call_recording_date);
  if (attorneyDated.length || analysis.some((row) => row.call_recording_date)) {
    const allDates = analysis.map((row) => row.call_recording_date).filter(Boolean).sort();
    if (allDates.length) {
      const weekStart0 = new Date(`${allDates[0]}T00:00:00`);
      const weekBuckets = {};
      attorneyDated.forEach((row) => {
        const date = new Date(`${row.call_recording_date}T00:00:00`);
        const weekIndex = Math.max(0, Math.floor((date - weekStart0) / 86400000 / 7));
        weekBuckets[weekIndex] = (weekBuckets[weekIndex] || 0) + 1;
      });
      const lastDate = new Date(`${allDates[allDates.length - 1]}T00:00:00`);
      const maxWeekIndex = Math.max(0, Math.floor((lastDate - weekStart0) / 86400000 / 7));
      for (let index = 0; index <= maxWeekIndex; index += 1) {
        attorneyByWeek.push({ label: `Week ${index + 1}`, count: weekBuckets[index] || 0 });
      }
    }
  }

  const consultationItemsCount = consultationItems.length;
  const consultationRate = consultationItemsCount > 0 ? pct(consultationConverted, consultationItemsCount) : 0;
  const poorCalls = analysis.filter((row) => {
    const score = parseScore(row.overall_call_quality_score);
    return score !== null && score < 4;
  }).length;
  const belowAvgCallItems = analysis
    .filter((row) => {
      const score = parseScore(row.overall_call_quality_score);
      return score !== null && score >= 4 && score < 6;
    })
    .map((row) => {
      const refs = getCallRefCandidates(row);
      const record = refs.map((ref) => recordByRef[ref]).find(Boolean)
        || (row.call_id != null ? recordById[row.call_id] || null : null);
      return enrichUiRow(row, record, resolveAgentForCall(row, { refToAgent }) || "Unassigned");
    });
  const poorCallItems = analysis
    .filter((row) => {
      const score = parseScore(row.overall_call_quality_score);
      return score !== null && score < 4;
    })
    .map((row) => {
      const refs = getCallRefCandidates(row);
      const record = refs.map((ref) => recordByRef[ref]).find(Boolean)
        || (row.call_id != null ? recordById[row.call_id] || null : null);
      return enrichUiRow(row, record, resolveAgentForCall(row, { refToAgent }) || "Unassigned");
    });

  const clientTypeStats = { new: clientType.new, existing: clientType.existing, na: clientType.na };
  const positiveSentiment = pct(sentiment.positive, analysisCompleted.length || total);
  const negativeSentiment = pct(sentiment.negative, analysisCompleted.length || total);
  const followUps = openFollowUps.length;

  const concernAnxietyModalItems = analysis
    .map((row) => {
      const statusLabel = concernAnxietyStatus(row);
      if (!statusLabel) return null;
      const refs = getCallRefCandidates(row);
      const record = refs.map((ref) => recordByRef[ref]).find(Boolean)
        || (row.call_id != null ? recordById[row.call_id] || null : null);
      return { ...enrichUiRow(row, record, resolveAgentForCall(row, { refToAgent }) || "Unassigned"), concernLabel: statusLabel };
    })
    .filter(Boolean);
  const reassuranceProvidedModalItems = analysis
    .map((row) => {
      const statusLabel = reassuranceProvidedStatus(row);
      if (!statusLabel) return null;
      const refs = getCallRefCandidates(row);
      const record = refs.map((ref) => recordByRef[ref]).find(Boolean)
        || (row.call_id != null ? recordById[row.call_id] || null : null);
      return { ...enrichUiRow(row, record, resolveAgentForCall(row, { refToAgent }) || "Unassigned"), reassuranceLabel: statusLabel };
    })
    .filter(Boolean);
  // VMU Sent items — built from call_analysis rows where voicemail_left_if_no_answer
  // is a concrete Yes/No value.  The dropdown in the modal edits this same field via
  // handleToggleVmuField / updateVoicemailLeft, so any change persists to the DB and
  // is reflected immediately in the Yes/No counts (summary re-computes via useMemo
  // when callAnalysis state updates).
  const vmuSentModalItems = analysis
    .map((row) => {
      const raw = String(row.voicemail_left_if_no_answer || "").trim().toLowerCase();
      if (raw !== "yes" && raw !== "no") return null;
      const vmuSentLabel = raw === "yes" ? "Yes" : "No";
      const refs = getCallRefCandidates(row);
      const record = refs.map((ref) => recordByRef[ref]).find(Boolean)
        || (row.call_id != null ? recordById[row.call_id] || null : null);
      return { ...enrichUiRow(row, record, resolveAgentForCall(row, { refToAgent }) || "Unassigned"), vmuSentLabel, vmuLabel: vmuSentLabel };
    })
    .filter(Boolean);
  const vmuSentYes = vmuSentModalItems.filter((row) => row.vmuSentLabel === "Yes").length;
  const vmuSentNo = vmuSentModalItems.filter((row) => row.vmuSentLabel === "No").length;

  const voicemailModalItems = vmuRows.map((row) => ({ ...row, vmuLabel: row.statusLabel }));

  // True N/A: no analysis row matched at all, the matched row's
  // front_desk_person_name is blank, OR it's literal N/A-ish text (e.g.
  // "N/A", "n/a", "none"). Distinct from "Other" below, where a real name
  // IS present but doesn't match any of the 4 known agents.
  const agentCallsNAItems = records
    .filter((call) => {
      const resolved = resolveAgentForCall(call, { refToAgent });
      return !resolved || isNAValue(resolved);
    })
    .map((call) => enrichUiRow(findAnalysisForRecord(call), call, "N/A"));

  const agentCallsOtherItems = records
    .filter((call) => {
      const resolved = resolveAgentForCall(call, { refToAgent });
      return !!resolved && !isNAValue(resolved) && !isKnownAgent(resolved);
    })
    .map((call) => enrichUiRow(findAnalysisForRecord(call), call, resolveAgentForCall(call, { refToAgent })));

  const scoreDistLower = {
    excellent: scoreDist.Excellent,
    good: scoreDist.Good,
    fair: scoreDist.Fair,
    poor: scoreDist.Poor,
  };

  const metrics = {
    total,
    callCount,
    analysisCount,
    inbound,
    outbound,
    inboundCalls: inbound,
    outboundCalls: outbound,
    voicemail,
    clientType,
    clientTypeStats,
    avgScore,
    scoreDist,
    scoreDistLower,
    sentimentDist: sentiment,
    sentiment,
    sentiment6,
    positiveRate,
    negativeRate,
    positiveSentiment,
    negativeSentiment,
    callsByHour,
    avgDurationSec,
    totalDurationSec,
    durationCallCount: durations.length,
    sopCompliant,
    startGreeting,
    endGreeting,
    empathy,
    nextStep,
    closedProperly,
    holdProtocol,
    leadSourceAsked,
    answerQualityDist,
    toneDist,
    sopMissCounts,
    caseTypeCounts,
    leadSourceCounts,
    consultationItems,
    consultationItemsCount,
    consultationConverted,
    consultationRate,
    noAudioCallsCount: noAudioCallItems.length,
    audioCorruptedCount: audioCorruptedCallItems.length,
    noAudioCallItems,
    audioCorruptedCallItems,
    belowAvgCalls: belowAvgCallItems.length,
    belowAvgCallItems,
    poorCallItems,
    topCaseTypes,
    topLeadSources,
    monthLabel: deriveMonthLabel(records),
    followUpItems,
    openFollowUps,
    overdueFollowUps,
    followUpsNoDeadline,
    followUpStatusDist,
    followUpCompletionRate,
    followUpsNoDeadlinePct,
    frontDeskActionsPending,
    clientActionsPending,
    escalationItems,
    escalationTargetCounts,
    escalationRateByAgent,
    escalationRateByCaseType,
    escalationsByCaseType,
    avgScoreByCaseType,
    newClientCaseTypeCounts,
    attorneyCalls,
    attorneyByCaseType,
    attorneyByAgent,
    attorneyConcernThemes,
    attorneyByWeek,
    callsByAgent,
    agentVolumeRows,
    agents,
    bestAgent,
    worstAgent,
    rawAnalysis: analysis,
    rawRecords: records,
    // "Attended" = resolved to one of the 4 known agents specifically —
    // NOT simply "has some name" — so a call bucketed as "Other" (a name
    // present but unrecognized) is correctly excluded from this count.
    agentCallsAttended: records.filter((call) => isKnownAgent(resolveAgentForCall(call, { refToAgent }))).length,
    agentCallsNAItems,
    agentCallsOtherItems,
    agentCallsOtherCount: agentCallsOtherItems.length,
    vmuStatusItems: vmuRows,
    concernAnxietyItems: concernAnxietyModalItems.map((row) => ({ ...row, statusLabel: row.concernLabel })),
    reassuranceProvidedItems: reassuranceProvidedModalItems.map((row) => ({ ...row, statusLabel: row.reassuranceLabel })),
    voicemailModalItems,
    vmuSentModalItems,
    vmuSentYes,
    vmuSentNo,
    concernAnxietyModalItems,
    reassuranceProvidedModalItems,
    vmuYesCount,
    vmuNoCount,
    concernAnxietyYes: concernAnxietyModalItems.filter((row) => row.concernLabel === "Yes").length,
    concernAnxietyNo: concernAnxietyModalItems.filter((row) => row.concernLabel === "No").length,
    reassuranceProvidedYes: reassuranceProvidedModalItems.filter((row) => row.reassuranceLabel === "Yes").length,
    reassuranceProvidedNo: reassuranceProvidedModalItems.filter((row) => row.reassuranceLabel === "No").length,
    concernAnxietyYesCount: analysis.filter((row) => concernAnxietyStatus(row) === "Yes").length,
    concernAnxietyNoCount: analysis.filter((row) => concernAnxietyStatus(row) === "No").length,
    reassuranceProvidedYesCount: analysis.filter((row) => reassuranceProvidedStatus(row) === "Yes").length,
    reassuranceProvidedNoCount: analysis.filter((row) => reassuranceProvidedStatus(row) === "No").length,
    dominantTone,
    poorCalls,
    voicemailLeftYes: vmuYesCount,
    voicemailNotLeftCalls: vmuNoCount,
    vmuPresentCalls: voicemail,
    followUps,
    escalations: escalationItems.length,
    consultationPitched: consultationItemsCount,
    consultationPending: consultationItems.filter((row) => isYes(row.follow_up_required) && !isResolvedFollowUp(row.follow_up_status)).length,
    totalAnalysis: analysisCompleted.length,
    analysisSummary: buildAnalysisStatusSummary(analysis),
  };

  metrics.rates = {
    sopCompliance: sopComplianceStat.rate,
    startGreeting: startGreetingStat.rate,
    endGreeting: endGreetingStat.rate,
    empathy: empathyStat.rate,
    nextStep: nextStepStat.rate,
    closedProperly: closedProperlyStat.rate,
    holdProtocol: holdProtocolStat.rate,
    leadSourceAsked: leadSourceAskedStat.rate,
    consultationPitch: paidConsultationStat.rate,
    positiveSentiment,
    negativeSentiment,
    voicemail: pct(voicemail, analysisCompleted.length || total),
    escalation: pct(escalationItems.length, analysisCompleted.length || total),
    followUpOpen: pct(openFollowUps.length, followUpItems.length),
  };

  return metrics;
}