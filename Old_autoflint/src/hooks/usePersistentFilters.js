import { useState, useEffect, useCallback } from "react";

const STORAGE_KEY = "call_records_filters";
const DASHBOARD_FILTERS_KEY = "dashboard_filters";

function getSavedFilters() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return JSON.parse(saved);
  } catch {
    // ignore
  }
  return null;
}

function getSavedDashboardFilters() {
  try {
    const saved = localStorage.getItem(DASHBOARD_FILTERS_KEY);
    if (saved) return JSON.parse(saved);
  } catch {
    // ignore
  }
  return null;
}

export function usePersistentFilters() {
  const [filters, setFilters] = useState(() => {
    const saved = getSavedFilters();
    return saved || {
      startDate: "",
      endDate: "",
      extension: "",
    };
  });

  // NOTE: The full call dataset is intentionally NOT read from or written
  // to localStorage here — caching the entire dataset (transcripts, audio
  // refs, etc.) is what previously exceeded the browser's storage quota
  // under the "call_records_data" key. Calls should always come from the
  // existing API/state flow instead; only lightweight filters are cached.
  const [calls, setCalls] = useState([]);

  // Save filters whenever they change
  // NOTE: Wrapped in try/catch (previously unguarded) so saving these small
  // filters can never throw and crash the app, even if localStorage is
  // still full for some other reason — it just skips the cache instead.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
    } catch (error) {
      if (error instanceof DOMException && error.name === "QuotaExceededError") {
        console.warn("localStorage quota exceeded. Skipping filter cache.");
      } else {
        throw error;
      }
    }
  }, [filters]);

  // NOTE: The previous "Save calls whenever they change" effect (which
  // wrote the full call dataset to localStorage under "call_records_data")
  // has been removed entirely — that write was the source of the
  // QuotaExceededError. Call data now only ever lives in this hook's React
  // state / the app's existing API flow, never in localStorage.

  const updateFilters = useCallback((updater) => {
    setFilters((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      return { ...prev, ...next };
    });
  }, []);

  const clearFilters = useCallback(() => {
    setFilters({ startDate: "", endDate: "", extension: "" });
    setCalls([]);
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem("call_records_data");
  }, []);

  return { filters, setFilters: updateFilters, calls, setCalls, clearFilters };
}

// ─── Dashboard-specific persistent filters ──────────────────────────────────
// Defaults the date filters to yesterday (start === end) so the page never
// opens with an unfiltered/empty date range.
function getYesterdayISO() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

export function useDashboardFilters() {
  const [dashboardFilters, setDashboardFilters] = useState(() => {
    const saved = getSavedDashboardFilters();

    return saved || {
      startDate: getYesterdayISO(),
      endDate: getYesterdayISO(),
      agent: "",
      minQualityScore: "",
      maxQualityScore: "",
    };
  });

  // Save dashboard filters whenever they change
  useEffect(() => {
    localStorage.setItem(DASHBOARD_FILTERS_KEY, JSON.stringify(dashboardFilters));
  }, [dashboardFilters]);

  const updateDashboardFilters = useCallback((updater) => {
    setDashboardFilters((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      return { ...prev, ...next };
    });
  }, []);

  const resetDashboardFilters = useCallback(() => {
    setDashboardFilters({
      startDate: getYesterdayISO(),
      endDate: getYesterdayISO(),
      agent: "",
      minQualityScore: "",
      maxQualityScore: "",
    });
  }, []);

  return {
    dashboardFilters,
    setDashboardFilters: updateDashboardFilters,
    resetDashboardFilters,
  };
}