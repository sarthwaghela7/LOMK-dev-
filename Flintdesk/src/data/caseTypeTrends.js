// Pure helpers for the Case Types + EQ trends popup and PDF. Everything shown comes from the server's verified
// report (services/case_type_trends.py); nothing here recounts or re-derives dates.

export const PERIOD_OPTIONS = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "custom", label: "Custom Date Range" },
];

const pad = (n) => String(n).padStart(2, "0");

// Calendar dates travel as YYYY-MM-DD and are formatted by parts, never through the browser's timezone.
export function formatIsoDate(iso, { year = true } = {}) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!match) return "";
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  return `${months[Number(match[2]) - 1]} ${Number(match[3])}${year ? `, ${match[1]}` : ""}`;
}

export function periodRangeText(period) {
  if (!period) return "";
  if (period.type === "daily" || period.start === period.end) return formatIsoDate(period.start);
  const sameYear = period.start.slice(0, 4) === period.end.slice(0, 4);
  return `${formatIsoDate(period.start, { year: !sameYear })} – ${formatIsoDate(period.end)}`;
}

// A comparison window as readable text: "October 8, 2026" for one day, else "September 28 – October 4, 2026".
export function windowText(window) {
  if (!window) return "";
  if (window.start === window.end) return formatIsoDate(window.start);
  const sameYear = window.start.slice(0, 4) === window.end.slice(0, 4);
  return `${formatIsoDate(window.start, { year: !sameYear })} – ${formatIsoDate(window.end)}`;
}

// "Oct 11, 2026, 6:05 PM CT" from the server's Central-time ISO timestamp (wall-clock parts, no conversion).
export function formatGeneratedAt(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ""));
  if (!match) return "";
  const hour = Number(match[4]);
  return `${formatIsoDate(`${match[1]}-${match[2]}-${match[3]}`)}, ${hour % 12 || 12}:${match[5]} ${hour < 12 ? "AM" : "PM"} CT`;
}

// Case_Types_EQ_Daily_2026-10-11.pdf | ..._Weekly_2026-10-05_to_2026-10-11.pdf | ..._Monthly_2026-10.pdf
export function reportFileName(report) {
  const { type, start, end } = report.period;
  if (type === "daily") return `Case_Types_EQ_Daily_${start}.pdf`;
  if (type === "custom") return `Case_Types_EQ_Custom_${start}_to_${end}.pdf`;
  if (type === "weekly") return `Case_Types_EQ_Weekly_${start}_to_${end}.pdf`;
  return `Case_Types_EQ_Monthly_${start.slice(0, 7)}.pdf`;
}

export const hasEnquiries = (report) => Boolean(report && report.totals && report.totals.enquiries > 0);

// Bars are scaled to the largest value shown; a tiny non-zero value still gets a visible bar.
export function barPercent(value, max) {
  if (!max || value <= 0) return 0;
  return Math.max(2, Math.round((value / max) * 1000) / 10);
}

export function limitItems(items, showAll, limit = 8) {
  return showAll || items.length <= limit ? items : items.slice(0, limit);
}

// Month view with many days: the label is shown every few columns so they stay readable.
export function labelEvery(count) {
  if (count <= 12) return 1;
  if (count <= 20) return 2;
  return 5;
}

// "10/05/2026" from a YYYY-MM-DD date, by parts (no timezone shift); the app's display format.
export function formatShortDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  return match ? `${match[2]}/${match[3]}/${match[1]}` : "";
}

// The full question list grouped by case type, in the report's order (most enquired first); a case type that only
// appears in the list (data changed since the report loaded) is added after them. Items keep their date order.
export function groupQuestionsByCategory(items, caseTypes = []) {
  const groups = new Map(caseTypes.map((c) => [c.name, []]));
  for (const item of items || []) {
    if (!groups.has(item.case_type)) groups.set(item.case_type, []);
    groups.get(item.case_type).push(item);
  }
  return [...groups].filter(([, list]) => list.length).map(([name, list]) => ({ name, items: list }));
}

// True when the question list covers exactly the enquiries the report counted (both come from the same records).
export const questionsMatchReport = (report, questions) =>
  Boolean(report && questions) && !questions.truncated && questions.total === report.totals?.enquiries;

// "4:34 PM CDT" (Central Time) and "3:04 AM GMT+5:30" (the viewer's own zone) for one instant, so people in either
// place can read the same moment. `localZone` is only for tests; the app uses the browser's zone.
export function formatAsOf(iso, localZone) {
  const date = new Date(iso || "");
  if (Number.isNaN(date.getTime())) return null;
  const show = (timeZone) => new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZoneName: "short", ...(timeZone ? { timeZone } : {}) }).format(date);
  return { ct: show("America/Chicago"), local: show(localZone) };
}

// The sentence that shows every call record in the range was considered and what became of each one.
// Parts that are zero are left out; the parts always add up to `callRecords`.
export function describeCoverage(summary) {
  if (!summary || !Number.isFinite(summary.callRecords)) return "";
  const parts = [
    [summary.enquiryQuestions, "with an enquiry question (listed below)"],
    [summary.voicemailOrSpam, "voicemail or spam"],
    [summary.noQuestionRecorded, "with no enquiry question recorded"],
    [summary.otherRecords, "other records not counted as enquiries"],
    [summary.waitingForAnalysis, "waiting for AI analysis"],
  ].filter(([count]) => count > 0).map(([count, label]) => `${count} ${label}`);
  const noun = summary.callRecords === 1 ? "call record" : "call records";
  return `${summary.callRecords} ${noun} in this date range reviewed${parts.length ? `: ${parts.join(" · ")}` : ""}.`;
}
