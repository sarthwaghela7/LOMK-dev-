import test from "node:test";
import assert from "node:assert/strict";
import { createPageRefresh, nextRefreshDelay } from "./pageRefresh.js";

test("refresh jitter stays in the 60–120 second window", () => {
  assert.equal(nextRefreshDelay(() => 0), 60000);
  assert.equal(nextRefreshDelay(() => 0.5), 90000);
  assert.equal(nextRefreshDelay(() => 0.99999), 119999);
});

test("inactive pages do not refresh and late responses become stale", async () => {
  let finish, context, calls = 0;
  const controller = createPageRefresh(async (ctx) => {
    calls += 1; context = ctx; await new Promise((resolve) => { finish = resolve; });
  });
  await controller.refresh(); assert.equal(calls, 0);
  controller.activate(); const request = controller.refresh();
  await controller.refresh(); assert.equal(calls, 1);
  controller.deactivate(); assert.equal(context.isCurrent(), false);
  await controller.refresh(); assert.equal(calls, 1);
  finish(); await request; assert.equal(calls, 1);
});

test("filter changes queue one new request without overlapping the old one", async () => {
  let finish, calls = 0, active = 0, peak = 0;
  const controller = createPageRefresh(async () => {
    calls += 1; active += 1; peak = Math.max(peak, active);
    if (calls === 1) await new Promise((resolve) => { finish = resolve; });
    active -= 1;
  });
  controller.activate(); const request = controller.refresh();
  controller.deactivate(); controller.activate();
  await controller.refresh(false, true); await controller.refresh(false, true);
  finish(); await request;
  assert.equal(calls, 2); assert.equal(peak, 1);
});
