// One controller survives filter changes, so an old request cannot overlap a
// new request. A newer filter refresh is queued once; timer ticks are dropped.
export function createPageRefresh(run) {
  let active = false;
  let generation = 0;
  let inFlight = false;
  let pending = false;
  async function refresh(silent = true, queue = false) {
    if (!active) return;
    if (inFlight) { if (queue) pending = true; return; }
    const started = generation;
    inFlight = true;
    try {
      await run({ silent, isCurrent: () => active && generation === started });
    } finally {
      inFlight = false;
      if (active && pending) { pending = false; await refresh(false); }
    }
  }
  return {
    refresh,
    activate() { active = true; generation += 1; },
    deactivate() { active = false; generation += 1; pending = false; },
  };
}
export const nextRefreshDelay = (random = Math.random) => 60_000 + Math.floor(random() * 60_000);
