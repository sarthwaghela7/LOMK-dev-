// Gentle live refresh for read-only views: one request at a time, never while the tab is hidden or the view is
// busy, and never overlapping (the next wait starts only after the previous request has finished).
//
// startPolling({ run, intervalMs, isActive }) -> stop()
//   run()       the refresh; may be async. A rejection is swallowed here (the caller keeps what it already shows).
//   isActive()  false = skip this tick (hidden tab, modal open, download running, ...); asked again next tick.
// `catchUp()` on the returned handle runs a refresh now if one is overdue (used when the tab becomes visible again).
export function startPolling({ run, intervalMs, isActive = () => true, now = () => Date.now(), timers = {} }) {
  // The browser's setTimeout/clearTimeout throw "Illegal invocation" when called as methods of another object, so
  // they are always called as plain functions.
  const setTimer = timers.setTimeout ? (fn, ms) => timers.setTimeout(fn, ms) : (fn, ms) => globalThis.setTimeout(fn, ms);
  const clearTimer = timers.clearTimeout ? (id) => timers.clearTimeout(id) : (id) => globalThis.clearTimeout(id);
  let stopped = false, timer = null, running = false, lastRun = now();

  const schedule = (delay) => {
    if (stopped) return;
    timer = setTimer(tick, delay);
  };
  const tick = async () => {
    timer = null;
    if (stopped) return;
    if (running || !isActive()) { schedule(intervalMs); return; }
    running = true;
    try { await run(); } catch { /* keep the data already on screen */ } finally { running = false; lastRun = now(); }
    schedule(intervalMs);
  };
  schedule(intervalMs);

  return {
    stop() { stopped = true; if (timer !== null) clearTimer(timer); timer = null; },
    catchUp() {
      if (stopped || running || now() - lastRun < intervalMs) return;
      if (timer !== null) { clearTimer(timer); timer = null; }
      tick();
    },
  };
}
