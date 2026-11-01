// SM Leads import: header-name based column mapping (never column position).
import { parseQuotedDelimitedText } from "./tabularImport.js";

export const SM_LEAD_IMPORT_FIELDS = [
  {
    key: "date_received", label: "Date of Received", required: true,
    // Earlier aliases are stronger: "Date Received" beats a bare "Date".
    aliases: ["date received", "date of received", "received date", "received on", "date"],
    keywords: ["date", "received"],
    exclude: ["added", "created", "import", "updated", "modified", "birth", "dob"],
  },
  {
    key: "name", label: "Name", required: true,
    aliases: ["name", "full name", "client name", "customer name"],
    keywords: ["name"],
    exclude: ["email", "mail", "phone", "mobile", "file", "company", "business"],
  },
  {
    key: "email", label: "Email ID", required: true,
    aliases: ["email", "email id", "email address", "e mail", "e mail id", "e mail address", "mail id"],
    keywords: ["email", "mail"],
    exclude: [],
  },
  {
    key: "phone", label: "Phone Number", required: true,
    aliases: ["phone number", "phone", "mobile number", "mobile", "contact number", "contact", "cell"],
    keywords: ["phone", "mobile", "contact", "cell", "tel", "whatsapp"],
    exclude: ["name", "email", "mail"],
  },
  {
    key: "enquiry_question", label: "Enquiry Question", required: false,
    aliases: ["enquiry question", "inquiry question", "enquiry", "inquiry", "question", "message", "query"],
    keywords: ["enquiry", "inquiry", "question", "message", "query"],
    exclude: [],
  },
];

// Lowercase, treat _ - . and other punctuation as spaces, collapse whitespace.
export const normalizeSmHeader = (value) => String(value ?? "")
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, " ")
  .trim();

function scoreHeader(header, field) {
  const normalized = normalizeSmHeader(header);
  if (!normalized) return 0;
  const tokens = normalized.split(" ");
  if (field.exclude.some((word) => tokens.includes(word))) return 0;
  const aliasIndex = field.aliases.findIndex((alias) => alias === normalized || alias.replace(/ /g, "") === normalized.replace(/ /g, ""));
  if (aliasIndex >= 0) return 100 - aliasIndex;
  // Partial match: a recognizable keyword; fewer unrelated words scores higher.
  const hasKeyword = field.keywords.some((word) => tokens.includes(word) || (word.length > 3 && tokens.some((token) => token.includes(word))));
  return hasKeyword ? Math.max(1, 40 - tokens.length) : 0;
}

/**
 * Map uploaded headers to SM Lead fields.
 * Returns { mapping: {field: columnIndex}, ambiguities: [{key,label,candidates:[{index,header}]}],
 *           missing: [label], ignored: [header] }.
 * A column feeds at most one field (its best score). When several columns tie
 * for a field the field is reported as ambiguous instead of being guessed.
 */
export function mapSmLeadHeaders(headers) {
  const best = headers.map((header) => {
    const scored = SM_LEAD_IMPORT_FIELDS.map((field) => [field, scoreHeader(header, field)]).sort((a, b) => b[1] - a[1]);
    const [field, score] = scored[0];
    // A header that is equally good for two fields cannot be placed safely.
    return score > 0 && scored[1][1] !== score ? { field, score } : null;
  });
  const mapping = {};
  const ambiguities = [];
  SM_LEAD_IMPORT_FIELDS.forEach((field) => {
    const candidates = best
      .map((entry, index) => (entry?.field.key === field.key ? { index, header: headers[index], score: entry.score } : null))
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);
    if (!candidates.length) return;
    const tied = candidates.filter((candidate) => candidate.score === candidates[0].score);
    if (tied.length === 1) mapping[field.key] = tied[0].index;
    else ambiguities.push({ key: field.key, label: field.label, candidates: tied.map(({ index, header }) => ({ index, header })) });
  });
  const used = new Set(Object.values(mapping));
  const missing = SM_LEAD_IMPORT_FIELDS
    .filter((field) => field.required && mapping[field.key] === undefined && !ambiguities.some((item) => item.key === field.key))
    .map((field) => field.label);
  const ignored = headers.filter((header, index) => String(header ?? "").trim() && !used.has(index)
    && !ambiguities.some((item) => item.candidates.some((candidate) => candidate.index === index)));
  return { mapping, ambiguities, missing, ignored };
}

// "9/25/2026 10:30" / "09-25-2026" -> "2026-09-25"; ISO dates pass through.
export function normalizeReceivedDate(value) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  const datePart = text.split(/[ T]/)[0];
  const us = datePart.match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/);
  if (us) return `${us[3]}-${us[1].padStart(2, "0")}-${us[2].padStart(2, "0")}`;
  return /^\d{4}-\d{2}-\d{2}$/.test(datePart) ? datePart : text;
}

export function parseSmLeadTable(text, delimiter) {
  const rows = parseQuotedDelimitedText(String(text || "").replace(/^﻿/, "").replace(/\r\n?/g, "\n"), delimiter);
  return { headers: rows[0] || [], rows: rows.slice(1) };
}

/** Validation message for the current table + (possibly user-confirmed) mapping. */
export function smLeadMappingProblems(table, analysis, choices = {}) {
  const problems = [];
  if (!table.headers.length) return ["The file has no header row."];
  analysis.missing.forEach((label) => problems.push(`Unable to identify a column for ${label}. Please check the uploaded file headers.`));
  analysis.ambiguities.forEach((item) => {
    if (choices[item.key] === undefined || choices[item.key] === "")
      problems.push(`Several columns could be ${item.label} (${item.candidates.map((c) => `"${c.header}"`).join(", ")}). Choose which one to use.`);
  });
  if (!table.rows.length) problems.push("No records were found to import.");
  return problems;
}

export function smLeadRecords(table, analysis, choices = {}) {
  const mapping = { ...analysis.mapping };
  analysis.ambiguities.forEach((item) => { if (choices[item.key] !== undefined && choices[item.key] !== "") mapping[item.key] = Number(choices[item.key]); });
  return table.rows.map((cells) => ({
    date_received: normalizeReceivedDate(cells[mapping.date_received]),
    name: String(cells[mapping.name] ?? "").trim(),
    email: String(cells[mapping.email] ?? "").trim(),
    phone: String(cells[mapping.phone] ?? "").trim(),
    enquiry_question: mapping.enquiry_question === undefined ? "" : String(cells[mapping.enquiry_question] ?? "").trim(),
  }));
}

/** Row-level checks mirroring the API so errors show before the request. */
export function smLeadRecordProblems(records) {
  const problems = [];
  records.forEach((record, index) => {
    const missing = [["date_received", "Date Received"], ["name", "Name"], ["email", "Email"], ["phone", "Phone Number"]]
      .filter(([key]) => !String(record[key] || "").trim()).map(([, label]) => label);
    if (missing.length) problems.push(`Row ${index + 1}: ${missing.join(", ")} required.`);
    else if (!/^\d{4}-\d{2}-\d{2}$/.test(record.date_received)) problems.push(`Row ${index + 1}: invalid Date Received "${record.date_received}".`);
  });
  return problems;
}
