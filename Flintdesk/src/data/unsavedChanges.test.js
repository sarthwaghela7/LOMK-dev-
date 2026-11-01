import test from "node:test";
import assert from "node:assert/strict";

import {
  confirmLeaveIfUnsaved, hasPendingSubmissions, pendingNavigationWork, registerNavigationGuard,
  setPendingSubmissionCount, setUnsavedCount, submitPendingNavigationWork,
} from "./unsavedChanges.js";

test("saved-but-unsubmitted rows warn on refresh / tab close but never trigger the plain unsaved confirm", () => {
  setPendingSubmissionCount("calls", 2);
  assert.equal(hasPendingSubmissions(), true);
  assert.equal(confirmLeaveIfUnsaved(), true);          // nothing unsaved: no window.confirm is raised
  setPendingSubmissionCount("calls", 0);
  assert.equal(hasPendingSubmissions(), false);
});

test("pending counts from several sources are tracked independently", () => {
  setPendingSubmissionCount("a", 1); setPendingSubmissionCount("b", 3);
  setPendingSubmissionCount("a", 0);
  assert.equal(hasPendingSubmissions(), true);
  setPendingSubmissionCount("b", 0);
  assert.equal(hasPendingSubmissions(), false);
});

test("an unsaved edit still uses the existing confirm and is not bypassed", () => {
  const asked = [];
  const original = globalThis.window;
  globalThis.window = { confirm: (message) => { asked.push(message); return false; }, addEventListener() {} };
  try {
    setUnsavedCount("calls", 1);
    assert.equal(confirmLeaveIfUnsaved(), false);
    assert.equal(asked.length, 1);
  } finally { setUnsavedCount("calls", 0); globalThis.window = original; }
});

test("the registered guard reports pending rows and submits through the page's own Submit", async () => {
  const rows = [{ id: "r1", ref: "AF#1", reason: "needs-submit", status: "Dropped" }];
  const unregister = registerNavigationGuard("calls", {
    getPending: () => ({ rows, unsavedCount: 1 }),
    submitAll: async () => ({ submitted: ["AF#1"], failed: [] }),
  });
  assert.deepEqual(pendingNavigationWork("calls"), { rows, unsavedCount: 1 });
  assert.deepEqual(await submitPendingNavigationWork("calls"), { submitted: ["AF#1"], failed: [] });
  unregister();
  assert.deepEqual(pendingNavigationWork("calls"), { rows: [], unsavedCount: 0 });
  assert.deepEqual(await submitPendingNavigationWork("calls"), { submitted: [], failed: [] });
});

test("a broken guard can never block navigation or throw", () => {
  const unregister = registerNavigationGuard("broken", { getPending: () => { throw new Error("x"); } });
  assert.deepEqual(pendingNavigationWork("broken"), { rows: [], unsavedCount: 0 });
  unregister();
});

test("an old registration cannot remove a newer one for the same page", () => {
  const first = registerNavigationGuard("calls", { getPending: () => ({ rows: [{ id: 1 }], unsavedCount: 0 }) });
  const second = registerNavigationGuard("calls", { getPending: () => ({ rows: [{ id: 2 }], unsavedCount: 0 }) });
  first();
  assert.equal(pendingNavigationWork("calls").rows[0].id, 2);
  second();
});
