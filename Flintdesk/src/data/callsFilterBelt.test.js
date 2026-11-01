import test from "node:test";
import assert from "node:assert/strict";

import {
  LIFECYCLE_OPTIONS, LOG_UPDATE_OPTIONS, PRIORITY_OPTIONS, activeBeltFilterCount, effectiveOpenCase, openCaseOptions, openCaseRequest,
  sameBeltSelection, validLogUpdate, validPriorityForm,
} from "./callsFilterBelt.js";

test("the belt offers exactly the requested values", () => {
  assert.deepEqual(LIFECYCLE_OPTIONS.map((o) => o.label), ["Open Cases", "In-Transition Cases", "Lost Cases", "Dropped Cases", "Success Cases"]);
  assert.deepEqual(PRIORITY_OPTIONS.map((o) => o.label), ["RFE", "I-130", "I-485", "H4", "H4-EAD"]);
  assert.deepEqual(LOG_UPDATE_OPTIONS.map((o) => o.label), ["Pending", "Completed"]);
});

test("the dependency choices are named after the chosen agent and need one", () => {
  const withAgent = openCaseOptions("Priyanka");
  assert.deepEqual(withAgent.map((o) => o.label), ["Same Client Context", "Priyanka Dependency", "Other Agents Dependency"]);
  assert.ok(withAgent.every((o) => !o.disabled));
  const without = openCaseOptions("all");
  assert.deepEqual(without.map((o) => Boolean(o.disabled)), [false, true, true]);
});

test("an Open Cases choice becomes the server filter it is defined by", () => {
  assert.deepEqual(openCaseRequest("same_context", "all"), { caseStatus: "Same Client Context", agingDependency: "" });
  assert.deepEqual(openCaseRequest("agent", "Priyanka"), { caseStatus: "", agingDependency: "agent" });
  assert.deepEqual(openCaseRequest("other_agent", "Priyanka", "Blocker"), { caseStatus: "Blocker", agingDependency: "other_agent" });
  assert.deepEqual(openCaseRequest("agent", "all"), { caseStatus: "", agingDependency: "" });      // no agent: cannot apply
  assert.equal(effectiveOpenCase("agent", ""), "");
  assert.equal(effectiveOpenCase("bogus", "Ravi"), "");
});

test("unknown restored values mean no filter", () => {
  assert.equal(validPriorityForm("RFE"), "RFE");
  assert.equal(validPriorityForm("H4-EAD"), "");        // the stored value is H4EAD
  assert.equal(validLogUpdate("completed"), "completed");
  assert.equal(validLogUpdate("submitted"), "all");
});

test("active filters are counted so they can be shown", () => {
  assert.equal(activeBeltFilterCount({}), 0);
  assert.equal(activeBeltFilterCount({ lifecycleStage: "open", logUpdate: "all" }), 1);
  assert.equal(activeBeltFilterCount({ lifecycleStage: "open", openCase: "same_context", priorityForm: "RFE", actionStatus: "Pending", logUpdate: "pending", followUpDate: "2026-10-30" }), 6);
});

test("draft and applied selections are compared field by field", () => {
  assert.equal(sameBeltSelection({ lifecycleStage: "open" }, { lifecycleStage: "open", priorityForm: "" }), true);
  assert.equal(sameBeltSelection({ lifecycleStage: "open" }, { lifecycleStage: "lost" }), false);
});
