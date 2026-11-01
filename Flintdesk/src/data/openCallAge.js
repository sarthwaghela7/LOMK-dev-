// Open Call Aging: how many calls are STILL OPEN and at least 3 / 5 / 10 / 15 days old.
//
// A call counts when its Case Status Change is blank (no status chosen) or exactly one of Open, Client Side Delay, Blocker,
// Dropped, Same Client Context, and it did not confirm another agent's closure of the same matter (closure_inherited).
// Any other status (the In-Transition and Success statuses, Wrong Number Dialed, Lost) is not counted. The server twin is
// services/analysis_read_query.py (AGING_STATUSES). Each call is one record, counted once per card.
//
// Age = whole calendar days from the call's date to today's date in America/Chicago (the business date the app uses
// for Case Status dates and Close Call Aging). The buckets are cumulative (a 12-day-old call is in 3+, 5+ and 10+), so
// they overlap and must never be added up; `total` is the number of distinct open calls with a usable date, and
// `missingDate` the open calls that have no usable date (never guessed, never counted in a bucket).
import { caseStatusKey } from "./statusOptions.js";

const AGING_STATUSES = new Set(["", "open", "client side delay", "blocker", "dropped", "same client context"]);

const centralDay = (instant) => {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).format(instant);
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(parts);
    if (match) return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  } catch { /* no Intl time-zone support: fall back to the UTC date */ }
  return Date.UTC(instant.getUTCFullYear(), instant.getUTCMonth(), instant.getUTCDate());
};

export function isOpenForAging(call) {
  // The saved Case Status Change only; the separate Action Status field is a different thing and never decides it.
  const status = caseStatusKey(call?.raw?.fd_m_case_status_change || "");
  return AGING_STATUSES.has(status) && !call?.raw?.closure_inherited;
}

// Every open call with a usable date, aged; plus how many open calls had no usable date.
export function summarizeOpenCallAges(calls = [], today = new Date()) {
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
export function calculateOpenCallAgeCounts(calls = [], today = new Date()) {
  const { ages } = summarizeOpenCallAges(calls, today);
  return {
    3: ages.filter((age) => age >= 3).length,
    5: ages.filter((age) => age >= 5).length,
    10: ages.filter((age) => age >= 10).length,
    15: ages.filter((age) => age >= 15).length,
  };
}

// Distinct open calls with a usable date (the population the buckets are drawn from) and those without one.
export function countOpenCalls(calls = [], today = new Date()) {
  const { ages, missingDate } = summarizeOpenCallAges(calls, today);
  return { total: ages.length, missingDate };
}
