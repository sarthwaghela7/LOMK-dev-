// Display helpers for cross-agent case closure (see AutoFlint_Backend/services/case_closure.py).
import { caseStatusKey } from "./statusOptions.js";

const CLOSURE_STATUSES = new Set(["dropped", "success ge", "success pc"]);

export const isClosureStatus = (value) => CLOSURE_STATUSES.has(caseStatusKey(value));

// "Gokul changed to Success - PC": shown in the Case Status Change cell of a call record whose closure the
// agent confirmed. Empty when the log has no confirmed closure.
export function closureBadgeText(inherited) {
  if (!inherited?.closing_agent_name) return "";
  return `${inherited.closing_agent_name} changed to ${inherited.closure_status}`;
}

const normalizeStatus = (value) => caseStatusKey(value);

// Shown whenever this record has a confirmed closure and its own status is either still open (older links) or
// the very status the closure set. Hidden if the agent later moved it to a different closure status of their own.
export function shouldShowInheritedClosure(ownStatus, inherited) {
  if (!inherited?.closing_agent_name) return false;
  return !isClosureStatus(ownStatus) || normalizeStatus(ownStatus) === normalizeStatus(inherited.closure_status);
}

// Server timestamps are instants; show them in the viewer's locale.
export function formatClosureTime(iso) {
  const date = new Date(iso || "");
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

// What happened when the recipient pressed Confirm, in the words shown inside the dialog.
// `result` is the backend's confirm response: {state, updated:[{call_reference,previous_status,new_status}], skipped:[{call_reference,reason}], closed_by, closure_status}.
export function describeConfirmation(result, closer) {
  const updated = Array.isArray(result?.updated) ? result.updated : [];
  const skipped = Array.isArray(result?.skipped) ? result.skipped : [];
  const who = result?.closed_by || closer || "Another agent";
  const lines = [];
  if (updated.length) {
    lines.push(`${who} closed this matter as ${result.closure_status}. You confirmed it.`);
    updated.forEach((log) => lines.push(`${log.call_reference} is now ${log.new_status}.`));
  } else if (result?.state === "already_confirmed") {
    lines.push("This closure was already confirmed.");
  } else {
    lines.push("Nothing needed to change.");
  }
  skipped.forEach((log) => lines.push(`${log.call_reference} was left as it is: ${log.reason}`));
  return lines;
}

// Offer the agent's own related logs only when a save newly puts the call into a closure status.
export function shouldOfferRelatedClosure(previousStatus, newStatus) {
  return isClosureStatus(newStatus) && normalizeStatus(previousStatus) !== normalizeStatus(newStatus);
}

// What happened when the closer confirmed their related logs, as lines for the dialog. `result` is the backend's
// {state, updated:[{call_reference,new_status}], skipped:[{call_reference,reason}], closure_status}.
export function describeRelatedClosure(result) {
  const updated = Array.isArray(result?.updated) ? result.updated : [];
  const skipped = Array.isArray(result?.skipped) ? result.skipped : [];
  const lines = [];
  if (result?.state === "reverted") {
    lines.push("The original call was reopened in the meantime, so nothing was changed.");
    return lines;
  }
  const noun = (count) => `related call log${count === 1 ? "" : "s"}`;
  if (updated.length) lines.push(`${updated.length} ${noun(updated.length)} closed as ${result.closure_status}.`);
  else lines.push("No related call logs were closed.");
  if (updated.length) lines.push(updated.map((log) => log.call_reference).filter(Boolean).join(", "));
  if (skipped.length) {
    lines.push(`${skipped.length} ${noun(skipped.length)} could not be closed and ${skipped.length === 1 ? "was" : "were"} left as ${skipped.length === 1 ? "it is" : "they are"}:`);
    skipped.forEach((log) => lines.push(`${log.call_reference || "A call log"}: ${log.reason}`));
  }
  return lines.filter(Boolean);
}
