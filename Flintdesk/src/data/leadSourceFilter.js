// The Leads "Source" filter offers these fixed values (the sheet's UTM Source
// as received) plus "Other". A lead whose Source is anything else (e.g.
// "google ads", "chatgpt.com") is filed under Other.
export const LEAD_SOURCE_OTHER = "Other";
export const LEAD_SOURCE_FILTER_OPTIONS = [
  "Websites",
  "Ads",
  "google",
  "metaads",
  "organic",
  "ig",
  "metaaads",
  LEAD_SOURCE_OTHER,
];

const key = (value) => String(value ?? "").trim().toLowerCase();
const KNOWN_SOURCES = new Set(
  LEAD_SOURCE_FILTER_OPTIONS.filter((option) => option !== LEAD_SOURCE_OTHER).map(key),
);

// Older saved filters held a single string; the filter now holds a list.
export const selectedSources = (value) =>
  (Array.isArray(value) ? value : value ? [value] : []).map(String).filter((item) => key(item));

// No selection means "All". A lead matches when its Source equals any chosen
// value (ignoring case and surrounding spaces), or when Other is chosen and
// its Source is filled in but not one of the fixed values.
export function matchesSourceFilter(source, selected) {
  const chosen = selectedSources(selected).map(key);
  if (!chosen.length) return true;
  const own = key(source);
  if (chosen.includes(own) && own) return true;
  return chosen.includes(key(LEAD_SOURCE_OTHER)) && own !== "" && !KNOWN_SOURCES.has(own);
}
