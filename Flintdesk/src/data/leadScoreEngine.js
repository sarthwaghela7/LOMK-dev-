// Paid Consultation — Lead Scoring Rubric Engine
// Implements the official 6-criteria scoring rubric defined in LeadScoreMatrixModal:
// 1. Urgency Signal (Max: 25)
// 2. Case Complexity (Max: 20)
// 3. Employer Involvement (Max: 15)
// 4. Lead Source Trust (Max: 15)
// 5. Existing Relationship (Max: 10)
// 6. Readiness / Decision Stage (Max: 15)
// Total Max: 100 points
// Tiers: 70–100 (BOOK NOW), 40–69 (GATHER MORE INFO), 0–39 (NURTURE)

const source = (item) => item?.raw || item?.analysis || item?.record || item || {};

function extractSearchText(item) {
  const raw = source(item);
  return [
    item?.caseType,
    item?.status,
    item?.notes,
    item?.transcript,
    item?.clientType,
    item?.leadSource,
    raw.case_type,
    raw.fd_a_case_type,
    raw.service_line,
    raw.category,
    raw.sub_category,
    raw.subcategory,
    raw.filing_type,
    raw.fee_source,
    raw.urgency_override,
    raw.fd_a_urgency_flag,
    raw.urgency_flag,
    raw.fd_a_client_type,
    raw.client_type,
    raw.fd_a_lead_source,
    raw.lead_source,
    raw.fd_a_call_outcome,
    raw.fd_a_call_summary,
    raw.call_summary,
    raw.fd_m_case_status_change,
    raw.evaluator_notes,
    raw.transcript,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

/**
 * 1. Urgency Signal (0, 10, or 25 pts)
 * - 25 pts: Client Distressed, RFE, NOID, Court Date, Filing Deadline, Grace Period, Visa Expiry
 * - 10 pts: Vague concern about timing / deadline / expiring
 * - 0 pts: None mentioned
 */
function scoreUrgencySignal(item, fullText) {
  const raw = source(item);
  const urgencyFlag = String(
    raw.urgency_override ||
      raw.fd_a_urgency_flag ||
      raw.urgency_flag ||
      item?.urgency_flag ||
      ""
  )
    .trim()
    .toLowerCase();

  const highUrgencyPatterns = [
    /\b(noid|notice of intent to deny)\b/i,
    /\b(rfe|request for evidence)\b/i,
    /\b(court date|court hearing|removal|deportation|immigration judge)\b/i,
    /\b(client distressed|distressed|emergency|panic)\b/i,
    /\b(filing deadline|grace period|visa expir|status expir)\b/i,
  ];

  if (
    urgencyFlag &&
    urgencyFlag !== "none" &&
    urgencyFlag !== "n/a" &&
    urgencyFlag !== "null"
  ) {
    return 25;
  }

  for (const pattern of highUrgencyPatterns) {
    if (pattern.test(fullText)) return 25;
  }

  // Moderate timing concern
  if (/\b(urgent|asap|soon|rush|timing|deadline|expir|time.?sensitive)\b/i.test(fullText)) {
    return 10;
  }

  return 0;
}

/**
 * 2. Case Complexity (0, 10, or 20 pts)
 * - 20 pts: PERM, EB-1 (EB-1A, EB-1B, EB-1C), EB-2 NIW, L-1, I-485 AOS, EB-5, multi-petition
 * - 10 pts: F-1/OPT, B-1/B-2, N-400, standard Green Card, H-1B, I-130, I-751, I-765, EAD, Advance Parole, I-539
 * - 0 pts: General "which visa is right for me" / consultation / none mentioned
 */
function scoreCaseComplexity(item, fullText) {
  const highComplexity = /\b(perm\b|labor cert|eta-?9089|eb-?1|eb-?1a|eb-?1b|eb-?1c|eb-?2|niw|national interest waiver|eb-?5|immigrant investor|l-?1|l-?1a|l-?1b|i-?485|adjustment of status|\baos\b|multi.?petition|consular processing)\b/i;
  if (highComplexity.test(fullText)) return 20;

  const mediumComplexity = /\b(h-?1b|f-?1|opt|stem opt|b-?1|b-?2|n-?400|citizenship|naturalization|green card|i-?130|i-?751|i-?765|\bead\b|advance parole|i-?131|i-?539|extension|k-?1|k-?3|i-?864|i-?824)\b/i;
  if (mediumComplexity.test(fullText)) return 10;

  return 0;
}

/**
 * 3. Employer Involvement (0, 8, or 15 pts)
 * - 15 pts: Employer/HR sponsoring or on the call; Organization/Employer client type
 * - 8 pts: Employer aware, not on call; employment-based visa with individual applicant
 * - 0 pts: Individual only, no employer
 */
function scoreEmployerInvolvement(item, fullText) {
  const raw = source(item);
  const clientType = String(
    raw.fd_a_client_type || raw.client_type || item?.clientType || ""
  ).toLowerCase();

  if (/\b(organization|employer|corporate|company)\b/i.test(clientType)) {
    return 15;
  }

  // Inherently employer-sponsored petitions
  if (/\b(perm\b|eta-?9089|eb-?1b|eb-?1c|l-?1a|l-?1b|sponsor.*company|company.*sponsor)\b/i.test(fullText)) {
    return 15;
  }

  // Work visa where employer is involved or known
  if (/\b(h-?1b|stem opt|opt|employment|sponsor|employer|work permit|w-?2|petitioning employer)\b/i.test(fullText)) {
    return 8;
  }

  return 0;
}

/**
 * 4. Lead Source Trust (0, 8, or 15 pts)
 * - 15 pts: Client Referral or repeat Paid Consultation Client
 * - 8 pts: Google Search, YouTube, Friends, Instagram, Website, Seminar, Organic
 * - 0 pts: Cold Ads/Brevo click, Email blast, or untrusted
 */
function scoreLeadSourceTrust(item, fullText) {
  const raw = source(item);
  const leadSource = String(
    raw.fd_a_lead_source || raw.lead_source || item?.leadSource || ""
  ).toLowerCase();

  if (/\b(referral|paid consult|repeat client)\b/i.test(leadSource)) {
    return 15;
  }

  if (/\b(google search|google|youtube|friend|family|instagram|website|seminar|lomk|tamil sangam|gmb|organic)\b/i.test(leadSource)) {
    return 8;
  }

  if (/\b(ad|brevo|cold|campaign|click|email)\b/i.test(leadSource)) {
    return 0;
  }

  // Check full text fallback
  if (/\b(referred by|client referral|repeat client)\b/i.test(fullText)) return 15;
  if (/\b(google|youtube|friend|family|instagram|website)\b/i.test(fullText)) return 8;

  return 5; // Moderate baseline if unknown
}

/**
 * 5. Existing Relationship (0, 5, or 10 pts)
 * - 10 pts: Existing client of the firm
 * - 5 pts: New, referred by current client
 * - 0 pts: New, no prior contact
 */
function scoreExistingRelationship(item, fullText) {
  const raw = source(item);
  const clientType = String(
    raw.fd_a_client_type || raw.client_type || item?.clientType || ""
  ).toLowerCase();
  const leadSource = String(
    raw.fd_a_lead_source || raw.lead_source || item?.leadSource || ""
  ).toLowerCase();

  if (/\bexisting client\b/i.test(clientType)) {
    return 10;
  }

  if (/\b(existing client|current client|already represented|you filed my|handled my)\b/i.test(fullText)) {
    return 10;
  }

  if (/\b(referral|referred by)\b/i.test(leadSource) || /\b(referred by a client|referred by current client)\b/i.test(fullText)) {
    return 5;
  }

  return 0;
}

/**
 * 6. Readiness / Decision Stage (0, 8, or 15 pts)
 * - 15 pts: Has deadline, documents in hand, consult booked/quoted, asking "what do I do next"
 * - 8 pts: Some documents, asking process questions, follow-up planned
 * - 0 pts: Just researching, no documents, no timeline, voicemail, dropped
 */
function scoreReadinessStage(item, fullText, urgencyScore) {
  const raw = source(item);
  const outcome = String(raw.fd_a_call_outcome || "").toLowerCase();
  const status = String(raw.fd_m_case_status_change || item?.status || "").toLowerCase();

  // High readiness signals
  if (
    /\b(booked|scheduled|success - pc|success - ge|fee details shared|retainer|hire|moving forward|ready to file)\b/i.test(
      `${outcome} ${status}`
    ) ||
    urgencyScore >= 25 ||
    /\b(documents in hand|ready to proceed|what do i do next|next step|sign up|start case)\b/i.test(fullText)
  ) {
    return 15;
  }

  // Moderate readiness signals
  if (
    /\b(follow up|follow-up|more info|process questions|review|checklist|evaluat|gathering)\b/i.test(
      `${outcome} ${status} ${fullText}`
    )
  ) {
    return 8;
  }

  // Voicemail / dropped / spam
  if (/\b(voicemail|dropped|spam|wrong number)\b/i.test(`${outcome} ${status}`)) {
    return 0;
  }

  return 8; // Reasonable default for incoming consultation calls
}

/**
 * Computes the 6-criteria breakdown for an item according to the rubric.
 * Returns an array of { label, max, value } or null if the item has no call context.
 */
export function mapLeadScoreBreakdown(item) {
  if (!item) return null;
  const fullText = extractSearchText(item);

  // If the record has no meaningful text at all, return null
  if (!fullText.trim()) return null;

  const urgency = scoreUrgencySignal(item, fullText);
  const complexity = scoreCaseComplexity(item, fullText);
  const employer = scoreEmployerInvolvement(item, fullText);
  const trust = scoreLeadSourceTrust(item, fullText);
  const relationship = scoreExistingRelationship(item, fullText);
  const readiness = scoreReadinessStage(item, fullText, urgency);

  return [
    { label: "Urgency Signal", max: 25, value: urgency },
    { label: "Case Complexity", max: 20, value: complexity },
    { label: "Employer Involvement", max: 15, value: employer },
    { label: "Lead Source Trust", max: 15, value: trust },
    { label: "Existing Relationship", max: 10, value: relationship },
    { label: "Readiness / Decision Stage", max: 15, value: readiness },
  ];
}

/**
 * Computes the overall Lead Score (0–100) from the rubric breakdown.
 * Returns null if the item has no context.
 */
export function mapLeadScore(item) {
  const breakdown = mapLeadScoreBreakdown(item);
  if (!breakdown) return null;

  const total = breakdown.reduce((sum, entry) => sum + (entry.value || 0), 0);
  return Math.min(100, Math.max(0, Math.round(total)));
}
