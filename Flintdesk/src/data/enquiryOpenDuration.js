const centralDate = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit",
});

// Date-only and zone-less business dates are already Central calendar dates.
// UTC/offset timestamps are instants and must first be converted to Chicago.
export function centralCalendarDay(value) {
  if (value == null || value === "") return NaN;
  const raw = String(value).trim();
  let parts;
  const iso = raw.match(/^(\d{4})-(\d{2})-(\d{2})(?:$|[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$)/);
  const us = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (iso) parts = [Number(iso[1]), Number(iso[2]), Number(iso[3])];
  else if (us) parts = [Number(us[3]), Number(us[1]), Number(us[2])];
  else {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return NaN;
    const fields = Object.fromEntries(centralDate.formatToParts(date).map(({type, value}) => [type, value]));
    parts = [Number(fields.year), Number(fields.month), Number(fields.day)];
  }
  const [year, month, day] = parts;
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return NaN;
  return date.getTime() / 86400000;
}

export function enquiryOpenDuration(call, now = new Date()) {
  const registered = centralCalendarDay(call?.registeredAt || call?.date || call?.date_received);
  const status = String(call?.raw?.fd_m_case_status_change || call?.raw?.action_status || call?.raw?.case_status || "").trim().toLowerCase();
  const changedDate = call?.raw?.fd_m_date_entry || call?.raw?.action_date || call?.raw?.case_closing_date;
  const end = centralCalendarDay(changedDate && status && status !== "open" ? changedDate : now);
  if (!Number.isFinite(registered) || !Number.isFinite(end)) return "-";
  return `${Math.max(0, end - registered)} days`;
}
