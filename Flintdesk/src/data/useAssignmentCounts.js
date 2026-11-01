import { useCallback, useEffect, useRef, useState } from "react";
import { getAssignmentCounts } from "./api.js";
import { startPolling } from "./polling.js";

const POLL_MS = 20_000;

// The signed-in agent's pending count per Assigning the Calls tab (server-side, always the caller's own). Refreshed on a quiet poll,
// when the tab becomes visible again, and whenever the app announces changed data (a new notification, a save), so a badge follows
// an assignment, completion or reassignment without a page refresh. `refresh()` re-reads on demand.
export function useAssignmentCounts(enabled) {
  const [counts, setCounts] = useState({});
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    const result = await getAssignmentCounts();
    if (alive.current) setCounts(result?.counts || {});
  }, []);

  useEffect(() => {
    alive.current = true;
    if (!enabled) { setCounts({}); return undefined; }
    refresh().catch(() => {});
    const poller = startPolling({ run: refresh, intervalMs: POLL_MS, isActive: () => document.visibilityState === "visible" });
    let changeTimer;
    const onChanged = () => { clearTimeout(changeTimer); changeTimer = setTimeout(() => refresh().catch(() => {}), 300); };
    const onVisible = () => { if (document.visibilityState === "visible") poller.catchUp(); };
    window.addEventListener("flintdesk-data-changed", onChanged);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive.current = false;
      poller.stop();
      clearTimeout(changeTimer);
      window.removeEventListener("flintdesk-data-changed", onChanged);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled, refresh]);

  return counts;
}
