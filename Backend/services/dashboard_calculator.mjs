//#region src/data/businessOpportunityFees.js
const FEE_RULES = [
	[/h-?1b.*(?:new|cap|lottery|specialty occupation)|(?:new|cap).*h-?1b/i, 3200],
	[/h-?1b.*(?:extension|transfer|portability|new employer)|(?:extension|transfer).*h-?1b/i, 2e3],
	[/h-?1b.*(?:amendment|material change|new location|job duties)|(?:amendment|material change).*h-?1b/i, 1500],
	[/\bperm\b|labor certification|eta-?9089/i, 4e3],
	[/eb-?5|immigrant investor|i-?526e|regional center/i, 12e3],
	[/eb-?1a|extraordinary ability|self.?petition.*o-?1/i, 4500],
	[/eb-?1b|outstanding professor|outstanding researcher/i, 3800],
	[/eb-?1c|multinational manager|manager.*executive.*green card/i, 4200],
	[/national interest waiver|\bniw\b/i, 3800],
	[/eb-?2|advanced degree/i, 3800],
	[/eb-?3.*(?:skilled|professional)|(?:skilled worker|bachelors degree).*perm/i, 3200],
	[/eb-?4|special immigrant|religious worker|\bi-?360\b/i, 4e3],
	[/\bi-?140\b|immigrant worker petition|priority date/i, 1500],
	[/employment.*(?:adjustment of status|aos)|(?:i-?485).*employment/i, 1600],
	[/i-?130.*(?:i-?485|aos).*(?:spouse|marriage)|(?:marriage|spouse).*(?:green card|aos)/i, 3200],
	[/i-?130.*(?:i-?485|aos).*(?:child|parent)|family green card.*(?:child|parent)/i, 3e3],
	[/\bk-?1\b|fianc(?:e|é)|i-?129f.*engagement/i, 2500],
	[/\bk-?3\b|spouse visa.*pending/i, 2500],
	[/i-?130.*(?:sibling|f4)|petition.*alien relative.*(?:spouse|child|sibling|parent)/i, 1200],
	[/\bi-?824\b/i, 400],
	[/\bb-?2\b.*(?:consular|ds-?160|visitor|tourist)|(?:visitor|tourist) visa.*(?:embassy|interview)/i, null],
	[/stem opt|i-?983/i, 750],
	[/\bopt\b|optional practical training|post.?completion/i, 750],
	[/\bf-?1\b.*(?:student|i-?20|sevis)|student status/i, 750],
	[/\bl-?1a\b|intracompany.*(?:manager|executive)/i, 3e3],
	[/\bl-?1b\b|specialized knowledge/i, 2500],
	[/change of status.*(?:f-?1|student)|(?:b-?2|h-?4).*to.*f-?1/i, 750],
	[/change of status.*\bb-?2\b|(?:h-?1b|h-?4).*to.*b-?2/i, 750],
	[/change of status.*\bl-?2\b|l-?1 spouse.*status/i, 750],
	[/change of status.*\bh-?4\b|h-?1b spouse.*status/i, 750],
	[/h-?4.*(?:ead|employment authorization)|h4-?ead/i, 450],
	[/extension of current status|i-?539 extension|(?:h-?4|b-?2|f-?2) extension/i, 750],
	[/f-?1.*(?:premium|i-?907)|premium.*student status/i, 750],
	[/consular processing|\bnvc\b|ds-?260|immigrant visa abroad/i, 2e3],
	[/\bi-?751\b|remove conditions|conditional residence/i, 2600],
	[/\bi-?864\b|affidavit of support|financial sponsor/i, 500],
	[/\bn-?400\b|naturalization|citizenship.*(?:civics|interview)/i, 800],
	[/\bi-?90\b|replace permanent resident|green card renewal/i, 600],
	[/ap.*ead renewal|combo card|(?:i-?131).*i-?765.*renewal/i, 500],
	[/advance parole.*renewal|travel document.*i-?131/i, 500],
	[/re.?entry permit|lpr travel|extended absence/i, 500],
	[/gc ead renewal|work permit.*(?:renewal|pending i-?485)|\bi-?765\b/i, 500]
];
function mapTypicalLawOfficeFee(item) {
	const raw = item?.raw || item?.analysis || item?.record || {};
	const text = [
		raw.service_line,
		raw.category,
		raw.sub_category,
		raw.subcategory,
		raw.filing_type,
		raw.fee_source,
		item?.caseType,
		raw.fd_a_case_type,
		raw.case_type,
		item?.transcript,
		raw.transcript
	].filter(Boolean).join(" | ");
	const match = FEE_RULES.find(([pattern]) => pattern.test(text));
	return match ? match[1] : null;
}
//#endregion
//#region src/data/leadScoreEngine.js
const source$1 = (item) => item?.raw || item?.analysis || item?.record || item || {};
function extractSearchText(item) {
	const raw = source$1(item);
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
		raw.transcript
	].filter(Boolean).join(" ").toLowerCase();
}
/**
* 1. Urgency Signal (0, 10, or 25 pts)
* - 25 pts: Client Distressed, RFE, NOID, Court Date, Filing Deadline, Grace Period, Visa Expiry
* - 10 pts: Vague concern about timing / deadline / expiring
* - 0 pts: None mentioned
*/
function scoreUrgencySignal(item, fullText) {
	const raw = source$1(item);
	const urgencyFlag = String(raw.urgency_override || raw.fd_a_urgency_flag || raw.urgency_flag || item?.urgency_flag || "").trim().toLowerCase();
	const highUrgencyPatterns = [
		/\b(noid|notice of intent to deny)\b/i,
		/\b(rfe|request for evidence)\b/i,
		/\b(court date|court hearing|removal|deportation|immigration judge)\b/i,
		/\b(client distressed|distressed|emergency|panic)\b/i,
		/\b(filing deadline|grace period|visa expir|status expir)\b/i
	];
	if (urgencyFlag && urgencyFlag !== "none" && urgencyFlag !== "n/a" && urgencyFlag !== "null") return 25;
	for (const pattern of highUrgencyPatterns) if (pattern.test(fullText)) return 25;
	if (/\b(urgent|asap|soon|rush|timing|deadline|expir|time.?sensitive)\b/i.test(fullText)) return 10;
	return 0;
}
/**
* 2. Case Complexity (0, 10, or 20 pts)
* - 20 pts: PERM, EB-1 (EB-1A, EB-1B, EB-1C), EB-2 NIW, L-1, I-485 AOS, EB-5, multi-petition
* - 10 pts: F-1/OPT, B-1/B-2, N-400, standard Green Card, H-1B, I-130, I-751, I-765, EAD, Advance Parole, I-539
* - 0 pts: General "which visa is right for me" / consultation / none mentioned
*/
function scoreCaseComplexity(item, fullText) {
	if (/\b(perm\b|labor cert|eta-?9089|eb-?1|eb-?1a|eb-?1b|eb-?1c|eb-?2|niw|national interest waiver|eb-?5|immigrant investor|l-?1|l-?1a|l-?1b|i-?485|adjustment of status|\baos\b|multi.?petition|consular processing)\b/i.test(fullText)) return 20;
	if (/\b(h-?1b|f-?1|opt|stem opt|b-?1|b-?2|n-?400|citizenship|naturalization|green card|i-?130|i-?751|i-?765|\bead\b|advance parole|i-?131|i-?539|extension|k-?1|k-?3|i-?864|i-?824)\b/i.test(fullText)) return 10;
	return 0;
}
/**
* 3. Employer Involvement (0, 8, or 15 pts)
* - 15 pts: Employer/HR sponsoring or on the call; Organization/Employer client type
* - 8 pts: Employer aware, not on call; employment-based visa with individual applicant
* - 0 pts: Individual only, no employer
*/
function scoreEmployerInvolvement(item, fullText) {
	const raw = source$1(item);
	const clientType = String(raw.fd_a_client_type || raw.client_type || item?.clientType || "").toLowerCase();
	if (/\b(organization|employer|corporate|company)\b/i.test(clientType)) return 15;
	if (/\b(perm\b|eta-?9089|eb-?1b|eb-?1c|l-?1a|l-?1b|sponsor.*company|company.*sponsor)\b/i.test(fullText)) return 15;
	if (/\b(h-?1b|stem opt|opt|employment|sponsor|employer|work permit|w-?2|petitioning employer)\b/i.test(fullText)) return 8;
	return 0;
}
/**
* 4. Lead Source Trust (0, 8, or 15 pts)
* - 15 pts: Client Referral or repeat Paid Consultation Client
* - 8 pts: Google Search, YouTube, Friends, Instagram, Website, Seminar, Organic
* - 0 pts: Cold Ads/Brevo click, Email blast, or untrusted
*/
function scoreLeadSourceTrust(item, fullText) {
	const raw = source$1(item);
	const leadSource = String(raw.fd_a_lead_source || raw.lead_source || item?.leadSource || "").toLowerCase();
	if (/\b(referral|paid consult|repeat client)\b/i.test(leadSource)) return 15;
	if (/\b(google search|google|youtube|friend|family|instagram|website|seminar|lomk|tamil sangam|gmb|organic)\b/i.test(leadSource)) return 8;
	if (/\b(ad|brevo|cold|campaign|click|email)\b/i.test(leadSource)) return 0;
	if (/\b(referred by|client referral|repeat client)\b/i.test(fullText)) return 15;
	if (/\b(google|youtube|friend|family|instagram|website)\b/i.test(fullText)) return 8;
	return 5;
}
/**
* 5. Existing Relationship (0, 5, or 10 pts)
* - 10 pts: Existing client of the firm
* - 5 pts: New, referred by current client
* - 0 pts: New, no prior contact
*/
function scoreExistingRelationship(item, fullText) {
	const raw = source$1(item);
	const clientType = String(raw.fd_a_client_type || raw.client_type || item?.clientType || "").toLowerCase();
	const leadSource = String(raw.fd_a_lead_source || raw.lead_source || item?.leadSource || "").toLowerCase();
	if (/\bexisting client\b/i.test(clientType)) return 10;
	if (/\b(existing client|current client|already represented|you filed my|handled my)\b/i.test(fullText)) return 10;
	if (/\b(referral|referred by)\b/i.test(leadSource) || /\b(referred by a client|referred by current client)\b/i.test(fullText)) return 5;
	return 0;
}
/**
* 6. Readiness / Decision Stage (0, 8, or 15 pts)
* - 15 pts: Has deadline, documents in hand, consult booked/quoted, asking "what do I do next"
* - 8 pts: Some documents, asking process questions, follow-up planned
* - 0 pts: Just researching, no documents, no timeline, voicemail, dropped
*/
function scoreReadinessStage(item, fullText, urgencyScore) {
	const raw = source$1(item);
	const outcome = String(raw.fd_a_call_outcome || "").toLowerCase();
	const status = String(raw.fd_m_case_status_change || item?.status || "").toLowerCase();
	if (/\b(booked|scheduled|success - pc|success - ge|fee details shared|retainer|hire|moving forward|ready to file)\b/i.test(`${outcome} ${status}`) || urgencyScore >= 25 || /\b(documents in hand|ready to proceed|what do i do next|next step|sign up|start case)\b/i.test(fullText)) return 15;
	if (/\b(follow up|follow-up|more info|process questions|review|checklist|evaluat|gathering)\b/i.test(`${outcome} ${status} ${fullText}`)) return 8;
	if (/\b(voicemail|dropped|spam|wrong number)\b/i.test(`${outcome} ${status}`)) return 0;
	return 8;
}
/**
* Computes the 6-criteria breakdown for an item according to the rubric.
* Returns an array of { label, max, value } or null if the item has no call context.
*/
function mapLeadScoreBreakdown(item) {
	if (!item) return null;
	const fullText = extractSearchText(item);
	if (!fullText.trim()) return null;
	const urgency = scoreUrgencySignal(item, fullText);
	const complexity = scoreCaseComplexity(item, fullText);
	const employer = scoreEmployerInvolvement(item, fullText);
	const trust = scoreLeadSourceTrust(item, fullText);
	const relationship = scoreExistingRelationship(item, fullText);
	const readiness = scoreReadinessStage(item, fullText, urgency);
	return [
		{
			label: "Urgency Signal",
			max: 25,
			value: urgency
		},
		{
			label: "Case Complexity",
			max: 20,
			value: complexity
		},
		{
			label: "Employer Involvement",
			max: 15,
			value: employer
		},
		{
			label: "Lead Source Trust",
			max: 15,
			value: trust
		},
		{
			label: "Existing Relationship",
			max: 10,
			value: relationship
		},
		{
			label: "Readiness / Decision Stage",
			max: 15,
			value: readiness
		}
	];
}
/**
* Computes the overall Lead Score (0–100) from the rubric breakdown.
* Returns null if the item has no context.
*/
function mapLeadScore(item) {
	const breakdown = mapLeadScoreBreakdown(item);
	if (!breakdown) return null;
	const total = breakdown.reduce((sum, entry) => sum + (entry.value || 0), 0);
	return Math.min(100, Math.max(0, Math.round(total)));
}
//#endregion
//#region src/data/priorityEngine.js
const numeric = (value) => {
	if (value === null || value === void 0 || String(value).trim() === "") return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
};
const source = (item) => item?.raw || item?.analysis || item?.record || item || {};
const first = (item, keys) => {
	const raw = source(item);
	for (const key of keys) {
		const value = raw[key] ?? item?.[key];
		if (value !== null && value !== void 0 && String(value).trim() !== "") return value;
	}
	return null;
};
function calculateLeadScore(item) {
	return numeric(first(item, ["lead_score", "leadScore"])) ?? mapLeadScore(item);
}
function calculateBusinessOpportunity(item) {
	const explicitValue = numeric(first(item, [
		"typical_law_office_fee",
		"business_opportunity_value",
		"firm_fee"
	]));
	return {
		score: numeric(first(item, [
			"bos_score",
			"businessOpportunityScore",
			"bosScore"
		])),
		value: explicitValue ?? mapTypicalLawOfficeFee(item),
		serviceLine: first(item, [
			"service_line",
			"fd_a_case_type",
			"case_type"
		]),
		tier: first(item, ["boi_priority_tier", "priority_tier"]),
		feeSource: first(item, ["fee_source"])
	};
}
function hasUrgencyOverride(item) {
	const value = String(first(item, [
		"urgency_override",
		"fd_a_urgency_flag",
		"urgency_flag"
	]) || "").trim().toUpperCase().replace(/[^A-Z]/g, "_");
	return [
		"NOID",
		"RFE",
		"COURT_DATE",
		"CLIENT_DISTRESSED",
		"URGENT_NOID",
		"URGENT_RFE"
	].includes(value);
}
function calculatePriority(item) {
	const leadScore = calculateLeadScore(item);
	const business = calculateBusinessOpportunity(item);
	return {
		leadScore,
		business,
		urgencyOverride: hasUrgencyOverride(item),
		leadBand: leadScore === null ? "N_A" : leadScore >= 70 ? "BOOK_NOW" : leadScore >= 40 ? "GATHER_MORE_INFO" : "NURTURE",
		businessTier: business.score === null ? "N_A" : business.score >= 8 ? "HIGH" : business.score >= 7 ? "HIGH_MID" : business.score >= 4 ? "STANDARD" : "N_A"
	};
}
//#endregion
//#region src/data/dashboardCalculations.js
const EMPTY_VALUES = /* @__PURE__ */ new Set([
	"",
	"n/a",
	"na",
	"none",
	"null",
	"undefined",
	"unknown"
]);
const CALL_OUTCOME_CATEGORIES = [
	"Connected - Resolved",
	"Connected - Follow up needed",
	"Connected - Redirected",
	"Voicemail Left",
	"Voicemail Full (Not Available)",
	"Call Back Scheduled"
];
const CALL_DIRECTION_CATEGORIES = [
	"Direct Inbound",
	"Direct Outbound",
	"Brevo Outbound",
	"Brevo Follow Up",
	"Missed Call Outbound"
];
const CASE_STATUS_CATEGORIES = [
	"Open",
	"Initial email sent.",
	"Success - PC",
	"Success - GE",
	"PC Follow Up",
	"PC shared and lost",
	"Fee details shared & FU",
	"Client Side delay",
	"Moved to next date",
	"Blocker",
	"Dropped",
	"Same client context",
	"initial email followup"
];
const URGENCY_FLAG_CATEGORIES = [
	"None",
	"Urgent - RFE",
	"Urgent - NOID",
	"Grace Period",
	"Visa Expiry",
	"Court Date",
	"Filing Deadline",
	"Client Distressed"
];
const CLIENT_TYPE_CATEGORIES = [
	"New Client - Employer",
	"Existing client - Organization",
	"Existing client - Individual",
	"New Client - Individual",
	"Voicemail Call"
];
const GOOGLE_REVIEW_CATEGORIES = [
	"Pending",
	"Requested",
	"Submitted",
	"Not Applicable"
];
function normalizeText(value) {
	if (value === null || value === void 0) return "";
	return String(value).trim().replace(/\s+/g, " ");
}
function normalizeKey(value) {
	return normalizeText(value).toLowerCase();
}
function isMeaningful(value) {
	const text = normalizeKey(value);
	if (!text) return false;
	return !EMPTY_VALUES.has(text);
}
function getFirstMeaningfulValue(row, fields) {
	for (const field of fields) {
		const value = row?.[field] ?? row?.raw?.[field];
		if (isMeaningful(value)) return value;
	}
	return "";
}
function safeNumber(value) {
	if (value === null || value === void 0 || value === "") return null;
	const parsed = Number(String(value).replace(/%/g, ""));
	return Number.isFinite(parsed) ? parsed : null;
}
function durationToSeconds(value) {
	if (value === null || value === void 0 || value === "") return 0;
	if (typeof value === "number" && Number.isFinite(value)) return value;
	const text = String(value).trim();
	if (/^\d+(?::\d{1,2}){1,2}$/.test(text)) {
		const parts = text.split(":").map(Number);
		return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
	}
	const numeric = Number(text.replace(/[^\d.]/g, ""));
	return Number.isFinite(numeric) ? numeric : 0;
}
function buildFixedBreakdown(rows, categories, fieldNames, normalizer) {
	const counts = new Map(categories.map((category) => [category, 0]));
	rows.forEach((row) => {
		const mappedLabel = normalizer(getFirstMeaningfulValue(row, fieldNames));
		if (!mappedLabel) return;
		if (!counts.has(mappedLabel)) return;
		counts.set(mappedLabel, (counts.get(mappedLabel) || 0) + 1);
	});
	const total = [...counts.values()].reduce((sum, count) => sum + count, 0);
	return {
		total,
		items: categories.map((label) => {
			const count = counts.get(label) || 0;
			return {
				label,
				count,
				percentage: total === 0 ? 0 : count / total * 100
			};
		})
	};
}
function normalizeCallOutcome(value) {
	const normalized = normalizeKey(value);
	if (!normalized) return "";
	if (normalized.includes("resolved") && normalized.includes("connected")) return "Connected - Resolved";
	if (normalized.includes("follow") && normalized.includes("needed") || normalized.includes("follow") && normalized.includes("connected")) return "Connected - Follow up needed";
	if (normalized.includes("redirected")) return "Connected - Redirected";
	if (normalized.includes("voicemail full") || normalized.includes("not available")) return "Voicemail Full (Not Available)";
	if (normalized.includes("voicemail") || normalized.includes("voicemail left")) return "Voicemail Left";
	if (normalized.includes("call back") || normalized.includes("scheduled")) return "Call Back Scheduled";
	return "";
}
function normalizeCallDirection(value) {
	const normalized = normalizeKey(value);
	if (!normalized) return "";
	if (normalized === "inbound" || normalized.includes("direct inbound")) return "Direct Inbound";
	if (normalized === "outbound" || normalized.includes("direct outbound")) return "Direct Outbound";
	if (normalized.includes("brevo outbound")) return "Brevo Outbound";
	if (normalized.includes("brevo follow up") || normalized.includes("brevo follow-up")) return "Brevo Follow Up";
	if (normalized.includes("missed") && normalized.includes("outbound")) return "Missed Call Outbound";
	if (normalized.includes("missed call")) return "Missed Call Outbound";
	return "";
}
function isInboundCall(row) {
	return normalizeCallDirection(getFirstMeaningfulValue(row, [
		"call_direction",
		"call_type",
		"direction"
	])) === "Direct Inbound";
}
function isOutboundCall(row) {
	const direction = normalizeCallDirection(getFirstMeaningfulValue(row, [
		"call_direction",
		"call_type",
		"direction"
	]));
	return [
		"Direct Outbound",
		"Brevo Outbound",
		"Brevo Follow Up",
		"Missed Call Outbound"
	].includes(direction);
}
function isPaidLead(row) {
	const opportunity = normalizeKey(getFirstMeaningfulValue(row, ["business_opportunity"]));
	const status = normalizeCaseStatus(getFirstMeaningfulValue(row, [
		"fd_m_case_status_change",
		"action_status",
		"case_status"
	]));
	return opportunity === "yes" && status === "Success - PC";
}
function normalizedIdentifier(value) {
	return normalizeText(value).toLowerCase();
}
/**
* Return one dashboard call for each unique client represented by Leads Page S1.
*
* The canonical S1 population comes from GET /api/flintdesk/leads: the backend
* selects call_analysis_results rows whose fd_m_leads JSON is non-null and links
* an S1 record to a call through fd_m_leads.matched_call_id. client_id is the
* permanent client-lifecycle identifier on calls. Rows without a client_id are
* excluded because they cannot be safely deduplicated as leads.
*/
function getUniqueLeadsFromS1Calls(rows = [], s1Leads = [], predicate = null) {
	const safeRows = Array.isArray(rows) ? rows.filter(Boolean) : [];
	const safeLeads = Array.isArray(s1Leads) ? s1Leads.filter(Boolean) : [];
	const s1CallIds = /* @__PURE__ */ new Set();
	safeLeads.forEach((lead) => {
		const normalized = normalizedIdentifier(lead?.record?.matched_call_id || lead?.call_details?.call_id);
		if (normalized) s1CallIds.add(normalized);
	});
	const uniqueClients = /* @__PURE__ */ new Map();
	safeRows.forEach((row) => {
		const callId = normalizedIdentifier(row?.callId ?? row?.call_id ?? row?.raw?.call_id);
		if (!callId || !s1CallIds.has(callId)) return;
		if (predicate && !predicate(row)) return;
		const clientId = normalizedIdentifier(row?.clientId ?? row?.client_id ?? row?.raw?.client_id);
		if (!clientId || uniqueClients.has(clientId)) return;
		uniqueClients.set(clientId, row);
	});
	return [...uniqueClients.values()];
}
function normalizeCaseStatus(value) {
	const normalized = normalizeKey(value);
	if (!normalized) return "";
	const match = Object.entries({
		"Open": ["open"],
		"Initial email sent.": ["initial email sent.", "initial email sent"],
		"Success - PC": [
			"success - pc",
			"success pc",
			"pc success"
		],
		"Success - GE": [
			"success - ge",
			"success ge",
			"ge success"
		],
		"PC Follow Up": ["pc follow up", "pc follow-up"],
		"PC shared and lost": ["pc shared and lost"],
		"Fee details shared & FU": [
			"fee details shared & fu",
			"fee details shared and fu",
			"fee details shared & fu"
		],
		"Client Side delay": ["client side delay", "client-side delay"],
		"Moved to next date": ["moved to next date", "move to next date"],
		"Blocker": ["blocker"],
		"Dropped": ["dropped"],
		"Same client context": ["same client context"],
		"initial email followup": ["initial email followup"]
	}).find(([, list]) => list.includes(normalized));
	return match ? match[0] : "";
}
function normalizeUrgency(value) {
	const normalized = normalizeKey(value);
	if (!normalized) return "";
	const match = Object.entries({
		"None": [
			"none",
			"no urgency",
			"n/a"
		],
		"Urgent - RFE": [
			"urgent - rfe",
			"rfe",
			"urgent rfe"
		],
		"Urgent - NOID": [
			"urgent - noid",
			"noid",
			"urgent noid"
		],
		"Grace Period": ["grace period", "grace-period"],
		"Visa Expiry": ["visa expiry", "visa-expiry"],
		"Court Date": ["court date", "court-date"],
		"Filing Deadline": ["filing deadline", "filing-deadline"],
		"Client Distressed": ["client distressed", "client-distressed"]
	}).find(([, list]) => list.includes(normalized));
	return match ? match[0] : "";
}
function normalizeClientType(value) {
	const normalized = normalizeKey(value);
	if (!normalized) return "";
	const match = Object.entries({
		"New Client - Employer": ["new client - employer", "new client employer"],
		"Existing client - Organization": ["existing client - organization", "existing client organization"],
		"Existing client - Individual": ["existing client - individual", "existing client individual"],
		"New Client - Individual": ["new client - individual", "new client individual"],
		"Voicemail Call": ["voicemail call", "voicemail"]
	}).find(([, list]) => list.includes(normalized));
	return match ? match[0] : "";
}
function normalizeGoogleReview(value) {
	const normalized = normalizeKey(value);
	if (!normalized) return "";
	const match = Object.entries({
		"Pending": ["pending"],
		"Requested": ["requested"],
		"Submitted": ["submitted"],
		"Not Applicable": [
			"not applicable",
			"n/a",
			"na"
		]
	}).find(([, list]) => list.includes(normalized));
	return match ? match[0] : "";
}
function calculateCallCompleteness(row) {
	const fieldGroups = [
		["fd_a_client_name", "client_name"],
		["fd_a_case_type", "case_type"],
		["call_type"],
		["call_duration"],
		[
			"front_desk_person_name",
			"fd_a_case_worker_assigned",
			"agent"
		],
		["fd_a_client_type", "client_type"],
		["fd_a_urgency_flag", "urgency"],
		[
			"fd_a_call_outcome",
			"call_outcome",
			"disposition"
		],
		[
			"fd_m_case_status_change",
			"action_status",
			"case_status"
		],
		["fd_m_self_action_item"],
		["fd_m_action_due_date"],
		["fd_m_manual_action_status"],
		["fd_m_attachment"],
		["fd_m_google_review"]
	];
	const filled = fieldGroups.filter((fields) => getFirstMeaningfulValue(row, fields)).length;
	return fieldGroups.length ? filled / fieldGroups.length * 100 : 0;
}
function calculateDataQualityScore(row) {
	return Math.min(5, Math.max(0, calculateCallCompleteness(row) / 20));
}
function averageOf(rows, callback) {
	const values = rows.map((row) => callback(row)).filter((value) => Number.isFinite(value));
	if (!values.length) return 0;
	return values.reduce((sum, value) => sum + value, 0) / values.length;
}
function calculateDashboardMetrics(rows = [], startDate = null, endDate = null, s1Leads = []) {
	const safeRows = Array.isArray(rows) ? rows.filter(Boolean) : [];
	let dayCount = 1;
	if (startDate && endDate) {
		const start = /* @__PURE__ */ new Date(`${startDate}T00:00:00`);
		const end = /* @__PURE__ */ new Date(`${endDate}T00:00:00`);
		if (!Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime()) && start <= end) dayCount = Math.max(1, Math.round((end - start) / 864e5) + 1);
	} else {
		const dates = /* @__PURE__ */ new Set();
		safeRows.forEach((row) => {
			const rawDate = row?.raw?.call_recording_date_normalized || row?.raw?.call_recording_date || row?.raw?.call_date || row?.date || row?.call_recording_date;
			if (rawDate) {
				const dateStr = String(rawDate).slice(0, 10);
				if (/^\d{4}-\d{2}-\d{2}/.test(dateStr)) dates.add(dateStr);
			}
		});
		dayCount = Math.max(1, dates.size);
	}
	const totalCalls = safeRows.length;
	const leads = getUniqueLeadsFromS1Calls(safeRows, s1Leads).length;
	const paidLeads = getUniqueLeadsFromS1Calls(safeRows, s1Leads, isPaidLead).length;
	const outboundCalls = safeRows.filter(isOutboundCall).length;
	const leadsPerDay = Number((leads / dayCount).toFixed(1));
	const leadsCalledPerDay = Number((leads / dayCount).toFixed(1));
	const completedCalls = safeRows.filter((row) => {
		if (getFirstMeaningfulValue(row, ["case_closing_completed"]) === true) return true;
		const value = normalizeKey(getFirstMeaningfulValue(row, [
			"action_status",
			"status",
			"fd_m_manual_action_status",
			"fd_m_case_status_change"
		]));
		return [
			"success - pc",
			"success - ge",
			"completed",
			"resolved",
			"closed"
		].includes(value);
	}).length;
	const resolvedLeads = safeRows.filter((row) => {
		const status = normalizeCaseStatus(getFirstMeaningfulValue(row, [
			"fd_m_case_status_change",
			"action_status",
			"case_status"
		]));
		return isInboundCall(row) && ["Success - PC", "Success - GE"].includes(status);
	}).length;
	const closedLoopRate = leads ? Number((resolvedLeads / leads * 100).toFixed(1)) : 0;
	return {
		totalCalls,
		leads,
		paidLeads,
		outboundCalls,
		resolvedLeads,
		completedCalls,
		urgentCalls: safeRows.filter((row) => {
			const value = normalizeKey(getFirstMeaningfulValue(row, ["fd_a_urgency_flag", "urgency"]));
			if (!value || ["none", "unknown"].includes(value)) return false;
			if (value.includes("urgent")) return true;
			return [
				"grace period",
				"visa expiry",
				"court date",
				"filing deadline",
				"client distressed"
			].includes(value);
		}).length,
		leadsPerDay,
		leadsCalledPerDay,
		closedLoopRate,
		averageCompletenessScore: averageOf(safeRows, calculateCallCompleteness),
		averageDataQualityScore: averageOf(safeRows, calculateDataQualityScore),
		averageLeadScore: averageOf(safeRows, (row) => safeNumber(getFirstMeaningfulValue(row, ["lead_score"])) ?? calculateLeadScore(row)),
		averageBusinessOpportunityScore: averageOf(safeRows, (row) => safeNumber(getFirstMeaningfulValue(row, ["bos_score"]))),
		totalDurationSeconds: safeRows.reduce((total, row) => total + durationToSeconds(row?.billsec ?? row?.raw?.billsec), 0),
		callOutcomeBreakdown: buildFixedBreakdown(safeRows, CALL_OUTCOME_CATEGORIES, [
			"fd_a_call_outcome",
			"call_outcome",
			"disposition"
		], normalizeCallOutcome),
		caseStatusBreakdown: buildFixedBreakdown(safeRows, CASE_STATUS_CATEGORIES, [
			"fd_m_case_status_change",
			"action_status",
			"case_status"
		], normalizeCaseStatus),
		urgencyFlagBreakdown: buildFixedBreakdown(safeRows, URGENCY_FLAG_CATEGORIES, ["fd_a_urgency_flag", "urgency"], normalizeUrgency),
		callDirectionBreakdown: buildFixedBreakdown(safeRows, CALL_DIRECTION_CATEGORIES, [
			"call_direction",
			"call_type",
			"direction"
		], normalizeCallDirection),
		clientTypeBreakdown: buildFixedBreakdown(safeRows, CLIENT_TYPE_CATEGORIES, ["fd_a_client_type", "client_type"], normalizeClientType),
		googleReviewBreakdown: buildFixedBreakdown(safeRows, GOOGLE_REVIEW_CATEGORIES, ["fd_m_google_review", "google_review"], normalizeGoogleReview)
	};
}
function buildCallVolumeSeries(rows = []) {
	const safeRows = Array.isArray(rows) ? rows.filter(Boolean) : [];
	const counts = /* @__PURE__ */ new Map();
	safeRows.forEach((row) => {
		const rawDate = getFirstMeaningfulValue(row, [
			"call_recording_date_normalized",
			"call_recording_date",
			"call_date"
		]);
		const label = rawDate ? String(rawDate).slice(0, 10) : "Unknown";
		counts.set(label, (counts.get(label) || 0) + 1);
	});
	return [...counts.entries()].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([label, count]) => ({
		label,
		count
	}));
}
function buildAgentPerformance(rows = []) {
	const safeRows = Array.isArray(rows) ? rows.filter(Boolean) : [];
	const totals = /* @__PURE__ */ new Map();
	const successes = /* @__PURE__ */ new Map();
	safeRows.forEach((row) => {
		const agent = getFirstMeaningfulValue(row, [
			"front_desk_person_name",
			"fd_a_case_worker_assigned",
			"agent",
			"agent_name"
		]) || "Unassigned";
		totals.set(agent, (totals.get(agent) || 0) + 1);
		const status = normalizeCaseStatus(getFirstMeaningfulValue(row, [
			"fd_m_case_status_change",
			"action_status",
			"case_status"
		]));
		if (status === "Success - PC" || status === "Success - GE") successes.set(agent, (successes.get(agent) || 0) + 1);
	});
	return [...totals.entries()].map(([label, count]) => ({
		label,
		count,
		performance: count ? Math.round((successes.get(label) || 0) / count * 100) : 0
	})).sort((a, b) => b.count - a.count);
}
//#endregion
//#region src/data/leadNumberFilter.js
const normalizeRoutingNumber = (value) => {
	const digits = String(value || "").replace(/\D/g, "");
	return digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
};
const LEAD_NUMBER_GROUPS = Object.freeze({
	"3128781255": Object.freeze({
		routingNumbers: Object.freeze(["601"]),
		extensions: Object.freeze([
			"101",
			"102",
			"255"
		])
	}),
	"2243374447": Object.freeze({
		routingNumbers: Object.freeze(["603"]),
		extensions: Object.freeze([
			"201",
			"202",
			"4447"
		])
	}),
	"2243939098": Object.freeze({
		routingNumbers: Object.freeze(["605"]),
		extensions: Object.freeze([
			"203",
			"204",
			"205"
		])
	})
});
const LEAD_NUMBER_OPTIONS = Object.freeze([
	"3128781255",
	"2243374447",
	"2243939098"
]);
const SUPPORTED_EXTENSIONS = Object.freeze([
	"101",
	"102",
	"201",
	"202",
	"203",
	"204",
	"205",
	"3128781255",
	"2243374447",
	"2243939098"
]);
const NON_SELECTABLE_ROUTING_EXTENSIONS = Object.freeze(/* @__PURE__ */ new Set(["601", "603", "605"]));
const isSelectableExtension = (extension) => !NON_SELECTABLE_ROUTING_EXTENSIONS.has(String(extension).trim()) && !(/* @__PURE__ */ new Set(["255", "4447"])).has(String(extension).trim());
const ALIAS_EXTENSION_FOR_LEAD_NUMBER = Object.freeze({
	"3128781255": "255",
	"2243374447": "4447"
});
const getOutboundLeadNumberOptions = () => [...LEAD_NUMBER_OPTIONS];
const getLeadNumberExtensions = (leadNumber) => {
	const normalized = normalizeRoutingNumber(leadNumber);
	const group = LEAD_NUMBER_GROUPS[normalized];
	if (!group) return [];
	const extensions = group.extensions.filter(isSelectableExtension);
	return ALIAS_EXTENSION_FOR_LEAD_NUMBER[normalized] ? [...extensions, normalized] : extensions;
};
const getAllLeadExtensions = () => [.../* @__PURE__ */ new Set([...Object.values(LEAD_NUMBER_GROUPS).flatMap((group) => group.extensions).filter(isSelectableExtension), ...SUPPORTED_EXTENSIONS])].sort();
const callMatchesLeadNumber = (call, leadNumber, _allCalls = [], _syncFilters = []) => {
	if (!leadNumber) return true;
	const expected = normalizeRoutingNumber(leadNumber);
	const group = LEAD_NUMBER_GROUPS[expected];
	const source = normalizeRoutingNumber(call.sourceNumber || call.raw?.src);
	const destination = normalizeRoutingNumber(call.destinationNumber || call.raw?.dst);
	return source === expected || destination === expected || Boolean(group?.routingNumbers.includes(destination));
};
//#endregion
//#region src/data/leadSource.js
const LEAD_SOURCE_GROUPS = Object.freeze([Object.freeze({
	label: "Organic Leads",
	options: Object.freeze([
		"Client Referral",
		"Lomk Seminars",
		"Friends",
		"Family",
		"Tamil Sangam",
		"Paid Consultation",
		"Google My Business"
	])
}), Object.freeze({
	label: "Paid Leads",
	options: Object.freeze([
		"Google Search",
		"Youtube",
		"Instagram",
		"Website",
		"Email",
		"Ads/Brevo"
	])
})]);
const LEAD_SOURCE_OPTIONS = Object.freeze([
	...LEAD_SOURCE_GROUPS.flatMap((group) => group.options),
	"LOMK seminar/workshops",
	"Paid Consultation Client"
]);
//#endregion
//#region src/data/callFieldOptions.js
const CLIENT_TYPE_OPTIONS = Object.freeze([
	"New Client - Individual",
	"New Client - Organization",
	"Existing client - Individual",
	"Existing client - Organization",
	"Voicemail Call",
	"Spam / Wrong Call"
]);
const URGENCY_FLAG_OPTIONS = Object.freeze([
	"None",
	"Urgent - RFE",
	"Urgent - NOID",
	"Grace Period",
	"Visa Expiry",
	"Court Date",
	"Filing Deadline",
	"Client Distressed"
]);
const LEAD_SOURCE_DROPDOWN_OPTIONS = LEAD_SOURCE_OPTIONS;
//#endregion
//#region src/data/openCallAge.js
// Open Call Aging: how many calls are STILL OPEN and at least 3 / 5 / 10 / 15 days old.
//
// One definition of "open" for the whole Dashboard and the Calls page ("Open Cases"): a call is closed ONLY by its Case
// Status Change being Success - PC / Success - GE / Dropped, or by a confirmed closure of the same matter
// (closure_inherited). Every other status - and no status at all - is open. The Mark Completed flag and the separate
// Action Status field never decide it (they used to here, which made these cards disagree with the Calls page).
//
// Age = whole calendar days from the call's date to today's date in America/Chicago (the business date the app uses
// for Case Status dates and Close Call Aging). The buckets are cumulative (a 12-day-old call is in 3+, 5+ and 10+), so
// they overlap and must never be added up; `total` is the number of distinct open calls with a usable date, and
// `missingDate` the open calls that have no usable date (never guessed, never counted in a bucket).
const AGING_STATUSES = new Set(["", "open", "client side delay", "blocker", "dropped", "same client context"]);

const centralDay = (instant) => {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).format(instant);
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(parts);
    if (match) return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  } catch { /* no Intl time-zone support: fall back to the UTC date */ }
  return Date.UTC(instant.getUTCFullYear(), instant.getUTCMonth(), instant.getUTCDate());
};

function isOpenForAging(call) {
  // The saved Case Status Change only: blank or one of Open / Client Side Delay / Blocker / Dropped / Same Client Context.
  const status = String(call?.raw?.fd_m_case_status_change || "").trim().replace(/\s+/g, " ").toLowerCase();
  return AGING_STATUSES.has(status) && !call?.raw?.closure_inherited;
}

// Every open call with a usable date, aged; plus how many open calls had no usable date.
function summarizeOpenCallAges(calls = [], today = new Date()) {
  const end = centralDay(today);
  const seen = new Set();
  const ages = [];
  let missingDate = 0;
  for (const call of calls) {
    if (!isOpenForAging(call)) continue;
    const identity = call?.callId || call?.raw?.call_id;     // one call is never counted twice
    if (identity) { if (seen.has(identity)) continue; seen.add(identity); }
    const match = String(call?.date || call?.raw?.call_recording_date_normalized || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
    const start = match ? Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : NaN;
    const valid = match && new Date(start).getUTCMonth() === Number(match[2]) - 1;   // rejects 2026-02-31 style dates
    if (!valid) { missingDate += 1; continue; }
    ages.push(Math.max(0, Math.floor((end - start) / 86400000)));
  }
  return { ages, missingDate };
}

// The four cumulative cards (unchanged shape). `total` below is NOT their sum: the buckets overlap.
function calculateOpenCallAgeCounts(calls = [], today = new Date()) {
  const { ages } = summarizeOpenCallAges(calls, today);
  return {
    3: ages.filter((age) => age >= 3).length,
    5: ages.filter((age) => age >= 5).length,
    10: ages.filter((age) => age >= 10).length,
    15: ages.filter((age) => age >= 15).length,
  };
}

// Distinct open calls with a usable date (the population the buckets are drawn from) and those without one.
function countOpenCalls(calls = [], today = new Date()) {
  const { ages, missingDate } = summarizeOpenCallAges(calls, today);
  return { total: ages.length, missingDate };
}
//#endregion
//#region src/data/callKind.js
function isVoicemailCall(call) {
	if (!call || call.isTaskRecord) return false;
	const raw = call.raw || call;
	const aiVoicemail = String(raw.voicemail_left_if_no_answer || "").trim().toLowerCase();
	if ([
		"yes",
		"true",
		"1"
	].includes(aiVoicemail)) return true;
	return String(raw.fd_a_call_outcome || raw.call_outcome || call.callOutcome || "").trim().toLowerCase().includes("voicemail");
}
//#endregion
//#region src/data/displayLabels.js
const CLIENT_TYPE_DISPLAY_LABELS = Object.freeze({
	"New Client - Organization": "New Client - Organisation",
	"Existing client - Individual": "Existing Client - Individual",
	"Existing client - Organization": "Existing Client - Organisation"
});
function displayClientType(value) {
	return CLIENT_TYPE_DISPLAY_LABELS[value] || value;
}
//#endregion
//#region src/data/displayDate.js
function parseDateParts(value) {
	const match = String(value ?? "").trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
	if (match) return match.slice(1).map(Number);
	const parsed = new Date(value);
	if (Number.isNaN(parsed.getTime())) return null;
	return [
		parsed.getFullYear(),
		parsed.getMonth() + 1,
		parsed.getDate()
	];
}
function formatDisplayDate(value, fallback = "-") {
	if (value === null || value === void 0 || String(value).trim() === "") return fallback;
	const parts = parseDateParts(value);
	if (!parts) return String(value);
	const [year, month, day] = parts;
	return `${String(month).padStart(2, "0")}/${String(day).padStart(2, "0")}/${String(year).padStart(4, "0")}`;
}
//#endregion
//#region src/data/dashboardSummary.js
const getLocalDateString = (date = /* @__PURE__ */ new Date()) => date.toLocaleDateString("en-CA");
const CALL_LOG_STATUS_COLUMNS = [
	["vm", "VM"],
	["open", "Open"],
	["initialEmail", "Initial Email"],
	["successPC", "Success - PC"],
	["successGE", "Success - GE"],
	["pcFollowUp", "PC Follow Up"],
	["pcSharedLost", "PC shared and lost"],
	["feeDetails", "Fee details shared & FU"],
	["clientDelay", "Client Side delay"],
	["movedNextDate", "Moved to next date"],
	["blocker", "Blocker"],
	["dropped", "Dropped"],
	["sameClientContext", "Same client context"],
	["initialEmailFollowup", "initial email followup"]
];
const LEAD_REACHED_OUT_FIELDS = [
	"agent_name",
	"call_direction",
	"call_duration",
	"case_type",
	"case_worker_assigned",
	"urgency_flag",
	"redirect_escalation_to",
	"call_outcome",
	"call_summary",
	"enquiry_question"
];
function getLeadDate(item) {
	const value = item?.record?.date_received || item?.call_details?.date_received;
	return /^\d{4}-\d{2}-\d{2}/.test(String(value || "")) ? String(value).slice(0, 10) : "";
}
const CASE_STATUS_OPTIONS = [
	"Open",
	"Client Side Delay",
	"Blocker",
	"Dropped",
	"Same Client Context",
	"Wrong Number Dialed",
	"Initial email sent (Fee Details shared)",
	"Initial Email Follow Up",
	"Fee details shared & FU",
	"PC link shared",
	"PC Follow Up",
	"Success PC",
	"Success GE"
];
// Spellings used before the dropdown change (and still fed to the formulas below) count in the column of their new wording.
const LEGACY_STATUS_LABELS = {
	"success - pc": "Success PC",
	"success - ge": "Success GE",
	"initial email followup": "Initial Email Follow Up",
	"initial email sent.": "Initial email sent (Fee Details shared)"
};
// Exact Case Status Change dropdown label for a lower-cased value (the Call Log Status columns are these labels).
const CASE_STATUS_LABEL_BY_LOWER = {
	...LEGACY_STATUS_LABELS,
	...Object.fromEntries(CASE_STATUS_OPTIONS.map((label) => [label.toLowerCase(), label]))
};
const CASE_STATUS_KEYS = {
	"initial email sent.": "initialEmail",
	"success - pc": "successPC",
	"success - ge": "successGE",
	"pc follow up": "pcFollowUp",
	"pc shared and lost": "pcSharedLost",
	"fee details shared & fu": "feeDetails",
	"client side delay": "clientDelay",
	"move to next date": "movedNextDate",
	blocker: "blocker",
	dropped: "dropped",
	"same client context": "sameClientContext",
	"initial email followup": "initialEmailFollowup"
};
function buildWebsitePaidAdsRows(leads, startDate, endDate) {
	return buildCallLogStatusRows([], startDate, endDate).map((row) => row.date).map((date) => {
		const dayLeads = (Array.isArray(leads) ? leads : []).filter((item) => getLeadDate(item) === date);
		const result = {
			date,
			received: dayLeads.length,
			reachedOut: 0,
			vm: 0,
			open: 0,
			initialEmail: 0,
			successPC: 0,
			successGE: 0,
			pcFollowUp: 0,
			pcSharedLost: 0,
			feeDetails: 0,
			clientDelay: 0,
			movedNextDate: 0,
			blocker: 0,
			dropped: 0,
			sameClientContext: 0,
			initialEmailFollowup: 0,
			qualityTotal: 0,
			qualityCount: 0,
			duration: 0
		};
		dayLeads.forEach((item) => {
			const record = item.record || {};
			if (LEAD_REACHED_OUT_FIELDS.some((key) => String(record[key] ?? "").trim())) result.reachedOut += 1;
			const outcome = String(record.call_outcome || "").trim().toLowerCase();
			const status = String(record.outbound_call_status || "").trim().toLowerCase();
			if (outcome === "vm" || outcome.includes("voicemail")) result.vm += 1;
			if (status === "open") result.open += 1;
			const statusKey = CASE_STATUS_KEYS[status];
			if (statusKey) result[statusKey] += 1;
			if (String(record.blocker_comment || "").trim()) result.blocker += 1;
			const quality = Number(record.data_quality_score ?? record.overall_data_quality_score);
			if (Number.isFinite(quality)) {
				result.qualityTotal += quality;
				result.qualityCount += 1;
			}
			result.duration += getNumericDuration(record.call_duration);
		});
		result.avgQuality = result.qualityCount ? Number((result.qualityTotal / result.qualityCount).toFixed(1)) : null;
		return result;
	});
}
const CALL_DIRECTION_TABLE_COLUMNS = [
	["Direct Inbound", "Direct Inbound"],
	["Direct Outbound", "Direct Outbound"],
	["Brevo Outbound", "Leads Outbound"],
	["Missed Call Outbound", "Missed Call Outbound"],
	["WA Inbound", "WA Inbound"],
	["WA Outbound", "WA Outbound"]
];
const URGENCY_TABLE_COLUMNS = URGENCY_FLAG_OPTIONS.map((value) => [value, value]);
const CLIENT_TYPE_TABLE_COLUMNS = CLIENT_TYPE_OPTIONS.map((value) => [value, displayClientType(value)]);
const LEADS_TABLE_COLUMNS = LEAD_SOURCE_DROPDOWN_OPTIONS.map((value) => [value, value]);
const CASE_TYPE_ENQUIRY_TYPES = [
	"H1B",
	"H4EAD",
	"F1",
	"I14A"
];
function getCallDate(row) {
	const value = row?.date || row?.raw?.call_recording_date_normalized || row?.raw?.call_recording_date;
	return /^\d{4}-\d{2}-\d{2}/.test(String(value || "")) ? String(value).slice(0, 10) : "";
}
const CLOSED_CASE_STATUSES = /* @__PURE__ */ new Set(["success - pc", "success - ge", "dropped"]);
function isOpenCaseRow(row) {
	return !CLOSED_CASE_STATUSES.has(String(row?.raw?.fd_m_case_status_change || row?.raw?.action_status || "").trim().toLowerCase()) && !row?.raw?.closure_inherited;
}
function buildCallLogStatusRows(rows, startDate, endDate) {
	const dates = [];
	const start = /* @__PURE__ */ new Date(`${startDate}T00:00:00`);
	const end = /* @__PURE__ */ new Date(`${endDate}T00:00:00`);
	if (!Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime()) && start <= end) for (const cursor = new Date(start); cursor <= end; cursor.setDate(cursor.getDate() + 1)) dates.push(getLocalDateString(cursor));
	rows.forEach((row) => {
		const rowDate = getCallDate(row);
		if (rowDate && (!startDate || rowDate >= startDate) && (!endDate || rowDate <= endDate) && !dates.includes(rowDate)) dates.push(rowDate);
	});
	dates.sort();
	return dates.map((date) => {
		const dayRows = rows.filter((row) => getCallDate(row) === date);
		const result = {
			date,
			total: dayRows.length,
			avgQuality: 0,
			duration: 0
		};
		CALL_LOG_STATUS_COLUMNS.forEach(([key]) => {
			result[key] = 0;
		});
		result.openLeads = 0;
		// Per Case Status Change dropdown label (exact option text): each column counts the calls whose saved status is that option,
		// "Open" included (a call with no status chosen is not in any column). `open` below is the dashboard's broader open rule, kept for other readers.
		result.statusCounts = Object.fromEntries(CASE_STATUS_OPTIONS.map((label) => [label, 0]));
		result.noStatus = 0;
		result.otherStatus = 0;
		result.lostCounts = {};
		dayRows.forEach((row) => {
			// The persisted Case Status Change only (the field the dropdown writes). The separate Action Status
			// field is a different thing and is never used to work out a case status.
			const status = String(row?.raw?.fd_m_case_status_change || "").trim().toLowerCase();
			if (isVoicemailCall(row)) result.vm += 1;
			// Open = every Case Status Change except Success - PC / Success - GE / Dropped (blank counts as open).
			const inherited = Boolean(row?.raw?.closure_inherited);
			if (!CLOSED_CASE_STATUSES.has(status) && !inherited) result.open += 1;
			// Call Updates "Open Leads": every call whose Case Status Change is
			// anything but a terminal value (Success - PC / Success - GE / Dropped),
			// including calls with no Case Status Change selected.
			if (!CLOSED_CASE_STATUSES.has(String(row?.raw?.fd_m_case_status_change || "").trim().toLowerCase()) && !row?.raw?.closure_inherited) result.openLeads += 1;
			const statusKey = CASE_STATUS_KEYS[status];
			if (statusKey) result[statusKey] += 1;
			const statusLabel = CASE_STATUS_LABEL_BY_LOWER[status];
			if (statusLabel) result.statusCounts[statusLabel] += 1;
			if (!status) { if (!inherited) result.noStatus += 1; } else if (!statusLabel) result.otherStatus += 1;
			if (status.endsWith(" & lost")) {
				const lostLabel = String(row?.raw?.fd_m_case_status_change || "").trim();
				result.lostCounts[lostLabel] = (result.lostCounts[lostLabel] || 0) + 1;
			}
			result.avgQuality += calculateDashboardMetrics([row]).averageDataQualityScore;
			const parts = String(row?.duration || "").split(":").map(Number);
			result.duration += parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : Number(row?.duration) || 0;
		});
		result.avgQuality = dayRows.length ? Number((result.avgQuality / dayRows.length).toFixed(1)) : 0;
		return result;
	});
}
const DAILY_CALL_TARGET = 35;
function buildDailyCallTargetRows(rows, agentNames, startDate, endDate) {
	const dates = buildCallLogStatusRows([], startDate, endDate).map((row) => row.date);
	const result = [];
	dates.forEach((date) => {
		agentNames.forEach((agentName) => {
			const callsMade = rows.filter((row) => (row.agent || "").trim() === agentName && getCallDate(row) === date && Boolean(row.raw?.fd_a_call_summary) && !isVoicemailCall(row)).length;
			const difference = callsMade - DAILY_CALL_TARGET;
			result.push({
				agent: agentName,
				date,
				callsMade,
				target: DAILY_CALL_TARGET,
				difference
			});
		});
	});
	return result;
}
function normalizeEnquiryCaseType(value) {
	const normalized = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
	if (normalized.includes("H4EAD")) return "H4EAD";
	if (normalized.includes("H1B")) return "H1B";
	if (normalized === "F1" || normalized.startsWith("F1VISA")) return "F1";
	if ([
		"I14A",
		"I140",
		"FORMI140"
	].some((type) => normalized.includes(type))) return "I14A";
	return "";
}
function getEnquiryQuestion(row) {
	return String(row?.raw?.fd_a_enquiry_question || row?.raw?.enquiry_question || row?.raw?.enquiry_details || "").trim();
}
function buildCaseTypeEnquiryRows(rows, startDate, endDate) {
	const start = /* @__PURE__ */ new Date(`${startDate}T00:00:00`);
	const end = /* @__PURE__ */ new Date(`${endDate}T00:00:00`);
	if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) return [];
	const buckets = [];
	for (const cursor = new Date(start); cursor <= end;) {
		const year = cursor.getFullYear();
		const month = cursor.getMonth();
		const week = Math.floor((cursor.getDate() - 1) / 7) + 1;
		const naturalStart = new Date(year, month, (week - 1) * 7 + 1);
		const naturalEnd = new Date(year, month, Math.min(week * 7, new Date(year, month + 1, 0).getDate()));
		const bucketStart = naturalStart < start ? new Date(start) : naturalStart;
		const bucketEnd = naturalEnd > end ? new Date(end) : naturalEnd;
		buckets.push({
			key: `${year}-${month}-${week}`,
			label: `${formatDisplayDate(getLocalDateString(cursor))} – Week ${week}`,
			range: `${formatDisplayDate(getLocalDateString(bucketStart))} – ${formatDisplayDate(getLocalDateString(bucketEnd))}`,
			start: getLocalDateString(bucketStart),
			end: getLocalDateString(bucketEnd)
		});
		cursor.setTime(naturalEnd.getTime());
		cursor.setDate(cursor.getDate() + 1);
	}
	return buckets.map((bucket) => ({
		...bucket,
		types: CASE_TYPE_ENQUIRY_TYPES.map((type) => {
			const questions = rows.filter((row) => {
				const date = getCallDate(row);
				return date >= bucket.start && date <= bucket.end && normalizeEnquiryCaseType(row.caseType || row.raw?.fd_a_case_type || row.raw?.case_type) === type;
			}).map(getEnquiryQuestion).filter(Boolean);
			return {
				type,
				questions: [...new Set(questions)]
			};
		})
	}));
}
function buildCallLoopClosingSummary(rows, startDate, endDate) {
	const finishedStatuses = /* @__PURE__ */ new Map([
		["success pc", "successPC"],
		["success ge", "successGE"],
		["dropped", "dropped"]
	]);
	const summary = {
		startDate,
		endDate,
		successPC: 0,
		successGE: 0,
		dropped: 0,
		closedCases: 0,
		averageDays: 0
	};
	const closingDays = [];
	rows.forEach((row) => {
		const status = String(row.raw?.fd_m_case_status_change || row.raw?.action_status || "").trim().toLowerCase().replace(/\s*-\s*/g, " ");
		const statusKey = finishedStatuses.get(status);
		if (!statusKey) return;
		summary[statusKey] += 1;
		summary.closedCases += 1;
		const opened = toDateOnly(row.raw?.registered_at || row.raw?.created_at || row.raw?.call_date || row.registeredAt || row.date);
		const closed = toDateOnly(row.raw?.fd_m_date_entry || row.raw?.action_date || row.raw?.case_closing_completed_at);
		if (!opened || !closed) return;
		const openedDate = /* @__PURE__ */ new Date(`${opened}T00:00:00`);
		const closedDate = /* @__PURE__ */ new Date(`${closed}T00:00:00`);
		if (!Number.isNaN(openedDate.getTime()) && !Number.isNaN(closedDate.getTime()) && closedDate >= openedDate) closingDays.push(Math.round((closedDate - openedDate) / 864e5));
	});
	summary.averageDays = closingDays.length ? Number((closingDays.reduce((total, days) => total + days, 0) / closingDays.length).toFixed(1)) : 0;
	return summary;
}
function normalizeTableValue(value) {
	return String(value || "").trim().toLowerCase().replace(/whatsapp/g, "wa").replace(/leads/g, "brevo").replace(/[-_]/g, " ").replace(/\s+/g, " ");
}
function buildDateCategoryRows(rows, startDate, endDate, columns, getValue) {
	return buildCallLogStatusRows(rows, startDate, endDate).map((row) => row.date).map((date) => {
		const dayRows = rows.filter((row) => getCallDate(row) === date);
		const counts = Object.fromEntries(columns.map(([key]) => [key, 0]));
		dayRows.forEach((row) => {
			const value = getValue(row);
			const match = columns.find(([key]) => normalizeTableValue(key) === normalizeTableValue(value));
			if (match) counts[match[0]] += 1;
		});
		return {
			date,
			counts
		};
	});
}
function getLeadTableValue(row) {
	return row.raw?.fd_a_lead_source || row.raw?.lead_source || "";
}
function getCallDirectionTableValue(row) {
	const direction = String(row.direction || "").trim().toLowerCase();
	if (direction === "outbound") return "Direct Outbound";
	if (direction === "inbound") return "Direct Inbound";
	return "";
}
function toDateOnly(value) {
	const text = String(value || "");
	return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : "";
}
function average(values) {
	const valid = values.filter((value) => Number.isFinite(value));
	return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : 0;
}
function getNumericDuration(value) {
	const text = String(value || "");
	const parts = text.split(":").map(Number);
	if (parts.length === 3 && parts.every(Number.isFinite)) return parts[0] * 3600 + parts[1] * 60 + parts[2];
	const numeric = Number(text.replace(/[^\d.]/g, ""));
	return Number.isFinite(numeric) ? numeric : 0;
}
function getOpenDays(row) {
	const registered = toDateOnly(row.raw?.registered_at || row.raw?.created_at || row.raw?.call_date || row.date);
	if (!registered) return 0;
	const status = String(row.raw?.fd_m_case_status_change || row.raw?.action_status || "").toLowerCase();
	const changed = toDateOnly(row.raw?.fd_m_date_entry || row.raw?.action_date);
	const end = changed && !["open", "pc follow up"].includes(status) ? changed : getLocalDateString();
	const startDate = /* @__PURE__ */ new Date(`${registered}T00:00:00`);
	const endDate = /* @__PURE__ */ new Date(`${end}T00:00:00`);
	return Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime()) ? 0 : Math.max(0, Math.round((endDate - startDate) / 864e5));
}
function getAverageOpenCaseDuration(rows) {
	return Number(average(rows.filter((row) => String(row.raw?.fd_m_case_status_change || row.raw?.action_status || "").toLowerCase() === "open").map(getOpenDays)).toFixed(1));
}
function getPerformanceAgent(row) {
	return String(row.agent || row.raw?.front_desk_person_name || row.raw?.fd_a_case_worker_assigned || "Unassigned").trim() || "Unassigned";
}
function buildPerformanceRows(rows, startDate, endDate) {
	return buildCallLogStatusRows(rows, startDate, endDate).map((row) => row.date).map((date) => {
		const dayRows = rows.filter((row) => getCallDate(row) === date);
		const statuses = dayRows.map((row) => String(row.raw?.fd_m_case_status_change || row.raw?.action_status || "").toLowerCase());
		const result = {
			date,
			enquiries: dayRows.length,
			successPC: statuses.filter((status) => status === "success - pc").length,
			successGE: statuses.filter((status) => status === "success - ge").length,
			quality: Math.round(average(dayRows.map((row) => Number(row.raw?.overall_call_quality_score)).filter(Number.isFinite))),
			closingDuration: average(dayRows.map((row) => getNumericDuration(row.duration))),
			openCases: dayRows.filter(isOpenCaseRow).length,
			dataQuality: Number(average(dayRows.map((row) => calculateDashboardMetrics([row]).averageDataQualityScore)).toFixed(1)),
			business: dayRows.reduce((total, row) => {
				const business = calculatePriority(row).business;
				return business.score === null || business.value === null ? total : total + business.value;
			}, 0),
			reviews: dayRows.filter((row) => String(row.raw?.fd_m_google_review || "").toLowerCase() === "submitted").length,
			averageOpenDays: getAverageOpenCaseDuration(dayRows),
			averageClosingDays: buildCallLoopClosingSummary(dayRows).averageDays
		};
		result.openCases = Number(result.openCases.toFixed(1));
		return result;
	});
}
function buildAgentRankingRows(rows, agentDirectory = []) {
	const grouped = /* @__PURE__ */ new Map();
	const agentsById = new Map(agentDirectory.map((agent) => [String(agent.id || "").trim(), agent]).filter(([id, agent]) => id && agent?.name));
	const agentsByName = new Map(agentDirectory.map((agent) => [String(agent.name || "").trim().toLowerCase(), agent]).filter(([name, agent]) => name && agent?.id));
	agentDirectory.forEach((agent) => grouped.set(agent.id, {
		agent,
		rows: []
	}));
	rows.forEach((row) => {
		const agent = agentsById.get(String(row.agentId || "").trim()) || agentsByName.get(getPerformanceAgent(row).toLowerCase());
		if (agent) grouped.get(agent.id).rows.push(row);
	});
	return [...grouped.values()].map(({ agent, rows: agentRows }) => {
		const performanceRows = buildPerformanceRows(agentRows, agentRows.map(getCallDate).filter(Boolean).sort()[0] || getLocalDateString(), getLocalDateString()).filter((row) => row.enquiries || row.successPC || row.successGE || row.quality || row.dataQuality);
		const totalCalls = agentRows.length;
		const successCount = agentRows.filter((row) => ["success - pc", "success - ge"].includes(String(row.raw?.fd_m_case_status_change || row.raw?.action_status || "").toLowerCase())).length;
		const reviews = agentRows.filter((row) => String(row.raw?.fd_m_google_review || "").toLowerCase() === "submitted").length;
		const business = agentRows.filter((row) => String(row.raw?.business_opportunity || "").toLowerCase() === "yes").length;
		const quality = Math.round(average(agentRows.map((row) => Number(row.raw?.overall_call_quality_score)).filter(Number.isFinite)));
		const dataQuality = Number(average(agentRows.map((row) => calculateDashboardMetrics([row]).averageDataQualityScore)).toFixed(1));
		const weightedPercent = quality * .25 + dataQuality / 5 * 100 * .25 + successCount / Math.max(1, totalCalls) * 100 * .25 + reviews / Math.max(1, totalCalls) * 100 * .15 + business / Math.max(1, totalCalls) * 100 * .1;
		const performanceScore = Number((Math.max(0, Math.min(100, weightedPercent)) / 10).toFixed(1));
		return {
			agent: agent.name,
			extensions: (agent.extensions || []).join(", ") || "-",
			enquiries: performanceRows.reduce((sum, row) => sum + row.enquiries, 0),
			successPC: agentRows.filter((row) => String(row.raw?.fd_m_case_status_change || row.raw?.action_status || "").toLowerCase() === "success - pc").length,
			successGE: agentRows.filter((row) => String(row.raw?.fd_m_case_status_change || row.raw?.action_status || "").toLowerCase() === "success - ge").length,
			quality,
			closingDuration: average(agentRows.map((row) => getNumericDuration(row.duration))),
			openCases: agentRows.filter(isOpenCaseRow).length,
			averageClosingDays: buildCallLoopClosingSummary(agentRows).averageDays,
			averageOpenDays: getAverageOpenCaseDuration(agentRows),
			dataQuality,
			business,
			reviews,
			performanceScore,
			totalCalls
		};
	}).sort((a, b) => b.performanceScore - a.performanceScore);
}
function computeDashboardSummary({ calls = [], dailyTargetCalls = [], leads = [], fdAgents = [], syncFilters = [], dateFrom, dateTo, selectedAgent = "all", selectedNumber = "", selectedLeadNumber = "" }) {
	const draftNumber = selectedNumber;
	const derive = (fn) => fn();
	const performanceCalls = calls.map((row) => ({
		agent: row.agent || row.raw?.front_desk_person_name || row.raw?.fd_a_case_worker_assigned || "-",
		date: row.date || row.raw?.call_recording_date_normalized || row.raw?.call_recording_date || "",
		duration: row.duration || row.raw?.call_duration || "-",
		raw: row.raw || row
	}));
	const agentOptions = derive(() => fdAgents.map((agent) => agent.name), [fdAgents]);
	const numberOptions = derive(() => {
		return getOutboundLeadNumberOptions(syncFilters, calls);
	}, [calls, syncFilters]);
	const extensionOptions = derive(() => {
		return draftNumber ? getLeadNumberExtensions(draftNumber) : getAllLeadExtensions();
	}, [draftNumber]);
	const filteredCalls = derive(() => {
		return calls.filter((row) => {
			const agent = row.agent || "";
			const extension = String(row.extension || "");
			const matchesLeadNumber = callMatchesLeadNumber(row, selectedNumber, calls, syncFilters);
			return (selectedAgent === "all" || String(agent).trim() === selectedAgent) && matchesLeadNumber && (!selectedLeadNumber || extension === selectedLeadNumber || extension === ALIAS_EXTENSION_FOR_LEAD_NUMBER[selectedLeadNumber]);
		});
	}, [
		calls,
		selectedAgent,
		selectedNumber,
		selectedLeadNumber,
		syncFilters
	]);
	const filteredDailyTargetCalls = derive(() => {
		return dailyTargetCalls.filter((row) => {
			const agent = row.agent || "";
			const extension = String(row.extension || "");
			const matchesLeadNumber = callMatchesLeadNumber(row, selectedNumber, dailyTargetCalls, syncFilters);
			return (selectedAgent === "all" || String(agent).trim() === selectedAgent) && matchesLeadNumber && (!selectedLeadNumber || extension === selectedLeadNumber || extension === ALIAS_EXTENSION_FOR_LEAD_NUMBER[selectedLeadNumber]);
		});
	}, [
		dailyTargetCalls,
		selectedAgent,
		selectedNumber,
		selectedLeadNumber,
		syncFilters
	]);
	const filteredLeads = derive(() => {
		const permittedExtensions = selectedNumber ? new Set(getLeadNumberExtensions(selectedNumber)) : null;
		return leads.filter((item) => {
			const record = item.record || {};
			const extension = String(record.extension || item.call_details?.extension || "");
			const agent = String(record.owner || record.agent_name || "").trim();
			return (selectedAgent === "all" || agent === selectedAgent) && (!permittedExtensions || permittedExtensions.has(extension)) && (!selectedLeadNumber || extension === selectedLeadNumber);
		});
	}, [
		leads,
		selectedAgent,
		selectedNumber,
		selectedLeadNumber
	]);
	const metrics = derive(() => calculateDashboardMetrics(filteredCalls, dateFrom, dateTo, filteredLeads), [
		filteredCalls,
		dateFrom,
		dateTo,
		filteredLeads
	]);
	const callKindCounts = derive(() => filteredDailyTargetCalls.filter((call) => !call.isTaskRecord).reduce((counts, call) => {
		counts.total += 1;
		if (isVoicemailCall(call)) counts.voicemail += 1;
		const agentName = String(call.agent || "").trim();
		if (agentName && agentName !== "-") {
			const direction = String(call.direction || "").trim().toLowerCase();
			if (direction === "inbound") counts.inbound += 1;
			else if (direction === "outbound") counts.outbound += 1;
		}
		return counts;
	}, {
		total: 0,
		voicemail: 0,
		inbound: 0,
		outbound: 0
	}), [filteredDailyTargetCalls]);
	const priorityMetrics = derive(() => filteredCalls.reduce((totals, call) => {
		const priority = calculatePriority(call);
		totals.bosTotal += priority.business.value || 0;
		if (priority.business.value !== null) totals.bosCount += 1;
		if (priority.leadScore !== null) totals[priority.leadBand] += 1;
		if (priority.urgencyOverride) totals.urgency += 1;
		totals[priority.businessTier] += 1;
		return totals;
	}, {
		BOOK_NOW: 0,
		bosTotal: 0,
		bosCount: 0,
		GATHER_MORE_INFO: 0,
		NURTURE: 0,
		urgency: 0,
		HIGH: 0,
		HIGH_MID: 0,
		STANDARD: 0,
		N_A: 0
	}), [filteredCalls]);
	const openCallAges = derive(() => calculateOpenCallAgeCounts(filteredCalls), [filteredCalls]);
	const leadPriorityMetrics = derive(() => filteredLeads.reduce((totals, lead) => {
		const score = calculatePriority(lead).leadScore ?? (Number.isFinite(Number(lead.lead_score)) ? Number(lead.lead_score) : null);
		const bos = calculatePriority(lead).business.value;
		if (Number.isFinite(score)) {
			totals[score >= 70 ? "BOOK_NOW" : score >= 40 ? "GATHER_MORE_INFO" : "NURTURE"] += 1;
			totals.leadTotal += score;
			totals.leadCount += 1;
		}
		if (Number.isFinite(bos)) {
			totals.bosTotal += bos;
			totals.bosCount += 1;
		}
		return totals;
	}, {
		BOOK_NOW: 0,
		GATHER_MORE_INFO: 0,
		NURTURE: 0,
		leadTotal: 0,
		leadCount: 0,
		bosTotal: 0,
		bosCount: 0
	}), [filteredLeads]);
	const callLogStatusRows = derive(() => buildCallLogStatusRows(filteredDailyTargetCalls.filter((call) => !call.isTaskRecord), dateFrom, dateTo), [
		filteredDailyTargetCalls,
		dateFrom,
		dateTo
	]);
	const dailyCallTargetRows = derive(() => buildDailyCallTargetRows(filteredDailyTargetCalls, selectedAgent === "all" ? agentOptions : [selectedAgent], dateFrom, dateTo), [
		filteredDailyTargetCalls,
		agentOptions,
		selectedAgent,
		dateFrom,
		dateTo
	]);
	const websitePaidAdsRows = derive(() => buildWebsitePaidAdsRows(filteredLeads, dateFrom, dateTo), [
		filteredLeads,
		dateFrom,
		dateTo
	]);
	const callDirectionRows = derive(() => buildDateCategoryRows(filteredCalls, dateFrom, dateTo, CALL_DIRECTION_TABLE_COLUMNS, getCallDirectionTableValue), [
		filteredCalls,
		dateFrom,
		dateTo
	]);
	const urgencyRows = derive(() => buildDateCategoryRows(filteredCalls, dateFrom, dateTo, URGENCY_TABLE_COLUMNS, (row) => row.raw?.fd_a_urgency_flag || row.raw?.urgency), [
		filteredCalls,
		dateFrom,
		dateTo
	]);
	const clientTypeRows = derive(() => buildDateCategoryRows(filteredCalls, dateFrom, dateTo, CLIENT_TYPE_TABLE_COLUMNS, (row) => row.raw?.fd_a_client_type || row.raw?.client_type), [
		filteredCalls,
		dateFrom,
		dateTo
	]);
	const leadsRows = derive(() => buildDateCategoryRows(filteredCalls, dateFrom, dateTo, LEADS_TABLE_COLUMNS, getLeadTableValue), [
		filteredCalls,
		dateFrom,
		dateTo
	]);
	const caseTypeEnquiryRows = derive(() => buildCaseTypeEnquiryRows(filteredCalls, dateFrom, dateTo), [
		filteredCalls,
		dateFrom,
		dateTo
	]);
	const performanceRows = derive(() => {
		return buildPerformanceRows(selectedAgent === "all" ? performanceCalls : performanceCalls.filter((row) => String(row.agent || "").trim() === selectedAgent), dateFrom, dateTo);
	}, [
		performanceCalls,
		selectedAgent,
		dateFrom,
		dateTo
	]);
	const callLoopClosing = derive(() => buildCallLoopClosingSummary(filteredCalls, dateFrom, dateTo), [
		filteredCalls,
		dateFrom,
		dateTo
	]);
	const rankingSource = derive(() => {
		const normalizedRows = performanceCalls.map((row) => ({
			...row,
			agent: row.agent || row.raw?.front_desk_person_name || "",
			agentId: row.raw?.front_desk_agent_id || "",
			extension: row.raw?.extension || "-"
		}));
		if (selectedAgent === "all") return normalizedRows;
		const selected = selectedAgent.trim().toLowerCase();
		return normalizedRows.filter((row) => getPerformanceAgent(row).toLowerCase() === selected);
	}, [selectedAgent, performanceCalls]);
	return {
		metrics,
		callKindCounts,
		priorityMetrics,
		openCallAges,
		leadPriorityMetrics,
		callLogStatusRows,
		dailyCallTargetRows,
		websitePaidAdsRows,
		callDirectionRows,
		urgencyRows,
		clientTypeRows,
		leadsRows,
		caseTypeEnquiryRows,
		performanceRows,
		callLoopClosing,
		rankingRows: derive(() => buildAgentRankingRows(rankingSource, fdAgents), [rankingSource, fdAgents]),
		callVolumeSeries: derive(() => buildCallVolumeSeries(filteredCalls), [filteredCalls]),
		agentPerformance: derive(() => buildAgentPerformance(filteredCalls), [filteredCalls]),
		agentOptions,
		numberOptions,
		extensionOptions,
		fdAgents,
		syncFilters
	};
}
//#endregion
//#region src/data/callTaskRecords.js
const TASK_LABELS = {
	missed_call: "Missed Call Task",
	non_responsiveness: "Non-Responsive Task"
};
function taskToCallsRecord(task = {}) {
	if (!Object.hasOwn(TASK_LABELS, task.task_type)) return null;
	const data = task.data || {};
	const missedCall = task.task_type === "missed_call";
	const label = TASK_LABELS[task.task_type];
	const recordDate = missedCall ? data.missed_call_date || data.miscall_added_date : data.date_added;
	const phone = missedCall ? data.contact_number : data.phone_number;
	const summary = data.summary || data.comments || "";
	const actionStatus = data.action_status || data.status || "Open";
	const caseType = missedCall ? "Missed Call" : data.type || "Non-Responsive Call";
	const raw = {
		task_id: task.id,
		task_type: task.task_type,
		record_source: "task",
		fd_a_client_name: data.name || "",
		fd_a_client_type: caseType,
		fd_a_email_address: data.email || "",
		fd_a_call_summary: summary,
		fd_m_self_action_item: caseType,
		fd_m_manual_action_status: actionStatus,
		fd_m_blocker_comment: data.comments || ""
	};
	return {
		id: `TASK-${String(task.id || "").slice(0, 8)}`,
		callId: "",
		taskId: task.id,
		clientId: "",
		analysisId: null,
		agent: task.owner_name || task.assigned_by_name || "-",
		direction: label,
		date: recordDate || "-",
		registeredAt: task.submitted_at || "",
		timestamp: "-",
		duration: "-",
		billsec: null,
		extension: "",
		parentNumber: "",
		sourceNumber: phone || "",
		destinationNumber: "",
		capturedNumber: phone || "-",
		capturedNumbers: phone ? [phone] : [],
		clientNumber: phone || "-",
		caseType,
		status: "task-record",
		reviewStatusLabel: label,
		notes: summary,
		transcript: "",
		hasAudio: false,
		isTaskRecord: true,
		recordLabel: label,
		raw
	};
}
function callTaskRecords(tasks = []) {
	return tasks.map(taskToCallsRecord).filter(Boolean);
}
//#endregion
//#region src/data/callRecordProjection.js
const STAGE_2_FIELDS = [
	"fd_m_self_action_item",
	"fd_m_action_due_date",
	"fd_m_manual_action_status",
	"fd_m_dependency",
	"fd_m_follow_up_dates",
	"fd_m_follow_up_attempts",
	"fd_m_blocker_comment"
];
const STAGE_3_FIELDS = [
	"fd_m_case_status_change",
	"fd_m_date_entry",
	"fd_m_verification_comment",
	"fd_m_attachment",
	"fd_m_stage4_attachment"
];
const STAGE_4_FIELDS = ["fd_m_google_review"];
function hasMeaningfulValue(value) {
	return value !== null && value !== void 0 && String(value).trim() !== "";
}
function formatAudioDuration(value) {
	const seconds = Number(value);
	if (!Number.isFinite(seconds) || seconds < 0) return value || "-";
	const wholeSeconds = Math.floor(seconds);
	return `${Math.floor(wholeSeconds / 60)}:${String(wholeSeconds % 60).padStart(2, "0")}`;
}
function calculateReviewStatus(record = {}) {
	const manualFields = [
		...STAGE_2_FIELDS,
		...STAGE_3_FIELDS,
		...STAGE_4_FIELDS
	];
	const filledCount = manualFields.filter((field) => hasMeaningfulValue(record[field])).length;
	if (filledCount === 0) return "needs-review";
	if (filledCount === manualFields.length) return "call-reviewed";
	return "partially-reviewed";
}
function getReviewStatusLabel(status) {
	const labels = {
		"needs-review": "Needs Review",
		"partially-reviewed": "Pending Calls",
		"call-reviewed": "Completed Calls"
	};
	return labels[status] || labels["needs-review"];
}
function _buildCallRecordsWithTasks(rows, calls, tasks, leads, { includeUnmatchedTasks = true } = {}) {
	const callsById = new Map(calls.map((c) => [c.call_id, c]));
	const analysisById = new Map(rows.map((row) => [row.call_id, row]));
	const callRecords = [...rows, ...calls.filter((call) => !analysisById.has(call.call_id))].map((row) => {
		const call = callsById.get(row.call_id);
		const assignedAgent = row.assigned_agent_name || call?.assigned_agent_name || "";
		const agent = row.front_desk_person_name || call?.front_desk_person_name || "-";
		const rawAgent = row.raw_front_desk_person_name || "";
		String(call?.client_name || row.fd_m_client_name || row.fd_a_client_name || "").trim();
		const reviewStatus = calculateReviewStatus(row);
		const direction = row.call_direction || row.call_type || call?.call_type || "Not Provided";
		const clientPhoneNumber = /outbound/i.test(String(direction)) ? call?.dst || row.dst || "" : /inbound/i.test(String(direction)) ? call?.src || row.src || "" : "";
		const capturedNumbers = [...new Set([
			call?.src,
			call?.dst,
			row.src,
			row.dst
		].map((value) => String(value || "").trim()).filter(Boolean))];
		return {
			id: row.formatted_call_id || row.call_reference || row.call_id,
			callId: row.call_id,
			clientId: row.client_id || call?.client_id || "",
			leadId: row.lead_id || call?.lead_id || "",
			analysisId: row.id,
			agent,
			rawAgent,
			assignedAgent,
			direction,
			date: row.call_recording_date_normalized || row.call_recording_date || call?.call_date || "-",
			registeredAt: call?.registered_at || row.registered_at || row.created_at || "",
			timestamp: row.call_time || call?.call_time || "-",
			duration: formatAudioDuration(row.call_duration || call?.billsec || row.billsec),
			billsec: call?.billsec ?? row.billsec ?? null,
			extension: call?.extension || row.extension || "-",
			parentNumber: call?.parent_number || row.parent_number || "",
			sourceNumber: call?.src || row.src || "",
			destinationNumber: call?.dst || row.dst || "",
			clientPhoneNumber,
			capturedNumber: capturedNumbers[0] || "-",
			capturedNumbers,
			clientNumber: row.client_phone_number || row.phone_number || row.client_number || call?.src || call?.dst || "-",
			caseType: row.case_type || "Not Provided",
			status: reviewStatus,
			reviewStatusLabel: getReviewStatusLabel(reviewStatus),
			notes: row.evaluator_notes || "",
			transcript: call?.transcript || row.transcript || "",
			hasAudio: Boolean(call?.audio_s3_uri || call?.audio_s3_key || row.audio_s3_uri || row.audio_s3_key),
			selfActionItem: row.fd_m_self_action_item || "",
			actionDueDate: row.fd_m_action_due_date || "",
			manualActionStatus: row.fd_m_manual_action_status || "",
			dependency: row.fd_m_dependency || "",
			followUpDates: row.fd_m_follow_up_dates || "",
			followUpAttempts: row.fd_m_follow_up_attempts ?? "",
			blockerComment: row.fd_m_blocker_comment || "",
			raw: {
				...row,
				fd_a_client_name: call?.client_name || row.fd_a_client_name || "",
				fd_a_email_address: call?.client_email || row.fd_a_email_address || "",
				fd_m_client_name: call?.client_name || row.fd_m_client_name || ""
			}
		};
	});
	const nonMissedCallTasks = tasks.filter((task) => task.task_type !== "missed_call");
	const tasksByCallId = new Map(nonMissedCallTasks.filter((task) => task.data?.call_id).map((task) => [task.data.call_id, task]));
	const unmatchedTasks = nonMissedCallTasks.filter((task) => !task.data?.call_id);
	const missedCallByCallbackCallId = new Map(tasks.filter((task) => task.task_type === "missed_call" && task.data?.callback?.call_id).map((task) => [task.data.callback.call_id, task]));
	const leadByCallId = new Map((leads || []).filter((lead) => lead.same_day_outbound_call_id).map((lead) => [lead.same_day_outbound_call_id, lead]));
	const callRecordsWithTasks = callRecords.map((record) => {
		const directTask = tasksByCallId.get(record.callId);
		const callbackTask = missedCallByCallbackCallId.get(record.callId);
		const lead = leadByCallId.get(record.callId);
		const leadTask = lead && {
			id: lead.id,
			type: "lead",
			label: "Leads"
		};
		const linkedTasks = [
			directTask && {
				id: directTask.id,
				type: directTask.task_type,
				label: TASK_LABELS[directTask.task_type]
			},
			!leadTask && callbackTask && {
				id: callbackTask.id,
				type: callbackTask.task_type,
				label: TASK_LABELS[callbackTask.task_type]
			},
			leadTask
		].filter(Boolean);
		if (!linkedTasks.length) return record;
		return {
			...record,
			linkedTask: linkedTasks[0],
			linkedTasks
		};
	});
	return includeUnmatchedTasks ? [...callRecordsWithTasks, ...callTaskRecords(unmatchedTasks)] : callRecordsWithTasks;
}
function buildDashboardCallRecords(rows, calls) {
	const callsById = new Map(calls.map((call) => [call.call_id, call]));
	const analysisById = new Map(rows.map((row) => [row.call_id, row]));
	return [...rows, ...calls.filter((call) => !analysisById.has(call.call_id))].map((row) => {
		const call = callsById.get(row.call_id);
		const agent = row.front_desk_person_name || call?.front_desk_person_name || "-";
		String(call?.client_name || row.fd_m_client_name || row.fd_a_client_name || "").trim();
		const capturedNumbers = [...new Set([
			call?.src,
			call?.dst,
			row.src,
			row.dst
		].map((value) => String(value || "").trim()).filter(Boolean))];
		const reviewStatus = calculateReviewStatus(row);
		const direction = row.call_direction || row.call_type || call?.call_type || "Not Provided";
		const clientPhoneNumber = /outbound/i.test(String(direction)) ? call?.dst || row.dst || "" : /inbound/i.test(String(direction)) ? call?.src || row.src || "" : "";
		return {
			id: row.formatted_call_id || row.call_reference || row.call_id,
			callId: row.call_id,
			clientId: row.client_id || call?.client_id || "",
			leadId: row.lead_id || call?.lead_id || "",
			analysisId: row.id,
			agent,
			direction,
			date: row.call_recording_date_normalized || row.call_recording_date || call?.call_date || "-",
			registeredAt: call?.registered_at || row.registered_at || row.created_at || "",
			timestamp: row.call_time || call?.call_time || "-",
			duration: formatAudioDuration(row.call_duration || call?.billsec),
			billsec: call?.billsec ?? row.billsec ?? null,
			extension: call?.extension || row.extension || "-",
			parentNumber: call?.parent_number || row.parent_number || "",
			sourceNumber: call?.src || row.src || "",
			destinationNumber: call?.dst || row.dst || "",
			clientPhoneNumber,
			capturedNumber: capturedNumbers[0] || "-",
			capturedNumbers,
			clientNumber: row.client_phone_number || row.phone_number || row.client_number || call?.src || call?.dst || "-",
			caseType: row.case_type || "Not Provided",
			status: reviewStatus,
			reviewStatusLabel: getReviewStatusLabel(reviewStatus),
			raw: {
				...row,
				fd_a_client_name: call?.client_name || row.fd_a_client_name || "",
				fd_a_email_address: call?.client_email || row.fd_a_email_address || "",
				fd_m_client_name: call?.client_name || row.fd_m_client_name || ""
			}
		};
	});
}
//#endregion
//#region scripts/dashboard-summary-entry.js
let input = "";
for await (const chunk of process.stdin) {
	input += chunk;
	if (input.length > 134217728) throw new Error("Dashboard input exceeds limit");
}
const payload = JSON.parse(input);
if (payload.rowPool) {
	for (const key of ["dashboardAnalysis", "analysis", "dashboardCalls", "calls"]) payload[key] = payload[key].map((index) => structuredClone(payload.rowPool[index]));
	delete payload.rowPool;
}
const calls = buildDashboardCallRecords(payload.dashboardAnalysis, payload.dashboardCalls);
if (payload.mode === "case_types_eq") {
	process.stdout.write(JSON.stringify({ caseTypeEnquiryRows: buildCaseTypeEnquiryRows(calls, payload.dateFrom, payload.dateTo) }));
	process.exit(0);
}
const dailyTargetCalls = _buildCallRecordsWithTasks(payload.analysis, payload.calls, payload.tasks, payload.leads);
const result = computeDashboardSummary({
	...payload,
	calls,
	dailyTargetCalls
});
process.stdout.write(JSON.stringify({
	...result,
	dependencySummary: payload.dependencySummary,
	taskSummary: payload.taskSummary,
	timeDurations: payload.timeDurations
}));
//#endregion
export {};
