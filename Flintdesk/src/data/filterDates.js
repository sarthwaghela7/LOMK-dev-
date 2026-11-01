export function toISODate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function getCurrentMonthRange(now = new Date()) {
  const start = new Date(now.getFullYear(), now.getMonth(), 1);
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  return { start: toISODate(start), end: toISODate(end) };
}

export function isValidDateRange(range) {
  return Boolean(range?.start && range?.end && range.start <= range.end);
}

// The business date (YYYY-MM-DD) at `now` in Central Time, the zone the business day is defined in.
export function businessDateCT(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const pick = (type) => parts.find((part) => part.type === type)?.value;
  return `${pick("year")}-${pick("month")}-${pick("day")}`;
}

// The previous working day before `now` (Central Time): yesterday, except that Saturday, Sunday and Monday step back to
// Friday. There is no holiday calendar in the application, so public holidays are not skipped.
export function previousWorkingDay(now = new Date()) {
  const [year, month, day] = businessDateCT(now).split("-").map(Number);
  const cursor = new Date(Date.UTC(year, month - 1, day));
  do { cursor.setUTCDate(cursor.getUTCDate() - 1); } while ([0, 6].includes(cursor.getUTCDay()));
  return cursor.toISOString().slice(0, 10);
}

// What every date-filtered page shows when it is first opened: the previous working day, start and end alike.
export function getDefaultDateRange(now = new Date()) {
  const day = previousWorkingDay(now);
  return { start: day, end: day };
}
