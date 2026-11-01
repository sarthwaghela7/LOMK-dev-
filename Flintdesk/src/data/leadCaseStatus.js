import { caseStatusKey } from "./statusOptions.js";

// Leads S2 "Case Status Change": text shown/exported and the filter match. The value itself comes from the server
// (the Case Status Change saved on the lead's call record), so this only formats and compares it.
export const NO_CASE_STATUS = "N/A";

export const caseStatusText = (value) => {
  const text = String(value ?? "").trim();
  return text || NO_CASE_STATUS;
};

// Spacing and letter case never hide a match, and a status still stored in its pre-change spelling matches its new
// wording; an empty filter matches everything.
export const matchesCaseStatus = (value, wanted) => {
  const target = String(wanted ?? "").trim();
  if (!target) return true;
  return caseStatusKey(value) === caseStatusKey(target);
};
