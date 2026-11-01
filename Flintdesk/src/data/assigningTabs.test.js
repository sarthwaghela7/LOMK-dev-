import test from "node:test";
import assert from "node:assert/strict";

import { ASSIGNING_TABS, assigningPageTitle, badgeText, DEFAULT_ASSIGNING_TAB, legacyPageTab, tabForNotification, validAssigningTab } from "./assigningTabs.js";

test("the page has the requested tabs, Leads first and by default", () => {
  assert.deepEqual(ASSIGNING_TABS.map((tab) => tab.label), ["Leads", "Follow Ups", "SM Leads", "Call Tasks", "Missed Calls"]);
  assert.equal(DEFAULT_ASSIGNING_TAB, "leads");
  assert.equal(validAssigningTab("bogus"), "leads");
  assert.equal(validAssigningTab("follow-ups"), "follow-ups");
});

test("old page names map onto their tab", () => {
  assert.equal(legacyPageTab("leads"), "leads");
  assert.equal(legacyPageTab("tasks"), "general");
  assert.equal(legacyPageTab("sm-leads"), "sm-leads");
  assert.equal(legacyPageTab("missed-calls"), "missed-calls");
  assert.equal(legacyPageTab("dashboard"), "");
});

test("assignment notifications open their own tab", () => {
  assert.equal(tabForNotification({ notification_type: "lead_assignment" }), "leads");
  assert.equal(tabForNotification({ notification_type: "sm_lead_assignment" }), "sm-leads");
  assert.equal(tabForNotification({ notification_type: "missed_call_assignment" }), "missed-calls");
  assert.equal(tabForNotification({ notification_type: "follow_up_assignment" }), "follow-ups");
  assert.equal(tabForNotification({ notification_type: "follow_up_unassigned" }), "follow-ups");
  assert.equal(tabForNotification({ notification_type: "non_responsiveness_assignment" }), "general");
  assert.equal(tabForNotification({ notification_type: "dependency_review" }), undefined);
});

test("agents see the page as Assigned Calls, everyone else as Assigning the Calls", () => {
  assert.equal(assigningPageTitle(true), "Assigned Calls");
  assert.equal(assigningPageTitle(false), "Assigning the Calls");
});

test("badges show nothing at zero and cap at 99+", () => {
  assert.equal(badgeText(0), "");
  assert.equal(badgeText(undefined), "");
  assert.equal(badgeText(5), "5");
  assert.equal(badgeText(120), "99+");
});
