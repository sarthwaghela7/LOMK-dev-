// Tracks pages that hold changes which are not saved yet, so leaving can warn - and only then.
const sources = new Map();
let listening = false;

export const UNSAVED_MESSAGE =
  "You have changes that are not saved yet. Leave without saving them?";

const onBeforeUnload = (event) => {
  if (!hasUnsavedChanges() && !hasPendingSubmissions()) return undefined;
  event.preventDefault();
  event.returnValue = ""; // the browser shows its own wording
  return "";
};

export function hasUnsavedChanges() {
  for (const count of sources.values()) if (count > 0) return true;
  return false;
}

// `count` = how many records on that page have unsaved changes (0 clears it).
export function setUnsavedCount(source, count) {
  if (count > 0) sources.set(source, count); else sources.delete(source);
  if (typeof window !== "undefined" && !listening) {
    window.addEventListener("beforeunload", onBeforeUnload);
    listening = true;
  }
}

// true = go ahead. Asks only when something is genuinely unsaved.
export function confirmLeaveIfUnsaved(message = UNSAVED_MESSAGE) {
  if (!hasUnsavedChanges()) return true;
  const ask = typeof window !== "undefined" ? window.confirm : undefined;
  return typeof ask === "function" ? Boolean(ask.call(window, message)) : true;
}

// ---- Saved but not Submitted -------------------------------------------------------------------------------------
// Rows whose Case Status change still needs the explicit Submit. These are NOT "unsaved" (they are on the server), so
// they stay out of the count above and its plain confirm; they only add a browser refresh / tab close warning here, and
// an in-app guard (see registerNavigationGuard) that the Calls page registers.
const pendingSubmissions = new Map();

export function setPendingSubmissionCount(source, count) {
  if (count > 0) pendingSubmissions.set(source, count); else pendingSubmissions.delete(source);
  if (typeof window !== "undefined" && !listening) {
    window.addEventListener("beforeunload", onBeforeUnload);
    listening = true;
  }
}

export function hasPendingSubmissions() {
  for (const count of pendingSubmissions.values()) if (count > 0) return true;
  return false;
}

// A page that can have unsubmitted work registers { getPending(), submitAll() }:
//   getPending() -> { rows: [{ id, ref, reason, status }], unsavedCount }   (fresh each call)
//   submitAll()  -> Promise<{ submitted: [ref], failed: [{ ref, error }] }>  (the page's own Submit, one row at a time)
const guards = new Map();

export function registerNavigationGuard(source, guard) {
  guards.set(source, guard);
  return () => { if (guards.get(source) === guard) guards.delete(source); };
}

export function pendingNavigationWork(source) {
  let pending = null;
  try { pending = guards.get(source)?.getPending?.(); } catch { pending = null; }
  return pending && Array.isArray(pending.rows) ? { rows: pending.rows, unsavedCount: Number(pending.unsavedCount) || 0 } : { rows: [], unsavedCount: 0 };
}

export async function submitPendingNavigationWork(source) {
  const guard = guards.get(source);
  if (!guard?.submitAll) return { submitted: [], failed: [] };
  return guard.submitAll();
}
