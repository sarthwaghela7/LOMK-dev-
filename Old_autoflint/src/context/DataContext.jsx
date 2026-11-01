import { createContext, useContext, useState, useCallback, useMemo } from "react";
import { getCalls, getCallAnalysis } from "../data/api";
import { buildMetrics } from "../utils/reportMetrics";

const DataContext = createContext(null);

export function DataProvider({ children }) {
  const [calls, setCalls] = useState([]);
  const [analysis, setAnalysis] = useState([]);
  const [dateRange, setDateRange] = useState({ start: "", end: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [customLeadSources, setCustomLeadSources] = useState(() => {
    try { return JSON.parse(localStorage.getItem("custom_lead_sources") || "[]"); } catch { return []; }
  });

  /** Fetch both calls and analysis for a date range, update shared state. */
  const fetchData = useCallback(async (startDate, endDate) => {
    if (!startDate || !endDate) return;
    setLoading(true);
    setError(null);
    try {
      const [recordsResp, analysisResp] = await Promise.all([
        getCalls(startDate, endDate),
        getCallAnalysis(startDate, endDate),
      ]);

      const recordsArray = Array.isArray(recordsResp)
        ? recordsResp
        : recordsResp?.calls || recordsResp?.results || [];

      const analysisArray = Array.isArray(analysisResp)
        ? analysisResp
        : analysisResp?.analysis || analysisResp?.results || analysisResp?.items || [];

      setCalls(recordsArray);
      setAnalysis(analysisArray);
      setDateRange({ start: startDate, end: endDate });
    } catch (err) {
      setError(err.message || "Failed to fetch data");
    } finally {
      setLoading(false);
    }
  }, []);

  /** Replace calls in-place (e.g. when CallRecords refetches). */
  const setSharedCalls = useCallback((newCalls) => {
    setCalls(Array.isArray(newCalls) ? newCalls : []);
  }, []);

  /** Replace analysis in-place. */
  const setSharedAnalysis = useCallback((newAnalysis) => {
    setAnalysis(Array.isArray(newAnalysis) ? newAnalysis : []);
  }, []);

  /** Set both at once. */
  const setSharedData = useCallback((newCalls, newAnalysis) => {
    setCalls(Array.isArray(newCalls) ? newCalls : []);
    setAnalysis(Array.isArray(newAnalysis) ? newAnalysis : []);
  }, []);

  /** Computed metrics — always derived from the shared calls + analysis. */
  const metrics = useMemo(
    () => buildMetrics(calls, analysis, { customLeadSources }),
    [calls, analysis, customLeadSources]
  );

  const value = useMemo(
    () => ({
      calls,
      analysis,
      dateRange,
      loading,
      error,
      metrics,
      customLeadSources,
      setCustomLeadSources,
      fetchData,
      setSharedCalls,
      setSharedAnalysis,
      setSharedData,
    }),
    [calls, analysis, dateRange, loading, error, metrics, customLeadSources, fetchData, setSharedCalls, setSharedAnalysis, setSharedData]
  );

  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}

export function useData() {
  const ctx = useContext(DataContext);
  if (!ctx) throw new Error("useData must be used within a DataProvider");
  return ctx;
}
