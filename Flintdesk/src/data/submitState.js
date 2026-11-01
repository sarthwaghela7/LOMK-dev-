// Save / Submit state for one Calls row. Pure so it can be tested without React.
//
// Three separate ideas that must never be mixed up:
//   * Stage 2 completeness  - a validation rule; it only decides whether Submit is allowed.
//   * Saved / Submitted      - whether the values on screen are persisted. "Saved" = they match the latest
//                              server values; "Submitted" is shown only after a Submit succeeded in this
//                              page session (nothing persistent records an earlier Submit).
//   * Completed              - Case Status Change decides it; it has its own indicator and is not touched here.

export const normalizeSaved = (value) => (value === null || value === undefined ? "" : String(value).trim());

// Queued Stage 3/4 edits that really differ from the saved value. An edit changed back to its saved value
// is a no-op, so it does not count (and is never sent).
export function pendingChanges(changes = {}) {
  return Object.entries(changes).filter(([, change]) =>
    !change.persisted && normalizeSaved(change.value) !== normalizeSaved(change.originalValue));
}

export function deriveSubmitState({
  stage2Missing = [],
  submitting = false,
  submitError = "",
  pendingFields = [],
  hasDeletedData = false,
  autosaving = false,
  completionDerived = false,
  completionPersisted = false,
  submittedThisSession = false,
} = {}) {
  const blocked = stage2Missing.length > 0;
  const blockedTitle = blocked ? `Complete Stage 2 first: ${stage2Missing.join(", ")}` : undefined;
  const unsaved = pendingFields.length > 0 || hasDeletedData;

  if (submitting) return { kind: "submitting", label: "Submitting…", disabled: true, tone: "busy", badge: "" };
  if (submitError) {
    return { kind: "failed", label: "Retry Submit", disabled: blocked, tone: "error", badge: "Save failed", title: blockedTitle, error: submitError };
  }
  if (unsaved) {
    return { kind: "unsaved", label: "Submit Changes", disabled: blocked, tone: "unsaved", badge: "Unsaved Changes", title: blockedTitle };
  }
  if (autosaving) return { kind: "saving", label: "Saving…", disabled: true, tone: "busy", badge: "" };
  if (blocked) return { kind: "blocked", label: "Submit", disabled: true, tone: "neutral", badge: "", title: blockedTitle };
  // The Completed indicator follows Case Status Change live, but the completion flag is only written by
  // Submit: when the two differ there is something Submit still has to save.
  if (Boolean(completionDerived) !== Boolean(completionPersisted)) {
    return { kind: "needs-submit", label: "Submit", disabled: false, tone: "neutral", badge: "", title: "Completion has not been saved yet. Click Submit to save it." };
  }
  if (submittedThisSession) return { kind: "submitted", label: "Submitted", disabled: true, tone: "ok", badge: "" };
  return { kind: "saved", label: "Saved", disabled: true, tone: "ok", badge: "" };
}

// The compact, always-visible status of one row (shown in the frozen columns, so no horizontal scrolling is needed).
// It is derived from the SAME state as the Save / Submit controls in the last column - never from anything else - so the
// two can never disagree. "Saved" appears only when nothing is unsaved, pending or failed; Completed is a separate chip.
export function saveStatusBadge(state, autosave, { completed = false } = {}) {
  const phase = autosave?.phase || "idle";
  let save;
  if (state.kind === "submitting") save = { label: "Submitting…", tone: "busy" };
  else if (state.kind === "failed") save = { label: "Save failed", tone: "error", title: state.error };
  else if (state.kind === "unsaved") save = { label: "Unsaved Changes", tone: "unsaved" };
  else if (phase === "error") save = { label: "Save failed", tone: "error", title: autosave.message };
  else if (state.kind === "saving" || phase === "saving") save = { label: "Saving…", tone: "busy" };
  else if (state.kind === "needs-submit") save = { label: "Submit Changes", tone: "unsaved", title: state.title };
  else if (state.kind === "submitted") save = { label: "Submitted", tone: "ok" };
  else save = { label: "Saved", tone: "ok", title: state.title };
  return { ...save, completed: Boolean(completed) };
}

// Does this row still need the agent's Submit before they leave the page? Decided from the SAME state as the Submit button
// (deriveSubmitState), never from autosave: a Case Status that autosaved but whose completion was not submitted still counts.
// "" = nothing to warn about (Saved, Submitted, or a status that has nothing to submit).
const PENDING_KINDS = new Set(["unsaved", "failed", "saving", "submitting", "needs-submit"]);
export function pendingSubmissionReason(state, { completionDerived = false, completionPersisted = false } = {}) {
  if (PENDING_KINDS.has(state?.kind)) return state.kind;
  // Submit is blocked by an incomplete Stage 2, but the Case Status asks for completion that was never saved.
  if (state?.kind === "blocked" && completionDerived && !completionPersisted) return "blocked";
  return "";
}

export const PENDING_REASON_LABELS = {
  unsaved: "Unsaved changes",
  failed: "Submit failed",
  saving: "Saving…",
  submitting: "Submitting…",
  "needs-submit": "Not submitted",
  blocked: "Stage 2 incomplete - cannot submit yet",
};
