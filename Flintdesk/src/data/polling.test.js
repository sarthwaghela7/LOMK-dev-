import test from "node:test";
import assert from "node:assert/strict";

import { startPolling } from "./polling.js";

// A manual clock/timer so the tests are instant and deterministic.
function fakeTimers() {
  let nextId = 1, clock = 0;
  const pending = new Map();
  return {
    timers: { setTimeout: (fn, ms) => { const id = nextId++; pending.set(id, { fn, at: clock + ms }); return id; }, clearTimeout: (id) => pending.delete(id) },
    now: () => clock,
    async advance(ms) {
      clock += ms;
      for (const [id, t] of [...pending]) if (t.at <= clock) { pending.delete(id); await t.fn(); }
    },
    count: () => pending.size,
  };
}

test("refreshes once per interval and keeps going", async () => {
  const t = fakeTimers(); let runs = 0;
  const poll = startPolling({ run: () => { runs += 1; }, intervalMs: 1000, ...t });
  await t.advance(999); assert.equal(runs, 0);
  await t.advance(1); assert.equal(runs, 1);
  await t.advance(1000); assert.equal(runs, 2);
  poll.stop();
});

test("skips a tick while inactive (hidden tab / modal open) and resumes afterwards", async () => {
  const t = fakeTimers(); let runs = 0, active = false;
  startPolling({ run: () => { runs += 1; }, intervalMs: 1000, isActive: () => active, ...t });
  await t.advance(1000); await t.advance(1000); assert.equal(runs, 0);
  active = true; await t.advance(1000); assert.equal(runs, 1);
});

test("never overlaps: a slow request delays the next one instead of stacking another", async () => {
  const t = fakeTimers(); let starts = 0, release;
  startPolling({ run: () => { starts += 1; return new Promise((resolve) => { release = resolve; }); }, intervalMs: 1000, ...t });
  const first = t.advance(1000);                          // first request starts and stays in flight
  await Promise.resolve(); assert.equal(starts, 1);
  await t.advance(5000); assert.equal(starts, 1);         // time passes, nothing else is started
  release(); await first;
});

test("a failed refresh does not stop the polling", async () => {
  const t = fakeTimers(); let runs = 0;
  startPolling({ run: () => { runs += 1; throw new Error("network"); }, intervalMs: 1000, ...t });
  await t.advance(1000); await t.advance(1000);
  assert.equal(runs, 2);
});

test("stop cancels the pending refresh (component unmount)", async () => {
  const t = fakeTimers(); let runs = 0;
  const poll = startPolling({ run: () => { runs += 1; }, intervalMs: 1000, ...t });
  poll.stop(); await t.advance(5000);
  assert.equal(runs, 0); assert.equal(t.count(), 0);
});

test("catchUp refreshes immediately only when a refresh is overdue", async () => {
  const t = fakeTimers(); let runs = 0;
  const poll = startPolling({ run: () => { runs += 1; }, intervalMs: 1000, ...t });
  poll.catchUp(); assert.equal(runs, 0);                   // not overdue yet
  await t.advance(1000); assert.equal(runs, 1);
  poll.catchUp(); assert.equal(runs, 1);                   // just ran
  poll.stop();
});

test("works with the real global timers (no detached-method call, which browsers reject)", async () => {
  // A strict stand-in for window.setTimeout: it throws unless it is called as a plain function, exactly like a browser.
  const original = { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  const calls = [];
  globalThis.setTimeout = function strictSetTimeout(fn, ms) { if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation"); calls.push(ms); return original.setTimeout(fn, 0); };
  globalThis.clearTimeout = function strictClearTimeout(id) { if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation"); return original.clearTimeout(id); };
  try {
    let runs = 0;
    const poll = startPolling({ run: () => { runs += 1; }, intervalMs: 60_000 });
    await new Promise((resolve) => original.setTimeout(resolve, 20));
    poll.stop();
    assert.ok(runs >= 1 && calls[0] === 60_000);
  } finally { Object.assign(globalThis, original); }
});
