import { useEffect, useRef } from "react";
import { createPageRefresh } from "./pageRefresh.js";

// Pages stay mounted in App. Refresh only when query inputs or an explicit
// fetch revision change; navigation, visibility and timers are not triggers.
export function useExplicitPageRefresh(refresh, revision = 0) {
  const latest = useRef(refresh);
  latest.current = refresh;
  const controller = useRef(null);
  if (!controller.current) controller.current = createPageRefresh((context) => latest.current(context));
  useEffect(() => {
    const current = controller.current;
    current.activate();
    void current.refresh(false, true);
    return () => current.deactivate();
  }, [refresh, revision]);
}
