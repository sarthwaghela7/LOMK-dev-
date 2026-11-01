// Follow-up dates are calendar dates. They travel as YYYY-MM-DD strings and are formatted by
// string parts, never through `new Date()`, so no timezone can shift them by a day.
export function formatFollowUpDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  return match ? `${match[2]}/${match[3]}/${match[1]}` : "";
}

// Sorted, de-duplicated, valid YYYY-MM-DD strings only.
export function cleanFollowUpDates(values) {
  return [...new Set((values || []).filter((value) => formatFollowUpDate(value)))].sort();
}
