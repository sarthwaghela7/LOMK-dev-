// Hands a single call from anywhere in the app (today: the closure notification's Done button) to the Calls page.
// The target travels through sessionStorage so it also works when the Calls page is not mounted yet, and an event
// wakes the page when it already is. The call's UUID is the identity: names, numbers and row positions are never used.
export const CALL_FOCUS_KEY = "flintdesk_call_focus";
export const CALL_FOCUS_EVENT = "flintdesk-call-focus";

// Every call record the confirmation actually changed, as { calls: [{ callId, callReference, callDate }] }, or null
// (nothing changed / the server sent no call ids). Duplicates are dropped. `result` is the backend's confirm
// response (see data/caseClosure.js describeConfirmation).
export function callFocusFromConfirmation(result) {
  const updated = Array.isArray(result?.updated) ? result.updated : [];
  const seen = new Set();
  const calls = [];
  for (const log of updated) {
    const callId = typeof log?.call_id === "string" ? log.call_id.trim() : "";
    if (!callId || seen.has(callId)) continue;
    seen.add(callId);
    calls.push({ callId, callReference: log.call_reference || "", callDate: log.call_date || "" });
  }
  return calls.length ? { calls } : null;
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

// Calendar-date arithmetic on YYYY-MM-DD strings in UTC, so the viewer's timezone can never shift the day.
export function shiftIsoDate(iso, days) {
  const match = ISO_DATE.exec(String(iso || ""));
  if (!match) return "";
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days));
  return date.toISOString().slice(0, 10);
}

// Date window for the Calls request: from a day before the earliest call to a day after the latest. null when no
// call date is known (the caller then falls back to its default range). Accepts one date or a list.
export function focusRangeFor(callDates) {
  const dates = (Array.isArray(callDates) ? callDates : [callDates]).filter((date) => ISO_DATE.test(String(date || ""))).sort();
  if (!dates.length) return null;
  return { start: shiftIsoDate(dates[0], -1), end: shiftIsoDate(dates[dates.length - 1], 1) };
}

export function requestCallFocus(target) {
  if (!Array.isArray(target?.calls) || !target.calls.length) return;
  try { sessionStorage.setItem(CALL_FOCUS_KEY, JSON.stringify(target)); } catch { /* Storage may be disabled. */ }
  window.dispatchEvent(new Event(CALL_FOCUS_EVENT));
}

// Reads and clears the pending target (each request is acted on once).
export function takeCallFocus() {
  try {
    const raw = sessionStorage.getItem(CALL_FOCUS_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(CALL_FOCUS_KEY);
    const target = JSON.parse(raw);
    return Array.isArray(target?.calls) && target.calls.every((call) => typeof call?.callId === "string" && call.callId) && target.calls.length ? target : null;
  } catch { return null; }
}
