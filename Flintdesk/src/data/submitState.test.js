import test from "node:test";
import assert from "node:assert/strict";

import { deriveSubmitState, normalizeSaved, pendingChanges, saveStatusBadge } from "./submitState.js";
import { confirmLeaveIfUnsaved, hasUnsavedChanges, setUnsavedCount } from "./unsavedChanges.js";

const base = { stage2Missing: [], pendingFields: [], completionDerived: false, completionPersisted: false };

test("a call that matches the server shows Saved, never 'Submitted' just because Stage 2 is complete", () => {
  const state = deriveSubmitState(base);
  assert.equal(state.kind, "saved");
  assert.equal(state.label, "Saved");
  assert.notEqual(state.label, "Submitted");
});

test("Submitted appears only after a Submit succeeded in this session", () => {
  assert.equal(deriveSubmitState({ ...base, submittedThisSession: true }).label, "Submitted");
});

test("queued edits or pending deletions show Unsaved Changes and Submit Changes", () => {
  for (const extra of [{ pendingFields: ["fd_m_verification_comment"] }, { hasDeletedData: true }]) {
    const state = deriveSubmitState({ ...base, ...extra, submittedThisSession: true });
    assert.deepEqual([state.kind, state.label, state.badge, state.disabled], ["unsaved", "Submit Changes", "Unsaved Changes", false]);
  }
});

test("Submit Changes stays disabled while Stage 2 is incomplete, with the reason", () => {
  const state = deriveSubmitState({ ...base, stage2Missing: ["Dependency"], pendingFields: ["x"] });
  assert.equal(state.disabled, true);
  assert.match(state.title, /Dependency/);
});

test("Stage 2 incomplete alone is a blocked Submit, not an error and not 'Submitted'", () => {
  const state = deriveSubmitState({ ...base, stage2Missing: ["Follow Up Dates"], submittedThisSession: true });
  assert.deepEqual([state.kind, state.label, state.disabled], ["blocked", "Submit", true]);
});

test("a request in progress cannot be submitted twice", () => {
  const state = deriveSubmitState({ ...base, pendingFields: ["x"], submitting: true });
  assert.deepEqual([state.kind, state.label, state.disabled], ["submitting", "Submitting…", true]);
});

test("a failed Submit stays unsaved, shows the error and allows a retry", () => {
  const state = deriveSubmitState({ ...base, pendingFields: ["x"], submitError: "Server unavailable", submittedThisSession: true });
  assert.deepEqual([state.kind, state.label, state.badge, state.disabled, state.error], ["failed", "Retry Submit", "Save failed", false, "Server unavailable"]);
});

test("autosave in flight shows Saving…", () => {
  const state = deriveSubmitState({ ...base, autosaving: true });
  assert.deepEqual([state.kind, state.label, state.disabled], ["saving", "Saving…", true]);
});

test("a completion that was never saved still needs Submit (and a no-op does not)", () => {
  assert.equal(deriveSubmitState({ ...base, completionDerived: true, completionPersisted: false }).kind, "needs-submit");
  assert.equal(deriveSubmitState({ ...base, completionDerived: true, completionPersisted: true }).kind, "saved");
  assert.equal(deriveSubmitState({ ...base, completionDerived: false, completionPersisted: true }).kind, "needs-submit"); // completion lapsed server-side
});

test("an edit changed back to its saved value is not a change", () => {
  const changes = {
    fd_m_verification_comment: { value: "  ok ", originalValue: "ok", persisted: false },
    fd_m_google_review: { value: "Yes", originalValue: "No", persisted: false },
    fd_m_attachment: { value: "path", originalValue: "", persisted: true }, // attachments persist on upload
  };
  assert.deepEqual(pendingChanges(changes).map(([field]) => field), ["fd_m_google_review"]);
  assert.equal(normalizeSaved(null), "");
  assert.equal(normalizeSaved(undefined), "");
  assert.equal(normalizeSaved(" x "), "x");
});

test("navigation warns only for genuinely unsaved changes", () => {
  const asked = [];
  const listeners = {};
  globalThis.window = {
    confirm: (message) => { asked.push(message); return false; },
    addEventListener: (type, handler) => { listeners[type] = handler; },
  };
  assert.equal(confirmLeaveIfUnsaved(), true);          // nothing unsaved: no prompt
  assert.equal(asked.length, 0);
  setUnsavedCount("calls", 2);
  assert.equal(hasUnsavedChanges(), true);
  assert.equal(confirmLeaveIfUnsaved(), false);         // user chooses to stay
  assert.equal(asked.length, 1);
  const closing = { prevented: false, preventDefault() { this.prevented = true; } };
  listeners.beforeunload(closing);                       // closing the tab with unsaved changes is blocked
  assert.equal(closing.prevented, true);
  globalThis.window.confirm = () => true;
  assert.equal(confirmLeaveIfUnsaved(), true);          // explicit confirmation to discard
  setUnsavedCount("calls", 0);                          // everything saved
  assert.equal(hasUnsavedChanges(), false);
  const quiet = { prevented: false, preventDefault() { this.prevented = true; } };
  listeners.beforeunload(quiet);
  assert.equal(quiet.prevented, false);                  // no warning when all changes are saved
  assert.equal(confirmLeaveIfUnsaved(), true);
  delete globalThis.window;
});

const badge = (input, autosave, completed) => saveStatusBadge(deriveSubmitState({ ...base, ...input }), autosave, { completed });

test("the frozen-column badge shows each save/submit state from the same state as the last column", () => {
  const pending = { pendingFields: ["fd_m_verification_comment"] };
  assert.equal(badge(pending).label, "Unsaved Changes");
  assert.equal(badge({ autosaving: true }, { phase: "saving", pending: 1 }).label, "Saving…");
  assert.equal(badge({}).label, "Saved");
  assert.equal(badge({ completionDerived: true, completionPersisted: false }).label, "Submit Changes");
  assert.equal(badge({ submitting: true }).label, "Submitting…");
  assert.equal(badge({ submittedThisSession: true }).label, "Submitted");
  assert.equal(badge({ submitError: "boom" }).tone, "error");
});

test("the badge never says Saved while edits are unsaved, in flight or failed", () => {
  for (const [input, autosave] of [
    [{ pendingFields: ["x"] }, undefined],
    [{ hasDeletedData: true }, undefined],
    [{ autosaving: true }, { phase: "saving", pending: 1 }],
    [{}, { phase: "saving", pending: 1 }],
    [{}, { phase: "error", message: "Failed", pending: 0 }],
    [{ submitError: "x" }, undefined],
    [{ submitting: true }, undefined],
  ]) assert.notEqual(badge(input, autosave).label, "Saved", JSON.stringify(input));
  assert.equal(badge({}, { phase: "error", message: "409: stale" }).title, "409: stale");
});

test("Completed is a separate flag from Saved and Submitted", () => {
  const submitted = badge({ submittedThisSession: true, completionDerived: true, completionPersisted: true }, undefined, true);
  assert.deepEqual([submitted.label, submitted.completed], ["Submitted", true]);
  const saved = badge({ completionDerived: true, completionPersisted: true }, undefined, true);
  assert.deepEqual([saved.label, saved.completed], ["Saved", true]);
  assert.equal(badge({}, undefined, false).completed, false);
});

test("each row's badge is independent of the others", () => {
  const rows = [badge({ pendingFields: ["x"] }), badge({}), badge({ submittedThisSession: true })];
  assert.deepEqual(rows.map((row) => row.label), ["Unsaved Changes", "Saved", "Submitted"]);
});

// A call closed by confirmation (related logs, or another agent's closure) holds a closing Case Status but its
// completion flag is only ever written by Submit. These are the states each closing status must show.
for (const status of ["Dropped", "Success - GE", "Success - PC"]) {
  test(`${status}: closed by confirmation -> Submit Changes + Completed until Submit, then Submitted, then Saved`, () => {
    const closed = { completionDerived: true, completionPersisted: false };
    const beforeSubmit = badge(closed, undefined, true);
    assert.deepEqual([beforeSubmit.label, beforeSubmit.completed], ["Submit Changes", true]);
    assert.equal(deriveSubmitState({ ...base, ...closed }).disabled, false);              // Submit is available
    const submitted = badge({ ...closed, completionPersisted: true, submittedThisSession: true }, undefined, true);
    assert.deepEqual([submitted.label, submitted.completed], ["Submitted", true]);
    const reloaded = badge({ completionDerived: true, completionPersisted: true }, undefined, true);
    assert.deepEqual([reloaded.label, reloaded.completed], ["Saved", true]);              // next visit: saved and completed
  });

  test(`${status}: with Stage 2 incomplete Submit stays blocked and nothing claims it was submitted`, () => {
    const blocked = deriveSubmitState({ ...base, stage2Missing: ["Dependency"], completionDerived: true, completionPersisted: false });
    assert.equal(blocked.kind, "blocked");
    assert.equal(blocked.disabled, true);
    assert.notEqual(saveStatusBadge(blocked, undefined, { completed: true }).label, "Submitted");
  });
}

test("an open (non-closing) status is never shown as Completed", () => {
  assert.equal(badge({ completionDerived: false, completionPersisted: false }, undefined, false).completed, false);
});

import { pendingSubmissionReason } from "./submitState.js";

test("only rows that still need the explicit Submit count as pending for the leave-page warning", () => {
  const reason = (input, flags) => pendingSubmissionReason(deriveSubmitState({ ...base, ...input }), flags);
  assert.equal(reason({ pendingFields: ["fd_m_verification_comment"] }), "unsaved");
  assert.equal(reason({ autosaving: true }), "saving");
  assert.equal(reason({ submitting: true }), "submitting");
  assert.equal(reason({ submitError: "boom" }), "failed");
  // saved to the server but Submit never clicked: completion asked for by the status is not persisted
  assert.equal(reason({ completionDerived: true, completionPersisted: false }, { completionDerived: true, completionPersisted: false }), "needs-submit");
});

test("Saved, Submitted and a status with nothing to submit never trigger the warning", () => {
  const reason = (input, flags) => pendingSubmissionReason(deriveSubmitState({ ...base, ...input }), flags);
  assert.equal(reason({}), "");                                                                       // saved
  assert.equal(reason({ submittedThisSession: true, completionDerived: true, completionPersisted: true }), "");   // submitted + completed
  assert.equal(reason({ completionDerived: true, completionPersisted: true }), "");                  // completed on an earlier visit
  assert.equal(reason({ completionDerived: false, completionPersisted: false }), "");                // e.g. Blocker: nothing to submit
});

test("an incomplete Stage 2 only warns when the Case Status asks for a completion that was never saved", () => {
  const blocked = deriveSubmitState({ ...base, stage2Missing: ["Dependency"], completionDerived: true, completionPersisted: false });
  assert.equal(pendingSubmissionReason(blocked, { completionDerived: true, completionPersisted: false }), "blocked");
  assert.equal(pendingSubmissionReason(blocked, { completionDerived: false, completionPersisted: false }), "");
  assert.equal(pendingSubmissionReason(blocked, { completionDerived: true, completionPersisted: true }), "");
});
