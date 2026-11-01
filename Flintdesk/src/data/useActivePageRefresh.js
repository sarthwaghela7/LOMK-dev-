import { useEffect, useRef } from "react";
import { createPageRefresh, nextRefreshDelay } from "./pageRefresh.js";

export function useActivePageRefresh(isActive, refresh) {
  const latest = useRef(refresh);
  latest.current = refresh;
  const controller = useRef(null);
  if (!controller.current) controller.current = createPageRefresh((context) => latest.current(context));
  useEffect(() => {
    const current = controller.current;
    let timer;
    let changeTimer;
    let disposed = false;
    const schedule = () => {
      if (disposed || !isActive || document.hidden) return;
      timer = setTimeout(async () => {
        await current.refresh(true);
        schedule();
      }, nextRefreshDelay());
    };
    const syncVisibility = () => {
      clearTimeout(timer);
      current.deactivate();
      if (!isActive || document.hidden) return;
      current.activate();
      void current.refresh(false, true);
      schedule();
    };
    const onChanged = () => {
      if (!isActive || document.hidden) return;
      clearTimeout(changeTimer);
      changeTimer = setTimeout(() => { void current.refresh(true, true); }, 250);
    };
    syncVisibility();
    document.addEventListener("visibilitychange", syncVisibility);
    window.addEventListener("flintdesk-data-changed", onChanged);
    const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("flintdesk-data-changed") : null;
    if (channel) channel.onmessage = onChanged;
    return () => {
      disposed = true;
      clearTimeout(timer);
      clearTimeout(changeTimer);
      current.deactivate();
      document.removeEventListener("visibilitychange", syncVisibility);
      window.removeEventListener("flintdesk-data-changed", onChanged);
      channel?.close();
    };
  }, [isActive, refresh]);
}
