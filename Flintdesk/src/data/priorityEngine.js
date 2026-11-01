import { mapTypicalLawOfficeFee } from "./businessOpportunityFees.js";
import { mapLeadScore, mapLeadScoreBreakdown } from "./leadScoreEngine.js";

const numeric = (value) => {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const source = (item) => item?.raw || item?.analysis || item?.record || item || {};
const first = (item, keys) => {
  const raw = source(item);
  for (const key of keys) {
    const value = raw[key] ?? item?.[key];
    if (value !== null && value !== undefined && String(value).trim() !== "") return value;
  }
  return null;
};

export function calculateLeadScore(item) {
  const explicit = numeric(first(item, ["lead_score", "leadScore"]));
  return explicit ?? mapLeadScore(item);
}

export function calculateBusinessOpportunity(item) {
  const explicitValue = numeric(first(item, ["typical_law_office_fee", "business_opportunity_value", "firm_fee"]));
  return {
    score: numeric(first(item, ["bos_score", "businessOpportunityScore", "bosScore"])),
    value: explicitValue ?? mapTypicalLawOfficeFee(item),
    serviceLine: first(item, ["service_line", "fd_a_case_type", "case_type"]),
    tier: first(item, ["boi_priority_tier", "priority_tier"]),
    feeSource: first(item, ["fee_source"]),
  };
}

export function hasUrgencyOverride(item) {
  const value = String(first(item, ["urgency_override", "fd_a_urgency_flag", "urgency_flag"]) || "").trim().toUpperCase().replace(/[^A-Z]/g, "_");
  return ["NOID", "RFE", "COURT_DATE", "CLIENT_DISTRESSED", "URGENT_NOID", "URGENT_RFE"].includes(value);
}

export function calculatePriority(item) {
  const leadScore = calculateLeadScore(item);
  const business = calculateBusinessOpportunity(item);
  const urgencyOverride = hasUrgencyOverride(item);
  const leadBand = leadScore === null ? "N_A" : leadScore >= 70 ? "BOOK_NOW" : leadScore >= 40 ? "GATHER_MORE_INFO" : "NURTURE";
  const businessTier = business.score === null ? "N_A" : business.score >= 8 ? "HIGH" : business.score >= 7 ? "HIGH_MID" : business.score >= 4 ? "STANDARD" : "N_A";
  return { leadScore, business, urgencyOverride, leadBand, businessTier };
}

const timeValue = (item) => {
  const value = first(item, ["timestamp", "registered_at", "created_at", "call_recording_date_normalized", "call_recording_date", "date_received", "date"]);
  const parsed = new Date(value || 0).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
};

export function rankCallsByPriority(items = []) {
  return items.map((item, index) => ({ item, index, priority: calculatePriority(item) })).sort((a, b) => {
    const leadDifference = (b.priority.leadScore ?? -1) - (a.priority.leadScore ?? -1);
    if (leadDifference) return leadDifference;
    const bosDifference = (b.priority.business.score ?? -1) - (a.priority.business.score ?? -1);
    if (bosDifference) return bosDifference;
    const valueDifference = (b.priority.business.value ?? -1) - (a.priority.business.value ?? -1);
    if (valueDifference) return valueDifference;
    if (a.priority.urgencyOverride !== b.priority.urgencyOverride) return a.priority.urgencyOverride ? -1 : 1;
    const dateDifference = timeValue(b.item) - timeValue(a.item);
    return dateDifference || a.index - b.index;
  }).map(({ item }) => item);
}

export function getLeadScoreBreakdown(item) {
  const raw = source(item);
  const breakdown = raw.lead_score_breakdown;
  if (breakdown && typeof breakdown === "object") {
    const criteria = [
      ["urgency_signal", "Urgency Signal", 25], ["case_complexity", "Case Complexity", 20],
      ["employer_involvement", "Employer Involvement", 15], ["lead_source_trust", "Lead Source Trust", 15],
      ["existing_relationship", "Existing Relationship", 10], ["readiness_decision_stage", "Readiness / Decision Stage", 15],
    ];
    const values = criteria.map(([key, label, max]) => ({ label, max, value: numeric(breakdown[key]) }));
    if (values.every((entry) => entry.value !== null)) return values;
  }
  return mapLeadScoreBreakdown(item);
}
